#include <hbfsim/prefetch_model.hpp>

#include <algorithm>
#include <cstdint>
#include <deque>
#include <queue>
#include <random>
#include <unordered_map>

namespace hbfsim {
namespace {

// A page number no stream produces, used when a prediction is wrong. It has to
// be outside every generated stream so a wrong prediction can never be right
// by accident.
constexpr std::uint64_t kWrongPageBase = 1ULL << 60;

// The media serves `max_in_flight` reads at once. Each server is described by
// the time it next becomes free, so a read issued at `now` starts at
// max(now, earliest free) and finishes one read latency later. This is the
// same shape as the queue depth the MQSim adapter enforces.
class MediaServers {
  public:
    MediaServers(std::uint32_t count, std::uint64_t read_latency_ns)
        : read_latency_ns_(read_latency_ns)
    {
        // A min-heap rather than a scan, so a large concurrency setting costs
        // log(count) per access instead of count. The sweep runs streams of
        // roughly a million accesses against concurrencies in the thousands,
        // where the scan would dominate the run.
        const auto servers = std::max<std::uint32_t>(1, count);
        for (std::uint32_t index = 0; index < servers; ++index) {
            free_at_.push(0);
        }
    }

    // Occupies the server that frees first and returns when the read lands.
    std::uint64_t issue(std::uint64_t now)
    {
        const auto earliest = free_at_.top();
        free_at_.pop();
        const auto start = std::max(now, earliest);
        const auto completion = start + read_latency_ns_;
        free_at_.push(completion);
        return completion;
    }

  private:
    std::priority_queue<std::uint64_t, std::vector<std::uint64_t>,
                        std::greater<std::uint64_t>>
        free_at_;
    std::uint64_t read_latency_ns_;
};

// Pages that have been fetched and not yet used, with the time each lands.
// Eviction is oldest-first: a page fetched too far ahead of its use is the one
// pushed out when the buffer is full.
class StagingBuffer {
  public:
    explicit StagingBuffer(std::uint32_t capacity)
        : capacity_(std::max<std::uint32_t>(1, capacity))
    {
    }

    [[nodiscard]] bool holds(std::uint64_t page) const
    {
        return arrival_.contains(page);
    }

    // Returns how many pages were dropped to make room.
    std::uint64_t insert(std::uint64_t page, std::uint64_t arrival_ns)
    {
        arrival_.emplace(page, arrival_ns);
        order_.push_back(page);
        std::uint64_t evicted = 0;
        while (order_.size() > capacity_) {
            const auto victim = order_.front();
            order_.pop_front();
            if (arrival_.erase(victim) != 0) {
                ++evicted;
            }
        }
        return evicted;
    }

    // Removes the page and reports when it landed.
    std::uint64_t take(std::uint64_t page)
    {
        const auto found = arrival_.find(page);
        const auto arrival = found->second;
        arrival_.erase(found);
        const auto position = std::find(order_.begin(), order_.end(), page);
        if (position != order_.end()) {
            order_.erase(position);
        }
        return arrival;
    }

    [[nodiscard]] std::size_t size() const { return arrival_.size(); }

  private:
    std::uint32_t capacity_;
    std::unordered_map<std::uint64_t, std::uint64_t> arrival_;
    std::deque<std::uint64_t> order_;
};

}  // namespace

double PrefetchStats::achieved_accuracy() const noexcept
{
    if (prefetch_issued == 0) {
        return 0.0;
    }
    return static_cast<double>(prefetch_hits) /
           static_cast<double>(prefetch_issued);
}

PrefetchStats simulate_prefetch(const std::vector<std::uint64_t>& pages,
                                const PrefetchConfig& config)
{
    PrefetchStats stats{};
    stats.demand_accesses = pages.size();
    if (pages.empty()) {
        return stats;
    }

    MediaServers media(config.max_in_flight, config.read_latency_ns);
    StagingBuffer buffer(config.buffer_pages);
    std::mt19937_64 rng(config.seed);
    std::uniform_real_distribution<double> draw(0.0, 1.0);

    std::uint64_t clock = 0;
    std::uint64_t wrong_page = kWrongPageBase;

    for (std::size_t index = 0; index < pages.size(); ++index) {
        // The accelerator does its work for this access first. This is the
        // interval a prefetch issued earlier has been hiding behind.
        clock += config.compute_ns_per_access;

        // Issue the prefetch for a later access. Lead distance 0 means the
        // fetch is issued at the point of use, which is not a prefetch and
        // cannot hide anything.
        if (config.policy != PrefetchPolicy::None && config.lead_distance > 0) {
            const auto target = index + config.lead_distance;
            bool predict = false;
            std::uint64_t predicted = 0;
            if (config.policy == PrefetchPolicy::NextPage) {
                // Carries no model of the workload: the page after this one.
                predicted = pages[index] + 1;
                predict = true;
            } else if (target < pages.size()) {
                if (draw(rng) < config.accuracy) {
                    predicted = pages[target];
                } else {
                    predicted = wrong_page++;
                }
                predict = true;
            }
            if (predict && !buffer.holds(predicted)) {
                const auto arrival = media.issue(clock);
                stats.prefetch_wasted += buffer.insert(predicted, arrival);
                ++stats.prefetch_issued;
            }
        }

        // Resolve the demand.
        const auto page = pages[index];
        if (buffer.holds(page)) {
            const auto arrival = buffer.take(page);
            ++stats.prefetch_hits;
            if (arrival > clock) {
                // Fetched early, but not early enough: the remainder is stall.
                stats.stall_ns += arrival - clock;
                clock = arrival;
            }
        } else {
            const auto arrival = media.issue(clock);
            ++stats.demand_misses;
            stats.stall_ns += arrival - clock;
            clock = arrival;
        }
    }

    // Anything still held was fetched and never used.
    stats.prefetch_wasted += buffer.size();
    stats.total_ns = clock;
    return stats;
}

std::vector<std::uint64_t> make_sequential_stream(std::uint64_t accesses)
{
    std::vector<std::uint64_t> pages;
    pages.reserve(accesses);
    for (std::uint64_t index = 0; index < accesses; ++index) {
        pages.push_back(index);
    }
    return pages;
}

std::vector<std::uint64_t> make_random_stream(std::uint64_t accesses,
                                              std::uint64_t pages_available,
                                              std::uint64_t seed)
{
    std::vector<std::uint64_t> pages;
    pages.reserve(accesses);
    std::mt19937_64 rng(seed);
    std::uniform_int_distribution<std::uint64_t> pick(
        0, pages_available == 0 ? 0 : pages_available - 1);
    for (std::uint64_t index = 0; index < accesses; ++index) {
        pages.push_back(pick(rng));
    }
    return pages;
}

std::vector<std::uint64_t> make_moe_stream(std::uint64_t tokens,
                                           std::uint64_t layers,
                                           std::uint64_t experts_per_layer,
                                           std::uint64_t experts_per_token,
                                           std::uint64_t pages_per_expert,
                                           std::uint64_t seed)
{
    std::vector<std::uint64_t> pages;
    if (experts_per_layer == 0 || pages_per_expert == 0) {
        return pages;
    }
    const auto chosen = std::min(experts_per_token, experts_per_layer);
    pages.reserve(tokens * layers * chosen * pages_per_expert);
    std::mt19937_64 rng(seed);
    std::vector<std::uint64_t> experts(experts_per_layer);
    for (std::uint64_t token = 0; token < tokens; ++token) {
        for (std::uint64_t layer = 0; layer < layers; ++layer) {
            for (std::uint64_t expert = 0; expert < experts_per_layer;
                 ++expert) {
                experts[expert] = expert;
            }
            // The router picks this layer's experts for this token. Which ones
            // is not known until the previous layer has produced its output,
            // so no policy that looks only at addresses can predict them.
            std::shuffle(experts.begin(), experts.end(), rng);
            for (std::uint64_t slot = 0; slot < chosen; ++slot) {
                const auto base =
                    (layer * experts_per_layer + experts[slot]) *
                    pages_per_expert;
                for (std::uint64_t page = 0; page < pages_per_expert; ++page) {
                    pages.push_back(base + page);
                }
            }
        }
    }
    return pages;
}

}  // namespace hbfsim

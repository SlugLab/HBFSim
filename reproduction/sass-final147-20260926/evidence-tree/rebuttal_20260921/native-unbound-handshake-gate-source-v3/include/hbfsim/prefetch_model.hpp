#pragma once

// A deterministic model of prefetching in front of the HBF tier.
//
// HBF's case rests on the accelerator issuing a read before the data is
// needed, so that the media latency is spent while something else runs. HBFSim
// models no prefetch at all: a registered access is charged where the load
// issues and nothing is ever fetched ahead of its use. This model exists to
// answer one question the paper has to answer without waiting for a full
// device-side implementation: how accurate does a prefetcher have to be before
// the HBF tier stops costing what it costs today?
//
// The model is a discrete-event simulation over an access stream, with no
// randomness beyond the seeded predictor, so a given configuration always
// produces the same numbers.
//
// WHAT IT DOES NOT CLAIM. This is not a measurement of any real predictor, of
// any GPU, or of any device. It says what a prefetcher of a stated accuracy
// and a stated lead distance would be worth against a stated media latency. It
// is the arithmetic the paper's latency argument rests on, made explicit and
// swept, not a hardware result.

#include <cstdint>
#include <vector>

namespace hbfsim {

enum class PrefetchPolicy {
    // Nothing is fetched ahead. This is what HBFSim does today and is the
    // baseline every other policy is reported against.
    None,
    // On a demand for page N, fetch page N+1. Carries no model of the
    // workload and needs no training: the naive policy. Its lead is one
    // access by construction, so `lead_distance` only switches it on and off
    // and does not change what it predicts.
    NextPage,
    // Predicts the page the stream will demand `lead_distance` accesses from
    // now, and is correct with probability `accuracy`. This is how the
    // accuracy axis is swept without inventing a predictor.
    Accuracy,
};

struct PrefetchConfig {
    PrefetchPolicy policy{PrefetchPolicy::None};

    // Probability that an Accuracy-policy prediction names the page the
    // stream will really demand. Ignored by the other policies.
    double accuracy{1.0};

    // How many accesses ahead of its use a prefetch is issued. 0 means the
    // fetch is issued at the moment of use, which cannot hide anything.
    std::uint32_t lead_distance{1};

    // Pages that can be held after being fetched and before being used. A
    // prefetch issued too far ahead is evicted before it is used.
    std::uint32_t buffer_pages{64};

    // How many media reads the device serves at once.
    std::uint32_t max_in_flight{32};

    // Media read latency, the tR the sweep varies.
    std::uint64_t read_latency_ns{10'000};

    // Time the accelerator spends between two accesses. This is what a
    // prefetch has to hide behind, and setting it to zero means there is no
    // work to overlap with.
    std::uint64_t compute_ns_per_access{0};

    std::uint64_t seed{1};
};

struct PrefetchStats {
    // Modeled time for the whole stream.
    std::uint64_t total_ns{0};
    // Of total_ns, the part spent waiting for media rather than computing.
    std::uint64_t stall_ns{0};
    std::uint64_t demand_accesses{0};
    // Demands that found no resident page and paid a full media read.
    std::uint64_t demand_misses{0};
    // Demands that found a page a prefetch had already brought in. A page that
    // was fetched early but has not arrived yet counts here too, and its
    // remaining wait is in stall_ns.
    std::uint64_t prefetch_hits{0};
    std::uint64_t prefetch_issued{0};
    // Prefetched pages that were evicted or left over without ever being used.
    // These cost media bandwidth and bought nothing.
    std::uint64_t prefetch_wasted{0};

    // prefetch_hits / prefetch_issued. Zero when nothing was issued.
    [[nodiscard]] double achieved_accuracy() const noexcept;
};

// `pages` is the sequence of media pages the workload demands, in order.
PrefetchStats simulate_prefetch(const std::vector<std::uint64_t>& pages,
                                const PrefetchConfig& config);

// Access streams the sweep runs over. `pages` is the number of distinct pages
// the stream may touch.
std::vector<std::uint64_t> make_sequential_stream(std::uint64_t accesses);
std::vector<std::uint64_t> make_random_stream(std::uint64_t accesses,
                                              std::uint64_t pages,
                                              std::uint64_t seed);
// Repeats a fixed set of layers, and within each layer picks `experts_per_token`
// out of `experts_per_layer` at random. The pages of a layer's chosen experts
// cannot be known before that layer's router has run, which is the case a
// next-page policy cannot serve.
std::vector<std::uint64_t> make_moe_stream(std::uint64_t tokens,
                                           std::uint64_t layers,
                                           std::uint64_t experts_per_layer,
                                           std::uint64_t experts_per_token,
                                           std::uint64_t pages_per_expert,
                                           std::uint64_t seed);

}  // namespace hbfsim

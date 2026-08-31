// Contract for the prefetch model. Every assertion here is a property the
// paper's latency argument depends on, so a change that breaks one of them
// changes what the paper may claim.
//
// Written before include/hbfsim/prefetch_model.hpp had an implementation.

#include <hbfsim/prefetch_model.hpp>

#include <cstdio>
#include <vector>

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            std::printf("failed at line %d: %s\n", __LINE__, #condition);      \
            return __LINE__;                                                   \
        }                                                                      \
    } while (false)

namespace {

hbfsim::PrefetchConfig base_config()
{
    hbfsim::PrefetchConfig config{};
    config.lead_distance = 4;
    config.buffer_pages = 64;
    config.max_in_flight = 32;
    config.read_latency_ns = 10'000;
    // Each access has real work behind it, so a prefetch has something to hide
    // behind. Without this every policy is bounded by media time alone.
    config.compute_ns_per_access = 4'000;
    config.seed = 7;
    return config;
}

}  // namespace

int main()
{
    const auto sequential = hbfsim::make_sequential_stream(2'000);
    const auto random_stream = hbfsim::make_random_stream(2'000, 100'000, 11);

    // 1. No prefetch pays a full media read on every distinct page.
    hbfsim::PrefetchStats none{};
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::None;
        none = hbfsim::simulate_prefetch(sequential, config);
        CHECK(none.demand_accesses == sequential.size());
        CHECK(none.prefetch_issued == 0);
        CHECK(none.demand_misses == sequential.size());
        CHECK(none.stall_ns > 0);
    }

    // 2. The model is deterministic: the same configuration twice gives the
    // same numbers, or no swept figure is reproducible.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        config.accuracy = 0.5;
        const auto first = hbfsim::simulate_prefetch(sequential, config);
        const auto second = hbfsim::simulate_prefetch(sequential, config);
        CHECK(first.total_ns == second.total_ns);
        CHECK(first.prefetch_hits == second.prefetch_hits);
    }

    // 3. Accuracy 0 buys nothing. It must not come out faster than no prefetch
    // at all, and it must waste every page it fetched.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        config.accuracy = 0.0;
        const auto stats = hbfsim::simulate_prefetch(sequential, config);
        CHECK(stats.prefetch_hits == 0);
        CHECK(stats.prefetch_issued > 0);
        CHECK(stats.prefetch_wasted == stats.prefetch_issued);
        CHECK(stats.total_ns >= none.total_ns);
    }

    // 4. Perfect accuracy with enough lead, buffer and concurrency removes
    // every stall except the unavoidable warm-up. A prefetcher issuing L
    // accesses ahead cannot cover the first L accesses: nothing predicted
    // them. So the misses are exactly the warm-up, and the whole stream costs
    // its compute plus at most those L media reads.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        config.accuracy = 1.0;
        config.lead_distance = 8;
        const auto stats = hbfsim::simulate_prefetch(sequential, config);
        CHECK(stats.demand_misses == config.lead_distance);
        CHECK(stats.total_ns <=
              sequential.size() * config.compute_ns_per_access +
                  config.lead_distance * config.read_latency_ns);
        // The steady state carries no stall at all, so the whole run must be
        // far cheaper than paying a media read on every access.
        CHECK(stats.total_ns < none.total_ns / 2);
    }

    // 5. Lead distance 0 hides nothing even at perfect accuracy. This is the
    // point the paper cannot drop: knowing the address is not the same as
    // having time to use it.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        config.accuracy = 1.0;
        config.lead_distance = 0;
        const auto stats = hbfsim::simulate_prefetch(sequential, config);
        CHECK(stats.stall_ns > 0);
        CHECK(stats.total_ns >= none.total_ns);
    }

    // 6. Time is monotone in accuracy: more accurate never costs more.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        std::uint64_t previous = 0;
        bool first = true;
        for (const double accuracy : {0.0, 0.25, 0.5, 0.75, 1.0}) {
            config.accuracy = accuracy;
            const auto stats = hbfsim::simulate_prefetch(sequential, config);
            if (!first) {
                CHECK(stats.total_ns <= previous);
            }
            previous = stats.total_ns;
            first = false;
        }
    }

    // 7. A one-page buffer cannot hold a prefetch issued eight accesses early,
    // so a large lead with a tiny buffer must do worse than the same lead with
    // a large buffer. Capacity is a real limit, not a formality.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        config.accuracy = 1.0;
        config.lead_distance = 8;
        config.buffer_pages = 1;
        const auto small = hbfsim::simulate_prefetch(sequential, config);
        config.buffer_pages = 64;
        const auto large = hbfsim::simulate_prefetch(sequential, config);
        CHECK(small.total_ns > large.total_ns);
    }

    // 8. The naive next-page policy is near-perfect on a sequential stream and
    // worthless on a random one. This is the claim the paper wants to make
    // about a prefetcher that carries no model of the workload.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::NextPage;
        config.lead_distance = 1;
        const auto ordered = hbfsim::simulate_prefetch(sequential, config);
        CHECK(ordered.achieved_accuracy() > 0.9);
        CHECK(ordered.total_ns < none.total_ns);

        const auto scattered = hbfsim::simulate_prefetch(random_stream, config);
        CHECK(scattered.achieved_accuracy() < 0.1);
    }

    // 9. Concurrency bounds the benefit: with one media read at a time, a
    // perfect prefetcher still cannot keep up with a stream whose compute is
    // shorter than one read.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        config.accuracy = 1.0;
        config.lead_distance = 8;
        config.max_in_flight = 1;
        const auto stats = hbfsim::simulate_prefetch(sequential, config);
        CHECK(stats.stall_ns > 0);
    }

    // 10. A Mixture-of-Experts stream is where the next-page policy has
    // nothing to go on, because the pages of a layer are chosen per token.
    {
        const auto moe = hbfsim::make_moe_stream(40, 48, 128, 8, 2, 5);
        CHECK(!moe.empty());
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::NextPage;
        const auto naive = hbfsim::simulate_prefetch(moe, config);
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        config.accuracy = 1.0;
        const auto perfect = hbfsim::simulate_prefetch(moe, config);
        CHECK(perfect.total_ns < naive.total_ns);
    }

    // 11. Every prefetched page is either used or wasted; nothing is lost.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        config.accuracy = 0.6;
        const auto stats = hbfsim::simulate_prefetch(sequential, config);
        CHECK(stats.prefetch_hits + stats.prefetch_wasted ==
              stats.prefetch_issued);
        CHECK(stats.prefetch_hits + stats.demand_misses ==
              stats.demand_accesses);
    }

    // 12. Total time always accounts for itself.
    {
        auto config = base_config();
        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        config.accuracy = 0.8;
        const auto stats = hbfsim::simulate_prefetch(sequential, config);
        CHECK(stats.total_ns ==
              sequential.size() * config.compute_ns_per_access +
                  stats.stall_ns);
    }

    return 0;
}

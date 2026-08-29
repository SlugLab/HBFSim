// Sweeps prefetch accuracy against media latency and writes the cells the
// accuracy figure is drawn from.
//
// The figure this feeds has prefetch accuracy on the x axis and modeled time
// on the y axis, one curve per media latency. Two reference points are
// reported alongside every curve: the no-prefetch baseline, which is what
// HBFSim models today, and the next-page policy, which carries no model of the
// workload at all and shows what accuracy a prefetcher reaches for free.
//
// Every number this prints comes out of the model in
// src/prefetch/prefetch_model.cpp. None of it is a hardware measurement.

#include <hbfsim/prefetch_model.hpp>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

struct Options {
    std::string stream{"sequential"};
    std::uint64_t accesses{20'000};
    std::uint64_t compute_ns_per_access{4'000};
    std::uint32_t lead_distance{8};
    std::uint32_t buffer_pages{64};
    std::uint32_t max_in_flight{32};
    std::uint64_t seed{7};
    // Mixture-of-Experts stream shape. The defaults follow Qwen3-30B-A3B:
    // 48 layers, 128 experts per layer, 8 activated per token.
    std::uint64_t layers{48};
    std::uint64_t experts_per_layer{128};
    std::uint64_t experts_per_token{8};
    std::uint64_t pages_per_expert{2};
};

// The six media latencies the experiment list fixes: 1, 2, 4, 5, 10, 20 us.
const std::vector<std::uint64_t> kReadLatencyNs{1'000, 2'000, 4'000,
                                                5'000, 10'000, 20'000};

const std::vector<double> kAccuracies{0.0,  0.1,  0.2,  0.3,  0.4,  0.5,
                                      0.6,  0.7,  0.8,  0.9,  0.95, 1.0};

std::vector<std::uint64_t> build_stream(const Options& options)
{
    if (options.stream == "random") {
        return hbfsim::make_random_stream(options.accesses,
                                          options.accesses * 8, options.seed);
    }
    if (options.stream == "moe") {
        const auto per_token = options.layers * options.experts_per_token *
                               options.pages_per_expert;
        const auto tokens =
            per_token == 0 ? 0 : std::max<std::uint64_t>(1, options.accesses /
                                                                per_token);
        return hbfsim::make_moe_stream(tokens, options.layers,
                                       options.experts_per_layer,
                                       options.experts_per_token,
                                       options.pages_per_expert, options.seed);
    }
    return hbfsim::make_sequential_stream(options.accesses);
}

void print_cell(const char* policy, double requested_accuracy,
                std::uint64_t read_latency_ns,
                const hbfsim::PrefetchStats& stats,
                std::uint64_t baseline_ns, std::uint64_t compute_floor_ns,
                bool last)
{
    // recovered_fraction is how much of the gap between doing nothing and the
    // compute-bound floor this configuration closed. 0 means it bought
    // nothing; 1 means the media cost disappeared.
    const auto span = baseline_ns > compute_floor_ns
                          ? baseline_ns - compute_floor_ns
                          : 0;
    const double recovered =
        span == 0 ? 0.0
                  : static_cast<double>(baseline_ns - stats.total_ns) /
                        static_cast<double>(span);
    std::printf(
        "    {\"policy\": \"%s\", \"requested_accuracy\": %.4f, "
        "\"read_latency_ns\": %llu, \"total_ns\": %llu, \"stall_ns\": %llu, "
        "\"demand_misses\": %llu, \"prefetch_issued\": %llu, "
        "\"prefetch_hits\": %llu, \"prefetch_wasted\": %llu, "
        "\"achieved_accuracy\": %.6f, \"speedup_over_no_prefetch\": %.6f, "
        "\"recovered_fraction\": %.6f}%s\n",
        policy, requested_accuracy,
        static_cast<unsigned long long>(read_latency_ns),
        static_cast<unsigned long long>(stats.total_ns),
        static_cast<unsigned long long>(stats.stall_ns),
        static_cast<unsigned long long>(stats.demand_misses),
        static_cast<unsigned long long>(stats.prefetch_issued),
        static_cast<unsigned long long>(stats.prefetch_hits),
        static_cast<unsigned long long>(stats.prefetch_wasted),
        stats.achieved_accuracy(),
        stats.total_ns == 0
            ? 0.0
            : static_cast<double>(baseline_ns) /
                  static_cast<double>(stats.total_ns),
        recovered, last ? "" : ",");
}

bool read_u64(int argc, char** argv, int& index, const char* name,
              std::uint64_t& out)
{
    if (std::strcmp(argv[index], name) != 0) {
        return false;
    }
    if (index + 1 >= argc) {
        std::fprintf(stderr, "hbf_prefetch_bench: missing value for %s\n",
                     name);
        std::exit(64);
    }
    out = std::strtoull(argv[++index], nullptr, 10);
    return true;
}

}  // namespace

int main(int argc, char** argv)
{
    Options options{};
    std::uint64_t scratch = 0;
    for (int index = 1; index < argc; ++index) {
        if (std::strcmp(argv[index], "--stream") == 0 && index + 1 < argc) {
            options.stream = argv[++index];
        } else if (read_u64(argc, argv, index, "--accesses", scratch)) {
            options.accesses = scratch;
        } else if (read_u64(argc, argv, index, "--compute-ns", scratch)) {
            options.compute_ns_per_access = scratch;
        } else if (read_u64(argc, argv, index, "--lead", scratch)) {
            options.lead_distance = static_cast<std::uint32_t>(scratch);
        } else if (read_u64(argc, argv, index, "--buffer-pages", scratch)) {
            options.buffer_pages = static_cast<std::uint32_t>(scratch);
        } else if (read_u64(argc, argv, index, "--max-in-flight", scratch)) {
            options.max_in_flight = static_cast<std::uint32_t>(scratch);
        } else if (read_u64(argc, argv, index, "--seed", scratch)) {
            options.seed = scratch;
        } else if (read_u64(argc, argv, index, "--pages-per-expert",
                            scratch)) {
            options.pages_per_expert = scratch;
        } else {
            std::fprintf(stderr,
                         "usage: hbf_prefetch_bench [--stream "
                         "sequential|random|moe] [--accesses N] "
                         "[--compute-ns N] [--lead N] [--buffer-pages N] "
                         "[--max-in-flight N] [--seed N] "
                         "[--pages-per-expert N]\n");
            return 64;
        }
    }

    const auto pages = build_stream(options);
    if (pages.empty()) {
        std::fprintf(stderr, "hbf_prefetch_bench: empty access stream\n");
        return 65;
    }

    hbfsim::PrefetchConfig config{};
    config.lead_distance = options.lead_distance;
    config.buffer_pages = options.buffer_pages;
    config.max_in_flight = options.max_in_flight;
    config.compute_ns_per_access = options.compute_ns_per_access;
    config.seed = options.seed;

    const auto compute_floor_ns =
        static_cast<std::uint64_t>(pages.size()) *
        options.compute_ns_per_access;

    std::printf("{\n");
    std::printf("  \"schema_version\": 1,\n");
    std::printf("  \"model\": \"src/prefetch/prefetch_model.cpp\",\n");
    std::printf(
        "  \"disclaimer\": \"modeled, not measured on any device or GPU\",\n");
    std::printf(
        "  \"workload\": {\"stream\": \"%s\", \"accesses\": %llu, "
        "\"compute_ns_per_access\": %llu, \"lead_distance\": %u, "
        "\"buffer_pages\": %u, \"max_in_flight\": %u, \"seed\": %llu},\n",
        options.stream.c_str(),
        static_cast<unsigned long long>(pages.size()),
        static_cast<unsigned long long>(options.compute_ns_per_access),
        options.lead_distance, options.buffer_pages, options.max_in_flight,
        static_cast<unsigned long long>(options.seed));
    std::printf("  \"compute_floor_ns\": %llu,\n",
                static_cast<unsigned long long>(compute_floor_ns));
    std::printf("  \"cells\": [\n");

    bool first = true;
    for (const auto read_latency_ns : kReadLatencyNs) {
        config.read_latency_ns = read_latency_ns;

        config.policy = hbfsim::PrefetchPolicy::None;
        const auto baseline = hbfsim::simulate_prefetch(pages, config);
        if (!first) {
            std::printf(",\n");
        }
        first = false;
        print_cell("none", 0.0, read_latency_ns, baseline, baseline.total_ns,
                   compute_floor_ns, true);

        config.policy = hbfsim::PrefetchPolicy::NextPage;
        const auto naive = hbfsim::simulate_prefetch(pages, config);
        std::printf(",\n");
        print_cell("next_page", naive.achieved_accuracy(), read_latency_ns,
                   naive, baseline.total_ns, compute_floor_ns, true);

        config.policy = hbfsim::PrefetchPolicy::Accuracy;
        for (const auto accuracy : kAccuracies) {
            config.accuracy = accuracy;
            const auto stats = hbfsim::simulate_prefetch(pages, config);
            std::printf(",\n");
            print_cell("accuracy", accuracy, read_latency_ns, stats,
                       baseline.total_ns, compute_floor_ns, true);
        }
    }

    std::printf("\n  ]\n}\n");
    return 0;
}

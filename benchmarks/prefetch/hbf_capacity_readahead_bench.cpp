// Drives the real capacity-mode readahead over an access stream and reports
// how many demands reached the media with readahead off and with it on.
//
// This measures the implementation in src/host_service/capacity_page_service.cpp,
// not the model in src/prefetch/prefetch_model.cpp. The two answer different
// questions and are reported separately on purpose: the model says what a
// prefetcher of a stated accuracy would be worth, and this says what the
// readahead actually built into the page service achieves on the same stream.
// Where both can speak -- the hit rate a next-page policy reaches on a
// Mixture-of-Experts stream -- they should agree, and disagreement is a defect
// in one of them.
//
// Nothing here runs on a GPU. It exercises the host-side page service directly,
// so it reports media reads avoided, not wall-clock time.

#include <hbfsim/prefetch_model.hpp>

#include "../../src/cuda_runtime/hbm_cache.hpp"
#include "../../src/host_service/backing_store.hpp"
#include "../../src/host_service/capacity_page_service.hpp"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <span>
#include <string>
#include <unordered_map>
#include <vector>

#include <unistd.h>

namespace {

constexpr std::size_t kPageBytes = 4096;

struct Options {
    std::string stream{"moe"};
    std::uint64_t frames{64};
    std::uint32_t readahead{4};
    // Kept small on purpose: the backing store is a real file, so the stream's
    // highest page number decides how large it has to be.
    std::uint64_t layers{8};
    std::uint64_t experts_per_layer{16};
    std::uint64_t experts_per_token{4};
    std::uint64_t pages_per_expert{8};
    std::uint64_t tokens{4};
    std::uint64_t accesses{2048};
    std::uint64_t seed{5};
    // 0 means the worker fully keeps up with the queue.
    std::uint64_t drain_per_demand{0};
};

struct Run {
    std::uint64_t demands{0};
    std::uint64_t media_reads{0};
    std::uint64_t hits{0};
    std::uint64_t readahead_fetched{0};
    std::uint64_t readahead_skipped{0};
};

std::vector<std::uint64_t> build_stream(const Options& options)
{
    if (options.stream == "sequential") {
        return hbfsim::make_sequential_stream(options.accesses);
    }
    if (options.stream == "random") {
        return hbfsim::make_random_stream(options.accesses, options.accesses,
                                          options.seed);
    }
    return hbfsim::make_moe_stream(options.tokens, options.layers,
                                   options.experts_per_layer,
                                   options.experts_per_token,
                                   options.pages_per_expert, options.seed);
}

Run drive(const std::vector<std::uint64_t>& pages, const Options& options,
          std::uint32_t readahead_pages, std::uint64_t drain_per_demand,
          hbfsim::host_service::BackingStore& backing,
          std::unordered_map<std::uint64_t, std::vector<std::byte>>& memory,
          const std::vector<std::uint64_t>& frame_addresses)
{
    hbfsim::runtime::HbmCache cache(frame_addresses);
    hbfsim::host_service::CapacityPageService service(
        backing, cache, kPageBytes,
        {
            .host_to_frame = [&](std::uint64_t frame,
                                 std::span<const std::byte> data) {
                std::ranges::copy(data, memory.at(frame).begin());
                return true;
            },
            .frame_to_host = [&](std::uint64_t frame,
                                 std::span<std::byte> data) {
                std::ranges::copy(memory.at(frame), data.begin());
                return true;
            },
        });
    service.set_readahead_pages(readahead_pages);

    Run run{};
    for (const auto page : pages) {
        const auto resolved = service.resolve(page, 0);
        if (resolved.status != hbfsim::RequestStatus::Ready) {
            std::fprintf(stderr, "resolve failed on page %llu\n",
                         static_cast<unsigned long long>(page));
            std::exit(70);
        }
        ++run.demands;
        if ((resolved.media.flags &
             hbfsim::host_service::CapacityMediaRead) != 0) {
            ++run.media_reads;
        } else {
            ++run.hits;
        }
        // Stands in for the worker's idle time between two demands.
        // drain_per_demand is how many queued pages that idle time is worth;
        // 0 means the worker keeps up with the queue completely, which is the
        // most favourable assumption. Draining more slowly than the queue
        // fills makes prefetched pages arrive after the demand that wanted
        // them, so this parameter decides the result and has to be reported
        // with it.
        if (drain_per_demand == 0) {
            while (service.run_one_readahead()) {
            }
        } else {
            for (std::uint64_t drained = 0; drained < drain_per_demand;
                 ++drained) {
                if (!service.run_one_readahead()) {
                    break;
                }
            }
        }
    }
    // Anything still queued when the stream ends never had a chance to help.
    while (service.run_one_readahead()) {
    }
    run.readahead_fetched = service.readahead_pages_fetched();
    run.readahead_skipped = service.readahead_pages_skipped();
    return run;
}

// Two different quantities, and reporting only the first is how the earlier
// version of this benchmark reached a wrong conclusion. `demand_media_reads`
// are the reads a warp waits on, so they set the latency. Every successful
// readahead is ALSO a read of the backing store, so the bandwidth the device
// must supply is the sum. A readahead that removes demand reads while raising
// the total is trading bandwidth for latency, which is the wrong trade when
// the tier is bandwidth-bound.
void print_run(const char* label, const Run& run,
               std::uint64_t baseline_total_reads)
{
    const double hit_rate =
        run.demands == 0 ? 0.0
                         : static_cast<double>(run.hits) /
                               static_cast<double>(run.demands);
    const auto total_reads = run.media_reads + run.readahead_fetched;
    const double demand_avoided =
        run.demands == 0
            ? 0.0
            : 1.0 - static_cast<double>(run.media_reads) /
                        static_cast<double>(run.demands);
    const double total_change =
        baseline_total_reads == 0
            ? 0.0
            : static_cast<double>(total_reads) /
                      static_cast<double>(baseline_total_reads) -
                  1.0;
    std::printf(
        "    {\"policy\": \"%s\", \"demands\": %llu, "
        "\"demand_media_reads\": %llu, \"readahead_media_reads\": %llu, "
        "\"total_media_reads\": %llu, \"hits\": %llu, \"hit_rate\": %.6f, "
        "\"demand_reads_avoided_fraction\": %.6f, "
        "\"total_media_reads_change_fraction\": %.6f, "
        "\"readahead_skipped\": %llu}",
        label, static_cast<unsigned long long>(run.demands),
        static_cast<unsigned long long>(run.media_reads),
        static_cast<unsigned long long>(run.readahead_fetched),
        static_cast<unsigned long long>(total_reads),
        static_cast<unsigned long long>(run.hits), hit_rate, demand_avoided,
        total_change,
        static_cast<unsigned long long>(run.readahead_skipped));
}

bool take_u64(int argc, char** argv, int& index, const char* name,
              std::uint64_t& out)
{
    if (std::strcmp(argv[index], name) != 0) {
        return false;
    }
    if (index + 1 >= argc) {
        std::fprintf(stderr, "missing value for %s\n", name);
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
        } else if (take_u64(argc, argv, index, "--frames", scratch)) {
            options.frames = scratch;
        } else if (take_u64(argc, argv, index, "--readahead", scratch)) {
            options.readahead = static_cast<std::uint32_t>(scratch);
        } else if (take_u64(argc, argv, index, "--pages-per-expert",
                            scratch)) {
            options.pages_per_expert = scratch;
        } else if (take_u64(argc, argv, index, "--tokens", scratch)) {
            options.tokens = scratch;
        } else if (take_u64(argc, argv, index, "--accesses", scratch)) {
            options.accesses = scratch;
        } else if (take_u64(argc, argv, index, "--seed", scratch)) {
            options.seed = scratch;
        } else if (take_u64(argc, argv, index, "--drain-per-demand",
                            scratch)) {
            options.drain_per_demand = scratch;
        } else {
            std::fprintf(stderr,
                         "usage: hbf_capacity_readahead_bench "
                         "[--stream sequential|random|moe] [--frames N] "
                         "[--readahead N] [--pages-per-expert N] "
                         "[--tokens N] [--accesses N] [--seed N]\n");
            return 64;
        }
    }

    const auto pages = build_stream(options);
    if (pages.empty()) {
        std::fprintf(stderr, "empty access stream\n");
        return 65;
    }
    const auto highest = *std::ranges::max_element(pages);
    // Room for the highest page the stream touches plus everything the
    // readahead may run past the end into.
    const auto store_pages = highest + options.readahead + 2;

    const auto path = std::filesystem::temp_directory_path() /
                      ("hbfsim-readahead-bench-" +
                       std::to_string(static_cast<long long>(::getpid())));
    std::filesystem::remove(path);
    auto backing = hbfsim::host_service::BackingStore::create_deterministic(
        path, store_pages * kPageBytes, 0x13579);

    std::unordered_map<std::uint64_t, std::vector<std::byte>> memory;
    std::vector<std::uint64_t> frame_addresses;
    for (std::uint64_t index = 0; index < options.frames; ++index) {
        const auto address = 0x100000 + index * 0x1000;
        memory.emplace(address, std::vector<std::byte>(kPageBytes));
        frame_addresses.push_back(address);
    }

    const auto without = drive(pages, options, 0, options.drain_per_demand,
                               backing, memory, frame_addresses);
    const auto with = drive(pages, options, options.readahead,
                            options.drain_per_demand, backing, memory,
                            frame_addresses);

    std::printf("{\n");
    std::printf("  \"schema_version\": 1,\n");
    std::printf(
        "  \"measures\": "
        "\"src/host_service/capacity_page_service.cpp readahead\",\n");
    std::printf(
        "  \"disclaimer\": \"host-side page service only; no GPU, and media "
        "reads avoided rather than wall-clock time\",\n");
    std::printf(
        "  \"workload\": {\"stream\": \"%s\", \"accesses\": %llu, "
        "\"distinct_pages_in_store\": %llu, \"frames\": %llu, "
        "\"readahead_pages\": %u, \"pages_per_expert\": %llu, "
        "\"tokens\": %llu, \"layers\": %llu, \"experts_per_layer\": %llu, "
        "\"experts_per_token\": %llu, \"seed\": %llu, "
        "\"drain_per_demand\": %llu},\n",
        options.stream.c_str(),
        static_cast<unsigned long long>(pages.size()),
        static_cast<unsigned long long>(store_pages),
        static_cast<unsigned long long>(options.frames), options.readahead,
        static_cast<unsigned long long>(options.pages_per_expert),
        static_cast<unsigned long long>(options.tokens),
        static_cast<unsigned long long>(options.layers),
        static_cast<unsigned long long>(options.experts_per_layer),
        static_cast<unsigned long long>(options.experts_per_token),
        static_cast<unsigned long long>(options.seed),
        static_cast<unsigned long long>(options.drain_per_demand));
    std::printf("  \"runs\": [\n");
    const auto baseline_total = without.media_reads + without.readahead_fetched;
    print_run("demand_only", without, baseline_total);
    std::printf(",\n");
    print_run("readahead", with, baseline_total);
    std::printf("\n  ]\n}\n");

    std::filesystem::remove(path);
    return 0;
}

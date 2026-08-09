#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <json.hpp>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <exception>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr std::uint64_t kDefaultCacheCapacity = 64ULL * 1024 * 1024;
constexpr std::uint64_t kDefaultBlocksPerPlane = 16;

struct Options {
    std::string profile_path;
    std::uint64_t requests{1024};
    std::uint32_t bytes{16384};
    std::string operation{"read"};
    std::uint64_t arrival_gap_ns{0};
    std::uint64_t capacity_bytes{0};
};

[[noreturn]] void usage_error(const std::string& message)
{
    throw std::invalid_argument(
        message +
        "\nusage: hbf_mqsim_bench --profile FILE [--requests N] "
        "[--bytes N] [--operation read|write|mixed] "
        "[--arrival-gap-ns N] [--capacity-bytes N]");
}

std::uint64_t parse_u64(std::string_view text, std::string_view option)
{
    std::uint64_t value = 0;
    const auto result =
        std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size()) {
        usage_error("invalid value for " + std::string(option));
    }
    return value;
}

Options parse_options(int argc, char** argv)
{
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string_view option{argv[index]};
        if (index + 1 >= argc) {
            usage_error("missing value for " + std::string(option));
        }
        const std::string_view value{argv[++index]};
        if (option == "--profile") {
            options.profile_path = value;
        } else if (option == "--requests") {
            options.requests = parse_u64(value, option);
        } else if (option == "--bytes") {
            const auto bytes = parse_u64(value, option);
            if (bytes > std::numeric_limits<std::uint32_t>::max()) {
                usage_error("--bytes exceeds the request ABI limit");
            }
            options.bytes = static_cast<std::uint32_t>(bytes);
        } else if (option == "--operation") {
            options.operation = value;
        } else if (option == "--arrival-gap-ns") {
            options.arrival_gap_ns = parse_u64(value, option);
        } else if (option == "--capacity-bytes") {
            options.capacity_bytes = parse_u64(value, option);
        } else {
            usage_error("unknown option " + std::string(option));
        }
    }

    if (options.profile_path.empty()) {
        usage_error("--profile is required");
    }
    if (options.requests == 0) {
        usage_error("--requests must be non-zero");
    }
    if (options.bytes == 0 || options.bytes % 512 != 0) {
        usage_error("--bytes must be a non-zero multiple of 512");
    }
    if (options.operation != "read" && options.operation != "write" &&
        options.operation != "mixed") {
        usage_error("--operation must be read, write, or mixed");
    }
    if (options.capacity_bytes != 0 && options.capacity_bytes < options.bytes) {
        usage_error("--capacity-bytes must fit one request");
    }
    if (options.requests > 1 &&
        options.arrival_gap_ns >
            std::numeric_limits<std::uint64_t>::max() /
                (options.requests - 1)) {
        usage_error("benchmark arrival timeline overflows");
    }
    return options;
}

std::uint32_t operation_for(const Options& options, std::uint64_t index)
{
    const bool write = options.operation == "write" ||
                       (options.operation == "mixed" && index % 2 != 0);
    return static_cast<std::uint32_t>(
        write ? hbfsim::RequestOperation::Write
              : hbfsim::RequestOperation::Read);
}

std::uint64_t percentile(const std::vector<std::uint64_t>& sorted,
                         std::uint64_t percent)
{
    const auto rank = (percent * sorted.size() + 99) / 100;
    return sorted[std::max<std::size_t>(1, rank) - 1];
}

std::uint64_t capacity_for_blocks(const hbfsim::Profile& profile,
                                  std::uint64_t blocks_per_plane)
{
    const auto capacity =
        static_cast<unsigned __int128>(profile.page_bytes) *
        profile.pages_per_block * profile.planes_per_die *
        profile.dies_per_channel * profile.channels * blocks_per_plane;
    if (capacity > std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error(
            "benchmark profile geometry exceeds uint64 capacity");
    }
    return static_cast<std::uint64_t>(capacity);
}

}  // namespace

int main(int argc, char** argv)
{
    try {
        const auto options = parse_options(argc, argv);
        auto profile = hbfsim::load_profile(options.profile_path);
        if (options.capacity_bytes == 0) {
            profile.capacity_bytes = std::min(
                profile.capacity_bytes,
                capacity_for_blocks(profile, kDefaultBlocksPerPlane));
        } else {
            profile.capacity_bytes = options.capacity_bytes;
        }
        profile.hbm_cache_bytes =
            std::min(profile.capacity_bytes, kDefaultCacheCapacity);
        hbfsim::validate_profile(profile);
        const auto effective_blocks_per_plane = hbfsim::blocks_per_plane(profile);
        if (options.operation != "read" && effective_blocks_per_plane <= 10) {
            usage_error(
                "write workloads require more than 10 blocks per plane; "
                "increase --capacity-bytes");
        }

        std::vector<hbfsim::HbfCompletion> completions;
        completions.reserve(options.requests);
        const auto wall_start = std::chrono::steady_clock::now();
        {
            hbfsim::MqsimOnlineEngine engine(profile);
            const auto address_slots = profile.capacity_bytes / options.bytes;
            for (std::uint64_t index = 0; index < options.requests; ++index) {
                const auto arrival = static_cast<std::uint64_t>(
                    static_cast<unsigned __int128>(index) *
                    options.arrival_gap_ns);
                const auto address = static_cast<std::uint64_t>(
                    (static_cast<unsigned __int128>(index) * options.bytes) %
                    (address_slots * options.bytes));
                engine.submit(hbfsim::HbfRequest{
                    .request_id = index + 1,
                    .sequence = index + 1,
                    .arrival_ns = arrival,
                    .logical_address = address,
                    .deadline_ns = 0,
                    .bytes = options.bytes,
                    .range_id = 1,
                    .stream_id = 0,
                    .operation = operation_for(options, index),
                    .page_generation = 1,
                    .flags = 0,
                });
            }
            while (engine.pending() != 0) {
                auto completion = engine.run_next_completion();
                if (!completion.has_value()) {
                    throw std::runtime_error(
                        "MQSim stopped with pending benchmark requests");
                }
                completions.push_back(*completion);
            }
        }
        const auto wall_end = std::chrono::steady_clock::now();
        const auto wall_ns = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(wall_end -
                                                                 wall_start)
                .count());

        std::vector<std::uint64_t> latencies;
        latencies.reserve(completions.size());
        std::uint64_t modeled_end_ns = 0;
        unsigned __int128 latency_sum = 0;
        for (const auto& completion : completions) {
            if (completion.status !=
                static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready)) {
                throw std::runtime_error(
                    "MQSim returned a non-ready completion");
            }
            latencies.push_back(completion.modeled_ns);
            latency_sum += completion.modeled_ns;
            modeled_end_ns =
                std::max(modeled_end_ns, completion.modeled_completion_ns);
        }
        std::sort(latencies.begin(), latencies.end());

        const auto total_bytes = static_cast<long double>(options.requests) *
                                 static_cast<long double>(options.bytes);
        const auto average_latency = static_cast<std::uint64_t>(
            latency_sum / static_cast<unsigned __int128>(latencies.size()));
        const auto modeled_bandwidth = static_cast<double>(
            total_bytes * 1.0e9L / static_cast<long double>(modeled_end_ns));
        const auto simulator_rate = static_cast<double>(
            static_cast<long double>(options.requests) * 1.0e9L /
            static_cast<long double>(std::max<std::uint64_t>(wall_ns, 1)));

        const nlohmann::json result{
            {"schema_version", 1},
            {"engine", "mqsim-hbf-media-only"},
            {"profile", profile.name},
            {"effective_profile",
             {{"capacity_bytes", profile.capacity_bytes},
              {"hbm_cache_bytes", profile.hbm_cache_bytes},
              {"blocks_per_plane", effective_blocks_per_plane},
              {"channels", profile.channels},
              {"read_latency_ns", profile.read_latency_ns},
              {"program_latency_ns", profile.program_latency_ns},
              {"aggregate_bandwidth_bytes_per_s",
               profile.aggregate_bandwidth_bytes_per_s}}},
            {"workload",
             {{"operation", options.operation},
              {"requests", options.requests},
              {"bytes_per_request", options.bytes},
              {"arrival_gap_ns", options.arrival_gap_ns}}},
            {"requests",
             {{"submitted", options.requests},
              {"completed", completions.size()}}},
            {"timing_ns", {{"modeled", modeled_end_ns}, {"wall", wall_ns}}},
            {"latency_ns",
             {{"average", average_latency},
              {"p50", percentile(latencies, 50)},
              {"p99", percentile(latencies, 99)}}},
            {"modeled_bandwidth_bytes_per_s", modeled_bandwidth},
            {"simulator_requests_per_s", simulator_rate},
        };
        std::cout << result.dump(2) << '\n';
        return 0;
    } catch (const std::invalid_argument& error) {
        std::cerr << "hbf_mqsim_bench: " << error.what() << '\n';
        return 64;
    } catch (const std::exception& error) {
        std::cerr << "hbf_mqsim_bench: " << error.what() << '\n';
        return 70;
    }
}

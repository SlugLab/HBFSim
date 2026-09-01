#include <hbfsim/hybrid_model.hpp>
#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <json.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {

struct Options {
    std::string profile_path;
    std::string events_path;
    std::string mode;
};

struct ParsedEvent {
    std::uint64_t logical_address;
    std::uint64_t bytes;
    std::uint32_t page_bytes;
    hbfsim::RequestOperation operation;
};

[[noreturn]] void usage_error(const std::string& message)
{
    throw std::invalid_argument(
        message +
        "\nusage: hbf_trace_timing --profile FILE --events JSONL "
        "--mode fast|hybrid|mqsim");
}

Options parse_options(int argc, char** argv)
{
    Options result;
    for (int index = 1; index < argc; index += 2) {
        if (index + 1 >= argc) {
            usage_error("missing option value");
        }
        const std::string_view option(argv[index]);
        if (option == "--profile") {
            result.profile_path = argv[index + 1];
        } else if (option == "--events") {
            result.events_path = argv[index + 1];
        } else if (option == "--mode") {
            result.mode = argv[index + 1];
        } else {
            usage_error("unknown option " + std::string(option));
        }
    }
    if (result.profile_path.empty() || result.events_path.empty() ||
        (result.mode != "fast" && result.mode != "hybrid" &&
         result.mode != "mqsim")) {
        usage_error("all options are required and --mode must be valid");
    }
    return result;
}

std::uint64_t saturating_add(std::uint64_t left,
                             std::uint64_t right) noexcept
{
    return right > std::numeric_limits<std::uint64_t>::max() - left
               ? std::numeric_limits<std::uint64_t>::max()
               : left + right;
}

std::uint64_t transfer_ns(std::uint32_t bytes,
                          std::uint64_t bandwidth_bytes_per_s)
{
    if (bytes == 0 || bandwidth_bytes_per_s == 0) {
        throw std::invalid_argument("invalid fast transfer inputs");
    }
    const auto value =
        (static_cast<unsigned __int128>(bytes) * 1'000'000'000ULL +
         bandwidth_bytes_per_s - 1) /
        bandwidth_bytes_per_s;
    if (value > std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("fast transfer time overflows");
    }
    return static_cast<std::uint64_t>(value);
}

ParsedEvent parse_event(const nlohmann::json& event,
                        std::uint64_t input_sequence)
{
    if (event.value("schema_version", 0) != 1 ||
        event.value("sequence", std::uint64_t{0}) != input_sequence) {
        throw std::invalid_argument("invalid or non-contiguous event sequence");
    }
    const auto bytes64 = event.at("bytes").get<std::uint64_t>();
    const auto page_bytes64 = event.value("page_bytes", bytes64);
    const auto operation = event.value("operation", std::string{});
    if (bytes64 == 0 || page_bytes64 == 0 ||
        page_bytes64 > std::numeric_limits<std::uint32_t>::max() ||
        bytes64 % page_bytes64 != 0 || page_bytes64 % 512 != 0 ||
        (operation != "read" && operation != "write")) {
        throw std::invalid_argument("invalid event/page size or operation");
    }
    const auto logical_address =
        event.at("logical_address").get<std::uint64_t>();
    if (logical_address % page_bytes64 != 0 ||
        bytes64 > std::numeric_limits<std::uint64_t>::max() - logical_address) {
        throw std::invalid_argument("event range is invalid or misaligned");
    }
    return {
        .logical_address = logical_address,
        .bytes = bytes64,
        .page_bytes = static_cast<std::uint32_t>(page_bytes64),
        .operation = operation == "read" ? hbfsim::RequestOperation::Read
                                         : hbfsim::RequestOperation::Write,
    };
}

hbfsim::HbfCompletion run_reference(hbfsim::MqsimOnlineEngine& engine,
                                    const hbfsim::HbfRequest& request)
{
    engine.submit(request);
    const auto completion = engine.run_next_completion();
    if (!completion.has_value() || completion->request_id != request.request_id ||
        completion->status !=
            static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready)) {
        throw std::runtime_error("MQSim did not return the expected completion");
    }
    return *completion;
}

}  // namespace

int main(int argc, char** argv)
{
    try {
        const auto options = parse_options(argc, argv);
        const auto profile = hbfsim::load_profile(options.profile_path);
        std::ifstream events(options.events_path);
        if (!events) {
            throw std::runtime_error("failed to open timing event JSONL");
        }

        const hbfsim::FastModelProfile fast_profile{
            .read_latency_ns = profile.read_latency_ns,
            .program_latency_ns = profile.program_latency_ns,
            .aggregate_bandwidth_bytes_per_s =
                profile.aggregate_bandwidth_bytes_per_s,
        };
        hbfsim::HybridSampler sampler(profile.reference_warmup_requests,
                                      profile.reference_sample_rate, 0);
        std::unique_ptr<hbfsim::MqsimOnlineEngine> reference;
        if (options.mode != "fast") {
            reference = std::make_unique<hbfsim::MqsimOnlineEngine>(profile);
        }

        std::uint64_t input_events = 0;
        std::uint64_t sequence = 0;
        std::uint64_t modeled_clock_ns = 0;
        std::uint64_t modeled_service_ns = 0;
        std::uint64_t fast_requests = 0;
        std::uint64_t reference_requests = 0;
        std::string line;
        const auto wall_start = std::chrono::steady_clock::now();
        while (std::getline(events, line)) {
            if (line.empty()) {
                continue;
            }
            ++input_events;
            const auto event = nlohmann::json::parse(line);
            const auto parsed = parse_event(event, input_events);
            if (parsed.page_bytes != profile.page_bytes ||
                parsed.logical_address + parsed.bytes > profile.capacity_bytes) {
                throw std::invalid_argument(
                    "event page size or range differs from the frozen profile");
            }
            for (std::uint64_t offset = 0; offset < parsed.bytes;
                 offset += parsed.page_bytes) {
                ++sequence;
                const hbfsim::HbfRequest request{
                    .request_id = sequence,
                    .sequence = sequence,
                    .arrival_ns = modeled_clock_ns,
                    .logical_address = parsed.logical_address + offset,
                    .deadline_ns = 0,
                    .bytes = parsed.page_bytes,
                    .range_id = 1,
                    .stream_id = 0,
                    .operation = static_cast<std::uint32_t>(parsed.operation),
                    .page_generation = 1,
                    .flags = 0,
                };
                const hbfsim::AccessClass access{
                    .operation = parsed.operation,
                    .bytes = request.bytes,
                    .queue_bucket = 0,
                    .locality_bucket = 0,
                };
                const auto use_reference =
                    options.mode == "mqsim" ||
                    (options.mode == "hybrid" &&
                     sampler.reference(sequence - 1, access));
                if (use_reference) {
                    const auto completion = run_reference(*reference, request);
                    modeled_clock_ns = completion.modeled_completion_ns;
                    modeled_service_ns = saturating_add(
                        modeled_service_ns, completion.modeled_ns);
                    ++reference_requests;
                } else {
                    const auto service =
                        hbfsim::fast_service_ns(fast_profile, access);
                    const auto base =
                        parsed.operation == hbfsim::RequestOperation::Write
                            ? profile.program_latency_ns
                            : profile.read_latency_ns;
                    const auto exposed = std::max(
                        base,
                        transfer_ns(request.bytes,
                                    profile.aggregate_bandwidth_bytes_per_s));
                    if (service == 0 || exposed == 0) {
                        throw std::runtime_error("fast model rejected an event");
                    }
                    modeled_service_ns =
                        saturating_add(modeled_service_ns, service);
                    modeled_clock_ns =
                        saturating_add(modeled_clock_ns, exposed);
                    ++fast_requests;
                }
            }
        }
        const auto wall_end = std::chrono::steady_clock::now();
        const auto wall_ns = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(wall_end -
                                                                 wall_start)
                .count());

        const nlohmann::json result{
            {"schema_version", 1},
            {"status", "PASS"},
            {"engine", options.mode == "mqsim"
                           ? "mqsim-reference"
                           : options.mode == "hybrid" ? "hbf-hybrid"
                                                        : "hbf-fast"},
            {"profile", profile.name},
            {"scheduling_semantics",
             "ordered blocking demand misses with no compute gaps"},
            {"requests",
             {{"input_expert_misses", input_events},
              {"submitted", sequence},
              {"fast", fast_requests},
              {"reference", reference_requests}}},
            {"modeled_device_service_ns", modeled_service_ns},
            {"demand_exposed_stall_ns", modeled_clock_ns},
            {"emulator_dispatcher_wall_time_ns", wall_ns},
            {"reference_sample_rate", profile.reference_sample_rate},
            {"reference_warmup_requests", profile.reference_warmup_requests},
        };
        std::cout << result.dump(2) << '\n';
        return 0;
    } catch (const std::invalid_argument& error) {
        std::cerr << "hbf_trace_timing: " << error.what() << '\n';
        return 64;
    } catch (const std::exception& error) {
        std::cerr << "hbf_trace_timing: " << error.what() << '\n';
        return 2;
    }
}

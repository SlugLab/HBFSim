#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/package_thermal.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <json.hpp>

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace thermal = hbfsim::package_thermal;

namespace {

struct Options {
    std::filesystem::path device_profile;
    std::filesystem::path package_profile;
    std::filesystem::path model;
    std::filesystem::path output;
    std::string arrival_mode{"periodic"};
    std::string workload{"read"};
    std::string pattern{"random"};
    std::uint64_t duration_ns{0};
    std::uint64_t offered_byte_rate{0};
    std::uint64_t peak_byte_rate{0};
    std::uint32_t request_bytes{0};
    std::uint32_t queue_depth{0};
    std::uint64_t seed{0};
};

[[noreturn]] void usage(const std::string& message)
{
    throw std::invalid_argument(
        message +
        "\nusage: phase2_thermal_load_runner --device-profile FILE "
        "--package-profile FILE --model FILE --output DIR "
        "--duration-ns N --offered-byte-rate N --peak-byte-rate N "
        "--request-bytes N --queue-depth N --seed N "
        "[--arrival-mode periodic|poisson|closed_loop] "
        "[--workload read|read_heavy|mixed|write_heavy|write] "
        "[--pattern sequential|random]");
}

std::uint64_t number(std::string_view text, std::string_view option)
{
    std::uint64_t value = 0;
    const auto parsed = std::from_chars(text.data(), text.data() + text.size(),
                                        value);
    if (parsed.ec != std::errc{} || parsed.ptr != text.data() + text.size()) {
        usage("invalid value for " + std::string(option));
    }
    return value;
}

Options parse(int argc, char** argv)
{
    Options result;
    for (int index = 1; index < argc; ++index) {
        const std::string_view option{argv[index]};
        if (++index >= argc) usage("missing value for " + std::string(option));
        const std::string_view value{argv[index]};
        if (option == "--device-profile") result.device_profile = value;
        else if (option == "--package-profile") result.package_profile = value;
        else if (option == "--model") result.model = value;
        else if (option == "--output") result.output = value;
        else if (option == "--arrival-mode") result.arrival_mode = value;
        else if (option == "--workload") result.workload = value;
        else if (option == "--pattern") result.pattern = value;
        else if (option == "--duration-ns") result.duration_ns = number(value, option);
        else if (option == "--offered-byte-rate") result.offered_byte_rate = number(value, option);
        else if (option == "--peak-byte-rate") result.peak_byte_rate = number(value, option);
        else if (option == "--request-bytes") {
            const auto parsed = number(value, option);
            if (parsed > std::numeric_limits<std::uint32_t>::max()) {
                usage("--request-bytes exceeds the request ABI");
            }
            result.request_bytes = static_cast<std::uint32_t>(parsed);
        } else if (option == "--queue-depth") {
            const auto parsed = number(value, option);
            if (parsed > std::numeric_limits<std::uint32_t>::max()) {
                usage("--queue-depth exceeds uint32");
            }
            result.queue_depth = static_cast<std::uint32_t>(parsed);
        } else if (option == "--seed") result.seed = number(value, option);
        else usage("unknown option " + std::string(option));
    }
    if (result.device_profile.empty() || result.package_profile.empty() ||
        result.model.empty() || result.output.empty() || result.duration_ns == 0 ||
        result.offered_byte_rate == 0 || result.peak_byte_rate == 0 ||
        result.request_bytes == 0 || result.queue_depth == 0) {
        usage("all required paths and positive numeric controls are mandatory");
    }
    if (result.request_bytes % 512 != 0) {
        usage("--request-bytes must be a multiple of 512");
    }
    if (result.arrival_mode != "periodic" && result.arrival_mode != "poisson" &&
        result.arrival_mode != "closed_loop") {
        usage("unsupported --arrival-mode");
    }
    if (result.workload != "read" && result.workload != "read_heavy" &&
        result.workload != "mixed" && result.workload != "write_heavy" &&
        result.workload != "write") {
        usage("unsupported --workload");
    }
    if (result.pattern != "sequential" && result.pattern != "random") {
        usage("unsupported --pattern");
    }
    return result;
}

thermal::NandOperation operation(hbfsim::MediaActivityKind kind)
{
    switch (kind) {
    case hbfsim::MediaActivityKind::Read: return thermal::NandOperation::Read;
    case hbfsim::MediaActivityKind::Program: return thermal::NandOperation::Program;
    case hbfsim::MediaActivityKind::Erase: return thermal::NandOperation::Erase;
    }
    throw std::runtime_error("unknown MQSim media event kind");
}

std::uint64_t splitmix64(std::uint64_t& state)
{
    state += 0x9e3779b97f4a7c15ULL;
    auto value = state;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

std::uint32_t write_percent(const std::string& workload)
{
    if (workload == "read") return 0;
    if (workload == "read_heavy") return 10;
    if (workload == "mixed") return 50;
    if (workload == "write_heavy") return 90;
    return 100;
}

std::uint64_t percentile(std::vector<std::uint64_t> values, double fraction)
{
    if (values.empty()) return 0;
    std::sort(values.begin(), values.end());
    const auto index = static_cast<std::size_t>(
        std::ceil(fraction * static_cast<double>(values.size()))) - 1;
    return values[std::min(index, values.size() - 1)];
}

struct WaitingRequest {
    hbfsim::HbfRequest request;
    bool thermal_delay_counted{false};
};

}  // namespace

int main(int argc, char** argv)
{
    try {
        const auto options = parse(argc, argv);
        std::filesystem::create_directories(options.output);
        auto device = hbfsim::load_profile(options.device_profile);
        auto package = thermal::load_package_thermal_profile(options.package_profile);
        if (!package.timeline.enabled ||
            package.clock_mode != thermal::ClockMode::ModelTimeReplay) {
            throw std::runtime_error(
                "runner requires timeline.enabled and model_time_replay");
        }
        const auto& geometry = package.topology.geometry();
        if (geometry.channels != device.channels || geometry.chips_per_channel != 1 ||
            geometry.dies_per_chip != device.dies_per_channel ||
            geometry.planes_per_die != device.planes_per_die) {
            throw std::runtime_error("package topology and device profile differ");
        }
        auto artifact = thermal::load_rom_artifact(options.model);
        thermal::validate_rom_artifact(
            artifact, package.topology.node_names(), package.topology.node_names());
        auto runtime = thermal::PackageThermalRuntime(
            std::move(package), thermal::make_rom_model(std::move(artifact)));
        const auto bin_ns = static_cast<std::uint64_t>(
            runtime.profile().bin_width_ns.value);
        thermal::PackageThermalTimelineWriter timeline(
            options.output / "package-thermal-timeline.csv", runtime.profile());

        std::uint64_t media_events = 0;
        hbfsim::MediaActivitySink sink = [&](const hbfsim::MediaActivity& event) {
            ++media_events;
            runtime.record(thermal::NandMediaActivity{
                .operation = operation(event.kind),
                .start_time_ns = event.start_time_ns,
                .end_time_ns = event.end_time_ns,
                .coordinate = {event.channel, event.chip, event.die, event.plane},
                .block = event.block,
                .page = event.page,
                .bytes = event.bytes,
            });
        };
        hbfsim::MqsimOnlineEngine engine(device, std::move(sink));
        std::deque<WaitingRequest> waiting;
        std::unordered_map<std::uint64_t, std::uint64_t> arrivals;
        std::vector<std::uint64_t> latencies;
        std::mt19937_64 random(options.seed);
        std::exponential_distribution<long double> exponential(
            static_cast<long double>(options.offered_byte_rate) /
            static_cast<long double>(options.request_bytes));
        std::uint64_t address_state = options.seed ^ 0x8f3f73b5cf1c9adeULL;
        std::uint64_t operation_state = options.seed ^ 0x243f6a8885a308d3ULL;
        std::uint64_t next_id = 1;
        std::uint64_t next_arrival_ns = 0;
        std::uint64_t offered = 0;
        std::uint64_t admitted = 0;
        std::uint64_t completed = 0;
        std::uint64_t thermal_blocked = 0;
        std::uint64_t served_bytes = 0;
        std::uint64_t sequential_slot = 0;
        const auto per_bin_peak_bytes =
            static_cast<long double>(options.peak_byte_rate) * bin_ns / 1.0e9L;
        const auto token_capacity = std::max(
            static_cast<long double>(options.request_bytes),
            per_bin_peak_bytes);
        long double tokens = token_capacity;
        const auto address_slots = device.capacity_bytes / options.request_bytes;
        if (address_slots == 0) throw std::runtime_error("request exceeds capacity");

        auto enqueue_arrival = [&](std::uint64_t when) {
            const auto write = splitmix64(operation_state) % 100 <
                               write_percent(options.workload);
            const auto slot = options.pattern == "random"
                                  ? splitmix64(address_state) % address_slots
                                  : sequential_slot++ % address_slots;
            hbfsim::HbfRequest request{
                .request_id = next_id++, .sequence = 0, .arrival_ns = when,
                .logical_address = slot * options.request_bytes,
                .deadline_ns = 0, .bytes = options.request_bytes, .range_id = 1,
                .stream_id = 0,
                .operation = static_cast<std::uint32_t>(
                    write ? hbfsim::RequestOperation::Write
                          : hbfsim::RequestOperation::Read),
                .page_generation = 1, .flags = 0,
            };
            arrivals.emplace(request.request_id, when);
            waiting.push_back(WaitingRequest{request, false});
            ++offered;
        };

        if (options.arrival_mode == "closed_loop") {
            for (std::uint32_t index = 0; index < options.queue_depth; ++index) {
                enqueue_arrival(0);
            }
        }

        std::uint64_t model_time = 0;
        while (model_time < options.duration_ns) {
            const auto target = std::min(options.duration_ns,
                                         model_time + bin_ns);
            if (options.arrival_mode != "closed_loop") {
                while (next_arrival_ns < target) {
                    enqueue_arrival(next_arrival_ns);
                    long double interval_ns = 0.0L;
                    if (options.arrival_mode == "periodic") {
                        interval_ns = static_cast<long double>(options.request_bytes) *
                                      1.0e9L / options.offered_byte_rate;
                    } else {
                        interval_ns = exponential(random) * 1.0e9L;
                    }
                    const auto increment = std::max<std::uint64_t>(
                        1, static_cast<std::uint64_t>(std::llround(interval_ns)));
                    if (next_arrival_ns >
                        std::numeric_limits<std::uint64_t>::max() - increment) {
                        throw std::runtime_error("arrival timeline overflow");
                    }
                    next_arrival_ns += increment;
                }
            }

            const auto& decision = runtime.decision();
            const auto elapsed = target - model_time;
            tokens = std::min(
                token_capacity,
                tokens + static_cast<long double>(options.peak_byte_rate) *
                             decision.service_scale * elapsed / 1.0e9L);
            if (!decision.admission_open) {
                for (auto& item : waiting) {
                    if (!item.thermal_delay_counted) {
                        item.thermal_delay_counted = true;
                        ++thermal_blocked;
                    }
                }
            }
            while (decision.admission_open && !waiting.empty() &&
                   engine.pending() < options.queue_depth &&
                   tokens + 1.0e-9L >= options.request_bytes) {
                auto item = waiting.front();
                waiting.pop_front();
                item.request.arrival_ns = std::max(
                    {item.request.arrival_ns, model_time, engine.current_time_ns()});
                engine.submit(item.request);
                tokens -= options.request_bytes;
                ++admitted;
            }

            while (engine.pending() != 0 && engine.current_time_ns() < target) {
                const auto completion = engine.run_next_completion();
                if (!completion.has_value()) {
                    throw std::runtime_error("MQSim stopped with pending work");
                }
                ++completed;
                served_bytes += options.request_bytes;
                const auto found = arrivals.find(completion->request_id);
                if (found == arrivals.end() ||
                    completion->modeled_completion_ns < found->second) {
                    throw std::runtime_error("completion/arrival accounting failed");
                }
                latencies.push_back(completion->modeled_completion_ns - found->second);
                arrivals.erase(found);
                if (options.arrival_mode == "closed_loop" &&
                    engine.current_time_ns() < options.duration_ns) {
                    enqueue_arrival(engine.current_time_ns());
                }
            }

            const auto advanced = std::max(target, engine.current_time_ns());
            const auto observations = runtime.advance_to(advanced);
            const thermal::ThermalServiceSnapshot service{
                .submitted_requests = offered,
                .admitted_requests = admitted,
                .completed_requests = completed,
                .queue_depth = waiting.size(),
                .thermal_blocked_requests = thermal_blocked,
            };
            for (const auto& observation : observations) {
                timeline.append(observation, service);
            }
            model_time = advanced;
        }

        const auto final_observations = runtime.finish(model_time);
        const thermal::ThermalServiceSnapshot final_service{
            .submitted_requests = offered, .admitted_requests = admitted,
            .completed_requests = completed, .queue_depth = waiting.size(),
            .thermal_blocked_requests = thermal_blocked,
        };
        for (const auto& observation : final_observations) {
            timeline.append(observation, final_service);
        }
        timeline.finish();
        thermal::write_package_thermal_report(
            options.output / "package-thermal.json", runtime,
            thermal::ThermalReportMetadata{
                .package_profile_path = options.package_profile,
                .model_path = options.model, .model_kind = "rom"},
            timeline.metrics());

        const nlohmann::json result{
            {"schema_version", 1},
            {"arrival_mode", options.arrival_mode},
            {"workload", options.workload},
            {"pattern", options.pattern},
            {"seed", options.seed},
            {"requested_duration_ns", options.duration_ns},
            {"modeled_duration_ns", model_time},
            {"request_bytes", options.request_bytes},
            {"offered_byte_rate", options.offered_byte_rate},
            {"peak_byte_rate", options.peak_byte_rate},
            {"queue_depth_limit", options.queue_depth},
            {"requests", {{"offered", offered}, {"admitted", admitted},
                          {"completed", completed}, {"queued", waiting.size()},
                          {"thermal_blocked", thermal_blocked}}},
            {"served_bytes", served_bytes},
            {"served_byte_rate", model_time == 0 ? 0.0 :
                 static_cast<double>(served_bytes) * 1.0e9 /
                 static_cast<double>(model_time)},
            {"latency_ns", {{"p50", percentile(latencies, 0.50)},
                            {"p95", percentile(latencies, 0.95)},
                            {"p99", percentile(latencies, 0.99)}}},
            {"media_events", media_events},
            {"maximum_hbf_temperature_c", runtime.stats().maximum_hbf_temperature_c},
            {"maximum_hbf_node", runtime.stats().maximum_node},
            {"thermal_steps", runtime.stats().thermal_steps},
            {"model_identity", runtime.model_identity()},
        };
        std::ofstream output(options.output / "runner-result.json",
                             std::ios::trunc);
        output << result.dump(2) << '\n';
        if (!output) throw std::runtime_error("failed to write runner result");
        const nlohmann::json request_summary{
            {"schema_version", 1},
            {"latency_ns", {{"p50_ns", percentile(latencies, 0.50)},
                            {"p95_ns", percentile(latencies, 0.95)},
                            {"p99_ns", percentile(latencies, 0.99)}}},
            {"requests", result.at("requests")},
            {"served_bytes", served_bytes},
            {"served_byte_rate", result.at("served_byte_rate")},
        };
        std::ofstream request_output(options.output / "request-summary.json",
                                     std::ios::trunc);
        request_output << request_summary.dump(2) << '\n';
        if (!request_output) {
            throw std::runtime_error("failed to write request summary");
        }
        std::cout << result.dump(2) << '\n';
        return 0;
    } catch (const std::invalid_argument& error) {
        std::cerr << "phase2_thermal_load_runner: " << error.what() << '\n';
        return 64;
    } catch (const std::exception& error) {
        std::cerr << "phase2_thermal_load_runner: " << error.what() << '\n';
        return 70;
    }
}

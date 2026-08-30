#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <json.hpp>

#include <algorithm>
#include <charconv>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <system_error>
#include <vector>

namespace {

struct Options {
    std::filesystem::path profile;
    std::filesystem::path trace;
    std::filesystem::path output;
};

struct AccessRecord {
    std::uint64_t sequence;
    std::uint64_t byte_offset;
    std::uint64_t media_logical_address;
    std::uint32_t media_bytes;
    std::uint32_t operation;
    std::uint64_t gpu_begin_ns;
    std::uint64_t gpu_end_ns;
};

[[noreturn]] void fail(const std::string& message)
{
    throw std::invalid_argument(message);
}

std::uint64_t parse_u64(std::string_view text, std::string_view field)
{
    std::uint64_t value = 0;
    const auto parsed = std::from_chars(
        text.data(), text.data() + text.size(), value);
    if (parsed.ec != std::errc{} ||
        parsed.ptr != text.data() + text.size()) {
        fail("invalid unsigned integer in " + std::string(field));
    }
    return value;
}

Options parse_options(int argc, char** argv)
{
    Options options;
    for (int index = 1; index < argc; ++index) {
        if (index + 1 >= argc) {
            fail("missing option value");
        }
        const std::string_view option{argv[index++]};
        const std::filesystem::path value{argv[index]};
        if (option == "--profile") {
            options.profile = value;
        } else if (option == "--trace") {
            options.trace = value;
        } else if (option == "--output") {
            options.output = value;
        } else {
            fail("unknown option " + std::string(option));
        }
    }
    if (options.profile.empty() || options.trace.empty() ||
        options.output.empty()) {
        fail("--profile, --trace and --output are required");
    }
    if (std::filesystem::exists(options.output)) {
        fail("refusing to overwrite replay output");
    }
    return options;
}

std::vector<std::string> split_csv(const std::string& line)
{
    std::vector<std::string> fields;
    std::stringstream stream(line);
    std::string field;
    while (std::getline(stream, field, ',')) {
        fields.push_back(field);
    }
    return fields;
}

std::vector<AccessRecord> read_trace(const std::filesystem::path& path,
                                     const hbfsim::Profile& profile)
{
    std::ifstream input(path);
    if (!input) {
        fail("cannot open access trace");
    }
    std::string line;
    if (!std::getline(input, line) ||
        line != "sequence,byte_offset,media_logical_address,media_bytes,operation,gpu_begin_ns,gpu_end_ns") {
        fail("access trace header mismatch");
    }
    std::vector<AccessRecord> records;
    while (std::getline(input, line)) {
        if (line.empty()) {
            continue;
        }
        const auto fields = split_csv(line);
        if (fields.size() != 7) {
            fail("access trace row must contain seven fields");
        }
        const auto media_bytes = parse_u64(fields[3], "media_bytes");
        const auto operation = parse_u64(fields[4], "operation");
        if (media_bytes > std::numeric_limits<std::uint32_t>::max() ||
            operation > std::numeric_limits<std::uint32_t>::max()) {
            fail("access trace field exceeds protocol width");
        }
        AccessRecord record{
            .sequence = parse_u64(fields[0], "sequence"),
            .byte_offset = parse_u64(fields[1], "byte_offset"),
            .media_logical_address =
                parse_u64(fields[2], "media_logical_address"),
            .media_bytes = static_cast<std::uint32_t>(media_bytes),
            .operation = static_cast<std::uint32_t>(operation),
            .gpu_begin_ns = parse_u64(fields[5], "gpu_begin_ns"),
            .gpu_end_ns = parse_u64(fields[6], "gpu_end_ns"),
        };
        if (record.sequence != records.size() || record.operation > 1 ||
            record.gpu_end_ns < record.gpu_begin_ns ||
            record.media_bytes != profile.page_bytes ||
            record.media_logical_address % profile.page_bytes != 0 ||
            record.media_logical_address !=
                (record.byte_offset / profile.page_bytes) * profile.page_bytes ||
            record.media_logical_address >
                profile.capacity_bytes - profile.page_bytes) {
            fail("access trace identity/accounting validation failed");
        }
        if (!records.empty() &&
            record.gpu_begin_ns < records.back().gpu_end_ns) {
            fail("access trace timestamps are not in program order");
        }
        records.push_back(record);
    }
    if (records.empty()) {
        fail("access trace is empty");
    }
    return records;
}

std::uint64_t percentile(std::vector<std::uint64_t> values,
                         std::uint64_t percent)
{
    if (values.empty()) {
        return 0;
    }
    std::sort(values.begin(), values.end());
    const auto rank = (percent * values.size() + 99) / 100;
    return values[std::max<std::size_t>(1, rank) - 1];
}

}  // namespace

int main(int argc, char** argv)
{
    try {
        const auto options = parse_options(argc, argv);
        const auto profile = hbfsim::load_profile(options.profile);
        const auto records = read_trace(options.trace, profile);
        const auto first_gpu_ns = records.front().gpu_begin_ns;

        std::uint64_t read_accesses = 0;
        std::uint64_t program_accesses = 0;
        std::vector<std::uint64_t> observed_durations;
        observed_durations.reserve(records.size());
        for (const auto& record : records) {
            read_accesses += record.operation == 0 ? 1 : 0;
            program_accesses += record.operation == 1 ? 1 : 0;
            observed_durations.push_back(
                record.gpu_end_ns - record.gpu_begin_ns);
        }

        std::uint64_t physical_reads = 0;
        std::uint64_t physical_programs = 0;
        std::uint64_t physical_erases = 0;
        std::uint64_t physical_bytes = 0;
        std::vector<hbfsim::HbfCompletion> completions;
        completions.reserve(records.size());
        const auto wall_begin = std::chrono::steady_clock::now();
        {
            hbfsim::MqsimOnlineEngine engine(
                profile, [&](const hbfsim::MediaActivity& activity) {
                    physical_reads +=
                        activity.kind == hbfsim::MediaActivityKind::Read ? 1 : 0;
                    physical_programs +=
                        activity.kind == hbfsim::MediaActivityKind::Program ? 1 : 0;
                    physical_erases +=
                        activity.kind == hbfsim::MediaActivityKind::Erase ? 1 : 0;
                    physical_bytes += activity.bytes;
                });
            for (const auto& record : records) {
                engine.submit(hbfsim::HbfRequest{
                    .request_id = record.sequence + 1,
                    .sequence = record.sequence + 1,
                    .arrival_ns = record.gpu_begin_ns - first_gpu_ns,
                    .logical_address = record.media_logical_address,
                    .deadline_ns = 0,
                    .bytes = record.media_bytes,
                    .range_id = 1,
                    .stream_id = 0,
                    .operation = record.operation,
                    .page_generation = 1,
                    .flags = 0,
                });
            }
            while (engine.pending() != 0) {
                auto completion = engine.run_next_completion();
                if (!completion.has_value()) {
                    throw std::runtime_error(
                        "MQSim stopped with pending observed accesses");
                }
                completions.push_back(*completion);
            }
        }
        const auto wall_end = std::chrono::steady_clock::now();
        if (completions.size() != records.size()) {
            throw std::runtime_error("MQSim completion accounting mismatch");
        }

        std::vector<std::uint64_t> modeled_latencies;
        modeled_latencies.reserve(completions.size());
        std::uint64_t modeled_end_ns = 0;
        for (const auto& completion : completions) {
            if (completion.status !=
                static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready)) {
                throw std::runtime_error("MQSim completion is not Ready");
            }
            modeled_latencies.push_back(completion.modeled_ns);
            modeled_end_ns = std::max(
                modeled_end_ns, completion.modeled_completion_ns);
        }

        const auto gpu_window_ns =
            records.back().gpu_end_ns - records.front().gpu_begin_ns;
        const auto simulator_wall_ns = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                wall_end - wall_begin)
                .count());
        const nlohmann::json result{
            {"schema_version", 1},
            {"status", "PASS"},
            {"engine", "mqsim-hbf-observed-access-replay"},
            {"profile", profile.name},
            {"trace",
             {{"path", options.trace.string()},
              {"access_records", records.size()},
              {"read_accesses", read_accesses},
              {"program_accesses", program_accesses},
              {"gpu_window_ns", gpu_window_ns},
              {"observed_access_duration_ns",
               {{"p50", percentile(observed_durations, 50)},
                {"p95", percentile(observed_durations, 95)},
                {"p99", percentile(observed_durations, 99)}}}}},
            {"replay",
             {{"requests_submitted", records.size()},
              {"requests_completed", completions.size()},
              {"modeled_end_ns", modeled_end_ns},
              {"simulator_wall_ns", simulator_wall_ns},
              {"modeled_latency_ns",
               {{"p50", percentile(modeled_latencies, 50)},
                {"p95", percentile(modeled_latencies, 95)},
                {"p99", percentile(modeled_latencies, 99)}}}}},
            {"media_activity",
             {{"read_events", physical_reads},
              {"program_events", physical_programs},
              {"erase_events", physical_erases},
              {"bytes", physical_bytes}}},
            {"semantics",
             {{"accounting_unit",
               "one observed global-memory instruction mapped to its containing HBF page"},
              {"arrival_mapping",
               "identity relative GPU globaltimer nanoseconds to MQSim arrival nanoseconds"},
              {"per_access_observation", true},
              {"per_access_live_injection", false},
              {"observation_injects_delay", false},
              {"replay_is_offline", true}}},
        };
        std::ofstream output(options.output);
        if (!output) {
            throw std::runtime_error("cannot open replay output");
        }
        output << result.dump(2) << '\n';
        std::cout << result.dump() << '\n';
        return 0;
    } catch (const std::invalid_argument& error) {
        std::cerr << "hbf_access_trace_replay: " << error.what() << '\n';
        return 64;
    } catch (const std::exception& error) {
        std::cerr << "hbf_access_trace_replay: " << error.what() << '\n';
        return 70;
    }
}

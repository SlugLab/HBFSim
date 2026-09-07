#include <hbfsim/api.h>
#include <hbfsim/timing_future_abi.hpp>

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <json.hpp>
#include <openssl/sha.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <fstream>
#include <iterator>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
using nlohmann::json;
namespace future = hbfsim::timing_future;

constexpr char kKernel[] = "c6_future_lifecycle_candidate";
constexpr char kOriginalPtxSha256[] =
    "eabc6abd294e65571f1ef5b52358a7279249348fdf7f9b1917df71ef729948d9";
constexpr std::size_t kReviewedTransformedPtxBytes = 169528;
constexpr char kReviewedTransformedPtxSha256[] =
    "65635db6979cd5d1d90a9aba49814437fa9b1e3834ff8d04153b52d458d4fa09";
constexpr std::size_t kReviewedCubinBytes = 90128;
constexpr char kReviewedCubinSha256[] =
    "e4954d3fc1370a8cbf9302c823daa1f58028b09038162eb2c713c07ca3211f8b";
constexpr char kReviewedBuildManifestSha256[] =
    "aa06c52950c101df7f155e663603d5c0dfab9eba4a69e28d4df370c6f367416f";
constexpr char kReviewedDisassemblyManifestSha256[] =
    "3cf03d5e00042857c5015c13e1e76541fe2670d8b2e5c0355aac3d3b71fc7cca";
constexpr char kReviewedNvdisasmSha256[] =
    "5a6bac301338087afbfbfeb9c1357ede2d81a1c352738deb85b83480a8ad5df4";
constexpr char kReviewedCuobjdumpSha256[] =
    "04637d85e56aa7e74d8ac6f17d5509c860f9962cca8802ca4a7a16a49caa76ae";
constexpr std::uint32_t kSeed = 0x9e3779b9U;
constexpr std::uint32_t kPageBytes = 4096;
constexpr std::size_t kInputBytes = 32 * kPageBytes;

struct Options {
    std::string profile;
    std::string plugin;
    std::string ptx;
    std::string cubin;
    std::string output;
    std::string report_dir;
};

struct Case {
    const char* name;
    std::uint32_t stride;
    std::uint32_t mode;
};

constexpr std::array<Case, 3> kCases{{
    {"overwrite_executed", 4, 0U},
    {"overwrite_false_consume", 4, 1U},
    {"unused_exit", 4, 2U},
}};

void require(bool value, const std::string& message) {
    if (!value) {
        throw std::runtime_error(message);
    }
}

void driver(CUresult result, const char* what) {
    const char* message = nullptr;
    (void)cuGetErrorString(result, &message);
    require(result == CUDA_SUCCESS,
            std::string(what) + ": " + (message ? message : "CUDA error"));
}

void runtime(cudaError_t result, const char* what) {
    require(result == cudaSuccess,
            std::string(what) + ": " + cudaGetErrorString(result));
}

std::string read_file(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    require(bool(stream), "cannot read " + path);
    return {std::istreambuf_iterator<char>(stream), {}};
}

std::string read_exact_binary(const std::string& path,
                              std::size_t expected_bytes) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    require(bool(stream), "cannot read " + path);
    const auto end = stream.tellg();
    require(end != std::streampos(-1) &&
                static_cast<std::uint64_t>(static_cast<std::streamoff>(end)) ==
                    expected_bytes,
            "reviewed native image length mismatch");
    std::string contents(expected_bytes, '\0');
    stream.seekg(0);
    stream.read(contents.data(), static_cast<std::streamsize>(contents.size()));
    require(stream.gcount() == static_cast<std::streamsize>(contents.size()) &&
                stream.peek() == std::char_traits<char>::eof(),
            "cannot read exact reviewed native image");
    return contents;
}

std::string sha256_hex(const std::string& contents) {
    std::array<unsigned char, SHA256_DIGEST_LENGTH> digest{};
    require(SHA256(reinterpret_cast<const unsigned char*>(contents.data()),
                   contents.size(), digest.data()) != nullptr,
            "SHA256 failed");
    constexpr char digits[] = "0123456789abcdef";
    std::string result(SHA256_DIGEST_LENGTH * 2, '0');
    for (std::size_t index = 0; index < digest.size(); ++index) {
        result[index * 2] = digits[digest[index] >> 4];
        result[index * 2 + 1] = digits[digest[index] & 0x0f];
    }
    return result;
}

void write_file(const std::string& path, const std::string& contents) {
    std::ofstream stream(path, std::ios::binary);
    require(bool(stream), "cannot open " + path);
    stream << contents << '\n';
    stream.flush();
    require(bool(stream), "cannot write " + path);
}

Options parse_options(int argc, char** argv) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string key = argv[index];
        require(index + 1 < argc, "missing value for " + key);
        const std::string value = argv[++index];
        if (key == "--profile") options.profile = value;
        else if (key == "--plugin") options.plugin = value;
        else if (key == "--ptx") options.ptx = value;
        else if (key == "--cubin") options.cubin = value;
        else if (key == "--output") options.output = value;
        else if (key == "--report-dir") options.report_dir = value;
        else throw std::runtime_error("unknown option " + key);
    }
    require(!options.profile.empty() && !options.plugin.empty() &&
                !options.ptx.empty() && !options.cubin.empty() &&
                !options.output.empty() &&
                !options.report_dir.empty(),
            "all artifact paths are required");
    const auto profile = json::parse(read_file(options.profile));
    require(profile.at("page_bytes").get<std::uint32_t>() == kPageBytes,
            "future-lifecycle diagnostic requires exact 4096-byte profile pages");
    require(profile.at("time_scale").get<std::uint32_t>() == 1 &&
                profile.at("read_latency_ns").get<std::uint64_t>() > 0 &&
                profile.at("program_latency_ns").get<std::uint64_t>() > 0 &&
                profile.at("aggregate_bandwidth_bytes_per_s")
                        .get<std::uint64_t>() > 0,
            "positive FAST profile with time_scale=1 required");
    return options;
}

template <class T>
CUdeviceptr module_object(CUmodule module, const char* name) {
    CUdeviceptr address = 0;
    std::size_t bytes = 0;
    driver(cuModuleGetGlobal(&address, &bytes, module, name), name);
    require(bytes == sizeof(T), std::string("wrong symbol size: ") + name);
    return address;
}

std::pair<CUdeviceptr, std::size_t> module_span(CUmodule module,
                                                const char* name) {
    CUdeviceptr address = 0;
    std::size_t bytes = 0;
    driver(cuModuleGetGlobal(&address, &bytes, module, name), name);
    return {address, bytes};
}

std::uint64_t subtract(std::uint64_t after, std::uint64_t before,
                       const char* field) {
    require(after >= before, std::string("counter regressed: ") + field);
    return after - before;
}

json counters_json(const future::Counters& value) {
    return {
        {"next_reservation", value.next_reservation},
        {"issued", value.issued},
        {"pending", value.pending},
        {"model_ready", value.model_ready},
        {"consumed", value.consumed},
        {"drained", value.drained},
        {"terminal_error", value.terminal_error},
        {"native_loads", value.native_loads},
        {"native_bytes", value.native_bytes},
        {"rejected", value.rejected},
        {"groups_issued", value.groups_issued},
        {"groups_completed", value.groups_completed},
        {"trace_count", value.trace_count},
        {"trace_overflow", value.trace_overflow},
    };
}

json counter_delta_json(const future::Counters& before,
                        const future::Counters& after) {
    return {
        {"next_reservation", subtract(after.next_reservation,
                                       before.next_reservation,
                                       "next_reservation")},
        {"issued", subtract(after.issued, before.issued, "issued")},
        {"pending", subtract(after.pending, before.pending, "pending")},
        {"model_ready", subtract(after.model_ready, before.model_ready,
                                  "model_ready")},
        {"consumed", subtract(after.consumed, before.consumed, "consumed")},
        {"drained", subtract(after.drained, before.drained, "drained")},
        {"terminal_error", subtract(after.terminal_error,
                                     before.terminal_error,
                                     "terminal_error")},
        {"native_loads", subtract(after.native_loads, before.native_loads,
                                   "native_loads")},
        {"native_bytes", subtract(after.native_bytes, before.native_bytes,
                                   "native_bytes")},
        {"rejected", subtract(after.rejected, before.rejected, "rejected")},
        {"groups_issued", subtract(after.groups_issued,
                                    before.groups_issued,
                                    "groups_issued")},
        {"groups_completed", subtract(after.groups_completed,
                                       before.groups_completed,
                                       "groups_completed")},
        {"trace_count", subtract(after.trace_count, before.trace_count,
                                  "trace_count")},
        {"trace_overflow", subtract(after.trace_overflow,
                                     before.trace_overflow,
                                     "trace_overflow")},
    };
}

std::uint32_t input_word(std::size_t index) {
    return static_cast<std::uint32_t>(index * 0x45d9f3bU) ^ 0xa5a55a5aU;
}

std::uint32_t independent_work(std::uint32_t lane) {
    const std::uint32_t x = kSeed ^ lane;
    const std::uint32_t mixed = x * 0x0019660dU + 0x3c6ef35fU;
    const std::uint32_t rotated = (mixed << 7) | (mixed >> 25);
    return (rotated ^ 0x85ebca6bU) * 0x85ebca77U;
}

std::uint32_t exit_sentinel(std::uint32_t lane) {
    return 0xc0dec000U ^ (lane * 0x01020304U);
}

json trace_json(const future::Trace& trace) {
    return {
        {"reservation_id", trace.reservation_id},
        {"address", trace.address},
        {"issue_ns", trace.issue_ns},
        {"ready_ns", trace.ready_ns},
        {"finish_ns", trace.finish_ns},
        {"instruction_id", trace.instruction_id},
        {"bytes", trace.bytes},
        {"lane", trace.lane},
        {"group_mask", trace.group_mask},
        {"event", trace.event},
        {"status", trace.status},
    };
}

class AcquisitionJournal {
  public:
    explicit AcquisitionJournal(std::string path) : path_(std::move(path)) {
        document_ = {
            {"schema_version", 1},
            {"evidence", "PARTIAL_GPU_DIAGNOSTIC"},
            {"validation_status", "UNVALIDATED"},
            {"stage", "options_validated"},
            {"cases", json::array()},
            {"cleanup", json::object()},
        };
        save();
    }

    void stage(const std::string& value) {
        document_["stage"] = value;
        save();
    }

    std::size_t begin_case(const json& value) {
        document_["cases"].push_back(value);
        save();
        return document_["cases"].size() - 1;
    }

    void case_data(std::size_t index, const json& value) {
        document_["cases"].at(index) = value;
        save();
    }

    void cleanup(const json& value) {
        document_["cleanup"] = value;
        save();
    }

    void field(const std::string& name, const json& value) {
        document_[name] = value;
        save();
    }

    void failure(const std::string& stage, const std::string& message) noexcept {
        try {
            document_["stage"] = stage;
            document_["failure"] = {{"stage", stage}, {"message", message}};
            save();
        } catch (...) {
        }
    }

    void cleanup_noexcept(const json& value) noexcept {
        try {
            cleanup(value);
        } catch (...) {
        }
    }

    void captured() {
        document_["stage"] = "capture_complete_unvalidated";
        document_["capture_complete"] = true;
        save();
    }

  private:
    void save() {
        const auto temporary = path_ + ".tmp";
        write_file(temporary, document_.dump(2));
        require(std::rename(temporary.c_str(), path_.c_str()) == 0,
                "cannot atomically replace partial acquisition journal");
    }

    std::string path_;
    json document_;
};

json check_case(const Case& item, const std::vector<std::uint32_t>& input_host,
                std::uint32_t* input, std::uint32_t* output,
                CUfunction kernel, CUdeviceptr counters_address,
                CUdeviceptr trace_address, std::uint64_t trace_capacity,
                std::uint32_t& instruction_id, bool& instruction_id_set,
                AcquisitionJournal& journal, std::string& stage) {
    constexpr std::uint64_t kMaximumCaseTraceRecords = 3 * 32;
    constexpr std::uint32_t producer_mask = 0xffffffffU;
    constexpr std::uint32_t active_lanes = 32;
    require(item.stride == 4 && item.mode <= 2, "future-lifecycle cases require one same-page producer group and fixed mode");
    constexpr std::uint32_t expected_groups = 1;
    std::array<std::uint32_t, 32> expected{};
    std::array<std::uint32_t, 32> sentinels{};
    for (std::uint32_t lane = 0; lane < 32; ++lane) {
        const auto value = input_host.at(std::size_t{lane} * item.stride / 4);
        const auto work = independent_work(lane);
        expected[lane] = item.mode == 0 ? (kSeed ^ lane) ^ work :
                         item.mode == 1 ? value ^ work : exit_sentinel(lane);
        sentinels[lane] = item.mode == 2 ? expected[lane] : expected[lane] ^ 0xffffffffU;
        require(item.mode == 2 || sentinels[lane] != expected[lane],
                "writing case sentinel must differ from expected value");
    }

    json raw{
        {"case", item.name},
        {"stride_bytes", item.stride},
        {"active_mask", producer_mask},
        {"mode", item.mode},
        {"overwrite_executed", item.mode == 0},
        {"output_store_executed", item.mode != 2},
        {"terminal_kind", item.mode == 1 ? "CONSUMED" : "DRAINED"},
        {"active_lanes", active_lanes},
        {"expected_groups", expected_groups},
        {"validation", "NOT_RUN"},
        {"acquisition", {{"counters_before", "NOT_ACQUIRED"},
                         {"sentinel_echo", "NOT_ACQUIRED"},
                         {"kernel_completion", "NOT_ACQUIRED"},
                         {"outputs", "NOT_ACQUIRED"},
                         {"counters_after", "NOT_ACQUIRED"},
                         {"traces", "NOT_ACQUIRED"}}},
        {"expected_outputs", expected},
        {"sentinel_outputs", sentinels},
    };
    const auto journal_index = journal.begin_case(raw);

    future::Counters before{};
    stage = std::string("case:") + item.name + ":copy_counters_before";
    journal.stage(stage);
    driver(cuMemcpyDtoH(&before, counters_address, sizeof(before)),
           "copy counters before case");
    raw["counters_before"] = counters_json(before);
    raw["acquisition"]["counters_before"] = "ACQUIRED";
    journal.case_data(journal_index, raw);

    stage = std::string("case:") + item.name + ":write_output_sentinels";
    journal.stage(stage);
    runtime(cudaMemcpy(output, sentinels.data(), sizeof(sentinels),
                       cudaMemcpyHostToDevice),
            "write per-case output sentinels");
    std::array<std::uint32_t, 32> sentinel_echo{};
    stage = std::string("case:") + item.name + ":confirm_output_sentinels";
    journal.stage(stage);
    runtime(cudaMemcpy(sentinel_echo.data(), output, sizeof(sentinel_echo),
                       cudaMemcpyDeviceToHost),
            "confirm per-case output sentinels");
    raw["sentinel_echo"] = sentinel_echo;
    raw["acquisition"]["sentinel_echo"] = "ACQUIRED";
    journal.case_data(journal_index, raw);
    require(sentinel_echo == sentinels,
            "device output sentinel initialization was not confirmed");

    std::uint32_t seed = kSeed;
    std::uint32_t stride = item.stride;
    std::uint32_t mode = item.mode;
    constexpr std::uint32_t mask = producer_mask;
    void* arguments[]{&input, &output, &seed, &stride, &mode};
    stage = std::string("case:") + item.name + ":launch";
    journal.stage(stage);
    driver(cuLaunchKernel(kernel, 1, 1, 1, 32, 1, 1, 0, nullptr,
                          arguments, nullptr),
           "launch one-warp diagnostic");
    stage = std::string("case:") + item.name + ":synchronize";
    journal.stage(stage);
    runtime(cudaDeviceSynchronize(), "complete one-warp diagnostic");
    raw["acquisition"]["kernel_completion"] = "CONFIRMED";
    journal.case_data(journal_index, raw);

    std::array<std::uint32_t, 32> observed{};
    stage = std::string("case:") + item.name + ":copy_outputs";
    journal.stage(stage);
    runtime(cudaMemcpy(observed.data(), output, sizeof(observed),
                       cudaMemcpyDeviceToHost),
            "copy per-lane outputs");
    raw["observed_outputs"] = observed;
    raw["acquisition"]["outputs"] = "ACQUIRED";
    journal.case_data(journal_index, raw);

    future::Counters after{};
    stage = std::string("case:") + item.name + ":copy_counters_after";
    journal.stage(stage);
    driver(cuMemcpyDtoH(&after, counters_address, sizeof(after)),
           "copy counters after case");
    raw["counters_after"] = counters_json(after);
    raw["acquisition"]["counters_after"] = "ACQUIRED";
    journal.case_data(journal_index, raw);

    const bool ordered_count = after.trace_count >= before.trace_count;
    const bool start_in_capacity = before.trace_count <= trace_capacity;
    const bool end_in_capacity = after.trace_count <= trace_capacity;
    const auto reported_count = ordered_count
                                    ? after.trace_count - before.trace_count
                                    : std::uint64_t{0};
    std::uint64_t safe_count = 0;
    if (ordered_count && start_in_capacity) {
        const auto capacity_remaining = trace_capacity - before.trace_count;
        safe_count = std::min(reported_count, capacity_remaining);
        safe_count = std::min(safe_count, kMaximumCaseTraceRecords);
    }
    const bool trace_truncated = !ordered_count || !start_in_capacity ||
                                 !end_in_capacity ||
                                 safe_count != reported_count;
    raw["trace_window"] = {
        {"before_count", before.trace_count},
        {"after_count", after.trace_count},
        {"configured_capacity", trace_capacity},
        {"maximum_case_copy_records", kMaximumCaseTraceRecords},
        {"ordered_count", ordered_count},
        {"start_in_capacity", start_in_capacity},
        {"end_in_capacity", end_in_capacity},
        {"reported_case_records", reported_count},
        {"copied_records", safe_count},
        {"truncated_or_anomalous", trace_truncated},
    };
    std::vector<future::Trace> traces(static_cast<std::size_t>(safe_count));
    raw["acquisition"]["traces"] = "COPY_PENDING";
    journal.case_data(journal_index, raw);
    stage = std::string("case:") + item.name + ":copy_bounded_traces";
    journal.stage(stage);
    if (!traces.empty()) {
        driver(cuMemcpyDtoH(traces.data(),
                            trace_address + before.trace_count * sizeof(future::Trace),
                            traces.size() * sizeof(future::Trace)),
               "copy bounded case trace slice");
    }
    raw["raw_traces"] = json::array();
    for (const auto& trace : traces) raw["raw_traces"].push_back(trace_json(trace));
    raw["acquisition"]["traces"] = trace_truncated ? "BOUNDED_PARTIAL" : "ACQUIRED";
    journal.case_data(journal_index, raw);

    // All available raw outputs, counter snapshots and bounded trace records
    // are saved before any semantic validation below can reject the case.
    stage = std::string("case:") + item.name + ":validate";
    journal.stage(stage);
    require(!trace_truncated, "trace count/window is anomalous or truncated");
    require(before.pending == 0, "pending work before case");
    require(before.trace_overflow == 0, "trace overflow before case");
    require(after.pending == 0, "pending work after synchronized case");
    require(subtract(after.issued, before.issued, "issued") == active_lanes,
            "issued count differs from active lanes");
    require(subtract(after.model_ready, before.model_ready, "model_ready") ==
                active_lanes,
            "model-ready count differs from active lanes");
    require(subtract(after.consumed, before.consumed, "consumed") ==
                (item.mode == 1 ? active_lanes : 0) &&
                subtract(after.drained, before.drained, "drained") ==
                (item.mode == 1 ? 0 : active_lanes) &&
                subtract(after.terminal_error, before.terminal_error,
                         "terminal_error") == 0,
            "executed-path consume/drain terminal counts differ");
    require(subtract(after.native_loads, before.native_loads, "native_loads") ==
                    0 &&
                subtract(after.native_bytes, before.native_bytes,
                         "native_bytes") == 0 &&
                subtract(after.rejected, before.rejected, "rejected") == 0,
            "instrumented input load bypassed or rejected");
    require(subtract(after.groups_issued, before.groups_issued,
                     "groups_issued") == expected_groups &&
                subtract(after.groups_completed, before.groups_completed,
                         "groups_completed") == expected_groups,
            "page-group issue/completion conservation failed");
    require(subtract(after.next_reservation, before.next_reservation,
                     "next_reservation") == expected_groups,
            "reservation allocation differs from page groups");
    require(reported_count == 2U * active_lanes,
            "expected one issue and one combined ready/terminal trace per lane");
    require(subtract(after.trace_overflow, before.trace_overflow,
                     "trace_overflow") == 0,
            "trace overflow in bounded one-warp case");
    require(after.issued == after.consumed + after.drained +
                                after.terminal_error + after.pending,
            "cumulative terminal conservation failed");
    require(after.groups_issued == after.groups_completed,
            "cumulative group conservation failed");

    std::array<std::uint32_t, 32> ranges{};
    std::array<std::uint64_t, 32> pages{};
    ranges.fill(1);
    for (std::uint32_t lane = 0; lane < 32; ++lane) {
        pages[lane] = (std::uint64_t{lane} * item.stride) / kPageBytes;
    }

    std::array<std::vector<future::Trace>, 32> by_lane;
    for (const auto& trace : traces) {
        require(trace.lane < 32 && (mask & (1U << trace.lane)),
                "trace attributed to inactive or invalid lane");
        by_lane[trace.lane].push_back(trace);
    }

    std::set<std::uint64_t> reservations;
    std::map<std::uint64_t, std::pair<std::uint64_t, std::uint64_t>> group_times;
    json output_json = json::array();
    json trace_output = json::array();
    std::uint64_t observed_checksum = 0;
    std::uint64_t expected_checksum = 0;
    for (std::uint32_t lane = 0; lane < 32; ++lane) {
        const bool active = (mask & (1U << lane)) != 0;
        require(observed[lane] == expected[lane],
                "per-lane output differs from independent CPU oracle");
        observed_checksum = observed_checksum * 131U ^ observed[lane];
        expected_checksum = expected_checksum * 131U ^ expected[lane];
        output_json.push_back({{"lane", lane},
                               {"active", active},
                               {"overwrite_executed", item.mode == 0},
                               {"output_store_executed", item.mode != 2},
                               {"observed", observed[lane]},
                               {"expected", expected[lane]},
                               {"sentinel", sentinels[lane]}});
        if (!active) {
            require(by_lane[lane].empty(), "inactive lane produced future trace");
            continue;
        }
        require(by_lane[lane].size() == 2,
                "active lane does not have exactly two transition records");
        const future::Trace* issue = nullptr;
        const future::Trace* terminal = nullptr;
        for (const auto& trace : by_lane[lane]) {
            if (trace.event == 0 && trace.status == future::kPending) {
                require(issue == nullptr, "duplicate issue trace");
                issue = &trace;
            } else if (trace.event ==
                           (future::kBecameReady | (item.mode == 1 ? future::kConsumed : future::kDrained)) &&
                       trace.status == future::kReady) {
                require(terminal == nullptr, "duplicate terminal trace");
                terminal = &trace;
            } else {
                throw std::runtime_error("unexpected trace event/status");
            }
        }
        require(issue && terminal, "missing issue or ready/terminal trace");
        const auto address = reinterpret_cast<std::uintptr_t>(input) +
                             std::uint64_t{lane} * item.stride;
        const auto group = future::page_group_mask(mask, ranges.data(),
                                                   pages.data(), lane);
        require(issue->reservation_id != 0 &&
                    issue->reservation_id == terminal->reservation_id &&
                    issue->address == address && terminal->address == address &&
                    issue->bytes == 4 && terminal->bytes == 4 &&
                    issue->group_mask == group && terminal->group_mask == group &&
                    issue->instruction_id == terminal->instruction_id &&
                    issue->issue_ns == terminal->issue_ns &&
                    issue->ready_ns == terminal->ready_ns,
                "trace identity/group binding failed");
        require(issue->finish_ns >= issue->issue_ns &&
                    terminal->finish_ns >= terminal->ready_ns,
                "trace transition clock ordering failed");
        if (!instruction_id_set) {
            instruction_id = issue->instruction_id;
            instruction_id_set = true;
        }
        require(issue->instruction_id == instruction_id &&
                    instruction_id != UINT32_MAX,
                "single static producer instruction identity changed");
        reservations.insert(issue->reservation_id);
        const auto times = std::pair{issue->issue_ns, issue->ready_ns};
        const auto [position, inserted] =
            group_times.emplace(issue->reservation_id, times);
        require(inserted || position->second == times,
                "lanes sharing a reservation disagree on group timing");
    }
    require(observed_checksum == expected_checksum,
            "whole-warp checksum differs from CPU oracle");
    require(reservations.size() == expected_groups,
            "unique reservation count differs from page-group oracle");
    for (const auto& trace : traces) {
        trace_output.push_back(trace_json(trace));
    }
    raw.update({
        {"case", item.name},
        {"stride_bytes", item.stride},
        {"active_mask", producer_mask},
        {"mode", item.mode},
        {"overwrite_executed", item.mode == 0},
        {"output_store_executed", item.mode != 2},
        {"terminal_kind", item.mode == 1 ? "CONSUMED" : "DRAINED"},
        {"active_lanes", active_lanes},
        {"expected_groups", expected_groups},
        {"observed_unique_reservations", reservations.size()},
        {"observed_checksum", observed_checksum},
        {"expected_checksum", expected_checksum},
        {"counters_before", counters_json(before)},
        {"counters_after", counters_json(after)},
        {"counter_delta", counter_delta_json(before, after)},
        {"outputs", output_json},
        {"traces", trace_output},
        {"validation", "PASS"},
    });
    journal.case_data(journal_index, raw);
    return raw;
}

struct CleanupResult {
    json report;
    bool safe_retirement_confirmed{false};
    bool all_observable_steps_succeeded{false};
};

CleanupResult cleanup_resources(CUmodule& module, hbfsim_context*& context,
                                bool& registered, std::uint32_t*& allocation,
                                std::uint32_t*& input, std::uint32_t*& output,
                                void*& plugin, AcquisitionJournal* journal,
                                std::string& stage) noexcept {
    CleanupResult result;
    result.report = {
        {"module_unload", {{"called", false}, {"confirmed", false}}},
        {"range_unregister", {{"called", false}, {"confirmed", false}}},
        {"context_destroy", {{"called", false},
                              {"completion", "NOT_CALLED"}}},
        {"output_free", {{"called", false}, {"confirmed", false}}},
        {"input_backing_free", {{"called", false}, {"confirmed", false}}},
        {"plugin_close", {{"called", false}, {"confirmed", false}}},
        {"safe_retirement_confirmed", false},
    };
    const auto publish = [&] {
        if (journal) journal->cleanup_noexcept(result.report);
    };

    if (module) {
        stage = "cleanup:module_unload_and_internal_sync";
        result.report["module_unload"]["called"] = true;
        const auto code = cuModuleUnload(module);
        result.report["module_unload"]["cuda_result"] =
            static_cast<std::uint32_t>(code);
        result.report["module_unload"]["confirmed"] = code == CUDA_SUCCESS;
        publish();
        if (code != CUDA_SUCCESS) {
            result.report["stopped_after"] = "module_unload";
            result.report["stop_reason"] =
                "module sync/disable/unload was not confirmed; later retirement and frees skipped";
            publish();
            return result;
        }
        module = nullptr;
    } else {
        result.report["module_unload"]["not_applicable"] = true;
    }

    if (registered) {
        stage = "cleanup:range_unregister";
        result.report["range_unregister"]["called"] = true;
        if (!context) {
            result.report["range_unregister"]["error"] =
                "registered range has no live context handle";
            result.report["stopped_after"] = "range_unregister";
            publish();
            return result;
        }
        const auto code = hbfsim_unregister(context, input);
        result.report["range_unregister"]["hbfsim_result"] = code;
        result.report["range_unregister"]["confirmed"] = code == HBFSIM_OK;
        publish();
        if (code != HBFSIM_OK) {
            result.report["stopped_after"] = "range_unregister";
            result.report["stop_reason"] =
                "range retirement was not confirmed; context and allocations retained until process exit";
            publish();
            return result;
        }
        registered = false;
    } else {
        result.report["range_unregister"]["not_applicable"] = true;
    }
    result.safe_retirement_confirmed = true;
    result.report["safe_retirement_confirmed"] = true;

    if (context) {
        stage = "cleanup:context_destroy_call";
        result.report["context_destroy"]["called"] = true;
        result.report["context_destroy"]["completion"] =
            "UNOBSERVABLE_VOID_API";
        publish();
        hbfsim_context_destroy(context);
        // The void API has no success result. Drop the caller's handle to
        // prevent a second call, without claiming internal destruction.
        context = nullptr;
    } else {
        result.report["context_destroy"]["not_applicable"] = true;
    }

    bool observable_success = true;
    if (output) {
        stage = "cleanup:output_free";
        result.report["output_free"]["called"] = true;
        const auto code = cudaFree(output);
        result.report["output_free"]["cuda_result"] =
            static_cast<std::uint32_t>(code);
        result.report["output_free"]["confirmed"] = code == cudaSuccess;
        observable_success &= code == cudaSuccess;
        if (code == cudaSuccess) output = nullptr;
        publish();
    } else {
        result.report["output_free"]["not_applicable"] = true;
    }
    if (allocation) {
        stage = "cleanup:input_backing_free";
        result.report["input_backing_free"]["called"] = true;
        const auto code = cudaFree(allocation);
        result.report["input_backing_free"]["cuda_result"] =
            static_cast<std::uint32_t>(code);
        result.report["input_backing_free"]["confirmed"] = code == cudaSuccess;
        observable_success &= code == cudaSuccess;
        if (code == cudaSuccess) {
            allocation = nullptr;
            input = nullptr;
        }
        publish();
    } else {
        result.report["input_backing_free"]["not_applicable"] = true;
    }
    if (plugin) {
        stage = "cleanup:plugin_close";
        result.report["plugin_close"]["called"] = true;
        const auto code = dlclose(plugin);
        result.report["plugin_close"]["result"] = code;
        result.report["plugin_close"]["confirmed"] = code == 0;
        observable_success &= code == 0;
        if (code == 0) plugin = nullptr;
        publish();
    } else {
        result.report["plugin_close"]["not_applicable"] = true;
    }
    result.all_observable_steps_succeeded = observable_success;
    result.report["all_observable_steps_succeeded"] = observable_success;
    publish();
    return result;
}
}  // namespace

int main(int argc, char** argv) {
    CUmodule module = nullptr;
    hbfsim_context* context = nullptr;
    std::uint32_t* allocation = nullptr;
    std::uint32_t* input = nullptr;
    std::uint32_t* output = nullptr;
    void* plugin = nullptr;
    bool registered = false;
    bool cleanup_attempted = false;
    std::string stage = "parse_options";
    std::unique_ptr<AcquisitionJournal> journal;
    CleanupResult cleanup;
    try {
        // No new cubin may execute until its own compile/disassembly identities are frozen.
        require(kReviewedCubinBytes > 0 && kReviewedTransformedPtxBytes > 0,
                "future-lifecycle native image identities are UNFROZEN");
        const auto options = parse_options(argc, argv);
        journal = std::make_unique<AcquisitionJournal>(
            options.output + ".partial.json");
        stage = "read_original_ptx";
        journal->stage(stage);
        auto ptx = read_file(options.ptx);
        stage = "load_actual_plugin";
        journal->stage(stage);
        plugin = dlopen(options.plugin.c_str(), RTLD_NOW | RTLD_GLOBAL);
        require(plugin != nullptr, "cannot load actual PTX plugin");
        auto process = reinterpret_cast<int (*)(const char*, int, char*)>(
            dlsym(plugin, "process_input"));
        auto begin_load = reinterpret_cast<std::uint64_t (*)(const char*, std::size_t)>(
            dlsym(RTLD_DEFAULT, "hbfsim_begin_module_load_from_ptx"));
        auto end_load = reinterpret_cast<void (*)(std::uint64_t)>(
            dlsym(RTLD_DEFAULT, "hbfsim_end_module_load"));
        require(process && begin_load && end_load,
                "plugin or launch-gate module association API unavailable");

        stage = "transform_candidate_ptx";
        journal->stage(stage);
        const auto request = json({{"input",
            {{"full_ptx", ptx}, {"to_patch_kernel", kKernel},
             {"transform_mode", "timing_load_future_v1"}}}}).dump();
        std::vector<char> response(32 * 1024 * 1024);
        require(process(request.c_str(), static_cast<int>(response.size()),
                        response.data()) == 0,
                "actual future transform failed");
        const auto transformed = json::parse(response.data());
        require(transformed.at("modified").get<bool>(),
                "future transform did not modify selected kernel");
        ptx = transformed.at("output_ptx").get<std::string>();
        require(ptx.size() == kReviewedTransformedPtxBytes &&
                    sha256_hex(ptx) == kReviewedTransformedPtxSha256,
                "transformed PTX differs from reviewed native-image source");
        stage = "persist_transformed_ptx";
        journal->stage(stage);
        write_file(options.output + ".transformed.ptx", ptx);

        stage = "validate_plugin_manifest";
        journal->stage(stage);
        const char* pass_path = std::getenv("HBFSIM_PASS_MANIFEST_PATH");
        require(pass_path && pass_path[0],
                "explicit plugin manifest destination required");
        const auto pass = json::parse(read_file(pass_path));
        require(pass.at("transform_mode") == "timing_load_future_v1" &&
                    pass.at("instrumented").get<bool>() &&
                    pass.at("kernel") == kKernel &&
                    pass.at("rewritten_instructions") == 1 &&
                    pass.at("unsupported_instructions") == 0 &&
                    pass.at("unsupported_opcodes").empty() &&
                    pass.at("unsupported_parameters").empty() &&
                    pass.at("future_kernel").at("static_producers") == 1 &&
                    pass.at("future_kernel").at("required_threads") ==
                        json::array({32, 1, 1}) &&
                    pass.at("future_contract").at("original_ptx_sha256") ==
                        kOriginalPtxSha256 &&
                    pass.at("future_contract").at("time_scale") == 1,
                "actual plugin manifest does not bind one future producer");

        stage = "read_reviewed_native_image";
        journal->stage(stage);
        const auto cubin = read_exact_binary(options.cubin, kReviewedCubinBytes);
        const auto cubin_sha256 = sha256_hex(cubin);
        require(cubin_sha256 == kReviewedCubinSha256,
                "reviewed native image hash mismatch");
        json native_image_binding{
            {"image_kind", "CUBIN"},
            {"image_path", options.cubin},
            {"image_bytes", cubin.size()},
            {"image_sha256", cubin_sha256},
            {"source_ptx_bytes", ptx.size()},
            {"source_ptx_sha256", sha256_hex(ptx)},
            {"original_ptx_sha256", kOriginalPtxSha256},
            {"selected_kernel", kKernel},
            {"compiler", {
                {"tool", "ptxas"},
                {"architecture", "sm_120"},
                {"optimization", "-O3"},
                {"build_manifest_sha256", kReviewedBuildManifestSha256},
            }},
            {"sass", {
                {"mapping_validation", "NOT_PROVEN"},
                {"disassembly_manifest_sha256",
                    kReviewedDisassemblyManifestSha256},
                {"nvdisasm_sha256", kReviewedNvdisasmSha256},
                {"cuobjdump_sha256", kReviewedCuobjdumpSha256},
            }},
            {"load_state", "VERIFIED_NOT_LOADED"},
            {"same_retained_buffer_passed_to_driver", false},
        };
        journal->field("native_image_binding", native_image_binding);

        stage = "initialize_cuda_runtime";
        journal->stage(stage);
        runtime(cudaFree(nullptr), "initialize CUDA runtime");
        cudaDeviceProp properties{};
        stage = "query_cuda_device";
        journal->stage(stage);
        runtime(cudaGetDeviceProperties(&properties, 0), "query device properties");
        require(properties.warpSize == 32, "diagnostic requires warpSize=32");

        stage = "allocate_input_backing";
        journal->stage(stage);
        runtime(cudaMalloc(reinterpret_cast<void**>(&allocation),
                           kInputBytes + kPageBytes - 1),
                "allocate aligned input backing");
        const auto raw = reinterpret_cast<std::uintptr_t>(allocation);
        const auto aligned = (raw + kPageBytes - 1) & ~(std::uintptr_t{kPageBytes - 1});
        input = reinterpret_cast<std::uint32_t*>(aligned);
        require(aligned % kPageBytes == 0 &&
                    aligned + kInputBytes <= raw + kInputBytes + kPageBytes - 1,
                "cannot form bounded 4096-byte-aligned input range");
        stage = "allocate_output";
        journal->stage(stage);
        runtime(cudaMalloc(reinterpret_cast<void**>(&output),
                           32 * sizeof(*output)),
                "allocate disjoint native output");
        const auto input_begin = reinterpret_cast<std::uintptr_t>(input);
        const auto input_end = input_begin + kInputBytes;
        const auto output_begin = reinterpret_cast<std::uintptr_t>(output);
        const auto output_end = output_begin + 32 * sizeof(*output);
        require(output_end <= input_begin || input_end <= output_begin,
                "native output overlaps registered HBF input range");

        std::vector<std::uint32_t> input_host(kInputBytes / sizeof(std::uint32_t));
        for (std::size_t index = 0; index < input_host.size(); ++index) {
            input_host[index] = input_word(index);
        }
        stage = "initialize_input";
        journal->stage(stage);
        runtime(cudaMemcpy(input, input_host.data(), kInputBytes,
                           cudaMemcpyHostToDevice),
                "initialize diagnostic input");

        hbfsim_options context_options{
            .profile_path = options.profile.c_str(),
            .report_dir = options.report_dir.c_str(),
            .mode = HBFSIM_MODEL_FAST,
            .ring_capacity = 256,
            .request_timeout_ns = 1'000'000'000,
        };
        stage = "create_owned_fast_context";
        journal->stage(stage);
        require(hbfsim_context_create(&context_options, &context) == HBFSIM_OK,
                "create owned FAST timing context");
        hbfsim_range_options range{
            .mode = HBFSIM_RANGE_MODE_TIMING,
            .permissions = HBFSIM_RANGE_READ,
            .cache_policy = HBFSIM_CACHE_POLICY_NONE,
            .stream_id = 0,
        };
        stage = "register_input_range";
        journal->stage(stage);
        require(hbfsim_register_device(context, input, kInputBytes, &range) ==
                    HBFSIM_OK,
                "register exact input range");
        registered = true;

        stage = "begin_module_association";
        journal->stage(stage);
        const auto token = begin_load(ptx.data(), ptx.size());
        require(token != 0, "launch gate rejected transformed PTX transaction");
        stage = "load_associated_future_module";
        journal->stage(stage);
        const auto load_result = cuModuleLoadDataEx(&module, cubin.data(), 0,
                                                     nullptr, nullptr);
        end_load(token);
        native_image_binding["same_retained_buffer_passed_to_driver"] = true;
        native_image_binding["driver_result"] =
            static_cast<std::int32_t>(load_result);
        native_image_binding["load_state"] =
            load_result == CUDA_SUCCESS ? "LOADED" : "LOAD_FAILED";
        journal->field("native_image_binding", native_image_binding);
        driver(load_result, "load associated future module");

        stage = "lookup_future_kernel_and_globals";
        journal->stage(stage);
        CUfunction kernel = nullptr;
        driver(cuModuleGetFunction(&kernel, module, kKernel), "lookup selected kernel");
        const auto config_address =
            module_object<future::ModuleConfig>(module,
                "__hbfsim_timing_future_config_v1");
        const auto counters_address =
            module_object<future::Counters>(module,
                "__hbfsim_timing_future_counters_v1");
        const auto [trace_address, trace_bytes] =
            module_span(module, "__hbfsim_timing_future_trace_v1");
        future::ModuleConfig config{};
        driver(cuMemcpyDtoH(&config, config_address, sizeof(config)),
               "copy gate-published module config");
        require(future::valid_trace_span(config) &&
                    config.trace_address == trace_address &&
                    trace_bytes == config.trace_capacity * sizeof(future::Trace),
                "module trace/config association is invalid");
        journal->field("module_config", {
            {"abi_version", config.abi_version},
            {"struct_bytes", config.struct_bytes},
            {"enabled", config.enabled},
            {"trace_capacity", config.trace_capacity},
            {"maximum_thread_futures", config.maximum_thread_futures},
            {"maximum_block_threads", config.maximum_block_threads},
        });

        json cases = json::array();
        std::uint32_t instruction_id = 0;
        bool instruction_id_set = false;
        for (const auto& item : kCases) {
            cases.push_back(check_case(item, input_host, input, output, kernel,
                                       counters_address, trace_address,
                                       config.trace_capacity, instruction_id,
                                       instruction_id_set, *journal, stage));
        }
        future::Counters final_counters{};
        stage = "copy_final_counters";
        journal->stage(stage);
        driver(cuMemcpyDtoH(&final_counters, counters_address,
                            sizeof(final_counters)),
               "copy final counters");
        journal->field("final_counters", counters_json(final_counters));

        cleanup_attempted = true;
        cleanup = cleanup_resources(module, context, registered, allocation,
                                    input, output, plugin, journal.get(), stage);
        require(cleanup.safe_retirement_confirmed,
                "safe module/range retirement was not confirmed");
        require(cleanup.all_observable_steps_succeeded,
                "an observable post-retirement cleanup step failed");

        json result{
            {"schema_version", 1},
            {"evidence", "GPU_ACQUISITION"},
            {"validation_status", "UNVALIDATED"},
            {"scientific_claim", false},
            {"g5_closed", false},
            {"native_completion_timing_claim", false},
            {"kernel", kKernel},
            {"launch", {{"grid", {1, 1, 1}}, {"block", {32, 1, 1}}}},
            {"profile_page_bytes", kPageBytes},
            {"seed", kSeed},
            {"instruction_id", instruction_id},
            {"native_image_binding", native_image_binding},
            {"cases", cases},
            {"final_counters", counters_json(final_counters)},
            {"cleanup", cleanup.report},
        };
        journal->captured();
        stage = "persist_final_raw";
        journal->stage(stage);
        write_file(options.output, result.dump(2));
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "c6_future_lifecycle_correctness: %s\n", error.what());
        const auto failure_stage = stage;
        if (journal) journal->failure(failure_stage, error.what());
        if (!cleanup_attempted) {
            cleanup_attempted = true;
            cleanup = cleanup_resources(module, context, registered,
                                        allocation, input, output, plugin,
                                        journal.get(), stage);
        }
        if (journal) journal->failure(failure_stage, error.what());
        return 1;
    }
}

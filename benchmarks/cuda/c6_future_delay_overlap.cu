#include <cstddef>
#include <cstdint>

namespace c6_delay {
constexpr std::uint64_t kMagic = 0x4836465554444c59ULL;
constexpr std::uint32_t kAbiVersion = 1;
constexpr std::uint32_t kLaneCount = 32;
constexpr std::uint32_t kRecordStride = 128;
constexpr std::uint32_t kDelayNs = 20'000;
constexpr std::uint32_t kWorkLong = 4096;

struct alignas(8) Config {
    std::uint64_t magic;
    std::uint32_t abi_version;
    std::uint32_t struct_bytes;
    std::uint32_t enabled;
    std::uint32_t expected_instruction_id;
    std::uint64_t delay_ns;
    std::uint64_t launch_epoch;
    std::uint64_t input_base;
    std::uint64_t input_bytes;
    std::uint64_t records_address;
    std::uint64_t records_bytes;
    std::uint32_t record_count;
    std::uint32_t record_stride;
    std::uint32_t grid_x;
    std::uint32_t block_x;
    std::uint32_t work_count;
    std::uint32_t reserved;
};

struct alignas(8) Record {
    std::uint64_t launch_epoch;
    std::uint64_t configured_delay_ns;
    std::uint64_t helper_entry_ns;
    std::uint64_t arrival_ns;
    std::uint64_t helper_issue_exit_ns;
    std::uint64_t native_instruction_after_ns;
    std::uint64_t work_begin_ns;
    std::uint64_t work_end_ns;
    std::uint64_t wait_enter_ns;
    std::uint64_t wait_exit_ns;
    std::uint64_t consumer_after_ns;
    std::uint64_t ready_ns;
    std::uint64_t reservation_id;
    std::uint32_t lane;
    std::uint32_t status;
    std::uint32_t valid_bits;
    std::uint32_t work_count;
    std::uint64_t output_bits;
};

static_assert(sizeof(Config) == 96);
static_assert(offsetof(Config, delay_ns) == 24);
static_assert(offsetof(Config, records_address) == 56);
static_assert(offsetof(Config, work_count) == 88);
static_assert(sizeof(Record) == 128);
static_assert(offsetof(Record, helper_entry_ns) == 16);
static_assert(offsetof(Record, wait_enter_ns) == 64);
static_assert(offsetof(Record, reservation_id) == 96);
static_assert(offsetof(Record, output_bits) == 120);

constexpr std::uint32_t kHelperEntry = 1U << 0;
constexpr std::uint32_t kArrival = 1U << 1;
constexpr std::uint32_t kIssueExit = 1U << 2;
constexpr std::uint32_t kNativeAfter = 1U << 3;
constexpr std::uint32_t kWorkBegin = 1U << 4;
constexpr std::uint32_t kWorkEnd = 1U << 5;
constexpr std::uint32_t kWaitEnter = 1U << 6;
constexpr std::uint32_t kWaitExit = 1U << 7;
constexpr std::uint32_t kConsumerAfter = 1U << 8;
constexpr std::uint32_t kOutput = 1U << 9;
constexpr std::uint32_t kKernelBits =
    kNativeAfter | kWorkBegin | kWorkEnd | kConsumerAfter | kOutput;
constexpr std::uint32_t kFutureBits = kKernelBits | kHelperEntry | kArrival |
                                      kIssueExit | kWaitEnter | kWaitExit;
} // namespace c6_delay

#if defined(HBFSIM_C6_FUTURE_DELAY_KERNEL_ONLY)

#define C6_PREDICATED_WORK_STEP                                             \
    "@%%c6_work_active mad.lo.u32 %0, %0, 0x0019660d, %1;\n\t"
#define C6_REPEAT_2(item) item item
#define C6_REPEAT_4(item) C6_REPEAT_2(item) C6_REPEAT_2(item)
#define C6_REPEAT_8(item) C6_REPEAT_4(item) C6_REPEAT_4(item)
#define C6_REPEAT_16(item) C6_REPEAT_8(item) C6_REPEAT_8(item)
#define C6_REPEAT_32(item) C6_REPEAT_16(item) C6_REPEAT_16(item)
#define C6_REPEAT_64(item) C6_REPEAT_32(item) C6_REPEAT_32(item)
#define C6_REPEAT_128(item) C6_REPEAT_64(item) C6_REPEAT_64(item)
#define C6_REPEAT_256(item) C6_REPEAT_128(item) C6_REPEAT_128(item)
#define C6_REPEAT_512(item) C6_REPEAT_256(item) C6_REPEAT_256(item)
#define C6_REPEAT_1024(item) C6_REPEAT_512(item) C6_REPEAT_512(item)
#define C6_REPEAT_2048(item) C6_REPEAT_1024(item) C6_REPEAT_1024(item)
#define C6_REPEAT_4096(item) C6_REPEAT_2048(item) C6_REPEAT_2048(item)

// Expand directly in each entry to keep CUDA line information in the
// existing parser's non-inlined form. The PTX predicate lives in the entry
// register namespace; no nested assembly scope or unprefixed name is needed.
// K=0 still pays the measured cost of 4096 predicated-off instructions.
// All original stores follow the first consumer. Host helper-field and
// counter checks remain independent of the final kernel validity mask.
#define C6_RUN_LANE(Future) \
    do { \
        const std::uint32_t lane = threadIdx.x; \
        auto* const record = &records[lane]; \
        std::uint32_t loaded; \
        asm volatile("ld.global.u32 %0, [%1];" \
                     : "=r"(loaded) : "l"(&input[lane]) : "memory"); \
        std::uint64_t native_after, work_begin, work_end, consumer_after; \
        asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(native_after) : : "memory"); \
        asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(work_begin) : : "memory"); \
        std::uint32_t work = seed ^ (lane * 0x9e3779b9U + 0x85ebca6bU); \
        const std::uint32_t addend = 0x3c6ef35fU + lane; \
        asm volatile( \
            ".reg .pred %%c6_work_active;\n\t" \
            "setp.ne.u32 %%c6_work_active, %2, 0;\n\t" \
            C6_REPEAT_4096(C6_PREDICATED_WORK_STEP) \
            : "+r"(work) : "r"(addend), "r"(work_count) : "memory"); \
        asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(work_end) : : "memory"); \
        const auto value = loaded ^ work ^ 0xd1b54a35U; \
        asm volatile("" : : "r"(value) : "memory"); \
        asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(consumer_after) : : "memory"); \
        record->launch_epoch = launch_epoch; \
        if constexpr (!(Future)) { \
            record->configured_delay_ns = 0; \
            record->status = 1; \
        } \
        record->native_instruction_after_ns = native_after; \
        record->work_begin_ns = work_begin; \
        record->work_end_ns = work_end; \
        record->consumer_after_ns = consumer_after; \
        asm volatile("st.global.u32 [%0], %1;" : : "l"(&record->lane), "r"(lane) : "memory"); \
        const std::uint32_t valid_mask = (Future) ? c6_delay::kFutureBits : c6_delay::kKernelBits; \
        asm volatile("st.global.u32 [%0], %1;" : : "l"(&record->valid_bits), "r"(valid_mask) : "memory"); \
        record->work_count = work_count; \
        record->output_bits = value; \
        output[lane] = value; \
    } while (false)

extern "C" __global__ __launch_bounds__(32) void c6_future_delay_native(
    const std::uint32_t* input, std::uint32_t* output,
    c6_delay::Record* records, std::uint32_t seed,
    std::uint32_t work_count, std::uint64_t launch_epoch)
{
    C6_RUN_LANE(false);
}

extern "C" __global__ __launch_bounds__(32) void c6_future_delay_future(
    const std::uint32_t* input, std::uint32_t* output,
    c6_delay::Record* records, std::uint32_t seed,
    std::uint32_t work_count, std::uint64_t launch_epoch)
{
    C6_RUN_LANE(true);
}

#else

#include <hbfsim/api.h>
#include <hbfsim/timing_future_abi.hpp>

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <json.hpp>
#include <openssl/sha.h>

#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <fstream>
#include <iterator>
#include <memory>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
using nlohmann::json;
namespace future = hbfsim::timing_future;
constexpr char kFutureKernel[] = "c6_future_delay_future";
constexpr char kNativeKernel[] = "c6_future_delay_native";
constexpr std::uint32_t kSeed = 0x9e3779b9U;
constexpr std::size_t kRegisteredBytes = 4096;
constexpr std::size_t kInputBytes = 32 * sizeof(std::uint32_t);
constexpr std::size_t kRecordBytes = 32 * sizeof(c6_delay::Record);
constexpr std::uint32_t kWarmups = 1;
constexpr std::uint32_t kSamples = 10;

struct Options {
    std::string profile;
    std::string plugin;
    std::string ptx;
    std::string transformed_ptx;
    std::string cubin;
    std::string binding;
    std::string native_binding;
    std::string output;
    std::string report_dir;
};

struct NativeBinding {
    std::size_t original_bytes{};
    std::string original_sha;
    std::size_t transformed_bytes{};
    std::string transformed_sha;
    std::size_t cubin_bytes{};
    std::string cubin_sha;
    std::string build_manifest_sha;
    std::string disassembly_manifest_sha;
    std::string nvdisasm_sha;
    std::string cuobjdump_sha;
};

struct OrdinaryNativeBinding {
    std::size_t original_bytes{}, cubin_bytes{};
    std::string original_sha, cubin_sha, cubin_path;
    std::string build_manifest_sha, disassembly_manifest_sha;
    std::string nvdisasm_sha, cuobjdump_sha;
};

struct Cell {
    const char* arm;
    std::uint32_t delay_ns;
    std::uint32_t work_count;
    bool future_arm;
};

constexpr std::array<Cell, 6> kCells{{
    {"native", 0, 0, false},
    {"future0", 0, 0, true},
    {"futureD", c6_delay::kDelayNs, 0, true},
    {"native", 0, c6_delay::kWorkLong, false},
    {"future0", 0, c6_delay::kWorkLong, true},
    {"futureD", c6_delay::kDelayNs, c6_delay::kWorkLong, true},
}};

void require(bool condition, const std::string& message)
{
    if (!condition) throw std::runtime_error(message);
}

void driver(CUresult result, const char* operation)
{
    const char* message = nullptr;
    (void)cuGetErrorString(result, &message);
    require(result == CUDA_SUCCESS,
            std::string(operation) + ": " + (message ? message : "CUDA error"));
}

void runtime(cudaError_t result, const char* operation)
{
    require(result == cudaSuccess,
            std::string(operation) + ": " + cudaGetErrorString(result));
}

std::string read_file(const std::string& path)
{
    std::ifstream stream(path, std::ios::binary);
    require(bool(stream), "cannot read " + path);
    return {std::istreambuf_iterator<char>(stream), {}};
}

void write_file(const std::string& path, const std::string& contents)
{
    std::ofstream stream(path, std::ios::binary);
    require(bool(stream), "cannot open " + path);
    stream << contents << '\n';
    stream.flush();
    require(bool(stream), "cannot write " + path);
}

std::string sha256_hex(const std::string& contents)
{
    std::array<unsigned char, SHA256_DIGEST_LENGTH> digest{};
    require(SHA256(reinterpret_cast<const unsigned char*>(contents.data()),
                   contents.size(), digest.data()) != nullptr,
            "SHA256 failed");
    constexpr char digits[] = "0123456789abcdef";
    std::string result(SHA256_DIGEST_LENGTH * 2, '0');
    for (std::size_t index = 0; index < digest.size(); ++index) {
        result[index * 2] = digits[digest[index] >> 4];
        result[index * 2 + 1] = digits[digest[index] & 15];
    }
    return result;
}

Options parse_options(int argc, char** argv)
{
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string key = argv[index];
        require(index + 1 < argc, "missing value for " + key);
        const std::string value = argv[++index];
        if (key == "--profile") options.profile = value;
        else if (key == "--plugin") options.plugin = value;
        else if (key == "--ptx") options.ptx = value;
        else if (key == "--transformed-ptx") options.transformed_ptx = value;
        else if (key == "--cubin") options.cubin = value;
        else if (key == "--binding") options.binding = value;
        else if (key == "--native-binding") options.native_binding = value;
        else if (key == "--output") options.output = value;
        else if (key == "--report-dir") options.report_dir = value;
        else throw std::runtime_error("unknown option " + key);
    }
    require(!options.profile.empty() && !options.plugin.empty() &&
                !options.ptx.empty() && !options.transformed_ptx.empty() &&
                !options.cubin.empty() &&
                !options.binding.empty() && !options.native_binding.empty() &&
                !options.output.empty() &&
                !options.report_dir.empty(),
            "all artifact paths are required");
    const auto profile = json::parse(read_file(options.profile));
    require(profile.at("page_bytes").get<std::uint32_t>() == 4096 &&
                profile.at("time_scale").get<std::uint32_t>() == 1 &&
                profile.at("read_latency_ns").get<std::uint64_t>() > 0 &&
                profile.at("program_latency_ns").get<std::uint64_t>() > 0 &&
                profile.at("aggregate_bandwidth_bytes_per_s")
                        .get<std::uint64_t>() > 0,
            "positive FAST 4096-byte profile with time_scale=1 required");
    return options;
}

NativeBinding parse_binding(const std::string& path)
{
    const auto value = json::parse(read_file(path));
    require(value.at("schema_version") == 1 &&
                value.at("selected_kernel") == kFutureKernel &&
                value.at("native_kernel") == kNativeKernel &&
                value.at("compiler").at("tool") == "ptxas" &&
                value.at("compiler").at("architecture") == "sm_120" &&
                value.at("compiler").at("optimization") == "-O3" &&
                value.at("mapping_validation") == "NOT_PROVEN",
            "native-image binding contract mismatch");
    NativeBinding result{
        value.at("original_ptx").at("bytes").get<std::size_t>(),
        value.at("original_ptx").at("sha256").get<std::string>(),
        value.at("transformed_ptx").at("bytes").get<std::size_t>(),
        value.at("transformed_ptx").at("sha256").get<std::string>(),
        value.at("cubin").at("bytes").get<std::size_t>(),
        value.at("cubin").at("sha256").get<std::string>(),
        value.at("build_manifest").at("sha256").get<std::string>(),
        value.at("disassembly_manifest").at("sha256").get<std::string>(),
        value.at("nvdisasm").at("sha256").get<std::string>(),
        value.at("cuobjdump").at("sha256").get<std::string>(),
    };
    const auto valid_sha = [](const std::string& text) {
        return text.size() == 64 && std::all_of(text.begin(), text.end(),
            [](char value) { return (value >= '0' && value <= '9') ||
                                     (value >= 'a' && value <= 'f'); });
    };
    require(result.original_bytes && result.transformed_bytes &&
                result.cubin_bytes && valid_sha(result.original_sha) &&
                valid_sha(result.transformed_sha) && valid_sha(result.cubin_sha) &&
                valid_sha(result.build_manifest_sha) &&
                valid_sha(result.disassembly_manifest_sha) &&
                valid_sha(result.nvdisasm_sha) && valid_sha(result.cuobjdump_sha),
            "native-image binding has invalid size or hash");
    return result;
}

OrdinaryNativeBinding parse_native_binding(const std::string& path,
                                           const NativeBinding& future_binding)
{
    const auto value = json::parse(read_file(path));
    require(value.size() == 11 && value.at("schema_version") == 1 &&
                value.at("selected_kernel") == kNativeKernel &&
                value.at("transform_mode") == "native_untransformed" &&
                value.at("mapping_validation") == "NOT_PROVEN" &&
                value.at("compiler") == json({{"tool", "ptxas"},
                    {"architecture", "sm_120"}, {"optimization", "-O3"}}),
            "ordinary native binding contract mismatch");
    OrdinaryNativeBinding result{
        value.at("original_ptx").at("bytes").get<std::size_t>(),
        value.at("cubin").at("bytes").get<std::size_t>(),
        value.at("original_ptx").at("sha256").get<std::string>(),
        value.at("cubin").at("sha256").get<std::string>(),
        value.at("cubin").at("path").get<std::string>(),
        value.at("build_manifest").at("sha256").get<std::string>(),
        value.at("disassembly_manifest").at("sha256").get<std::string>(),
        value.at("nvdisasm").at("sha256").get<std::string>(),
        value.at("cuobjdump").at("sha256").get<std::string>()};
    const auto valid_sha = [](const std::string& text) {
        return text.size() == 64 && std::all_of(text.begin(), text.end(),
            [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); });
    };
    require(result.original_bytes == future_binding.original_bytes &&
                result.original_sha == future_binding.original_sha &&
                result.cubin_bytes && !result.cubin_path.empty() &&
                result.cubin_path.front() == '/' && valid_sha(result.cubin_sha) &&
                valid_sha(result.build_manifest_sha) &&
                valid_sha(result.disassembly_manifest_sha) &&
                valid_sha(result.nvdisasm_sha) && valid_sha(result.cuobjdump_sha),
            "ordinary native binding has wrong source, size or hash");
    return result;
}

template <class T>
CUdeviceptr module_object(CUmodule module, const char* name)
{
    CUdeviceptr address = 0;
    std::size_t bytes = 0;
    driver(cuModuleGetGlobal(&address, &bytes, module, name), name);
    require(bytes == sizeof(T), std::string("wrong symbol size: ") + name);
    return address;
}

std::pair<CUdeviceptr, std::size_t> module_span(CUmodule module,
                                                const char* name)
{
    CUdeviceptr address = 0;
    std::size_t bytes = 0;
    driver(cuModuleGetGlobal(&address, &bytes, module, name), name);
    return {address, bytes};
}

json counters_json(const future::Counters& value)
{
    return {{"next_reservation", value.next_reservation},
            {"issued", value.issued}, {"pending", value.pending},
            {"model_ready", value.model_ready}, {"consumed", value.consumed},
            {"drained", value.drained},
            {"terminal_error", value.terminal_error},
            {"native_loads", value.native_loads},
            {"native_bytes", value.native_bytes}, {"rejected", value.rejected},
            {"groups_issued", value.groups_issued},
            {"groups_completed", value.groups_completed},
            {"trace_count", value.trace_count},
            {"trace_overflow", value.trace_overflow}};
}

json trace_json(const future::Trace& value)
{
    return {{"reservation_id", value.reservation_id}, {"address", value.address},
            {"issue_ns", value.issue_ns}, {"ready_ns", value.ready_ns},
            {"finish_ns", value.finish_ns},
            {"instruction_id", value.instruction_id}, {"bytes", value.bytes},
            {"lane", value.lane}, {"group_mask", value.group_mask},
            {"event", value.event}, {"status", value.status}};
}

json record_json(const c6_delay::Record& value)
{
    return {{"launch_epoch", value.launch_epoch},
            {"configured_delay_ns", value.configured_delay_ns},
            {"helper_entry_ns", value.helper_entry_ns},
            {"arrival_ns", value.arrival_ns},
            {"helper_issue_exit_ns", value.helper_issue_exit_ns},
            {"native_instruction_after_ns", value.native_instruction_after_ns},
            {"work_begin_ns", value.work_begin_ns},
            {"work_end_ns", value.work_end_ns},
            {"wait_enter_ns", value.wait_enter_ns},
            {"wait_exit_ns", value.wait_exit_ns},
            {"consumer_after_ns", value.consumer_after_ns},
            {"ready_ns", value.ready_ns},
            {"reservation_id", value.reservation_id}, {"lane", value.lane},
            {"status", value.status}, {"valid_bits", value.valid_bits},
            {"work_count", value.work_count}, {"output_bits", value.output_bits}};
}

std::uint32_t input_word(std::uint32_t lane)
{
    return lane * 0x45d9f3bU ^ 0xa5a55a5aU;
}

std::uint32_t cpu_work(std::uint32_t seed, std::uint32_t lane,
                       std::uint32_t count)
{
    std::uint32_t value = seed ^ (lane * 0x9e3779b9U + 0x85ebca6bU);
    const std::uint32_t addend = 0x3c6ef35fU + lane;
    for (std::uint32_t index = 0; index < count; ++index)
        value = value * 0x0019660dU + addend;
    return value;
}

class Journal {
  public:
    explicit Journal(std::string path) : path_(std::move(path))
    {
        value_ = {{"schema_version", 1}, {"evidence", "PARTIAL_GPU_DIAGNOSTIC"},
                  {"validation_status", "UNVALIDATED"},
                  {"stage", "options_validated"}, {"launches", json::array()},
                  {"cleanup", json::object()}};
        save();
    }
    void stage(const std::string& stage) { value_["stage"] = stage; save(); }
    std::size_t begin(const json& launch)
    {
        value_["launches"].push_back(launch); save();
        return value_["launches"].size() - 1;
    }
    void launch(std::size_t index, const json& launch)
    {
        value_["launches"].at(index) = launch; save();
    }
    void field(const std::string& name, const json& value)
    {
        value_[name] = value; save();
    }
    void cleanup(const json& value) noexcept
    {
        try { value_["cleanup"] = value; save(); } catch (...) {}
    }
    void failure(const std::string& stage, const std::string& message) noexcept
    {
        try { value_["stage"] = stage; value_["failure"] =
            {{"stage", stage}, {"message", message}}; save(); } catch (...) {}
    }
    void captured() { value_["stage"] = "capture_complete_unvalidated";
        value_["capture_complete"] = true; save(); }
  private:
    void save()
    {
        const auto temporary = path_ + ".tmp";
        write_file(temporary, value_.dump(2));
        require(std::rename(temporary.c_str(), path_.c_str()) == 0,
                "cannot replace partial journal");
    }
    std::string path_;
    json value_;
};

struct EventPair {
    cudaEvent_t begin{};
    cudaEvent_t end{};
    EventPair()
    {
        runtime(cudaEventCreate(&begin), "create begin event");
        try { runtime(cudaEventCreate(&end), "create end event"); }
        catch (...) { (void)cudaEventDestroy(begin); throw; }
    }
    ~EventPair()
    {
        if (end) (void)cudaEventDestroy(end);
        if (begin) (void)cudaEventDestroy(begin);
    }
};

void reset_launch(CUdeviceptr diagnostic_config, CUdeviceptr counters_address,
                  CUdeviceptr trace_address, std::size_t trace_bytes,
                  c6_delay::Record* records, const c6_delay::Config& config)
{
    future::Counters counters{};
    counters.next_reservation = 1;
    driver(cuMemcpyHtoD(counters_address, &counters, sizeof(counters)),
           "reset future counters");
    driver(cuMemsetD8(trace_address, 0, trace_bytes), "reset future traces");
    runtime(cudaMemset(records, 0, kRecordBytes), "reset delay records");
    driver(cuMemcpyHtoD(diagnostic_config, &config, sizeof(config)),
           "publish future delay config");
}

std::uint32_t discover_instruction(
    CUfunction future_kernel, std::uint32_t* input, std::uint32_t* output,
    c6_delay::Record* records, CUdeviceptr diagnostic_config,
    CUdeviceptr counters_address, CUdeviceptr trace_address,
    std::size_t trace_bytes, Journal& journal, std::string& stage)
{
    c6_delay::Config disabled{};
    reset_launch(diagnostic_config, counters_address, trace_address, trace_bytes,
                 records, disabled);
    const std::uint32_t work = 0;
    const std::uint64_t epoch = 1;
    std::array<c6_delay::Record, 32> discovery_records{};
    for (std::uint32_t lane = 0; lane < 32; ++lane) {
        discovery_records[lane].launch_epoch = epoch;
        discovery_records[lane].lane = lane;
        discovery_records[lane].work_count = work;
    }
    runtime(cudaMemcpy(records, discovery_records.data(),
                       sizeof(discovery_records), cudaMemcpyHostToDevice),
            "initialize instruction discovery records");
    std::uint32_t seed = kSeed;
    std::uint32_t launch_work = work;
    std::uint64_t launch_epoch = epoch;
    void* arguments[]{&input, &output, &records, &seed, &launch_work,
                      &launch_epoch};
    stage = "discover_instruction:launch"; journal.stage(stage);
    driver(cuLaunchKernel(future_kernel, 1, 1, 1, 32, 1, 1, 0, nullptr,
                          arguments, nullptr), "launch instruction discovery");
    runtime(cudaDeviceSynchronize(), "synchronize instruction discovery");
    future::Counters counters{};
    driver(cuMemcpyDtoH(&counters, counters_address, sizeof(counters)),
           "read instruction discovery counters");
    require(counters.pending == 0 && counters.rejected == 0 &&
                counters.trace_overflow == 0 && counters.trace_count == 64,
            "instruction discovery conservation failed");
    std::array<future::Trace, 64> traces{};
    driver(cuMemcpyDtoH(traces.data(), trace_address, sizeof(traces)),
           "read instruction discovery traces");
    std::set<std::uint32_t> instructions;
    json raw = json::array();
    for (const auto& trace : traces) {
        raw.push_back(trace_json(trace));
        instructions.insert(trace.instruction_id);
    }
    journal.field("instruction_discovery",
        {{"config", "ALL_ZERO_DIAGNOSTIC_DISABLED"},
         {"counters", counters_json(counters)}, {"traces", raw}});
    require(instructions.size() == 1 && *instructions.begin() != UINT32_MAX,
            "instruction discovery did not identify one producer");
    return *instructions.begin();
}

json acquire_launch(
    const Cell& cell, bool warmup, std::uint32_t sample,
    std::uint64_t epoch, std::uint32_t instruction, CUfunction kernel,
    std::uint32_t* input, std::uint32_t* output, c6_delay::Record* records,
    CUdeviceptr diagnostic_config, CUdeviceptr counters_address,
    CUdeviceptr trace_address, std::size_t trace_bytes, Journal& journal,
    std::string& stage)
{
    c6_delay::Config config{};
    if (cell.future_arm) {
        config = {c6_delay::kMagic, c6_delay::kAbiVersion, sizeof(config), 1,
                  instruction, cell.delay_ns, epoch,
                  reinterpret_cast<std::uint64_t>(input), kInputBytes,
                  reinterpret_cast<std::uint64_t>(records), kRecordBytes,
                  32, sizeof(c6_delay::Record), 1, 32, cell.work_count, 0};
    }
    reset_launch(diagnostic_config, counters_address, trace_address, trace_bytes,
                 records, config);
    std::array<std::uint32_t, 32> expected{};
    std::array<std::uint32_t, 32> sentinel{};
    for (std::uint32_t lane = 0; lane < 32; ++lane) {
        expected[lane] = input_word(lane) ^ cpu_work(kSeed, lane, cell.work_count) ^
                         0xd1b54a35U;
        sentinel[lane] = expected[lane] ^ 0xffffffffU;
    }
    runtime(cudaMemcpy(output, sentinel.data(), sizeof(sentinel),
                       cudaMemcpyHostToDevice), "write output sentinels");
    std::array<std::uint32_t, 32> sentinel_echo{};
    runtime(cudaMemcpy(sentinel_echo.data(), output, sizeof(sentinel_echo),
                       cudaMemcpyDeviceToHost), "confirm output sentinels");
    require(sentinel_echo == sentinel, "sentinel initialization not confirmed");

    json raw{{"arm", cell.arm}, {"delay_ns", cell.delay_ns},
             {"work_count", cell.work_count}, {"warmup", warmup},
             {"sample", sample}, {"launch_epoch", epoch},
             {"sentinel_outputs", sentinel}, {"expected_outputs", expected},
             {"validation", "NOT_RUN"}};
    const auto index = journal.begin(raw);
    std::uint32_t seed = kSeed;
    std::uint32_t launch_work = cell.work_count;
    std::uint64_t launch_epoch = epoch;
    void* arguments[]{&input, &output, &records, &seed, &launch_work,
                      &launch_epoch};
    EventPair events;
    stage = std::string("launch:") + cell.arm; journal.stage(stage);
    runtime(cudaEventRecord(events.begin), "record launch begin");
    driver(cuLaunchKernel(kernel, 1, 1, 1, 32, 1, 1, 0, nullptr,
                          arguments, nullptr), "launch future-delay cell");
    runtime(cudaEventRecord(events.end), "record launch end");
    runtime(cudaEventSynchronize(events.end), "synchronize future-delay cell");
    float elapsed_ms = 0;
    runtime(cudaEventElapsedTime(&elapsed_ms, events.begin, events.end),
            "measure launch elapsed time");

    std::array<std::uint32_t, 32> observed{};
    std::array<c6_delay::Record, 32> observed_records{};
    future::Counters counters{};
    runtime(cudaMemcpy(observed.data(), output, sizeof(observed),
                       cudaMemcpyDeviceToHost), "copy outputs");
    runtime(cudaMemcpy(observed_records.data(), records, sizeof(observed_records),
                       cudaMemcpyDeviceToHost), "copy delay records");
    driver(cuMemcpyDtoH(&counters, counters_address, sizeof(counters)),
           "copy future counters");
    const auto safe_trace_count = std::min<std::uint64_t>(
        counters.trace_count, trace_bytes / sizeof(future::Trace));
    std::vector<future::Trace> traces(safe_trace_count);
    if (!traces.empty())
        driver(cuMemcpyDtoH(traces.data(), trace_address,
                            traces.size() * sizeof(future::Trace)),
               "copy bounded future traces");
    raw["cuda_event_elapsed_ms"] = elapsed_ms;
    raw["observed_outputs"] = observed;
    raw["records"] = json::array();
    for (const auto& record : observed_records)
        raw["records"].push_back(record_json(record));
    raw["counters"] = counters_json(counters);
    raw["traces"] = json::array();
    for (const auto& trace : traces) raw["traces"].push_back(trace_json(trace));
    raw["trace_copy_bounded"] = safe_trace_count == counters.trace_count;
    journal.launch(index, raw);

    stage = std::string("validate:") + cell.arm; journal.stage(stage);
    require(observed == expected, "per-lane output mismatch");
    require(safe_trace_count == counters.trace_count,
            "future trace count exceeds bounded storage");
    for (std::uint32_t lane = 0; lane < 32; ++lane) {
        const auto& record = observed_records[lane];
        require(record.launch_epoch == epoch && record.lane == lane &&
                    record.work_count == cell.work_count &&
                    record.output_bits == expected[lane],
                "record identity/output mismatch");
        if (cell.future_arm) {
            require(record.configured_delay_ns == cell.delay_ns &&
                        record.valid_bits == c6_delay::kFutureBits &&
                        record.helper_entry_ns != 0 &&
                        record.arrival_ns != 0 &&
                        record.helper_issue_exit_ns != 0 &&
                        record.native_instruction_after_ns != 0 &&
                        record.work_begin_ns != 0 && record.work_end_ns != 0 &&
                        record.wait_enter_ns != 0 && record.wait_exit_ns != 0 &&
                        record.consumer_after_ns != 0 && record.ready_ns != 0 &&
                        record.helper_entry_ns <= record.arrival_ns &&
                        record.arrival_ns <= record.helper_issue_exit_ns &&
                        record.helper_issue_exit_ns <=
                            record.native_instruction_after_ns &&
                        record.native_instruction_after_ns <= record.work_begin_ns &&
                        record.work_begin_ns <= record.work_end_ns &&
                        record.work_end_ns <= record.wait_enter_ns &&
                        record.wait_enter_ns <= record.wait_exit_ns &&
                        record.wait_exit_ns <= record.consumer_after_ns &&
                        record.ready_ns >= record.arrival_ns &&
                        record.ready_ns - record.arrival_ns == cell.delay_ns &&
                        record.reservation_id == 1 &&
                        record.status == future::kReady,
                    "future diagnostic record invalid");
        } else {
            require(record.configured_delay_ns == 0 &&
                        record.valid_bits == c6_delay::kKernelBits &&
                        record.native_instruction_after_ns != 0 &&
                        record.work_begin_ns != 0 && record.work_end_ns != 0 &&
                        record.consumer_after_ns != 0 &&
                        record.native_instruction_after_ns <= record.work_begin_ns &&
                        record.work_begin_ns <= record.work_end_ns &&
                        record.work_end_ns <= record.consumer_after_ns,
                    "native diagnostic record invalid");
        }
    }
    if (cell.future_arm) {
        require(counters.next_reservation == 2 && counters.issued == 32 &&
                    counters.pending == 0 && counters.model_ready == 32 &&
                    counters.consumed == 32 && counters.drained == 0 &&
                    counters.terminal_error == 0 && counters.native_loads == 0 &&
                    counters.native_bytes == 0 && counters.rejected == 0 &&
                    counters.groups_issued == 1 && counters.groups_completed == 1 &&
                    counters.trace_count == 64 && counters.trace_overflow == 0,
                "future counter conservation failed");
    } else {
        require(counters.next_reservation == 1 && counters.issued == 0 &&
                    counters.pending == 0 && counters.model_ready == 0 &&
                    counters.consumed == 0 && counters.drained == 0 &&
                    counters.terminal_error == 0 && counters.rejected == 0 &&
                    counters.groups_issued == 0 && counters.groups_completed == 0 &&
                    counters.trace_count == 0 && counters.trace_overflow == 0,
                "native arm unexpectedly entered future helper");
    }
    raw["validation"] = "PASS";
    journal.launch(index, raw);
    return raw;
}

struct Resources {
    CUmodule module{};
    CUmodule native_module{};
    hbfsim_context* context{};
    std::uint32_t* input_backing{};
    std::uint32_t* input{};
    std::uint32_t* native_input{};
    std::uint32_t* output{};
    c6_delay::Record* records{};
    void* plugin{};
    bool registered{};
    CUdeviceptr diagnostic_config{};
};

json cleanup(Resources& value, Journal* journal) noexcept
{
    json report{{"diagnostic_disable", {{"called", false}, {"confirmed", false}}},
                {"module_unload", {{"called", false}, {"confirmed", false}}},
                {"native_module_unload", {{"called", false}, {"confirmed", false}}},
                {"range_unregister", {{"called", false}, {"confirmed", false}}},
                {"context_destroy", {{"called", false},
                                      {"completion", "NOT_CALLED"}}},
                {"records_free", {{"called", false}, {"confirmed", false}}},
                {"output_free", {{"called", false}, {"confirmed", false}}},
                {"input_free", {{"called", false}, {"confirmed", false}}},
                {"native_input_free", {{"called", false}, {"confirmed", false}}},
                {"plugin_close", {{"called", false}, {"confirmed", false}}}};
    const auto publish = [&] { if (journal) journal->cleanup(report); };
    if (value.module && value.diagnostic_config) {
        report["diagnostic_disable"]["called"] = true;
        const c6_delay::Config disabled{};
        const auto copy = cuMemcpyHtoD(value.diagnostic_config, &disabled,
                                       sizeof(disabled));
        const auto sync = copy == CUDA_SUCCESS ? cudaDeviceSynchronize()
                                                : cudaErrorUnknown;
        report["diagnostic_disable"]["copy_result"] = copy;
        report["diagnostic_disable"]["sync_result"] = sync;
        report["diagnostic_disable"]["confirmed"] =
            copy == CUDA_SUCCESS && sync == cudaSuccess;
        publish();
        if (copy != CUDA_SUCCESS || sync != cudaSuccess) {
            report["stopped_after"] = "diagnostic_disable";
            report["later_resources_retained_until_process_exit"] = true;
            publish(); return report;
        }
    }
    if (value.module) {
        report["module_unload"]["called"] = true;
        const auto code = cuModuleUnload(value.module);
        report["module_unload"]["result"] = code;
        report["module_unload"]["confirmed"] = code == CUDA_SUCCESS;
        publish();
        if (code != CUDA_SUCCESS) {
            report["stopped_after"] = "module_unload";
            report["later_resources_retained_until_process_exit"] = true;
            publish(); return report;
        }
        value.module = nullptr;
    }
    if (value.native_module) {
        report["native_module_unload"]["called"] = true;
        const auto sync = cudaDeviceSynchronize();
        const auto code = sync == cudaSuccess ? cuModuleUnload(value.native_module)
                                              : CUDA_ERROR_NOT_READY;
        report["native_module_unload"]["sync_result"] = sync;
        report["native_module_unload"]["result"] = code;
        report["native_module_unload"]["confirmed"] =
            sync == cudaSuccess && code == CUDA_SUCCESS;
        publish();
        if (sync != cudaSuccess || code != CUDA_SUCCESS) {
            report["stopped_after"] = "native_module_unload";
            report["later_resources_retained_until_process_exit"] = true;
            publish(); return report;
        }
        value.native_module = nullptr;
    }
    if (value.registered) {
        report["range_unregister"]["called"] = true;
        if (!value.context) {
            report["range_unregister"]["error"] = "missing owned context";
            report["stopped_after"] = "range_unregister";
            report["later_resources_retained_until_process_exit"] = true;
            publish(); return report;
        }
        const auto code = hbfsim_unregister(value.context, value.input);
        report["range_unregister"]["result"] = code;
        report["range_unregister"]["confirmed"] = code == HBFSIM_OK;
        publish();
        if (code != HBFSIM_OK) {
            report["stopped_after"] = "range_unregister";
            report["later_resources_retained_until_process_exit"] = true;
            publish(); return report;
        }
        value.registered = false;
    }
    if (value.context) {
        report["context_destroy"]["called"] = true;
        report["context_destroy"]["completion"] = "UNOBSERVABLE_VOID_API";
        publish(); hbfsim_context_destroy(value.context); value.context = nullptr;
    }
    const auto free_one = [&](auto*& pointer, const char* name) {
        if (!pointer) return true;
        report[name]["called"] = true;
        const auto code = cudaFree(pointer);
        report[name]["result"] = code;
        report[name]["confirmed"] = code == cudaSuccess;
        if (code == cudaSuccess) pointer = nullptr;
        publish(); return code == cudaSuccess;
    };
    bool success = free_one(value.records, "records_free");
    success = free_one(value.output, "output_free") && success;
    success = free_one(value.input_backing, "input_free") && success;
    success = free_one(value.native_input, "native_input_free") && success;
    value.input = value.input_backing ? value.input : nullptr;
    if (value.plugin) {
        report["plugin_close"]["called"] = true;
        const auto code = dlclose(value.plugin);
        report["plugin_close"]["result"] = code;
        report["plugin_close"]["confirmed"] = code == 0;
        if (code == 0) value.plugin = nullptr;
        success = code == 0 && success; publish();
    }
    report["safe_retirement_confirmed"] = true;
    report["all_observable_steps_succeeded"] = success;
    publish(); return report;
}
} // namespace

int main(int argc, char** argv)
{
    Resources resources;
    std::unique_ptr<Journal> journal;
    std::string stage = "parse_options";
    bool cleanup_attempted = false;
    try {
        const auto options = parse_options(argc, argv);
        const auto binding = parse_binding(options.binding);
        const auto native_binding = parse_native_binding(options.native_binding, binding);
        journal = std::make_unique<Journal>(options.output + ".partial.json");
        auto original_ptx = read_file(options.ptx);
        require(original_ptx.size() == binding.original_bytes &&
                    sha256_hex(original_ptx) == binding.original_sha,
                "original PTX differs from reviewed binding");
        auto cubin = read_file(options.cubin);
        require(cubin.size() == binding.cubin_bytes &&
                    sha256_hex(cubin) == binding.cubin_sha,
                "cubin differs from reviewed binding");
        const auto native_cubin = read_file(native_binding.cubin_path);
        require(native_cubin.size() == native_binding.cubin_bytes &&
                    sha256_hex(native_cubin) == native_binding.cubin_sha &&
                    native_cubin.size() >= 4 &&
                    native_cubin.compare(0, 4, "\x7f" "ELF", 4) == 0,
                "ordinary native cubin differs from retained binding");
        json native_control{{"original_ptx_sha256", native_binding.original_sha},
            {"cubin_sha256", sha256_hex(native_cubin)},
            {"cubin_bytes", native_cubin.size()},
            {"build_manifest_sha256", native_binding.build_manifest_sha},
            {"disassembly_manifest_sha256", native_binding.disassembly_manifest_sha},
            {"nvdisasm_sha256", native_binding.nvdisasm_sha},
            {"cuobjdump_sha256", native_binding.cuobjdump_sha},
            {"selected_kernel", kNativeKernel}, {"mapping_validation", "NOT_PROVEN"},
            {"same_retained_buffer", true}, {"load_result", "NOT_CALLED"},
            {"future_requirements_absent", false}, {"distinct_modules", false}};
        journal->field("native_control_binding", native_control);
        resources.plugin = dlopen(options.plugin.c_str(), RTLD_NOW | RTLD_GLOBAL);
        require(resources.plugin != nullptr, "cannot load actual PTX plugin");
        auto process = reinterpret_cast<int (*)(const char*, int, char*)>(
            dlsym(resources.plugin, "process_input"));
        auto begin_load = reinterpret_cast<std::uint64_t (*)(const char*, std::size_t)>(
            dlsym(RTLD_DEFAULT, "hbfsim_begin_module_load_from_ptx"));
        auto end_load = reinterpret_cast<void (*)(std::uint64_t)>(
            dlsym(RTLD_DEFAULT, "hbfsim_end_module_load"));
        require(process && begin_load && end_load, "plugin/gate API unavailable");
        const auto request = json({{"input", {{"full_ptx", original_ptx},
            {"to_patch_kernel", kFutureKernel},
            {"transform_mode", "timing_load_future_v1"}}}}).dump();
        std::vector<char> response(32 * 1024 * 1024);
        require(process(request.c_str(), static_cast<int>(response.size()),
                        response.data()) == 0,
                "actual future transform failed");
        const auto transformed = json::parse(response.data());
        require(transformed.at("modified").get<bool>(), "transform did not modify kernel");
        const auto transformed_ptx = transformed.at("output_ptx").get<std::string>();
        require(transformed_ptx.size() == binding.transformed_bytes &&
                    sha256_hex(transformed_ptx) == binding.transformed_sha,
                "transformed PTX differs from reviewed cubin source");
        const auto reviewed_transformed_ptx = read_file(options.transformed_ptx);
        require(reviewed_transformed_ptx == transformed_ptx,
                "runtime transform differs from retained reviewed PTX bytes");
        write_file(options.output + ".transformed.ptx", transformed_ptx);
        const char* pass_path = std::getenv("HBFSIM_PASS_MANIFEST_PATH");
        require(pass_path && pass_path[0], "pass manifest path required");
        const auto pass = json::parse(read_file(pass_path));
        require(pass.at("kernel") == kFutureKernel &&
                    pass.at("rewritten_instructions") == 1 &&
                    pass.at("unsupported_instructions") == 0 &&
                    pass.at("future_kernel").at("static_producers") == 1 &&
                    pass.at("future_kernel").at("required_threads") ==
                        json::array({0, 0, 0}) &&
                    pass.at("future_kernel").at("maximum_threads") ==
                        json::array({32, 1, 1}),
                "plugin manifest does not bind one one-warp producer");

        runtime(cudaFree(nullptr), "initialize CUDA runtime");
        runtime(cudaMalloc(reinterpret_cast<void**>(&resources.input_backing),
                           kRegisteredBytes + 4095), "allocate input backing");
        const auto raw = reinterpret_cast<std::uintptr_t>(resources.input_backing);
        const auto aligned = (raw + 4095) & ~std::uintptr_t{4095};
        resources.input = reinterpret_cast<std::uint32_t*>(aligned);
        require(aligned + kRegisteredBytes <=
                    raw + kRegisteredBytes + 4095,
                "cannot form aligned registered input");
        runtime(cudaMalloc(reinterpret_cast<void**>(&resources.output),
                           kInputBytes), "allocate output");
        runtime(cudaMalloc(reinterpret_cast<void**>(&resources.records),
                           kRecordBytes), "allocate records");
        runtime(cudaMalloc(reinterpret_cast<void**>(&resources.native_input),
                           kInputBytes), "allocate ordinary native input");
        const auto input_begin = aligned;
        const auto input_end = input_begin + kRegisteredBytes;
        const auto output_begin = reinterpret_cast<std::uintptr_t>(resources.output);
        const auto output_end = output_begin + kInputBytes;
        const auto records_begin = reinterpret_cast<std::uintptr_t>(resources.records);
        const auto records_end = records_begin + kRecordBytes;
        const auto native_begin = reinterpret_cast<std::uintptr_t>(resources.native_input);
        const auto native_end = native_begin + kInputBytes;
        require((input_end <= output_begin || output_end <= input_begin) &&
                    (input_end <= records_begin || records_end <= input_begin) &&
                    (output_end <= records_begin || records_end <= output_begin),
                "input/output/record spans overlap");
        require((native_end <= input_begin || input_end <= native_begin) &&
                    (native_end <= output_begin || output_end <= native_begin) &&
                    (native_end <= records_begin || records_end <= native_begin),
                "ordinary native input overlaps another span");
        std::array<std::uint32_t, 32> input{};
        for (std::uint32_t lane = 0; lane < 32; ++lane) input[lane] = input_word(lane);
        runtime(cudaMemset(resources.input, 0, kRegisteredBytes), "clear input range");
        runtime(cudaMemcpy(resources.input, input.data(), sizeof(input),
                           cudaMemcpyHostToDevice), "initialize input");
        runtime(cudaMemcpy(resources.native_input, input.data(), sizeof(input),
                           cudaMemcpyHostToDevice), "initialize ordinary native input");
        std::array<std::uint32_t, 32> native_echo{}, future_echo{};
        runtime(cudaMemcpy(native_echo.data(), resources.native_input, sizeof(native_echo),
                           cudaMemcpyDeviceToHost), "read back ordinary native input");
        runtime(cudaMemcpy(future_echo.data(), resources.input, sizeof(future_echo),
                           cudaMemcpyDeviceToHost), "read back future input");
        const json native_input{{"address", native_begin}, {"bytes", kInputBytes},
            {"registered", false}, {"words", native_echo}, {"future_words", future_echo},
            {"future_address", input_begin}, {"future_registered_bytes", kRegisteredBytes},
            {"contents_equal", native_echo == input && future_echo == input},
            {"disjoint", true}};
        journal->field("native_input", native_input);
        require(native_echo == input && future_echo == input, "native/future input readback mismatch");

        hbfsim_options context_options{.profile_path = options.profile.c_str(),
            .report_dir = options.report_dir.c_str(), .mode = HBFSIM_MODEL_FAST,
            .ring_capacity = 256, .request_timeout_ns = 1'000'000'000};
        require(hbfsim_context_create(&context_options, &resources.context) == HBFSIM_OK,
                "create FAST context");
        hbfsim_range_options range{.mode = HBFSIM_RANGE_MODE_TIMING,
            .permissions = HBFSIM_RANGE_READ,
            .cache_policy = HBFSIM_CACHE_POLICY_NONE, .stream_id = 0};
        require(hbfsim_register_device(resources.context, resources.input,
                                       kRegisteredBytes, &range) == HBFSIM_OK,
                "register input range");
        resources.registered = true;

        const auto token = begin_load(transformed_ptx.data(), transformed_ptx.size());
        require(token != 0, "launch gate rejected transformed module");
        const auto load_result = cuModuleLoadDataEx(&resources.module, cubin.data(),
                                                     0, nullptr, nullptr);
        end_load(token);
        driver(load_result, "load associated reviewed cubin");
        CUfunction future_kernel = nullptr;
        CUfunction native_kernel = nullptr;
        driver(cuModuleGetFunction(&future_kernel, resources.module, kFutureKernel),
               "lookup future kernel");
        stage = "load_ordinary_native_module"; journal->stage(stage);
        // No future transaction is fabricated for this ordinary untransformed
        // image. The existing interposer still checks the module classification.
        const auto native_load = cuModuleLoadDataEx(&resources.native_module,
            native_cubin.data(), 0, nullptr, nullptr);
        native_control["load_result"] = native_load;
        native_control["distinct_modules"] = resources.native_module &&
            resources.native_module != resources.module;
        journal->field("native_control_binding", native_control);
        driver(native_load, "load retained ordinary native image");
        require(resources.native_module != resources.module, "native/future module alias");
        CUdeviceptr unexpected_future{}; std::size_t unexpected_bytes{};
        const auto lookup = cuModuleGetGlobal(&unexpected_future, &unexpected_bytes,
            resources.native_module, "__hbfsim_timing_future_requirements_v1");
        native_control["future_requirements_lookup"] = lookup;
        native_control["future_requirements_absent"] = lookup == CUDA_ERROR_NOT_FOUND;
        journal->field("native_control_binding", native_control);
        require(lookup == CUDA_ERROR_NOT_FOUND, "ordinary native module has future metadata or unreadable classification");
        driver(cuModuleGetFunction(&native_kernel, resources.native_module, kNativeKernel),
               "lookup ordinary native kernel");
        resources.diagnostic_config = module_object<c6_delay::Config>(
            resources.module, "__hbfsim_eval_future_delay_config_v1");
        const auto counters_address = module_object<future::Counters>(
            resources.module, "__hbfsim_timing_future_counters_v1");
        const auto [trace_address, trace_bytes] = module_span(
            resources.module, "__hbfsim_timing_future_trace_v1");
        require(trace_bytes >= 64 * sizeof(future::Trace),
                "future trace storage too small");

        const auto instruction = discover_instruction(
            future_kernel, resources.input, resources.output, resources.records,
            resources.diagnostic_config, counters_address, trace_address,
            trace_bytes, *journal, stage);
        json launches = json::array();
        std::uint64_t epoch = 100;
        for (const auto& cell : kCells) {
            for (std::uint32_t ordinal = 0; ordinal < kWarmups + kSamples;
                 ++ordinal) {
                const bool warmup = ordinal < kWarmups;
                launches.push_back(acquire_launch(
                    cell, warmup, warmup ? 0 : ordinal - kWarmups, ++epoch,
                    instruction, cell.future_arm ? future_kernel : native_kernel,
                    cell.future_arm ? resources.input : resources.native_input,
                    resources.output, resources.records,
                    resources.diagnostic_config, counters_address, trace_address,
                    trace_bytes, *journal, stage));
            }
        }
        cleanup_attempted = true;
        const auto cleanup_report = cleanup(resources, journal.get());
        require(cleanup_report.value("safe_retirement_confirmed", false) &&
                    cleanup_report.value("all_observable_steps_succeeded", false),
                "cleanup did not confirm observable retirement/frees");
        const json result{{"schema_version", 1},
            {"evidence", "GPU_ACQUISITION"},
            {"validation_status", "UNVALIDATED"},
            {"scientific_claim", false}, {"c6_3_closed", false},
            {"c6_4_closed", false}, {"g5_closed", false},
            {"overlap_closed", false}, {"native_completion_timing", false},
            {"instruction_id", instruction},
            {"launch_contract", {{"grid", {1, 1, 1}}, {"block", {32, 1, 1}},
                                  {"warmups_per_cell", kWarmups},
                                  {"samples_per_cell", kSamples}}},
            {"native_image_binding", {{"same_retained_buffer", true},
                {"original_ptx_sha256", binding.original_sha},
                {"transformed_ptx_sha256", binding.transformed_sha},
                {"cubin_sha256", binding.cubin_sha},
                {"build_manifest_sha256", binding.build_manifest_sha},
                {"disassembly_manifest_sha256", binding.disassembly_manifest_sha},
                {"nvdisasm_sha256", binding.nvdisasm_sha},
                {"cuobjdump_sha256", binding.cuobjdump_sha},
                {"mapping_validation", "NOT_PROVEN"}}},
            {"native_control_binding", native_control}, {"native_input", native_input},
            {"launches", launches}, {"cleanup", cleanup_report}};
        journal->captured();
        write_file(options.output, result.dump(2));
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "c6_future_delay_overlap: %s\n", error.what());
        if (journal) journal->failure(stage, error.what());
        if (!cleanup_attempted) (void)cleanup(resources, journal.get());
        return 1;
    }
}
#endif

#include "../../../src/cuda_runtime/capacity_runtime.hpp"
#include "../../../src/host_service/backing_store.hpp"
#include "../../../src/host_service/control_layout.hpp"

#include <hbfsim/profile.hpp>

#include <cuda.h>
#include <cuda_runtime_api.h>

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <limits>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

namespace {

constexpr std::uint64_t kLogicalBytes = 110ULL << 30;
constexpr std::uint64_t kCacheBytes = 2ULL << 30;
constexpr std::uint64_t kCommonAlignment = 64ULL << 10;
constexpr std::uint32_t kSampleIntervals = 64;
constexpr std::uint32_t kRangeId = 1;
constexpr std::uint64_t kSeed = 0x524542555454414cULL;

struct Options {
    std::uint32_t page_bytes{0};
    std::filesystem::path output;
    std::filesystem::path backing_dir;
};

struct SampleResult {
    std::uint32_t index{0};
    std::string position;
    std::uint64_t logical_offset{0};
    std::uint64_t logical_page{0};
    std::uint64_t observed_frame_alias{0};
    std::uint64_t frame_address{0};
    std::uint64_t expected_hash{0};
    std::uint64_t actual_hash{0};
    bool passed{false};
};

[[noreturn]] void fail(const std::string& message)
{
    throw std::runtime_error(message);
}

void require(bool condition, const std::string& message)
{
    if (!condition) {
        fail(message);
    }
}

std::uint64_t parse_u64(std::string_view text, const char* name)
{
    const std::string value(text);
    char* end = nullptr;
    errno = 0;
    const auto parsed = std::strtoull(value.c_str(), &end, 10);
    if (errno != 0 || end == nullptr || *end != '\0') {
        fail(std::string("invalid ") + name);
    }
    return parsed;
}

Options parse_options(int argc, char** argv)
{
    Options result;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (index + 1 >= argc) {
            fail("missing value for " + argument);
        }
        const std::string value = argv[++index];
        if (argument == "--page-bytes") {
            const auto parsed = parse_u64(value, "page bytes");
            if (parsed > std::numeric_limits<std::uint32_t>::max()) {
                fail("page bytes overflow");
            }
            result.page_bytes = static_cast<std::uint32_t>(parsed);
        } else if (argument == "--output") {
            result.output = value;
        } else if (argument == "--backing-dir") {
            result.backing_dir = value;
        } else {
            fail("unknown option " + argument);
        }
    }
    const std::vector<std::uint32_t> allowed{4096, 8192, 16384, 32768, 65536};
    require(std::find(allowed.begin(), allowed.end(), result.page_bytes) !=
                allowed.end(),
            "page bytes must be one of 4096,8192,16384,32768,65536");
    require(!result.output.empty(), "--output is required");
    require(!result.backing_dir.empty(), "--backing-dir is required");
    return result;
}

std::uint64_t splitmix(std::uint64_t value)
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

std::vector<std::byte> payload(std::uint64_t offset, std::uint32_t page_bytes)
{
    std::vector<std::byte> result(page_bytes);
    std::uint64_t state = splitmix(kSeed ^ offset ^ page_bytes);
    for (std::size_t index = 0; index < result.size(); ++index) {
        if ((index & 7U) == 0) {
            state = splitmix(state + index);
        }
        result[index] = static_cast<std::byte>(
            (state >> ((index & 7U) * 8U)) & 0xffU);
    }
    return result;
}

std::uint64_t fnv1a(const std::vector<std::byte>& bytes)
{
    std::uint64_t value = 1469598103934665603ULL;
    for (const auto byte : bytes) {
        value ^= static_cast<std::uint8_t>(byte);
        value *= 1099511628211ULL;
    }
    return value;
}

std::string json_escape(std::string_view text)
{
    std::string result;
    for (const char value : text) {
        if (value == '\\' || value == '"') result.push_back('\\');
        if (value == '\n') {
            result += "\\n";
        } else if (value == '\r') {
            result += "\\r";
        } else if (value == '\t') {
            result += "\\t";
        } else {
            result.push_back(value);
        }
    }
    return result;
}

std::vector<std::uint64_t> sample_offsets(std::uint32_t page_bytes)
{
    const auto last = kLogicalBytes - kCommonAlignment;
    std::vector<std::uint64_t> offsets;
    offsets.reserve(kSampleIntervals + 1);
    for (std::uint64_t index = 0; index <= kSampleIntervals; ++index) {
        const auto raw = (last / kSampleIntervals) * index +
                         ((last % kSampleIntervals) * index) /
                             kSampleIntervals;
        const auto aligned = (raw / kCommonAlignment) * kCommonAlignment;
        if (offsets.empty() || offsets.back() != aligned) {
            offsets.push_back(aligned);
        }
    }
    const auto middle = (kLogicalBytes / 2 / kCommonAlignment) *
                        kCommonAlignment;
    offsets.push_back(middle);
    std::ranges::sort(offsets);
    offsets.erase(std::unique(offsets.begin(), offsets.end()), offsets.end());
    require(offsets.size() >= 64, "fewer than 64 distinct sample offsets");
    require(offsets.front() == 0 && offsets.back() == last,
            "sample endpoints are incomplete");
    require(std::find(offsets.begin(), offsets.end(),
                      middle) != offsets.end(),
            "middle sample is missing");
    // Retain the common 64 KiB-aligned comparison set, but also cover the
    // actual last page for each geometry. For pages below 64 KiB this is a
    // distinct offset and must not be mislabeled as common-aligned.
    offsets.push_back(kLogicalBytes - page_bytes);
    std::ranges::sort(offsets);
    offsets.erase(std::unique(offsets.begin(), offsets.end()), offsets.end());
    return offsets;
}

std::string position_name(std::uint64_t offset, std::uint32_t page_bytes)
{
    if (offset == 0) return "first";
    if (offset == (kLogicalBytes / 2 / kCommonAlignment) * kCommonAlignment)
        return "middle";
    if (offset == kLogicalBytes - page_bytes && page_bytes == kCommonAlignment)
        return "last_common_and_page";
    if (offset == kLogicalBytes - page_bytes) return "last_page";
    if (offset == kLogicalBytes - kCommonAlignment) return "last_common";
    return "uniform";
}

void exact_pwrite(int fd, const std::vector<std::byte>& data,
                  std::uint64_t offset)
{
    std::size_t written = 0;
    while (written != data.size()) {
        const auto result = ::pwrite(fd, data.data() + written,
                                     data.size() - written,
                                     static_cast<off_t>(offset + written));
        if (result > 0) {
            written += static_cast<std::size_t>(result);
        } else if (result < 0 && errno == EINTR) {
            continue;
        } else {
            fail("sparse backing pwrite failed");
        }
    }
}

std::filesystem::path prepare_sparse_backing(
    const std::filesystem::path& directory,
    const std::vector<std::uint64_t>& offsets, std::uint32_t page_bytes)
{
    std::filesystem::create_directories(directory);
    auto pattern = (directory / "r3-capacity-XXXXXX").string();
    std::vector<char> path(pattern.begin(), pattern.end());
    path.push_back('\0');
    const int fd = ::mkstemp(path.data());
    if (fd < 0) fail("mkstemp failed");
    try {
        if (::ftruncate(fd, static_cast<off_t>(kLogicalBytes)) != 0) {
            fail("sparse backing ftruncate failed");
        }
        for (const auto offset : offsets) {
            exact_pwrite(fd, payload(offset, page_bytes), offset);
        }
        if (::fdatasync(fd) != 0) fail("sparse backing fdatasync failed");
        if (::close(fd) != 0) fail("sparse backing close failed");
    } catch (...) {
        ::close(fd);
        ::unlink(path.data());
        throw;
    }
    return path.data();
}

hbfsim::Profile profile(std::uint32_t page_bytes)
{
    return {
        .name = "rebuttal-r3-capacity-payload-fixture",
        .capacity_bytes = kLogicalBytes,
        .page_bytes = page_bytes,
        .read_latency_ns = 1,
        .program_latency_ns = 1,
        .channels = 1,
        .dies_per_channel = 1,
        .planes_per_die = 1,
        .pages_per_block = 1,
        .channel_width_bits = 8,
        .channel_transfer_rate_mtps = 1,
        .queue_depth = 1,
        .aggregate_bandwidth_bytes_per_s = 1,
        .hbm_cache_bytes = kCacheBytes,
        .reference_sample_rate = 0.0,
        .reference_warmup_requests = 0,
        .time_scale = 1,
        .timing_tolerance_ns = 0,
    };
}

hbfsim::HbfRequest request(std::uint64_t ticket, std::uint64_t offset,
                           std::uint32_t page_bytes)
{
    return {
        .request_id = ticket + 1,
        .sequence = ticket,
        .logical_address = offset,
        .bytes = page_bytes,
        .range_id = kRangeId,
        .operation = 0,
    };
}

bool wait_for_result(hbfsim::host_service::ControlView control,
                     const hbfsim::HbfRequest& pending,
                     hbfsim::host_service::CapacityHandoffResult& result)
{
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(30);
    while (std::chrono::steady_clock::now() < deadline) {
        if (control.capacity_handoff_result(pending, result)) return true;
        std::this_thread::sleep_for(std::chrono::microseconds(50));
    }
    return false;
}

class ContextOwner {
  public:
    ~ContextOwner()
    {
        if (context != nullptr) {
            (void)::cuCtxSetCurrent(nullptr);
            (void)::cuDevicePrimaryCtxRelease(device);
        }
    }
    CUcontext context{nullptr};
    CUdevice device{0};
};

void write_json(const Options& options, std::size_t cuda_granularity,
                const std::vector<SampleResult>& samples,
                std::uint64_t wall_ns, std::string_view status,
                std::string_view failure_phase = {},
                std::string_view failure_message = {})
{
    std::ofstream output(options.output);
    if (!output) fail("cannot open result output");
    const auto passed = std::count_if(samples.begin(), samples.end(),
                                      [](const auto& item) { return item.passed; });
    output << "{\n"
           << "  \"schema_version\": 1,\n"
           << "  \"experiment\": \"R3_CAPACITY_PAYLOAD_FIXTURE\",\n"
           << "  \"evidence_class\": \"CAPACITY_RUNTIME_PAYLOAD_FIXTURE_NOT_MQSIM_TIMING\",\n"
           << "  \"status\": \"" << status << "\",\n"
           << "  \"failure_phase\": \"" << json_escape(failure_phase) << "\",\n"
           << "  \"failure_message\": \"" << json_escape(failure_message) << "\",\n"
           << "  \"logical_bytes\": " << kLogicalBytes << ",\n"
           << "  \"cache_bytes\": " << kCacheBytes << ",\n"
           << "  \"page_bytes\": " << options.page_bytes << ",\n"
           << "  \"frame_count\": " << kCacheBytes / options.page_bytes << ",\n"
           << "  \"cuda_minimum_granularity\": " << cuda_granularity << ",\n"
           << "  \"common_offset_alignment\": " << kCommonAlignment << ",\n"
           << "  \"sequential_access_only\": true,\n"
           << "  \"concurrent_eviction_tested\": false,\n"
           << "  \"sample_count\": " << samples.size() << ",\n"
           << "  \"passed_count\": " << passed << ",\n"
           << "  \"wall_ns\": " << wall_ns << ",\n"
           << "  \"samples\": [\n";
    for (std::size_t index = 0; index < samples.size(); ++index) {
        const auto& item = samples[index];
        output << "    {\"index\": " << item.index
               << ", \"position\": \"" << item.position
               << "\", \"logical_offset\": " << item.logical_offset
               << ", \"logical_page\": " << item.logical_page
           << ", \"observed_frame_alias\": " << item.observed_frame_alias
               << ", \"frame_address\": \"0x" << std::hex
               << item.frame_address << std::dec
               << "\", \"expected_hash\": \"0x" << std::hex
               << item.expected_hash << "\", \"actual_hash\": \"0x"
               << item.actual_hash << std::dec
               << "\", \"passed\": " << (item.passed ? "true" : "false")
               << "}" << (index + 1 == samples.size() ? "\n" : ",\n");
    }
    output << "  ]\n}\n";
}

}  // namespace

int main(int argc, char** argv)
{
    Options options;
    bool options_ready = false;
    std::string phase = "option parsing";
    std::vector<SampleResult> samples;
    std::size_t cuda_granularity = 0;
    const auto process_begin = std::chrono::steady_clock::now();
    std::filesystem::path backing_path;
    std::unique_ptr<hbfsim::runtime::CapacityRuntime> runtime;
    std::shared_ptr<hbfsim::host_service::BackingStore> backing;
    try {
        options = parse_options(argc, argv);
        options_ready = true;
        phase = "sample construction";
        const auto offsets = sample_offsets(options.page_bytes);
        phase = "sparse backing preparation";
        backing_path = prepare_sparse_backing(options.backing_dir, offsets,
                                              options.page_bytes);

        phase = "CUDA initialization";
        require(::cuInit(0) == CUDA_SUCCESS, "cuInit failed");
        ContextOwner context;
        require(::cuDeviceGet(&context.device, 0) == CUDA_SUCCESS,
                "cuDeviceGet failed");
        require(::cuDevicePrimaryCtxRetain(&context.context, context.device) ==
                    CUDA_SUCCESS,
                "cuDevicePrimaryCtxRetain failed");
        require(::cuCtxSetCurrent(context.context) == CUDA_SUCCESS,
                "cuCtxSetCurrent failed");

        hbfsim::runtime::CudaVmmDriver granularity_probe;
        cuda_granularity = granularity_probe.granularity(context.device);
        require(cuda_granularity != 0, "CUDA VMM granularity query failed");

        constexpr std::uint32_t slots = 2;
        std::vector<std::byte> control_memory(
            hbfsim::host_service::control_region_bytes(slots));
        hbfsim::host_service::ControlView control(control_memory.data(),
                                                   control_memory.size());
        require(control.initialize(slots), "control initialization failed");
        phase = "capacity runtime construction";
        runtime = hbfsim::runtime::CapacityRuntime::create(
            profile(options.page_bytes), control,
            reinterpret_cast<std::uintptr_t>(context.context), context.device);
        require(runtime != nullptr, "CapacityRuntime::create failed");

        phase = "backing publication";
        backing = std::make_shared<hbfsim::host_service::BackingStore>(
            backing_path, 0, kLogicalBytes, false);
        const auto page_count = kLogicalBytes / options.page_bytes;
        const auto token = runtime->router().stage(kRangeId, 0, page_count,
                                                   false, backing);
        require(token != 0 && runtime->router().activate(token),
                "backing publication failed");

        std::map<std::uint64_t, std::uint64_t> observed_frames;
        const auto begin = std::chrono::steady_clock::now();
        for (std::size_t index = 0; index < offsets.size(); ++index) {
            phase = "sample " + std::to_string(index) + " handoff/readback";
            const auto offset = offsets[index];
            auto pending = request(index, offset, options.page_bytes);
            require(control.begin_capacity_handoff(pending),
                    "capacity handoff begin failed");
            hbfsim::host_service::CapacityHandoffResult result{};
            require(wait_for_result(control, pending, result),
                    "capacity handoff timed out");
            require(result.status == hbfsim::RequestStatus::Ready &&
                        result.frame_address != 0,
                    "capacity handoff returned non-ready status");

            std::vector<std::byte> actual(options.page_bytes);
            require(::cuMemcpyDtoH(actual.data(),
                                   static_cast<CUdeviceptr>(result.frame_address),
                                   actual.size()) == CUDA_SUCCESS,
                    "cuMemcpyDtoH frame readback failed");
            auto expected = payload(offset, options.page_bytes);
            auto [frame, inserted] = observed_frames.emplace(
                result.frame_address, observed_frames.size());
            (void)inserted;
            samples.push_back({
                .index = static_cast<std::uint32_t>(index),
                .position = position_name(offset, options.page_bytes),
                .logical_offset = offset,
                .logical_page = offset / options.page_bytes,
                // This is an encounter-order alias for the returned CUDA
                // frame address, not an internal HbmCache index.
                .observed_frame_alias = frame->second,
                .frame_address = result.frame_address,
                .expected_hash = fnv1a(expected),
                .actual_hash = fnv1a(actual),
                .passed = actual == expected,
            });
            require(control.release_capacity_handoff(pending),
                    "capacity handoff release failed");
        }
        const auto end = std::chrono::steady_clock::now();

        phase = "clean shutdown";
        runtime->stop();
        require(runtime->router().deactivate(kRangeId) ==
                    hbfsim::RequestStatus::Ready,
                "backing deactivation failed");
        require(runtime->release_cuda_resources(),
                "CUDA capacity resource release failed");
        runtime.reset();
        backing.reset();
        std::filesystem::remove(backing_path);
        backing_path.clear();

        phase = "result write";
        write_json(options, cuda_granularity, samples,
                   std::chrono::duration_cast<std::chrono::nanoseconds>(end - begin)
                       .count(),
                   std::all_of(samples.begin(), samples.end(),
                               [](const auto& item) { return item.passed; })
                       ? "PASS"
                       : "FAIL");
        return std::all_of(samples.begin(), samples.end(),
                           [](const auto& item) { return item.passed; })
                   ? 0
                   : 1;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "capacity_payload_fixture: %s\n", error.what());
        if (runtime != nullptr) runtime->stop();
        runtime.reset();
        backing.reset();
        if (!backing_path.empty()) std::filesystem::remove(backing_path);
        if (options_ready) {
            try {
                write_json(
                    options, cuda_granularity, samples,
                    std::chrono::duration_cast<std::chrono::nanoseconds>(
                        std::chrono::steady_clock::now() - process_begin)
                        .count(),
                    "FAIL", phase, error.what());
            } catch (...) {
                std::fprintf(stderr, "capacity_payload_fixture: failed to write failure result\n");
            }
        }
        return 1;
    }
}

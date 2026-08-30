#include <hbfsim/api.h>

#include <cuda_runtime_api.h>
#include <cuda.h>

#include <algorithm>
#include <bit>
#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

namespace {

struct Options {
    std::string pattern{"sequential"};
    std::string mode{"baseline"};
    std::string profile;
    std::string report_dir;
    std::string backing_dir{"/mnt/disk2"};
    std::string output;
    std::string package_thermal_stage{"off"};
    std::string package_thermal_profile;
    std::string package_thermal_model;
    std::uint64_t bytes{8ULL << 20};
    std::uint64_t iterations{256};
    std::uint64_t seed{0};
};

[[noreturn]] void fail(const std::string& message)
{
    throw std::runtime_error(message);
}

void cuda_check(cudaError_t status, const char* operation)
{
    if (status != cudaSuccess) {
        fail(std::string(operation) + ": " + cudaGetErrorString(status));
    }
}

void driver_check(CUresult status, const char* operation)
{
    if (status != CUDA_SUCCESS) {
        const char* text = nullptr;
        (void)cuGetErrorString(status, &text);
        fail(std::string(operation) + ": " +
             (text == nullptr ? "unknown CUDA driver error" : text));
    }
}

std::uint64_t splitmix(std::uint64_t value)
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

__device__ std::uint64_t device_splitmix(std::uint64_t value)
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

__device__ std::uint64_t rotate_left(std::uint64_t value, unsigned shift)
{
    return (value << shift) | (value >> (64 - shift));
}

}  // namespace

extern "C" __global__ void hbf_access_kernel(
    std::uint64_t* data, const std::uint64_t* byte_offsets,
    std::uint64_t iterations, std::uint64_t seed, bool mixed_write,
    std::uint64_t* output)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) {
        return;
    }
    std::uint64_t checksum = device_splitmix(seed ^ 0x48424653494dULL);
    for (std::uint64_t index = 0; index < iterations; ++index) {
        const auto word = byte_offsets[index] / sizeof(std::uint64_t);
        auto value = data[word];
        if (mixed_write && (index & 1U) != 0) {
            value ^= device_splitmix(seed + index);
            data[word] = value;
        }
        checksum = rotate_left(checksum ^ (value + index), 13);
    }
    *output = checksum;
}

namespace {

std::uint64_t parse_unsigned(std::string_view value, const char* name)
{
    const std::string text(value);
    char* end = nullptr;
    errno = 0;
    const auto parsed = std::strtoull(text.c_str(), &end, 10);
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
        if (argument == "--pattern") result.pattern = value;
        else if (argument == "--mode") result.mode = value;
        else if (argument == "--profile") result.profile = value;
        else if (argument == "--report-dir") result.report_dir = value;
        else if (argument == "--backing-dir") result.backing_dir = value;
        else if (argument == "--output") result.output = value;
        else if (argument == "--package-thermal-stage") result.package_thermal_stage = value;
        else if (argument == "--package-thermal-profile") result.package_thermal_profile = value;
        else if (argument == "--package-thermal-model") result.package_thermal_model = value;
        else if (argument == "--bytes") result.bytes = parse_unsigned(value, "bytes");
        else if (argument == "--iterations") result.iterations = parse_unsigned(value, "iterations");
        else if (argument == "--seed") result.seed = parse_unsigned(value, "seed");
        else fail("unknown option " + argument);
    }
    const std::unordered_set<std::string> patterns{
        "sequential", "random", "strided", "pointer_chase", "mixed_rw"};
    const std::unordered_set<std::string> modes{
        "baseline", "timing", "reference", "fast", "hybrid", "capacity"};
    if (!patterns.contains(result.pattern) || !modes.contains(result.mode) ||
        result.bytes < sizeof(std::uint64_t) ||
        result.bytes % sizeof(std::uint64_t) != 0 ||
        result.iterations < 2 || result.profile.empty() ||
        result.report_dir.empty() || result.output.empty()) {
        fail("invalid benchmark configuration");
    }
    const std::unordered_set<std::string> thermal_stages{
        "off", "read_only", "shadow", "active"};
    if (!thermal_stages.contains(result.package_thermal_stage)) {
        fail("invalid package thermal stage");
    }
    const bool package_enabled = result.package_thermal_stage != "off";
    if (package_enabled != (!result.package_thermal_profile.empty() &&
                            !result.package_thermal_model.empty())) {
        fail("package thermal profile/model are required together for a non-off stage");
    }
    return result;
}

std::vector<std::uint64_t> make_offsets(const Options& options)
{
    const auto words = options.bytes / sizeof(std::uint64_t);
    std::vector<std::uint64_t> offsets(options.iterations);
    offsets.front() = 0;
    offsets.back() = (words - 1) * sizeof(std::uint64_t);
    std::uint64_t state = options.seed;
    for (std::uint64_t index = 1; index + 1 < options.iterations; ++index) {
        std::uint64_t word = 0;
        if (options.pattern == "sequential") {
            word = index % words;
        } else if (options.pattern == "strided") {
            word = (index * 4099ULL) % words;
        } else if (options.pattern == "pointer_chase") {
            state = splitmix(state);
            word = state % words;
        } else {
            word = splitmix(options.seed + index) % words;
        }
        offsets[index] = word * sizeof(std::uint64_t);
    }
    return offsets;
}

std::unordered_map<std::uint64_t, std::uint64_t> initial_values(
    const Options& options, const std::vector<std::uint64_t>& offsets)
{
    std::unordered_map<std::uint64_t, std::uint64_t> values;
    for (const auto offset : offsets) {
        values.emplace(offset, splitmix(offset ^ options.seed));
    }
    return values;
}

std::uint64_t reference_checksum(
    const Options& options, const std::vector<std::uint64_t>& offsets,
    std::unordered_map<std::uint64_t, std::uint64_t> values)
{
    auto checksum = splitmix(options.seed ^ 0x48424653494dULL);
    for (std::uint64_t index = 0; index < options.iterations; ++index) {
        auto& value = values.at(offsets[index]);
        if (options.pattern == "mixed_rw" && (index & 1U) != 0) {
            value ^= splitmix(options.seed + index);
        }
        checksum = std::rotl(checksum ^ (value + index), 13);
    }
    return checksum;
}

std::string prepare_backing(
    const Options& options,
    const std::unordered_map<std::uint64_t, std::uint64_t>& values)
{
    std::filesystem::create_directories(options.backing_dir);
    std::string pattern = options.backing_dir + "/hbfsim-capacity-XXXXXX";
    std::vector<char> path(pattern.begin(), pattern.end());
    path.push_back('\0');
    const int descriptor = ::mkstemp(path.data());
    if (descriptor < 0) fail("mkstemp failed");
    if (::ftruncate(descriptor, static_cast<off_t>(options.bytes)) != 0) {
        ::close(descriptor);
        ::unlink(path.data());
        fail("sparse backing ftruncate failed");
    }
    for (const auto& [offset, value] : values) {
        if (::pwrite(descriptor, &value, sizeof(value),
                     static_cast<off_t>(offset)) != sizeof(value)) {
            ::close(descriptor);
            ::unlink(path.data());
            fail("sparse backing pwrite failed");
        }
    }
    if (::close(descriptor) != 0) {
        ::unlink(path.data());
        fail("sparse backing close failed");
    }
    return path.data();
}

void write_report(const Options& options, std::uint64_t checksum,
                  std::uint64_t baseline, std::uint64_t elapsed_ns,
                  std::uint64_t span, const hbfsim_stats& stats)
{
    std::ofstream output(options.output);
    if (!output) fail("cannot open benchmark output");
    output << "{\n"
           << "  \"schema_version\": 1,\n"
           << "  \"pattern\": \"" << options.pattern << "\",\n"
           << "  \"mode\": \"" << options.mode << "\",\n"
           << "  \"package_thermal_stage\": \""
           << options.package_thermal_stage << "\",\n"
           << "  \"logical_bytes\": " << options.bytes << ",\n"
           << "  \"iterations\": " << options.iterations << ",\n"
           << "  \"seed\": " << options.seed << ",\n"
           << "  \"access_span_bytes\": " << span << ",\n"
           << "  \"checksum\": " << checksum << ",\n"
           << "  \"baseline_checksum\": " << baseline << ",\n"
           << "  \"wall_ns\": " << elapsed_ns << ",\n"
           << "  \"requests\": {\"submitted\": "
           << stats.requests_submitted << ", \"completed\": "
           << stats.requests_completed << ", \"fast\": "
           << stats.fast_requests << ", \"reference\": "
           << stats.reference_requests << "},\n"
           << "  \"timing\": {\"modeled_ns\": "
           << stats.fast_modeled_ns << "},\n"
           << "  \"coverage\": {\"unsafe_launches\": 0}\n"
           << "}\n";
}

}  // namespace

int main(int argc, char** argv)
{
    std::string backing;
    hbfsim_context* context = nullptr;
    void* data = nullptr;
    std::uint64_t* device_offsets = nullptr;
    std::uint64_t* device_output = nullptr;
    CUmodule module = nullptr;
    try {
        const auto options = parse_options(argc, argv);
        const auto offsets = make_offsets(options);
        const auto values = initial_values(options, offsets);
        const auto expected = reference_checksum(options, offsets, values);
        cuda_check(cudaFree(nullptr), "initialize CUDA");

        const bool baseline_mode = options.mode == "baseline";
        hbfsim_options context_options{
            .profile_path = options.profile.c_str(),
            .report_dir = options.report_dir.c_str(),
            .mode = options.mode == "fast" ? HBFSIM_MODEL_FAST
                  : options.mode == "hybrid" ? HBFSIM_MODEL_HYBRID
                                               : HBFSIM_MODEL_REFERENCE,
            .ring_capacity = 256,
            .request_timeout_ns = 30'000'000'000ULL,
        };
        if (!baseline_mode) {
            int create_status = HBFSIM_OK;
            if (options.package_thermal_stage == "off") {
                create_status = hbfsim_context_create(&context_options, &context);
            } else {
                const auto stage = options.package_thermal_stage == "read_only"
                    ? HBFSIM_PACKAGE_THERMAL_READ_ONLY
                    : options.package_thermal_stage == "shadow"
                    ? HBFSIM_PACKAGE_THERMAL_SHADOW
                    : HBFSIM_PACKAGE_THERMAL_ACTIVE;
                const hbfsim_options_v2 extended{
                    .struct_size = sizeof(hbfsim_options_v2),
                    .version = HBFSIM_OPTIONS_V2_VERSION,
                    .base = context_options,
                    .package_thermal_mode = HBFSIM_PACKAGE_THERMAL_PACKAGE_RC,
                    .package_thermal_stage = static_cast<std::uint32_t>(stage),
                    .package_thermal_model_kind = HBFSIM_PACKAGE_THERMAL_MODEL_ROM,
                    .reserved = 0,
                    .package_thermal_profile_path =
                        options.package_thermal_profile.c_str(),
                    .package_thermal_model_path =
                        options.package_thermal_model.c_str(),
                };
                create_status = hbfsim_context_create_v2(&extended, &context);
            }
            if (create_status != HBFSIM_OK) {
                fail("HBFSim context creation failed");
            }
        }

        if (options.mode == "capacity") {
            backing = prepare_backing(options, values);
            const hbfsim_range_options range{
                .mode = HBFSIM_RANGE_MODE_CAPACITY,
                .permissions = options.pattern == "mixed_rw"
                                   ? HBFSIM_RANGE_READ_WRITE
                                   : HBFSIM_RANGE_READ,
                .cache_policy = HBFSIM_CACHE_POLICY_NONE,
                .stream_id = 0,
            };
            if (hbfsim_map_file(context, backing.c_str(), 0, options.bytes,
                                &range, &data) != HBFSIM_OK) {
                fail("hbfsim_map_file failed");
            }
            // The backing store owns an open descriptor after mapping. Unlinking
            // makes the very large sparse validation object self-cleaning.
            ::unlink(backing.c_str());
            backing.clear();
        } else {
            cuda_check(cudaMalloc(&data, options.bytes), "cudaMalloc data");
            std::vector<std::uint64_t> host(options.bytes / sizeof(std::uint64_t));
            for (const auto& [offset, value] : values) {
                host[offset / sizeof(std::uint64_t)] = value;
            }
            cuda_check(cudaMemcpy(data, host.data(), options.bytes,
                                  cudaMemcpyHostToDevice), "initialize data");
            if (!baseline_mode) {
                const hbfsim_range_options range{
                    .mode = HBFSIM_RANGE_MODE_TIMING,
                    .permissions = HBFSIM_RANGE_READ_WRITE,
                    .cache_policy = HBFSIM_CACHE_POLICY_NONE,
                    .stream_id = 0,
                };
                if (hbfsim_register_device(context, data, options.bytes,
                                           &range) != HBFSIM_OK) {
                    fail("hbfsim_register_device failed");
                }
            }
        }

        cuda_check(cudaMalloc(&device_offsets,
                              offsets.size() * sizeof(std::uint64_t)),
                   "cudaMalloc offsets");
        cuda_check(cudaMemcpy(device_offsets, offsets.data(),
                              offsets.size() * sizeof(std::uint64_t),
                              cudaMemcpyHostToDevice), "copy offsets");
        cuda_check(cudaMalloc(&device_output, sizeof(std::uint64_t)),
                   "cudaMalloc output");
        std::ifstream ptx_input(HBFSIM_MICROBENCH_PTX_PATH,
                                std::ios::binary);
        if (!ptx_input) fail("cannot open microbenchmark PTX");
        const std::string ptx{std::istreambuf_iterator<char>(ptx_input), {}};
        driver_check(cuModuleLoadDataEx(&module, ptx.c_str(), 0, nullptr,
                                       nullptr), "cuModuleLoadDataEx");
        CUfunction function = nullptr;
        driver_check(cuModuleGetFunction(&function, module,
                                         "hbf_access_kernel"),
                     "cuModuleGetFunction");
        auto iterations = options.iterations;
        auto seed = options.seed;
        bool mixed_write = options.pattern == "mixed_rw";
        void* arguments[]{&data, &device_offsets, &iterations, &seed,
                          &mixed_write, &device_output};
        const auto begin = std::chrono::steady_clock::now();
        driver_check(cuLaunchKernel(function, 1, 1, 1, 1, 1, 1, 0, nullptr,
                                    arguments, nullptr),
                     "cuLaunchKernel hbf_access_kernel");
        cuda_check(cudaDeviceSynchronize(), "synchronize hbf_access_kernel");
        const auto end = std::chrono::steady_clock::now();
        std::uint64_t checksum = 0;
        cuda_check(cudaMemcpy(&checksum, device_output, sizeof(checksum),
                              cudaMemcpyDeviceToHost), "copy checksum");
        hbfsim_stats stats{};
        if (context != nullptr && hbfsim_get_stats(context, &stats) != HBFSIM_OK) {
            fail("hbfsim_get_stats failed");
        }
        if (options.mode == "capacity" && hbfsim_flush(context) != HBFSIM_OK) {
            fail("hbfsim_flush failed");
        }
        if (checksum != expected) fail("GPU checksum differs from CPU reference");
        const auto span = *std::max_element(offsets.begin(), offsets.end()) -
                          *std::min_element(offsets.begin(), offsets.end()) + 8;
        write_report(options, checksum, expected,
                     std::chrono::duration_cast<std::chrono::nanoseconds>(
                     end - begin).count(), span, stats);

        driver_check(cuModuleUnload(module), "cuModuleUnload");
        module = nullptr;

        cuda_check(cudaFree(device_output), "cudaFree output");
        device_output = nullptr;
        cuda_check(cudaFree(device_offsets), "cudaFree offsets");
        device_offsets = nullptr;
        if (options.mode == "capacity") {
            if (hbfsim_unregister(context, data) != HBFSIM_OK) {
                fail("hbfsim_unregister failed");
            }
            hbfsim_context_destroy(context);
            context = nullptr;
        } else if (context != nullptr) {
            hbfsim_context_destroy(context);
            context = nullptr;
            cuda_check(cudaFree(data), "cudaFree data");
        } else {
            cuda_check(cudaFree(data), "cudaFree data");
        }
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "hbf_microbench: %s\n", error.what());
        if (!backing.empty()) ::unlink(backing.c_str());
        if (module != nullptr) (void)cuModuleUnload(module);
        if (context != nullptr) hbfsim_context_destroy(context);
        if (device_output != nullptr) (void)cudaFree(device_output);
        if (device_offsets != nullptr) (void)cudaFree(device_offsets);
        return 1;
    }
}

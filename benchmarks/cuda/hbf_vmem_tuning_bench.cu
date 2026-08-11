#include <cstddef>
#include <cstdint>

#if !defined(HBFSIM_VMEM_HOST_ONLY)
extern "C" __global__ void hbf_vmem_sequential(
    const volatile std::uint8_t* input, std::uint32_t pages,
    std::uint64_t* checksum)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) {
        return;
    }
    std::uint64_t value = 0;
    for (std::uint32_t page = 0; page < pages; ++page) {
        value = (value * 131) ^
                input[static_cast<std::size_t>(page) * 4096];
    }
    *checksum = value;
}
#endif

#if !defined(HBFSIM_VMEM_KERNEL_ONLY)
#include <hbfsim/api.h>

#include <cuda.h>
#include <cuda_runtime_api.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

struct Options {
    std::string mode;
    std::string profile;
    std::string report_dir;
    std::string output;
    std::uint32_t pages{0};
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

std::uint32_t parse_pages(std::string_view text)
{
    const std::string value(text);
    char* end = nullptr;
    const auto parsed = std::strtoul(value.c_str(), &end, 10);
    if (end == nullptr || *end != '\0' || parsed == 0 || parsed > 512) {
        fail("pages must be in [1, 512]");
    }
    return static_cast<std::uint32_t>(parsed);
}

Options parse_options(int argc, char** argv)
{
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (index + 1 >= argc) {
            fail("missing value for " + argument);
        }
        const std::string value = argv[++index];
        if (argument == "--mode") {
            options.mode = value;
        } else if (argument == "--profile") {
            options.profile = value;
        } else if (argument == "--report-dir") {
            options.report_dir = value;
        } else if (argument == "--output") {
            options.output = value;
        } else if (argument == "--pages") {
            options.pages = parse_pages(value);
        } else {
            fail("unknown option " + argument);
        }
    }
    if ((options.mode != "baseline" && options.mode != "tuned") ||
        options.profile.empty() || options.report_dir.empty() ||
        options.output.empty() || options.pages == 0) {
        fail("invalid benchmark configuration");
    }
    return options;
}

std::uint64_t reference_checksum(const std::vector<std::uint8_t>& input,
                                 std::uint32_t pages)
{
    std::uint64_t value = 0;
    for (std::uint32_t page = 0; page < pages; ++page) {
        value = (value * 131) ^ input[std::size_t{page} * 4096];
    }
    return value;
}

void write_result(const Options& options, std::uint64_t checksum,
                  std::uint64_t expected, std::uint64_t wall_ns,
                  const hbfsim_stats& stats)
{
    std::ofstream output(options.output);
    if (!output) {
        fail("cannot open benchmark output");
    }
    output << "{\n"
           << "  \"schema_version\": 1,\n"
           << "  \"mode\": \"" << options.mode << "\",\n"
           << "  \"pages\": " << options.pages << ",\n"
           << "  \"bytes\": " << std::uint64_t{options.pages} * 4096
           << ",\n"
           << "  \"checksum\": " << checksum << ",\n"
           << "  \"expected_checksum\": " << expected << ",\n"
           << "  \"wall_ns\": " << wall_ns << ",\n"
           << "  \"requests\": {\"fast\": " << stats.fast_requests
           << ", \"reference\": " << stats.reference_requests << "},\n"
           << "  \"modeled_ns\": " << stats.fast_modeled_ns << "\n"
           << "}\n";
}

}  // namespace

int main(int argc, char** argv)
{
    hbfsim_context* context = nullptr;
    void* input = nullptr;
    std::uint64_t* output = nullptr;
    CUmodule cuda_module = nullptr;
    bool registered = false;
    try {
        const auto options = parse_options(argc, argv);
        const auto bytes = std::size_t{options.pages} * 4096;
        std::vector<std::uint8_t> host(bytes);
        for (std::size_t index = 0; index < host.size(); ++index) {
            host[index] = static_cast<std::uint8_t>(
                (index * 17 + (index >> 12) * 29 + 0x5a) & 0xff);
        }
        const auto expected = reference_checksum(host, options.pages);

        cuda_check(cudaFree(nullptr), "initialize CUDA");
        cuda_check(cudaMalloc(&input, bytes), "cudaMalloc input");
        cuda_check(cudaMemcpy(input, host.data(), bytes,
                              cudaMemcpyHostToDevice), "initialize input");
        cuda_check(cudaMalloc(reinterpret_cast<void**>(&output),
                              sizeof(*output)), "cudaMalloc output");

        if (options.mode == "tuned") {
            const hbfsim_options context_options{
                .profile_path = options.profile.c_str(),
                .report_dir = options.report_dir.c_str(),
                .mode = HBFSIM_MODEL_FAST,
                .ring_capacity = 256,
                .request_timeout_ns = 30'000'000'000ULL,
            };
            if (hbfsim_context_create(&context_options, &context) !=
                HBFSIM_OK) {
                fail("hbfsim_context_create failed");
            }
            const hbfsim_range_options range{
                .mode = HBFSIM_RANGE_MODE_TIMING,
                .permissions = HBFSIM_RANGE_READ,
                .cache_policy = HBFSIM_CACHE_POLICY_NONE,
                .stream_id = 0,
            };
            if (hbfsim_register_device(context, input, bytes, &range) !=
                HBFSIM_OK) {
                fail("hbfsim_register_device failed");
            }
            registered = true;
        }

        std::ifstream ptx_input(HBFSIM_VMEM_TUNING_PTX_PATH,
                                std::ios::binary);
        if (!ptx_input) {
            fail("cannot open vmem tuning PTX");
        }
        const std::string ptx{std::istreambuf_iterator<char>(ptx_input), {}};
        driver_check(cuModuleLoadDataEx(&cuda_module, ptx.c_str(), 0, nullptr,
                                       nullptr), "cuModuleLoadDataEx");
        CUfunction function = nullptr;
        driver_check(cuModuleGetFunction(&function, cuda_module,
                                         "hbf_vmem_sequential"),
                     "cuModuleGetFunction");
        auto pages = options.pages;
        void* arguments[]{&input, &pages, &output};
        const auto begin = std::chrono::steady_clock::now();
        driver_check(cuLaunchKernel(function, 1, 1, 1, 1, 1, 1, 0, nullptr,
                                    arguments, nullptr),
                     "cuLaunchKernel hbf_vmem_sequential");
        cuda_check(cudaDeviceSynchronize(), "synchronize benchmark");
        const auto end = std::chrono::steady_clock::now();

        std::uint64_t checksum = 0;
        cuda_check(cudaMemcpy(&checksum, output, sizeof(checksum),
                              cudaMemcpyDeviceToHost), "copy checksum");
        if (checksum != expected) {
            fail("GPU checksum differs from CPU reference");
        }
        hbfsim_stats stats{};
        if (context != nullptr &&
            hbfsim_get_stats(context, &stats) != HBFSIM_OK) {
            fail("hbfsim_get_stats failed");
        }
        write_result(
            options, checksum, expected,
            std::chrono::duration_cast<std::chrono::nanoseconds>(end - begin)
                .count(),
            stats);

        driver_check(cuModuleUnload(cuda_module), "cuModuleUnload");
        cuda_module = nullptr;
        if (registered) {
            if (hbfsim_unregister(context, input) != HBFSIM_OK) {
                fail("hbfsim_unregister failed");
            }
            registered = false;
        }
        if (context != nullptr) {
            hbfsim_context_destroy(context);
            context = nullptr;
        }
        cuda_check(cudaFree(output), "cudaFree output");
        output = nullptr;
        cuda_check(cudaFree(input), "cudaFree input");
        input = nullptr;
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "hbf_vmem_tuning_bench: %s\n", error.what());
        if (cuda_module != nullptr) {
            (void)cuModuleUnload(cuda_module);
        }
        if (registered && context != nullptr) {
            (void)hbfsim_unregister(context, input);
        }
        if (context != nullptr) {
            hbfsim_context_destroy(context);
        }
        if (output != nullptr) {
            (void)cudaFree(output);
        }
        if (input != nullptr) {
            (void)cudaFree(input);
        }
        return 1;
    }
}
#endif

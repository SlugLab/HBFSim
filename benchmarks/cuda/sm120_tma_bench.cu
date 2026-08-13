#include <hbfsim/api.h>

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <json.hpp>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

extern "C" int process_input(const char*, int, char*);

namespace {

using json = nlohmann::json;

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
             (text == nullptr ? "unknown CUDA error" : text));
    }
}

std::string read_ptx()
{
    std::ifstream input(HBFSIM_SM120_TMA_PTX_PATH, std::ios::binary);
    if (!input) fail("cannot open SM120 TMA PTX");
    return {std::istreambuf_iterator<char>(input), {}};
}

std::string transform(const std::string& ptx)
{
    const json request{{"input",
                        {{"full_ptx", ptx},
                         {"to_patch_kernel", "sm120_tma_load_2d"},
                         {"async_futures", true}}},
                       {"ebpf_instructions", json::array()}};
    std::vector<char> output(64U << 20);
    const auto encoded = request.dump();
    const auto status = process_input(encoded.c_str(),
                                      static_cast<int>(output.size()),
                                      output.data());
    if (status != 0) fail("TMA PTX pass failed: " + std::to_string(status));
    const auto response = json::parse(output.data());
    if (!response.at("modified").get<bool>()) {
        fail("TMA PTX pass did not modify kernel");
    }
    return response.at("output_ptx").get<std::string>();
}

CUmodule load_module(const std::string& image, bool instrumented)
{
    using begin_type = std::uint64_t (*)(const char*, std::size_t);
    auto begin = reinterpret_cast<begin_type>(
        dlsym(RTLD_DEFAULT, "hbfsim_begin_module_load_from_ptx"));
    if (instrumented) {
        if (begin == nullptr || begin(image.data(), image.size()) == 0) {
            fail("HBFSim module-load transaction was rejected");
        }
    }
    CUmodule module = nullptr;
    std::array<char, 16384> error_log{};
    CUjit_option options[]{CU_JIT_ERROR_LOG_BUFFER,
                          CU_JIT_ERROR_LOG_BUFFER_SIZE_BYTES};
    void* values[]{error_log.data(),
                   reinterpret_cast<void*>(error_log.size())};
    const auto status =
        cuModuleLoadDataEx(&module, image.c_str(), 2, options, values);
    if (status != CUDA_SUCCESS) {
        fail("cuModuleLoadDataEx: " + std::string(error_log.data()));
    }
    return module;
}

std::uint64_t independent_work(std::uint64_t value)
{
    for (int index = 0; index < 1024; ++index) {
        value ^= value << 13;
        value ^= value >> 7;
        value ^= value << 17;
    }
    return value;
}

}  // namespace

int main(int argc, char** argv)
{
    try {
        std::string mode;
        std::string profile;
        std::string report_dir;
        std::string output_path;
        for (int index = 1; index < argc; index += 2) {
            if (index + 1 >= argc) fail("missing option value");
            const std::string key = argv[index];
            if (key == "--mode") mode = argv[index + 1];
            else if (key == "--profile") profile = argv[index + 1];
            else if (key == "--report-dir") report_dir = argv[index + 1];
            else if (key == "--output") output_path = argv[index + 1];
            else fail("unknown option " + key);
        }
        if ((mode != "native" && mode != "instrumented") ||
            output_path.empty() || (mode == "instrumented" &&
                                    (profile.empty() || report_dir.empty()))) {
            fail("invalid benchmark configuration");
        }
        cuda_check(cudaFree(nullptr), "initialize CUDA runtime");
        driver_check(cuInit(0), "cuInit");

        constexpr std::size_t input_bytes = 4096;
        constexpr std::size_t tile_bytes = 256;
        std::array<std::uint8_t, input_bytes> input{};
        for (std::size_t index = 0; index < input.size(); ++index) {
            input[index] = static_cast<std::uint8_t>((index * 29 + 7) & 0xff);
        }
        void* device_input = nullptr;
        std::uint32_t* device_output = nullptr;
        std::uint64_t* device_stamps = nullptr;
        cuda_check(cudaMalloc(&device_input, input_bytes), "allocate input");
        cuda_check(cudaMalloc(&device_output, tile_bytes), "allocate output");
        cuda_check(cudaMalloc(&device_stamps, 4 * sizeof(std::uint64_t)),
                   "allocate stamps");
        cuda_check(cudaMemcpy(device_input, input.data(), input.size(),
                              cudaMemcpyHostToDevice), "initialize input");
        cuda_check(cudaMemset(device_output, 0, tile_bytes), "clear output");
        cuda_check(cudaMemset(device_stamps, 0, 4 * sizeof(std::uint64_t)),
                   "clear stamps");

        hbfsim_context* context = nullptr;
        if (mode == "instrumented") {
            const hbfsim_options options{
                .profile_path = profile.c_str(),
                .report_dir = report_dir.c_str(),
                .mode = HBFSIM_MODEL_FAST,
                .ring_capacity = 64,
                .request_timeout_ns = 5'000'000'000ULL,
            };
            if (hbfsim_context_create(&options, &context) != HBFSIM_OK) {
                fail("hbfsim_context_create failed");
            }
            const hbfsim_range_options range{
                .mode = HBFSIM_RANGE_MODE_TIMING,
                .permissions = HBFSIM_RANGE_READ_WRITE,
                .cache_policy = HBFSIM_CACHE_POLICY_NONE,
                .stream_id = 0,
            };
            if (hbfsim_register_device(context, device_input, input_bytes,
                                       &range) != HBFSIM_OK) {
                fail("hbfsim_register_device failed");
            }
        }

        alignas(64) CUtensorMap tensor_map{};
        const cuuint64_t global_dim[2]{64, 16};
        const cuuint64_t global_stride[1]{256};
        const cuuint32_t box_dim[2]{64, 1};
        const cuuint32_t element_stride[2]{1, 1};
        driver_check(cuTensorMapEncodeTiled(
                         &tensor_map, CU_TENSOR_MAP_DATA_TYPE_UINT32, 2,
                         device_input, global_dim, global_stride, box_dim,
                         element_stride, CU_TENSOR_MAP_INTERLEAVE_NONE,
                         CU_TENSOR_MAP_SWIZZLE_NONE,
                         CU_TENSOR_MAP_L2_PROMOTION_NONE,
                         CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE),
                     "cuTensorMapEncodeTiled");

        const auto original = read_ptx();
        const bool instrumented = mode == "instrumented";
        const auto image = instrumented ? transform(original) : original;
        CUmodule module = load_module(image, instrumented);
        CUfunction function = nullptr;
        driver_check(cuModuleGetFunction(&function, module,
                                         "sm120_tma_load_2d"),
                     "cuModuleGetFunction");
        constexpr std::uint64_t seed = 0x2468ace013579bdfULL;
        auto mutable_seed = seed;
        void* arguments[]{&tensor_map, &device_output, &device_stamps,
                          &mutable_seed};
        driver_check(cuLaunchKernel(function, 1, 1, 1, 1, 1, 1, 0, nullptr,
                                    arguments, nullptr), "launch TMA kernel");
        cuda_check(cudaDeviceSynchronize(), "synchronize TMA kernel");

        std::array<std::uint8_t, tile_bytes> result{};
        std::array<std::uint64_t, 4> stamps{};
        cuda_check(cudaMemcpy(result.data(), device_output, result.size(),
                              cudaMemcpyDeviceToHost), "copy TMA output");
        cuda_check(cudaMemcpy(stamps.data(), device_stamps, sizeof(stamps),
                              cudaMemcpyDeviceToHost), "copy timestamps");
        const bool bit_exact = std::equal(result.begin(), result.end(),
                                          input.begin());
        hbfsim_tma_stats stats{};
        if (context != nullptr && hbfsim_get_tma_stats(context, &stats) !=
                                      HBFSIM_OK) {
            fail("hbfsim_get_tma_stats failed");
        }
        const json report{
            {"schema_version", 1},
            {"mode", mode},
            {"bit_exact", bit_exact},
            {"timestamps", {{"issue", stamps[0]},
                             {"independent_end", stamps[1]},
                             {"wait_end", stamps[2]}}},
            {"independent_checksum", stamps[3]},
            {"expected_independent_checksum", independent_work(seed)},
            {"tma", {{"issued", stats.issued},
                      {"hbm_bytes", stats.hbm_bytes},
                      {"hbf_bytes", stats.hbf_bytes},
                      {"oob_bytes", stats.oob_bytes},
                      {"fanout_targets", stats.fanout_targets},
                      {"stale_generations", stats.stale_generations},
                      {"faults", stats.faults},
                      {"leaked", stats.leaked}}},
        };
        std::ofstream output(output_path);
        output << report.dump(2) << '\n';
        if (!output) fail("write output failed");

        driver_check(cuModuleUnload(module), "cuModuleUnload");
        if (context != nullptr) {
            (void)hbfsim_unregister(context, device_input);
            hbfsim_context_destroy(context);
        }
        cuda_check(cudaFree(device_stamps), "free stamps");
        cuda_check(cudaFree(device_output), "free output");
        cuda_check(cudaFree(device_input), "free input");
        return bit_exact ? 0 : 2;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "sm120_tma_bench: %s\n", error.what());
        return 70;
    }
}

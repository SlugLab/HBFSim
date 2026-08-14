#include <hbfsim/api.h>

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <json.hpp>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

extern "C" int process_input(const char*, int, char*);

namespace {

using json = nlohmann::json;

enum class ScenarioKind {
    TiledLoad,
    TiledStore,
    OobZero,
    PhaseReuse,
    SourceReuse,
    HostReplace,
    DeviceReplace,
    DescriptorCopy,
    Im2colLoad,
    Im2colStore,
    Im2colWideLoad,
    Multicast,
    OobNan,
};

struct Scenario {
    std::string name;
    std::string kernel;
    ScenarioKind kind;
    std::uint32_t rank;
    std::size_t output_bytes{256};
    std::uint32_t issues{1};
    std::uint32_t fanout{1};
    CUtensorMapDataType element_type{CU_TENSOR_MAP_DATA_TYPE_UINT32};
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
             (text == nullptr ? "unknown CUDA error" : text));
    }
}

Scenario scenario(std::string_view name)
{
    for (std::uint32_t rank = 1; rank <= 5; ++rank) {
        const auto load = "load_" + std::to_string(rank) + "d";
        if (name == load) {
            return {load, "sm120_tma_" + load, ScenarioKind::TiledLoad,
                    rank};
        }
        const auto store = "store_" + std::to_string(rank) + "d";
        if (name == store) {
            return {store, "sm120_tma_" + store, ScenarioKind::TiledStore,
                    rank};
        }
    }
    if (name == "oob_zero") {
        return {"oob_zero", "sm120_tma_oob_zero_1d",
                ScenarioKind::OobZero, 1};
    }
    if (name == "phase_reuse") {
        return {"phase_reuse", "sm120_tma_phase_reuse_1d",
                ScenarioKind::PhaseReuse, 1, 256, 2};
    }
    if (name == "source_reuse") {
        return {"source_reuse", "sm120_tma_source_reuse_1d",
                ScenarioKind::SourceReuse, 1};
    }
    if (name == "host_replace") {
        return {"host_replace", "sm120_tma_load_1d",
                ScenarioKind::HostReplace, 1};
    }
    if (name == "device_replace") {
        return {"device_replace", "sm120_tma_device_replace_1d",
                ScenarioKind::DeviceReplace, 1};
    }
    if (name == "descriptor_copy") {
        return {"descriptor_copy", "sm120_tma_descriptor_copy_1d",
                ScenarioKind::DescriptorCopy, 1};
    }
    if (name == "im2col_load") {
        return {"im2col_load", "sm120_tma_im2col_load_3d",
                ScenarioKind::Im2colLoad, 3};
    }
    if (name == "im2col_store") {
        return {"im2col_store", "sm120_tma_im2col_store_3d",
                ScenarioKind::Im2colStore, 3};
    }
    if (name == "im2col_wide") {
        return {"im2col_wide", "sm120_tma_im2col_wide_load_3d",
                ScenarioKind::Im2colWideLoad, 3};
    }
    if (name == "multicast") {
        return {"multicast", "sm120_tma_multicast_5d",
                ScenarioKind::Multicast, 5, 512, 1, 2};
    }
    struct OobNanScenario {
        std::string_view name;
        CUtensorMapDataType element_type;
        std::size_t element_bytes;
    };
    constexpr OobNanScenario oob_nan_scenarios[]{
        {"f16", CU_TENSOR_MAP_DATA_TYPE_FLOAT16, 2},
        {"f32", CU_TENSOR_MAP_DATA_TYPE_FLOAT32, 4},
        {"f64", CU_TENSOR_MAP_DATA_TYPE_FLOAT64, 8},
        {"bf16", CU_TENSOR_MAP_DATA_TYPE_BFLOAT16, 2},
        {"f32_ftz", CU_TENSOR_MAP_DATA_TYPE_FLOAT32_FTZ, 4},
        {"tf32", CU_TENSOR_MAP_DATA_TYPE_TFLOAT32, 4},
        {"tf32_ftz", CU_TENSOR_MAP_DATA_TYPE_TFLOAT32_FTZ, 4},
    };
    for (const auto& item : oob_nan_scenarios) {
        if (name == "oob_nan_" + std::string(item.name)) {
            return {std::string(name),
                    "sm120_tma_oob_nan_" + std::string(item.name) + "_1d",
                    ScenarioKind::OobNan, 1, 64 * item.element_bytes, 1, 1,
                    item.element_type};
        }
    }
    fail("unknown scenario " + std::string(name));
}

bool is_store(const Scenario& value)
{
    return value.kind == ScenarioKind::TiledStore ||
           value.kind == ScenarioKind::SourceReuse ||
           value.kind == ScenarioKind::Im2colStore;
}

bool uses_replacement(const Scenario& value)
{
    return value.kind == ScenarioKind::HostReplace ||
           value.kind == ScenarioKind::DeviceReplace ||
           value.kind == ScenarioKind::DescriptorCopy;
}

std::string read_ptx()
{
    std::ifstream input(HBFSIM_SM120_TMA_PTX_PATH, std::ios::binary);
    if (!input) fail("cannot open SM120 TMA PTX");
    return {std::istreambuf_iterator<char>(input), {}};
}

std::string transform(const std::string& ptx, const std::string& kernel)
{
    const json request{{"input",
                        {{"full_ptx", ptx},
                         {"to_patch_kernel", kernel},
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
        fail("TMA PTX pass did not modify kernel: " +
             response.value("error", "unspecified rejection"));
    }
    return response.at("output_ptx").get<std::string>();
}

CUmodule load_module(const std::string& image, bool instrumented)
{
    using begin_type = std::uint64_t (*)(const char*, std::size_t);
    auto begin = reinterpret_cast<begin_type>(
        dlsym(RTLD_DEFAULT, "hbfsim_begin_module_load_from_ptx"));
    if (instrumented &&
        (begin == nullptr || begin(image.data(), image.size()) == 0)) {
        fail("HBFSim module-load transaction was rejected");
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

std::string hex(std::span<const std::uint8_t> bytes)
{
    static constexpr char digits[] = "0123456789abcdef";
    std::string result(bytes.size() * 2, '0');
    for (std::size_t index = 0; index < bytes.size(); ++index) {
        result[index * 2] = digits[bytes[index] >> 4];
        result[index * 2 + 1] = digits[bytes[index] & 15];
    }
    return result;
}

void encode_tiled(CUtensorMap& map, void* base, std::uint32_t rank)
{
    const cuuint64_t dimensions[5][5]{{64, 0, 0, 0, 0},
                                      {16, 4, 0, 0, 0},
                                      {8, 4, 2, 0, 0},
                                      {4, 4, 2, 2, 0},
                                      {4, 2, 2, 2, 2}};
    const cuuint64_t strides[5][4]{{0, 0, 0, 0},
                                   {64, 0, 0, 0},
                                   {32, 128, 0, 0},
                                   {16, 64, 128, 0},
                                   {16, 32, 64, 128}};
    const cuuint32_t box[5][5]{{64, 0, 0, 0, 0},
                               {16, 4, 0, 0, 0},
                               {8, 4, 2, 0, 0},
                               {4, 4, 2, 2, 0},
                               {4, 2, 2, 2, 2}};
    const cuuint32_t element_strides[5]{1, 1, 1, 1, 1};
    if (rank == 0 || rank > 5) fail("invalid tiled TensorMap rank");
    const auto row = rank - 1;
    driver_check(cuTensorMapEncodeTiled(
                     &map, CU_TENSOR_MAP_DATA_TYPE_UINT32, rank, base,
                     dimensions[row], strides[row], box[row], element_strides,
                     CU_TENSOR_MAP_INTERLEAVE_NONE,
                     CU_TENSOR_MAP_SWIZZLE_NONE,
                     CU_TENSOR_MAP_L2_PROMOTION_NONE,
                     CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE),
                 "cuTensorMapEncodeTiled");
}

void encode_oob_nan(CUtensorMap& map, void* base,
                    CUtensorMapDataType element_type)
{
    const cuuint64_t dimensions[1]{64};
    const cuuint64_t strides[1]{0};
    const cuuint32_t box[1]{64};
    const cuuint32_t element_strides[1]{1};
    driver_check(cuTensorMapEncodeTiled(
                     &map, element_type, 1, base, dimensions, strides, box,
                     element_strides, CU_TENSOR_MAP_INTERLEAVE_NONE,
                     CU_TENSOR_MAP_SWIZZLE_NONE,
                     CU_TENSOR_MAP_L2_PROMOTION_NONE,
                     CU_TENSOR_MAP_FLOAT_OOB_FILL_NAN_REQUEST_ZERO_FMA),
                 "cuTensorMapEncodeTiled OOB NaN");
}

void encode_im2col(CUtensorMap& map, void* base)
{
    const cuuint64_t dimensions[3]{64, 1, 1};
    const cuuint64_t strides[2]{256, 256};
    const int lower[1]{0};
    const int upper[1]{0};
    const cuuint32_t element_strides[3]{1, 1, 1};
    driver_check(cuTensorMapEncodeIm2col(
                     &map, CU_TENSOR_MAP_DATA_TYPE_UINT32, 3, base,
                     dimensions, strides, lower, upper, 64, 1,
                     element_strides, CU_TENSOR_MAP_INTERLEAVE_NONE,
                     CU_TENSOR_MAP_SWIZZLE_NONE,
                     CU_TENSOR_MAP_L2_PROMOTION_NONE,
                     CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE),
                 "cuTensorMapEncodeIm2col");
}

void encode_im2col_wide(CUtensorMap& map, void* base)
{
    const cuuint64_t dimensions[3]{16, 4, 1};
    const cuuint64_t strides[2]{64, 256};
    const cuuint32_t element_strides[3]{1, 1, 1};
    driver_check(cuTensorMapEncodeIm2colWide(
                     &map, CU_TENSOR_MAP_DATA_TYPE_UINT32, 3, base,
                     dimensions, strides, 0, 0, 16, 4, element_strides,
                     CU_TENSOR_MAP_INTERLEAVE_NONE,
                     CU_TENSOR_MAP_IM2COL_WIDE_MODE_W,
                     CU_TENSOR_MAP_SWIZZLE_64B,
                     CU_TENSOR_MAP_L2_PROMOTION_NONE,
                     CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE),
                 "cuTensorMapEncodeIm2colWide");
}

void read_exact(int descriptor, std::span<std::uint8_t> output)
{
    std::size_t done = 0;
    while (done != output.size()) {
        const auto count = ::pread(descriptor, output.data() + done,
                                   output.size() - done,
                                   static_cast<off_t>(done));
        if (count <= 0) {
            fail("read capacity result: " + std::string(std::strerror(errno)));
        }
        done += static_cast<std::size_t>(count);
    }
}

}  // namespace

int main(int argc, char** argv)
{
    try {
        std::string mode;
        std::string backend;
        std::string profile;
        std::string report_dir;
        std::string output_path;
        std::string reference_path;
        std::string scenario_name = "load_2d";
        std::string backing_dir = std::filesystem::temp_directory_path();
        std::uint32_t iterations = 1;
        for (int index = 1; index < argc; index += 2) {
            if (index + 1 >= argc) fail("missing option value");
            const std::string key = argv[index];
            if (key == "--mode") mode = argv[index + 1];
            else if (key == "--backend") backend = argv[index + 1];
            else if (key == "--profile") profile = argv[index + 1];
            else if (key == "--report-dir") report_dir = argv[index + 1];
            else if (key == "--output") output_path = argv[index + 1];
            else if (key == "--reference") reference_path = argv[index + 1];
            else if (key == "--scenario") scenario_name = argv[index + 1];
            else if (key == "--backing-dir") backing_dir = argv[index + 1];
            else if (key == "--iterations") {
                const auto parsed = std::stoul(argv[index + 1]);
                if (parsed == 0 || parsed > 16) fail("invalid iteration count");
                iterations = static_cast<std::uint32_t>(parsed);
            } else fail("unknown option " + key);
        }
        const auto test = scenario(scenario_name);
        const bool instrumented = mode == "instrumented";
        if ((mode != "native" && !instrumented) ||
            (backend != "hbm" && backend != "timing" &&
             backend != "capacity") ||
            (mode == "native" && backend != "hbm") ||
            output_path.empty() ||
            (instrumented && (profile.empty() || report_dir.empty() ||
                              reference_path.empty()))) {
            fail("invalid benchmark configuration");
        }

        cuda_check(cudaFree(nullptr), "initialize CUDA runtime");
        driver_check(cuInit(0), "cuInit");

        constexpr std::size_t allocation_bytes = 4096;
        constexpr std::size_t tile_bytes = 256;
        std::array<std::uint8_t, allocation_bytes> input{};
        std::array<std::uint8_t, allocation_bytes> replacement{};
        for (std::size_t index = 0; index < input.size(); ++index) {
            input[index] = static_cast<std::uint8_t>((index * 29 + 7) & 0xff);
            replacement[index] =
                static_cast<std::uint8_t>((index * 17 + 0xb3) & 0xff);
        }
        if (test.kind == ScenarioKind::OobNan &&
            (test.element_type == CU_TENSOR_MAP_DATA_TYPE_FLOAT32_FTZ ||
             test.element_type == CU_TENSOR_MAP_DATA_TYPE_TFLOAT32 ||
             test.element_type == CU_TENSOR_MAP_DATA_TYPE_TFLOAT32_FTZ)) {
            constexpr std::uint32_t conversion_oracle[16]{
                0x00000000U, 0x80000000U, 0x00000001U, 0x00002000U,
                0x80002000U, 0x007fffffU, 0x00800000U, 0x3f801000U,
                0x3f803000U, 0x7f7fffffU, 0x7f800000U, 0xff800000U,
                0x7fc12345U, 0x7fa12345U, 0xffc12345U, 0xffa12345U,
            };
            std::memcpy(input.data() + 48 * sizeof(std::uint32_t),
                        conversion_oracle, sizeof(conversion_oracle));
        }

        void* device_source = nullptr;
        void* device_replacement = nullptr;
        void* device_tensor = nullptr;
        std::uint8_t* device_output = nullptr;
        std::uint64_t* device_stamps = nullptr;
        CUtensorMap* device_map_a = nullptr;
        CUtensorMap* device_map_b = nullptr;
        cuda_check(cudaMalloc(&device_source, allocation_bytes),
                   "allocate source");
        cuda_check(cudaMalloc(&device_replacement, allocation_bytes),
                   "allocate replacement");
        cuda_check(cudaMalloc(&device_output, 512), "allocate output");
        cuda_check(cudaMalloc(&device_stamps, 4 * sizeof(std::uint64_t)),
                   "allocate stamps");
        cuda_check(cudaMemcpy(device_source, input.data(), input.size(),
                              cudaMemcpyHostToDevice), "initialize source");
        cuda_check(cudaMemcpy(device_replacement, replacement.data(),
                              replacement.size(), cudaMemcpyHostToDevice),
                   "initialize replacement");
        cuda_check(cudaMemset(device_output, 0, 512), "clear output");
        cuda_check(cudaMemset(device_stamps, 0, 4 * sizeof(std::uint64_t)),
                   "clear stamps");

        hbfsim_context* context = nullptr;
        void* registered_hbf = nullptr;
        std::string backing_path;
        int backing_descriptor = -1;
        if (instrumented) {
            const hbfsim_options options{
                .profile_path = profile.c_str(),
                .report_dir = report_dir.c_str(),
                .mode = static_cast<std::uint32_t>(
                    backend == "capacity" ? HBFSIM_MODEL_REFERENCE
                                            : HBFSIM_MODEL_FAST),
                .ring_capacity = 64,
                .request_timeout_ns = 5'000'000'000ULL,
            };
            if (hbfsim_context_create(&options, &context) != HBFSIM_OK) {
                fail("hbfsim_context_create failed");
            }
        }

        if (backend == "capacity") {
            std::filesystem::create_directories(backing_dir);
            backing_path = backing_dir + "/hbfsim-sm120-tma-XXXXXX";
            std::vector<char> path(backing_path.begin(), backing_path.end());
            path.push_back('\0');
            backing_descriptor = ::mkstemp(path.data());
            std::array<std::uint8_t, allocation_bytes> initial{};
            if (!is_store(test)) initial = input;
            if (uses_replacement(test)) {
                std::copy_n(replacement.begin(), tile_bytes,
                            initial.begin() + 512);
            }
            if (backing_descriptor < 0 ||
                ::ftruncate(backing_descriptor, allocation_bytes) != 0 ||
                ::pwrite(backing_descriptor, initial.data(), initial.size(), 0) !=
                    static_cast<ssize_t>(initial.size())) {
                fail("initialize TMA capacity backing failed");
            }
            backing_path = path.data();
            const hbfsim_range_options range{
                .mode = HBFSIM_RANGE_MODE_CAPACITY,
                .permissions = HBFSIM_RANGE_READ_WRITE,
                .cache_policy = HBFSIM_CACHE_POLICY_NONE,
                .stream_id = 0,
            };
            if (hbfsim_map_file(context, backing_path.c_str(), 0,
                                allocation_bytes, &range, &device_tensor) !=
                HBFSIM_OK) {
                fail("hbfsim_map_file failed");
            }
            registered_hbf = device_tensor;
        } else if (is_store(test)) {
            cuda_check(cudaMalloc(&device_tensor, allocation_bytes),
                       "allocate tensor target");
            cuda_check(cudaMemset(device_tensor, 0, allocation_bytes),
                       "clear tensor target");
        } else {
            device_tensor = device_source;
        }

        if (backend == "timing") {
            void* classified = uses_replacement(test) ? device_replacement
                                                       : device_tensor;
            std::size_t classified_offset = tile_bytes / 2;
            std::size_t classified_bytes = tile_bytes / 2;
            if (test.kind == ScenarioKind::OobNan) {
                const auto element_bytes = test.output_bytes / 64;
                const auto in_bounds_bytes = 16 * element_bytes;
                classified_offset = 48 * element_bytes + in_bounds_bytes / 2;
                classified_bytes = in_bounds_bytes / 2;
            }
            registered_hbf = static_cast<std::byte*>(classified) +
                             classified_offset;
            const hbfsim_range_options range{
                .mode = HBFSIM_RANGE_MODE_TIMING,
                .permissions = HBFSIM_RANGE_READ_WRITE,
                .cache_policy = HBFSIM_CACHE_POLICY_NONE,
                .stream_id = 0,
            };
            if (hbfsim_register_device(context, registered_hbf,
                                       classified_bytes, &range) != HBFSIM_OK) {
                fail("hbfsim_register_device failed");
            }
        }

        void* tensor_replacement =
            backend == "capacity"
                ? static_cast<void*>(static_cast<std::byte*>(device_tensor) +
                                     512)
                : device_replacement;
        alignas(64) CUtensorMap tensor_map{};
        if (test.kind == ScenarioKind::OobNan) {
            encode_oob_nan(tensor_map, device_tensor, test.element_type);
        } else if (test.kind == ScenarioKind::Im2colLoad ||
            test.kind == ScenarioKind::Im2colStore) {
            encode_im2col(tensor_map, device_tensor);
        } else if (test.kind == ScenarioKind::Im2colWideLoad) {
            encode_im2col_wide(tensor_map, device_tensor);
        } else {
            encode_tiled(tensor_map, device_tensor, test.rank);
        }
        if (test.kind == ScenarioKind::HostReplace) {
            driver_check(cuTensorMapReplaceAddress(&tensor_map,
                                                   tensor_replacement),
                         "cuTensorMapReplaceAddress");
        }
        if (test.kind == ScenarioKind::DeviceReplace ||
            test.kind == ScenarioKind::DescriptorCopy) {
            cuda_check(cudaMalloc(&device_map_a, sizeof(CUtensorMap)),
                       "allocate source TensorMap");
            cuda_check(cudaMemcpy(device_map_a, &tensor_map,
                                  sizeof(CUtensorMap), cudaMemcpyHostToDevice),
                       "initialize source TensorMap");
        }
        if (test.kind == ScenarioKind::DescriptorCopy) {
            cuda_check(cudaMalloc(&device_map_b, sizeof(CUtensorMap)),
                       "allocate destination TensorMap");
            cuda_check(cudaMemset(device_map_b, 0, sizeof(CUtensorMap)),
                       "clear destination TensorMap");
        }

        const auto original = read_ptx();
        const auto image = instrumented ? transform(original, test.kernel)
                                        : original;
        CUmodule module = load_module(image, instrumented);
        CUfunction function = nullptr;
        driver_check(cuModuleGetFunction(&function, module,
                                         test.kernel.c_str()),
                     "cuModuleGetFunction");
        constexpr std::uint64_t seed = 0x2468ace013579bdfULL;
        auto mutable_seed = seed;
        void* load_arguments[]{&tensor_map, &device_output, &device_stamps,
                               &mutable_seed};
        void* store_arguments[]{&tensor_map, &device_source, &device_stamps,
                                &mutable_seed};
        void* device_replace_arguments[]{&device_map_a, &tensor_replacement,
                                          &device_output, &device_stamps,
                                          &mutable_seed};
        void* descriptor_copy_arguments[]{&device_map_a, &device_map_b,
                                           &tensor_replacement,
                                           &device_output, &device_stamps,
                                           &mutable_seed};
        void** arguments = is_store(test) ? store_arguments : load_arguments;
        if (test.kind == ScenarioKind::DeviceReplace) {
            arguments = device_replace_arguments;
        } else if (test.kind == ScenarioKind::DescriptorCopy) {
            arguments = descriptor_copy_arguments;
        }
        std::vector<hbfsim_stats> iteration_runtime;
        for (std::uint32_t iteration = 0; iteration < iterations; ++iteration) {
            driver_check(cuLaunchKernel(function,
                                        test.kind == ScenarioKind::Multicast
                                            ? 2 : 1,
                                        1, 1,
                                        test.kind ==
                                                ScenarioKind::DescriptorCopy
                                            ? 32
                                            : 1,
                                        1, 1, 0, nullptr, arguments,
                                        nullptr), "launch TMA kernel");
            cuda_check(cudaDeviceSynchronize(), "synchronize TMA kernel");
            hbfsim_stats sample{};
            if (context != nullptr && hbfsim_get_stats(context, &sample) !=
                                          HBFSIM_OK) {
                fail("hbfsim_get_stats failed");
            }
            iteration_runtime.push_back(sample);
        }

        std::vector<std::uint8_t> result(test.output_bytes);
        std::array<std::uint64_t, 4> stamps{};
        if (is_store(test)) {
            if (backend == "capacity") {
                if (hbfsim_flush(context) != HBFSIM_OK) {
                    fail("hbfsim_flush failed");
                }
                read_exact(backing_descriptor, result);
            } else {
                cuda_check(cudaMemcpy(result.data(), device_tensor,
                                      result.size(), cudaMemcpyDeviceToHost),
                           "copy TMA store output");
            }
        } else {
            cuda_check(cudaMemcpy(result.data(), device_output, result.size(),
                                  cudaMemcpyDeviceToHost),
                       "copy TMA load output");
        }
        cuda_check(cudaMemcpy(stamps.data(), device_stamps, sizeof(stamps),
                              cudaMemcpyDeviceToHost), "copy timestamps");

        const auto result_hex = hex(result);
        bool bit_exact = true;
        std::string reference_source = "self_checked_native";
        if (instrumented) {
            std::ifstream reference_input(reference_path);
            if (!reference_input) fail("cannot open native reference report");
            const auto reference = json::parse(reference_input);
            if (reference.at("scenario") != test.name ||
                reference.at("mode") != "native") {
                fail("native reference scenario differs");
            }
            bit_exact = reference.at("output_hex").get<std::string>() ==
                        result_hex;
            reference_source = reference_path;
        } else if (test.kind == ScenarioKind::OobNan) {
            const auto element_bytes = test.output_bytes / 64;
            const auto prefix_bytes = 16 * element_bytes;
            const auto pattern = std::span(result).subspan(prefix_bytes,
                                                           element_bytes);
            bit_exact = test.element_type ==
                            CU_TENSOR_MAP_DATA_TYPE_FLOAT32_FTZ ||
                        test.element_type == CU_TENSOR_MAP_DATA_TYPE_TFLOAT32 ||
                        test.element_type ==
                            CU_TENSOR_MAP_DATA_TYPE_TFLOAT32_FTZ ||
                        std::equal(result.begin(),
                                   result.begin() + prefix_bytes,
                                   input.begin() + 48 * element_bytes);
            for (std::size_t offset = prefix_bytes;
                 bit_exact && offset < result.size(); offset += element_bytes) {
                bit_exact = std::equal(pattern.begin(), pattern.end(),
                                       result.begin() + offset);
            }
            reference_source = "native_hardware_oracle";
        } else if (test.kind != ScenarioKind::Im2colWideLoad) {
            std::vector<std::uint8_t> expected;
            if (test.kind == ScenarioKind::OobZero) {
                expected.insert(expected.end(), input.begin() + 192,
                                input.begin() + 256);
                expected.resize(256, 0);
            } else if (test.kind == ScenarioKind::Multicast) {
                expected.insert(expected.end(), input.begin(),
                                input.begin() + 256);
                expected.insert(expected.end(), input.begin(),
                                input.begin() + 256);
            } else {
                const auto& source = uses_replacement(test) ? replacement
                                                            : input;
                expected.assign(source.begin(), source.begin() + 256);
            }
            bit_exact = result == expected;
        } else {
            reference_source = "native_hardware_oracle";
        }

        auto expected_checksum = seed;
        const auto work_count = test.kind == ScenarioKind::PhaseReuse ? 2U : 1U;
        if (test.kind == ScenarioKind::Multicast) expected_checksum ^= 1U;
        for (std::uint32_t count = 0; count < work_count; ++count) {
            expected_checksum = independent_work(expected_checksum);
        }
        hbfsim_tma_stats stats{};
        if (context != nullptr && hbfsim_get_tma_stats(context, &stats) !=
                                      HBFSIM_OK) {
            fail("hbfsim_get_tma_stats failed");
        }
        hbfsim_stats final_runtime{};
        if (context != nullptr && hbfsim_get_stats(context, &final_runtime) !=
                                      HBFSIM_OK) {
            fail("final hbfsim_get_stats failed");
        }
        json runtime_samples = json::array();
        for (const auto& sample : iteration_runtime) {
            runtime_samples.push_back(
                {{"requests_submitted", sample.requests_submitted},
                 {"requests_completed", sample.requests_completed},
                 {"fast_requests", sample.fast_requests},
                 {"reference_requests", sample.reference_requests},
                 {"fast_modeled_ns", sample.fast_modeled_ns},
                 {"capacity_cache_hits", sample.capacity_cache_hits},
                 {"capacity_cache_misses", sample.capacity_cache_misses},
                 {"capacity_dirty_writebacks",
                  sample.capacity_dirty_writebacks}});
        }
        std::string oob_fill_pattern_hex;
        if (test.kind == ScenarioKind::OobNan) {
            const auto element_bytes = test.output_bytes / 64;
            oob_fill_pattern_hex = hex(std::span(result).subspan(
                16 * element_bytes, element_bytes));
        }
        const json report{
            {"schema_version", 2},
            {"scenario", test.name},
            {"kernel", test.kernel},
            {"mode", mode},
            {"backend", backend},
            {"iterations", iterations},
            {"bit_exact", bit_exact},
            {"reference_source", reference_source},
            {"output_hex", result_hex},
            {"oob_fill_pattern_hex", oob_fill_pattern_hex},
            {"timestamps", {{"issue", stamps[0]},
                             {"independent_end", stamps[1]},
                             {"wait_end", stamps[2]}}},
            {"independent_checksum", stamps[3]},
            {"expected_independent_checksum", expected_checksum},
            {"iteration_runtime", runtime_samples},
            {"final_runtime",
             {{"requests_submitted", final_runtime.requests_submitted},
              {"requests_completed", final_runtime.requests_completed},
              {"capacity_cache_hits", final_runtime.capacity_cache_hits},
              {"capacity_cache_misses", final_runtime.capacity_cache_misses},
              {"capacity_dirty_writebacks",
               final_runtime.capacity_dirty_writebacks}}},
            {"tma", {{"issued", stats.issued},
                     {"hbm_bytes", stats.hbm_bytes},
                     {"hbf_bytes", stats.hbf_bytes},
                     {"oob_bytes", stats.oob_bytes},
                     {"fanout_targets", stats.fanout_targets},
                     {"barrier_wait_ns", stats.barrier_wait_ns},
                     {"group_wait_ns", stats.group_wait_ns},
                     {"stale_generations", stats.stale_generations},
                     {"faults", stats.faults},
                     {"leaked", stats.leaked}}},
        };
        std::ofstream output(output_path);
        output << report.dump(2) << '\n';
        if (!output) fail("write output failed");

        driver_check(cuModuleUnload(module), "cuModuleUnload");
        if (context != nullptr) {
            if (registered_hbf != nullptr) {
                (void)hbfsim_unregister(context, registered_hbf);
            }
            hbfsim_context_destroy(context);
        }
        if (device_map_b != nullptr) cuda_check(cudaFree(device_map_b), "free destination TensorMap");
        if (device_map_a != nullptr) cuda_check(cudaFree(device_map_a), "free source TensorMap");
        cuda_check(cudaFree(device_stamps), "free stamps");
        cuda_check(cudaFree(device_output), "free output");
        if (device_tensor != nullptr && device_tensor != device_source &&
            backend != "capacity") {
            cuda_check(cudaFree(device_tensor), "free tensor target");
        }
        cuda_check(cudaFree(device_replacement), "free replacement");
        cuda_check(cudaFree(device_source), "free source");
        if (backing_descriptor >= 0) (void)::close(backing_descriptor);
        if (!backing_path.empty()) (void)::unlink(backing_path.c_str());
        return bit_exact ? 0 : 2;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "sm120_tma_bench: %s\n", error.what());
        return 70;
    }
}

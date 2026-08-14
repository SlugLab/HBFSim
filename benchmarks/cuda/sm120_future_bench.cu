#include <hbfsim/api.h>

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <json.hpp>

#include <array>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

extern "C" int process_input(const char*, int, char*);

namespace {

using json = nlohmann::json;

struct Options {
    std::string mode;
    std::string backend;
    std::string profile;
    std::string report_dir;
    std::string output;
    std::string backing_dir{std::filesystem::temp_directory_path()};
};

struct Allocation {
    hbfsim_context* context{nullptr};
    void* data{nullptr};
    std::uint64_t* output{nullptr};
    std::uint64_t* stamps{nullptr};
    std::string backing;
    bool capacity{false};
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

Options parse_options(int argc, char** argv)
{
    Options result;
    for (int index = 1; index < argc; ++index) {
        if (index + 1 >= argc) fail("missing option value");
        const std::string key = argv[index];
        const std::string value = argv[++index];
        if (key == "--mode") result.mode = value;
        else if (key == "--backend") result.backend = value;
        else if (key == "--profile") result.profile = value;
        else if (key == "--report-dir") result.report_dir = value;
        else if (key == "--output") result.output = value;
        else if (key == "--backing-dir") result.backing_dir = value;
        else fail("unknown option " + key);
    }
    if ((result.mode != "native" && result.mode != "synchronous" &&
         result.mode != "future") ||
        (result.backend != "hbm" && result.backend != "timing" &&
         result.backend != "capacity") ||
        result.profile.empty() || result.report_dir.empty() ||
        result.output.empty() ||
        (result.mode == "native" && result.backend != "hbm")) {
        fail("invalid benchmark configuration");
    }
    return result;
}

std::uint64_t independent_work(std::uint64_t value)
{
    for (int index = 0; index < 512; ++index) {
        value ^= value << 13;
        value ^= value >> 7;
        value ^= value << 17;
    }
    return value;
}

std::array<std::uint64_t, 8> initial_data()
{
    return {0x0123456789abcdefULL, 0x1111222233334444ULL, 100,
            0x3333444455556666ULL, 0xabcddcba01234567ULL,
            0x8877665544332211ULL, 0x777788889999aaaaULL,
            0xbbbbccccddddeeeeULL};
}

std::string prepare_backing(const Options& options)
{
    std::filesystem::create_directories(options.backing_dir);
    std::string pattern = options.backing_dir + "/hbfsim-sm120-future-XXXXXX";
    std::vector<char> path(pattern.begin(), pattern.end());
    path.push_back('\0');
    const int descriptor = ::mkstemp(path.data());
    if (descriptor < 0) fail("mkstemp failed");
    constexpr std::size_t bytes = 16 * 1024;
    const auto data = initial_data();
    bool valid = ::ftruncate(descriptor, bytes) == 0 &&
                 ::pwrite(descriptor, data.data(), sizeof(data), 0) ==
                     static_cast<ssize_t>(sizeof(data));
    if (::close(descriptor) != 0) valid = false;
    if (!valid) {
        ::unlink(path.data());
        fail("capacity backing initialization failed");
    }
    return path.data();
}

Allocation allocate(const Options& options)
{
    Allocation result;
    cuda_check(cudaFree(nullptr), "initialize CUDA");
    cuda_check(cudaMalloc(&result.output, 2 * sizeof(std::uint64_t)),
               "cudaMalloc output");
    cuda_check(cudaMalloc(&result.stamps, 3 * sizeof(std::uint64_t)),
               "cudaMalloc stamps");
    if (options.mode != "native") {
        const hbfsim_options context_options{
            .profile_path = options.profile.c_str(),
            .report_dir = options.report_dir.c_str(),
            .mode = static_cast<std::uint32_t>(
                options.backend == "capacity" ? HBFSIM_MODEL_REFERENCE
                                               : HBFSIM_MODEL_FAST),
            .ring_capacity = 64,
            .request_timeout_ns = 5'000'000'000ULL,
        };
        if (hbfsim_context_create(&context_options, &result.context) !=
            HBFSIM_OK) {
            fail("hbfsim_context_create failed");
        }
    }
    if (options.backend == "capacity") {
        result.backing = prepare_backing(options);
        const hbfsim_range_options range{
            .mode = HBFSIM_RANGE_MODE_CAPACITY,
            .permissions = HBFSIM_RANGE_READ_WRITE,
            .cache_policy = HBFSIM_CACHE_POLICY_NONE,
            .stream_id = 0,
        };
        const auto status = hbfsim_map_file(
            result.context, result.backing.c_str(), 0, 16 * 1024, &range,
            &result.data);
        if (status != HBFSIM_OK) {
            fail("hbfsim_map_file failed with status " +
                 std::to_string(status));
        }
    } else {
        cuda_check(cudaMalloc(&result.data, 16 * 1024), "cudaMalloc data");
        const auto data = initial_data();
        cuda_check(cudaMemcpy(result.data, data.data(), sizeof(data),
                              cudaMemcpyHostToDevice), "initialize data");
        if (options.backend == "timing") {
            const hbfsim_range_options range{
                .mode = HBFSIM_RANGE_MODE_TIMING,
                .permissions = HBFSIM_RANGE_READ_WRITE,
                .cache_policy = HBFSIM_CACHE_POLICY_NONE,
                .stream_id = 0,
            };
            if (hbfsim_register_device(result.context, result.data, 16 * 1024,
                                       &range) != HBFSIM_OK) {
                fail("hbfsim_register_device failed");
            }
        }
    }
    return result;
}

void release(Allocation& allocation)
{
    if (allocation.context != nullptr && allocation.data != nullptr) {
        (void)hbfsim_unregister(allocation.context, allocation.data);
    }
    if (allocation.context != nullptr) {
        hbfsim_context_destroy(allocation.context);
        allocation.context = nullptr;
    }
    if (!allocation.capacity && allocation.data != nullptr) {
        (void)cudaFree(allocation.data);
    }
    allocation.data = nullptr;
    if (allocation.output != nullptr) (void)cudaFree(allocation.output);
    if (allocation.stamps != nullptr) (void)cudaFree(allocation.stamps);
    allocation.output = nullptr;
    allocation.stamps = nullptr;
    if (!allocation.backing.empty()) {
        ::unlink(allocation.backing.c_str());
        allocation.backing.clear();
    }
}

std::string read_ptx()
{
    std::ifstream input(HBFSIM_SM120_FUTURE_PTX_PATH, std::ios::binary);
    if (!input) fail("cannot open SM120 future PTX");
    return {std::istreambuf_iterator<char>(input), {}};
}

std::string transform(const std::string& ptx, const std::string& kernel,
                      bool async_futures)
{
    const json request{{"input",
                        {{"full_ptx", ptx},
                         {"to_patch_kernel", kernel},
                         {"async_futures", async_futures}}},
                       {"ebpf_instructions", json::array()}};
    std::vector<char> output(64U << 20);
    const auto encoded = request.dump();
    const auto status = process_input(encoded.c_str(),
                                      static_cast<int>(output.size()),
                                      output.data());
    if (status != 0) {
        fail("PTX pass failed with status " + std::to_string(status));
    }
    const auto response = json::parse(output.data());
    if (!response.at("modified").get<bool>()) {
        fail("PTX pass did not instrument selected kernel");
    }
    return response.at("output_ptx").get<std::string>();
}

CUmodule load_module(const std::string& original, const std::string& image,
                     bool instrumented)
{
    using begin_type = std::uint64_t (*)(const char*, std::size_t);
    using end_type = void (*)(std::uint64_t);
    auto begin = reinterpret_cast<begin_type>(
        dlsym(RTLD_DEFAULT, "hbfsim_begin_module_load_from_ptx"));
    auto end = reinterpret_cast<end_type>(
        dlsym(RTLD_DEFAULT, "hbfsim_end_module_load"));
    std::uint64_t token = 0;
    if (instrumented) {
        if (begin == nullptr || end == nullptr) {
            fail("HBFSim launch gate is not preloaded");
        }
        token = begin(image.data(), image.size());
        if (token == 0) fail("module load transaction was rejected");
    }
    CUmodule module = nullptr;
    const auto status = cuModuleLoadDataEx(&module, image.c_str(), 0, nullptr,
                                           nullptr);
    if (status != CUDA_SUCCESS && token != 0) end(token);
    driver_check(status, "cuModuleLoadDataEx");
    return module;
}

hbfsim_future_stats future_stats(hbfsim_context* context)
{
    hbfsim_future_stats result{};
    if (context != nullptr && hbfsim_get_future_stats(context, &result) !=
                                  HBFSIM_OK) {
        fail("hbfsim_get_future_stats failed");
    }
    return result;
}

hbfsim_stats model_stats(hbfsim_context* context)
{
    hbfsim_stats result{};
    if (context != nullptr && hbfsim_get_stats(context, &result) !=
                                  HBFSIM_OK) {
        fail("hbfsim_get_stats failed");
    }
    return result;
}

json run_case(const Options& options, Allocation& allocation,
              const std::string& original_ptx, const std::string& name,
              const std::string& kernel, int enabled = 1)
{
    const bool instrumented = options.mode != "native";
    const auto image = instrumented
                           ? transform(original_ptx, kernel,
                                       options.mode == "future")
                           : original_ptx;
    const auto before = future_stats(allocation.context);
    const auto model_before = model_stats(allocation.context);
    CUmodule module = load_module(original_ptx, image, instrumented);
    CUfunction function = nullptr;
    driver_check(cuModuleGetFunction(&function, module, kernel.c_str()),
                 "cuModuleGetFunction");
    cuda_check(cudaMemset(allocation.output, 0, 2 * sizeof(std::uint64_t)),
               "clear output");
    cuda_check(cudaMemset(allocation.stamps, 0, 3 * sizeof(std::uint64_t)),
               "clear stamps");
    constexpr std::uint64_t seed = 0x2468ace013579bdfULL;
    void* arguments[5]{};
    if (kernel == "sm120_future_vector_branch") {
        arguments[0] = &allocation.data;
        arguments[1] = &enabled;
        auto mutable_seed = seed;
        arguments[2] = &mutable_seed;
        arguments[3] = &allocation.output;
        arguments[4] = &allocation.stamps;
        driver_check(cuLaunchKernel(function, 1, 1, 1, 1, 1, 1, 0, nullptr,
                                    arguments, nullptr), "launch vector");
    } else {
        auto mutable_seed = seed;
        arguments[0] = &allocation.data;
        arguments[1] = &mutable_seed;
        arguments[2] = &allocation.output;
        arguments[3] = &allocation.stamps;
        driver_check(cuLaunchKernel(function, 1, 1, 1, 1, 1, 1, 0, nullptr,
                                    arguments, nullptr), "launch ordinary");
    }
    cuda_check(cudaDeviceSynchronize(), "synchronize future kernel");
    std::array<std::uint64_t, 2> output{};
    std::array<std::uint64_t, 3> stamps{};
    cuda_check(cudaMemcpy(output.data(), allocation.output, sizeof(output),
                          cudaMemcpyDeviceToHost), "copy output");
    cuda_check(cudaMemcpy(stamps.data(), allocation.stamps, sizeof(stamps),
                          cudaMemcpyDeviceToHost), "copy stamps");
    const auto after = future_stats(allocation.context);
    const auto model_after = model_stats(allocation.context);
    driver_check(cuModuleUnload(module), "cuModuleUnload");
    return {
        {"name", name},
        {"output", output},
        {"timestamps", {{"issue", stamps[0]},
                         {"independent_end", stamps[1]},
                         {"dependency_wait_end", stamps[2]}}},
        {"wait_tail_ns", stamps[2] >= stamps[1]
                             ? stamps[2] - stamps[1] : 0},
        {"futures", {{"issued", after.issued - before.issued},
                      {"issue_throttle_ns",
                       after.issue_throttle_ns - before.issue_throttle_ns},
                      {"dependency_wait_ns",
                       after.dependency_wait_ns - before.dependency_wait_ns},
                      {"ordering_wait_ns",
                       after.ordering_wait_ns - before.ordering_wait_ns},
                      {"drained", after.drained - before.drained},
                      {"leaked", after.leaked},
                      {"faults", after.faults - before.faults}}},
        {"requests", {{"submitted", model_after.requests_submitted -
                                        model_before.requests_submitted},
                       {"completed", model_after.requests_completed -
                                        model_before.requests_completed},
                       {"fast", model_after.fast_requests -
                                   model_before.fast_requests},
                       {"reference", model_after.reference_requests -
                                        model_before.reference_requests}}},
    };
}

void validate_outputs(const std::vector<json>& cases)
{
    constexpr std::uint64_t seed = 0x2468ace013579bdfULL;
    const auto data = initial_data();
    const auto load_expected = data[0] ^ independent_work(seed);
    const auto store_expected = seed ^ 0x53544f5245ULL;
    const auto store_reused = independent_work(
        store_expected ^ 0x9e3779b97f4a7c15ULL);
    const auto atomic_independent = independent_work(seed ^ 0x41544f4d4943ULL);
    const auto acquire_independent = independent_work(seed ^ 0x41435152454cULL);
    const auto vector_independent = independent_work(seed ^ 0x564543544f52ULL);
    const std::array<std::array<std::uint64_t, 2>, 6> expected{{
        {load_expected, 0},
        {store_expected, store_reused},
        {100 ^ atomic_independent, 107},
        {data[0] ^ acquire_independent, data[3] + 11},
        {data[4] ^ data[5] ^ vector_independent, 0},
        {vector_independent, 0},
    }};
    if (cases.size() != expected.size()) fail("missing benchmark case");
    for (std::size_t index = 0; index < cases.size(); ++index) {
        const auto actual =
            cases[index].at("output").get<std::array<std::uint64_t, 2>>();
        if (actual != expected[index]) {
            fail("semantic mismatch in " +
                 cases[index].at("name").get<std::string>());
        }
    }
}

}  // namespace

int main(int argc, char** argv)
{
    Allocation allocation;
    try {
        int device_count = 0;
        if (cudaGetDeviceCount(&device_count) != cudaSuccess ||
            device_count == 0) {
            return 77;
        }
        cudaDeviceProp properties{};
        cuda_check(cudaGetDeviceProperties(&properties, 0),
                   "cudaGetDeviceProperties");
        if (properties.major != 12 || properties.minor != 0) return 77;
        const auto options = parse_options(argc, argv);
        std::filesystem::create_directories(options.report_dir);
        allocation = allocate(options);
        allocation.capacity = options.backend == "capacity";
        const auto ptx = read_ptx();
        std::vector<json> cases;
        cases.push_back(run_case(options, allocation, ptx, "load",
                                 "sm120_future_load"));
        cases.push_back(run_case(options, allocation, ptx, "store_fence",
                                 "sm120_future_store"));
        cases.push_back(run_case(options, allocation, ptx, "atomic",
                                 "sm120_future_atomic"));
        cases.push_back(run_case(options, allocation, ptx,
                                 "atomic_acq_rel_unused",
                                 "sm120_future_atomic_acq_rel_unused"));
        cases.push_back(run_case(options, allocation, ptx, "vector_branch_true",
                                 "sm120_future_vector_branch", 1));
        cases.push_back(run_case(options, allocation, ptx,
                                 "vector_branch_false",
                                 "sm120_future_vector_branch", 0));
        validate_outputs(cases);
        const auto final = future_stats(allocation.context);
        if (final.leaked != 0 || final.faults != 0) {
            fail("future conservation failed");
        }
        const json report{
            {"schema_version", 1},
            {"gpu", properties.name},
            {"compute_capability", "12.0"},
            {"mode", options.mode},
            {"backend", options.backend},
            {"cases", cases},
            {"future_totals", {{"issued", final.issued},
                               {"drained", final.drained},
                               {"leaked", final.leaked},
                               {"faults", final.faults}}},
            {"coverage", {{"unsafe_launches", 0}}},
        };
        std::ofstream output(options.output);
        if (!output) fail("cannot open report output");
        output << report.dump(2) << '\n';
        release(allocation);
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "sm120_future_bench: %s\n", error.what());
        release(allocation);
        return 1;
    }
}

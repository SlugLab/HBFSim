#include "hbfsim/coverage.hpp"

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <dlfcn.h>

#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <utility>

namespace {

const char* environment_or(const char* name, const char* fallback)
{
    const char* value = std::getenv(name);
    return value != nullptr && value[0] != '\0' ? value : fallback;
}

class RuntimeGate {
  public:
    RuntimeGate()
        : writer_(environment_or("HBFSIM_COVERAGE_PATH", "coverage.json"))
    {
    }

    hbfsim::CoverageGate& gate() { return gate_; }
    std::shared_lock<std::shared_mutex> launch_guard()
    {
        return range_launch_sync_.launch_guard();
    }
    void add_range(std::uintptr_t begin, std::uintptr_t end)
    {
        auto lock = range_launch_sync_.registration_guard();
        gate_.add_range(begin, end);
    }

    void refresh_manifests()
    {
        std::scoped_lock lock(manifest_mutex_);
        const char* path = std::getenv("HBFSIM_PASS_MANIFEST_PATH");
        if (path == nullptr || path[0] == '\0') {
            return;
        }
        std::ifstream input(path);
        std::string line;
        while (std::getline(input, line)) {
            try {
                gate_.add_module(hbfsim::module_manifest_from_json(line));
            } catch (const std::exception&) {
                // A concurrent pass may still be appending its last line.
            }
        }
    }

    bool approve(const hbfsim::GateDecision& decision) noexcept
    {
        const bool approved =
            hbfsim::coverage_decision_permits_launch(writer_, decision);
        if (!approved && decision.allowed) {
            std::cerr << "hbfsim coverage writer failed; rejecting launch\n";
        }
        return approved;
    }

  private:
    hbfsim::CoverageGate gate_;
    hbfsim::CoverageWriter writer_;
    std::mutex manifest_mutex_;
    hbfsim::LaunchRangeSynchronizer range_launch_sync_;
};

RuntimeGate& runtime_gate()
{
    static RuntimeGate runtime;
    return runtime;
}

void* driver_symbol(const char* name)
{
    static void* driver = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
    return driver == nullptr ? nullptr : dlsym(driver, name);
}

std::string handle_id(CUfunction function)
{
    using get_module_type = CUresult (*)(CUmodule*, CUfunction);
    static auto get_module =
        reinterpret_cast<get_module_type>(driver_symbol("cuFuncGetModule"));
    CUmodule module = nullptr;
    if (get_module == nullptr ||
        get_module(&module, function) != CUDA_SUCCESS) {
        return "cuda-module:unknown";
    }
    using get_global_type =
        CUresult (*)(CUdeviceptr*, std::size_t*, CUmodule, const char*);
    using copy_type = CUresult (*)(void*, CUdeviceptr, std::size_t);
    static auto get_global = reinterpret_cast<get_global_type>(
        driver_symbol("cuModuleGetGlobal_v2"));
    static auto copy =
        reinterpret_cast<copy_type>(driver_symbol("cuMemcpyDtoH_v2"));
    CUdeviceptr address = 0;
    std::size_t size = 0;
    std::uint64_t marker = 0;
    if (get_global != nullptr && copy != nullptr &&
        get_global(&address, &size, module, "__hbfsim_module_marker") ==
            CUDA_SUCCESS &&
        size == sizeof(marker) &&
        copy(&marker, address, sizeof(marker)) == CUDA_SUCCESS) {
        return hbfsim::module_id_from_marker(marker);
    }
    return {};
}

std::string function_name(CUfunction function)
{
    using get_name_type = CUresult (*)(const char**, CUfunction);
    static auto get_name =
        reinterpret_cast<get_name_type>(driver_symbol("cuFuncGetName"));
    const char* name = nullptr;
    return get_name != nullptr && get_name(&name, function) == CUDA_SUCCESS &&
                   name != nullptr
               ? name
               : "unknown_cufunction";
}

CUfunction kernel_function(cudaKernel_t kernel)
{
    using get_function_type = CUresult (*)(CUfunction*, CUkernel);
    static auto get_function = reinterpret_cast<get_function_type>(
        driver_symbol("cuKernelGetFunction"));
    CUfunction function = nullptr;
    if (get_function != nullptr) {
        get_function(&function, reinterpret_cast<CUkernel>(kernel));
    }
    return function;
}

hbfsim::GateDecision unavailable(std::string module_id, std::string kernel)
{
    return {
        .allowed = false,
        .module_id = std::move(module_id),
        .kernel = std::move(kernel),
        .reason = "launch_metadata_unavailable",
        .operation = "opaque_pointer_access",
    };
}

template <typename Handle, typename GetInfo>
hbfsim::GateDecision
inspect_bounded(Handle handle, std::string runtime_module_id,
                std::string kernel, void** arguments, GetInfo get_info)
{
    auto& runtime = runtime_gate();
    runtime.refresh_manifests();
    if (arguments == nullptr || get_info == nullptr) {
        return runtime.gate().has_ranges()
                   ? unavailable(std::move(runtime_module_id),
                                 std::move(kernel))
                   : hbfsim::GateDecision{.allowed = true,
                                          .module_id =
                                              std::move(runtime_module_id),
                                          .kernel = std::move(kernel)};
    }

    hbfsim::KernelLaunch launch{.module_id = runtime_module_id,
                                .kernel = kernel};
    for (std::size_t index = 0;; ++index) {
        std::size_t offset = 0;
        std::size_t width = 0;
        const auto status = get_info(handle, index, &offset, &width);
        if (status == CUDA_ERROR_INVALID_VALUE) {
            break;
        }
        if (status != CUDA_SUCCESS) {
            return runtime.gate().has_ranges()
                       ? unavailable(std::move(runtime_module_id),
                                     std::move(kernel))
                       : hbfsim::GateDecision{.allowed = true,
                                              .module_id =
                                                  std::move(runtime_module_id),
                                              .kernel = std::move(kernel)};
        }
        hbfsim::LaunchParameter parameter{
            .index = index,
            .offset = offset,
            .width = width,
            .opaque_aggregate = width > sizeof(std::uintptr_t),
        };
        // CUDA guarantees arguments[index] points to exactly width bytes. Only
        // a single pointer-width scalar is read. Wider values are opaque and
        // fail closed while any HBF range is active; no aggregate slot
        // guessing.
        if (width == sizeof(std::uintptr_t) && arguments[index] != nullptr) {
            std::uintptr_t value = 0;
            std::memcpy(&value, arguments[index], sizeof(value));
            parameter.slots.push_back({.offset = 0, .value = value});
        }
        launch.parameters.push_back(std::move(parameter));
    }
    auto decision = runtime.gate().check_launch(launch);
    if (decision.module_id.empty()) {
        decision.module_id = std::move(runtime_module_id);
    }
    return decision;
}

hbfsim::GateDecision inspect_function_launch(CUfunction function,
                                             void** arguments, void** extra)
{
    const auto module_id = handle_id(function);
    const auto kernel = function_name(function);
    if (extra != nullptr) {
        return runtime_gate().gate().has_ranges()
                   ? unavailable(module_id, kernel)
                   : hbfsim::GateDecision{.allowed = true,
                                          .module_id = module_id,
                                          .kernel = kernel};
    }
    using get_info_type =
        CUresult (*)(CUfunction, std::size_t, std::size_t*, std::size_t*);
    static auto get_info =
        reinterpret_cast<get_info_type>(driver_symbol("cuFuncGetParamInfo"));
    return inspect_bounded(function, module_id, kernel, arguments, get_info);
}

hbfsim::GateDecision inspect_kernel_launch(cudaKernel_t kernel,
                                           void** arguments)
{
    const CUfunction function = kernel_function(kernel);
    if (function != nullptr) {
        return inspect_function_launch(function, arguments, nullptr);
    }
    using get_name_type = CUresult (*)(const char**, CUkernel);
    using get_info_type =
        CUresult (*)(CUkernel, std::size_t, std::size_t*, std::size_t*);
    static auto get_name =
        reinterpret_cast<get_name_type>(driver_symbol("cuKernelGetName"));
    static auto get_info =
        reinterpret_cast<get_info_type>(driver_symbol("cuKernelGetParamInfo"));
    const char* raw_name = nullptr;
    const std::string name =
        get_name != nullptr &&
                get_name(&raw_name, reinterpret_cast<CUkernel>(kernel)) ==
                    CUDA_SUCCESS &&
                raw_name != nullptr
            ? raw_name
            : "unknown_cukernel";
    return inspect_bounded(reinterpret_cast<CUkernel>(kernel),
                           "cuda-module:unknown", name, arguments, get_info);
}

hbfsim::GateDecision inspect_symbol_launch(const void* symbol, void** arguments)
{
    using get_function_type = cudaError_t (*)(cudaFunction_t*, const void*);
    static auto get_function = reinterpret_cast<get_function_type>(
        dlsym(RTLD_NEXT, "cudaGetFuncBySymbol"));
    cudaFunction_t runtime_function = nullptr;
    if (get_function == nullptr ||
        get_function(&runtime_function, symbol) != cudaSuccess ||
        runtime_function == nullptr) {
        return runtime_gate().gate().has_ranges()
                   ? unavailable("cuda-module:unknown",
                                 "unknown_runtime_kernel")
                   : hbfsim::GateDecision{.allowed = true,
                                          .module_id = "cuda-module:unknown",
                                          .kernel = "unknown_runtime_kernel"};
    }
    return inspect_function_launch(
        reinterpret_cast<CUfunction>(runtime_function), arguments, nullptr);
}

thread_local bool runtime_launch_in_progress = false;
struct RuntimeLaunchScope {
    RuntimeLaunchScope() { runtime_launch_in_progress = true; }
    ~RuntimeLaunchScope() { runtime_launch_in_progress = false; }
};

bool approve(const hbfsim::GateDecision& decision)
{
    return runtime_gate().approve(decision);
}

using driver_launch_type = CUresult (*)(CUfunction, unsigned int, unsigned int,
                                        unsigned int, unsigned int,
                                        unsigned int, unsigned int,
                                        unsigned int, CUstream, void**, void**);

CUresult driver_launch(const char* symbol, CUfunction function,
                       unsigned int grid_x, unsigned int grid_y,
                       unsigned int grid_z, unsigned int block_x,
                       unsigned int block_y, unsigned int block_z,
                       unsigned int shared_memory, CUstream stream,
                       void** parameters, void** extra)
{
    auto original =
        reinterpret_cast<driver_launch_type>(dlsym(RTLD_NEXT, symbol));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    if (runtime_launch_in_progress) {
        return original(function, grid_x, grid_y, grid_z, block_x, block_y,
                        block_z, shared_memory, stream, parameters, extra);
    }
    auto launch_guard = runtime_gate().launch_guard();
    const auto decision = inspect_function_launch(function, parameters, extra);
    if (!approve(decision)) {
        return CUDA_ERROR_NOT_SUPPORTED;
    }
    return original(function, grid_x, grid_y, grid_z, block_x, block_y, block_z,
                    shared_memory, stream, parameters, extra);
}

using runtime_launch_type = cudaError_t (*)(const void*, dim3, dim3, void**,
                                            std::size_t, cudaStream_t);

cudaError_t runtime_launch(const char* symbol, const void* function, dim3 grid,
                           dim3 block, void** arguments,
                           std::size_t shared_memory, cudaStream_t stream)
{
    auto original =
        reinterpret_cast<runtime_launch_type>(dlsym(RTLD_NEXT, symbol));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    auto launch_guard = runtime_gate().launch_guard();
    const auto decision = inspect_symbol_launch(function, arguments);
    if (!approve(decision)) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(function, grid, block, arguments, shared_memory, stream);
}

using kernel_launch_type = cudaError_t (*)(cudaKernel_t, dim3, dim3, void**,
                                           std::size_t, cudaStream_t);

cudaError_t kernel_launch(const char* symbol, cudaKernel_t kernel, dim3 grid,
                          dim3 block, void** arguments,
                          std::size_t shared_memory, cudaStream_t stream)
{
    auto original =
        reinterpret_cast<kernel_launch_type>(dlsym(RTLD_NEXT, symbol));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    auto launch_guard = runtime_gate().launch_guard();
    const auto decision = inspect_kernel_launch(kernel, arguments);
    if (!approve(decision)) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(kernel, grid, block, arguments, shared_memory, stream);
}

}  // namespace

extern "C" int hbfsim_coverage_add_range(std::uintptr_t begin,
                                         std::uintptr_t end) noexcept
{
    try {
        runtime_gate().add_range(begin, end);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "hbfsim range registration: " << error.what() << '\n';
        return -1;
    }
}

#define HBFSIM_DRIVER_LAUNCH(name)                                             \
    extern "C" CUresult name(CUfunction function, unsigned int grid_x,         \
                             unsigned int grid_y, unsigned int grid_z,         \
                             unsigned int block_x, unsigned int block_y,       \
                             unsigned int block_z, unsigned int shared_memory, \
                             CUstream stream, void** parameters, void** extra) \
    {                                                                          \
        return driver_launch(#name, function, grid_x, grid_y, grid_z, block_x, \
                             block_y, block_z, shared_memory, stream,          \
                             parameters, extra);                               \
    }

HBFSIM_DRIVER_LAUNCH(cuLaunchKernel)
HBFSIM_DRIVER_LAUNCH(cuLaunchKernel_ptsz)

#define HBFSIM_DRIVER_COOPERATIVE(name)                                        \
    extern "C" CUresult name(CUfunction function, unsigned int grid_x,         \
                             unsigned int grid_y, unsigned int grid_z,         \
                             unsigned int block_x, unsigned int block_y,       \
                             unsigned int block_z, unsigned int shared_memory, \
                             CUstream stream, void** parameters)               \
    {                                                                          \
        using type =                                                           \
            CUresult (*)(CUfunction, unsigned int, unsigned int, unsigned int, \
                         unsigned int, unsigned int, unsigned int,             \
                         unsigned int, CUstream, void**);                      \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return CUDA_ERROR_NOT_INITIALIZED;                                 \
        if (runtime_launch_in_progress)                                        \
            return original(function, grid_x, grid_y, grid_z, block_x,         \
                            block_y, block_z, shared_memory, stream,           \
                            parameters);                                       \
        auto guard = runtime_gate().launch_guard();                            \
        if (!approve(inspect_function_launch(function, parameters, nullptr)))  \
            return CUDA_ERROR_NOT_SUPPORTED;                                   \
        return original(function, grid_x, grid_y, grid_z, block_x, block_y,    \
                        block_z, shared_memory, stream, parameters);           \
    }
HBFSIM_DRIVER_COOPERATIVE(cuLaunchCooperativeKernel)
HBFSIM_DRIVER_COOPERATIVE(cuLaunchCooperativeKernel_ptsz)

#define HBFSIM_RUNTIME_LAUNCH(name)                                            \
    extern "C" cudaError_t name(const void* function, dim3 grid, dim3 block,   \
                                void** arguments, std::size_t shared_memory,   \
                                cudaStream_t stream)                           \
    {                                                                          \
        return runtime_launch(#name, function, grid, block, arguments,         \
                              shared_memory, stream);                          \
    }

HBFSIM_RUNTIME_LAUNCH(cudaLaunchKernel)
HBFSIM_RUNTIME_LAUNCH(cudaLaunchKernel_ptsz)
HBFSIM_RUNTIME_LAUNCH(cudaLaunchCooperativeKernel)
HBFSIM_RUNTIME_LAUNCH(cudaLaunchCooperativeKernel_ptsz)

#define HBFSIM_KERNEL_LAUNCH(name)                                             \
    extern "C" cudaError_t name(cudaKernel_t kernel, dim3 grid, dim3 block,    \
                                void** arguments, std::size_t shared_memory,   \
                                cudaStream_t stream)                           \
    {                                                                          \
        return kernel_launch(#name, kernel, grid, block, arguments,            \
                             shared_memory, stream);                           \
    }

HBFSIM_KERNEL_LAUNCH(__cudaLaunchKernel)
HBFSIM_KERNEL_LAUNCH(__cudaLaunchKernel_ptsz)

#define HBFSIM_DRIVER_EX(name)                                                 \
    extern "C" CUresult name(const CUlaunchConfig* config,                     \
                             CUfunction function, void** parameters,           \
                             void** extra)                                     \
    {                                                                          \
        using type =                                                           \
            CUresult (*)(const CUlaunchConfig*, CUfunction, void**, void**);   \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return CUDA_ERROR_NOT_INITIALIZED;                                 \
        if (runtime_launch_in_progress)                                        \
            return original(config, function, parameters, extra);              \
        auto guard = runtime_gate().launch_guard();                            \
        const auto decision =                                                  \
            inspect_function_launch(function, parameters, extra);              \
        return approve(decision)                                               \
                   ? original(config, function, parameters, extra)             \
                   : CUDA_ERROR_NOT_SUPPORTED;                                 \
    }
HBFSIM_DRIVER_EX(cuLaunchKernelEx)
HBFSIM_DRIVER_EX(cuLaunchKernelEx_ptsz)

#define HBFSIM_RUNTIME_EX(name)                                                \
    extern "C" cudaError_t name(const cudaLaunchConfig_t* config,              \
                                const void* function, void** arguments)        \
    {                                                                          \
        using type =                                                           \
            cudaError_t (*)(const cudaLaunchConfig_t*, const void*, void**);   \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return cudaErrorInitializationError;                               \
        auto guard = runtime_gate().launch_guard();                            \
        const auto decision = inspect_symbol_launch(function, arguments);      \
        if (!approve(decision))                                                \
            return cudaErrorNotSupported;                                      \
        RuntimeLaunchScope scope;                                              \
        return original(config, function, arguments);                          \
    }
HBFSIM_RUNTIME_EX(cudaLaunchKernelExC)
HBFSIM_RUNTIME_EX(cudaLaunchKernelExC_ptsz)

hbfsim::GateDecision opaque_launch(const char* kind)
{
    return hbfsim::uninspectable_launch_decision(
        runtime_gate().gate().has_ranges(), kind);
}

extern "C" CUresult cuLaunch(CUfunction function)
{
    using type = CUresult (*)(CUfunction);
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuLaunch"));
    if (original == nullptr)
        return CUDA_ERROR_NOT_INITIALIZED;
    if (runtime_launch_in_progress)
        return original(function);
    auto guard = runtime_gate().launch_guard();
    return approve(opaque_launch("legacy_driver_launch"))
               ? original(function)
               : CUDA_ERROR_NOT_SUPPORTED;
}

extern "C" CUresult cuLaunchGrid(CUfunction function, int grid_width,
                                 int grid_height)
{
    using type = CUresult (*)(CUfunction, int, int);
    auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuLaunchGrid"));
    if (original == nullptr)
        return CUDA_ERROR_NOT_INITIALIZED;
    if (runtime_launch_in_progress) {
        return original(function, grid_width, grid_height);
    }
    auto guard = runtime_gate().launch_guard();
    return approve(opaque_launch("legacy_driver_launch"))
               ? original(function, grid_width, grid_height)
               : CUDA_ERROR_NOT_SUPPORTED;
}

extern "C" CUresult cuLaunchGridAsync(CUfunction function, int grid_width,
                                      int grid_height, CUstream stream)
{
    using type = CUresult (*)(CUfunction, int, int, CUstream);
    auto original =
        reinterpret_cast<type>(dlsym(RTLD_NEXT, "cuLaunchGridAsync"));
    if (original == nullptr)
        return CUDA_ERROR_NOT_INITIALIZED;
    if (runtime_launch_in_progress) {
        return original(function, grid_width, grid_height, stream);
    }
    auto guard = runtime_gate().launch_guard();
    return approve(opaque_launch("legacy_driver_launch"))
               ? original(function, grid_width, grid_height, stream)
               : CUDA_ERROR_NOT_SUPPORTED;
}

#define HBFSIM_DRIVER_GRAPH(name)                                              \
    extern "C" CUresult name(CUgraphExec graph, CUstream stream)               \
    {                                                                          \
        using type = CUresult (*)(CUgraphExec, CUstream);                      \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return CUDA_ERROR_NOT_INITIALIZED;                                 \
        if (runtime_launch_in_progress)                                        \
            return original(graph, stream);                                    \
        auto guard = runtime_gate().launch_guard();                            \
        return approve(opaque_launch("graph_launch"))                          \
                   ? original(graph, stream)                                   \
                   : CUDA_ERROR_NOT_SUPPORTED;                                 \
    }
HBFSIM_DRIVER_GRAPH(cuGraphLaunch)
HBFSIM_DRIVER_GRAPH(cuGraphLaunch_ptsz)

#define HBFSIM_RUNTIME_GRAPH(name)                                             \
    extern "C" cudaError_t name(cudaGraphExec_t graph, cudaStream_t stream)    \
    {                                                                          \
        using type = cudaError_t (*)(cudaGraphExec_t, cudaStream_t);           \
        auto original = reinterpret_cast<type>(dlsym(RTLD_NEXT, #name));       \
        if (original == nullptr)                                               \
            return cudaErrorInitializationError;                               \
        auto guard = runtime_gate().launch_guard();                            \
        if (!approve(opaque_launch("graph_launch")))                           \
            return cudaErrorNotSupported;                                      \
        RuntimeLaunchScope scope;                                              \
        return original(graph, stream);                                        \
    }
HBFSIM_RUNTIME_GRAPH(cudaGraphLaunch)
HBFSIM_RUNTIME_GRAPH(cudaGraphLaunch_ptsz)

extern "C" CUresult
cuLaunchCooperativeKernelMultiDevice(CUDA_LAUNCH_PARAMS* launches,
                                     unsigned int count, unsigned int flags)
{
    using type = CUresult (*)(CUDA_LAUNCH_PARAMS*, unsigned int, unsigned int);
    auto original = reinterpret_cast<type>(
        dlsym(RTLD_NEXT, "cuLaunchCooperativeKernelMultiDevice"));
    if (original == nullptr)
        return CUDA_ERROR_NOT_INITIALIZED;
    if (runtime_launch_in_progress)
        return original(launches, count, flags);
    auto guard = runtime_gate().launch_guard();
    if (launches == nullptr)
        return CUDA_ERROR_INVALID_VALUE;
    for (unsigned int index = 0; index < count; ++index) {
        if (!approve(inspect_function_launch(launches[index].function,
                                             launches[index].kernelParams,
                                             nullptr))) {
            return CUDA_ERROR_NOT_SUPPORTED;
        }
    }
    return original(launches, count, flags);
}

#if CUDART_VERSION < 13000
extern "C" cudaError_t
cudaLaunchCooperativeKernelMultiDevice(struct cudaLaunchParams* launches,
                                       unsigned int count, unsigned int flags)
{
    using type =
        cudaError_t (*)(struct cudaLaunchParams*, unsigned int, unsigned int);
    auto original = reinterpret_cast<type>(
        dlsym(RTLD_NEXT, "cudaLaunchCooperativeKernelMultiDevice"));
    if (original == nullptr)
        return cudaErrorInitializationError;
    auto guard = runtime_gate().launch_guard();
    if (launches == nullptr)
        return cudaErrorInvalidValue;
    for (unsigned int index = 0; index < count; ++index) {
        if (!approve(inspect_symbol_launch(launches[index].func,
                                           launches[index].args))) {
            return cudaErrorNotSupported;
        }
    }
    RuntimeLaunchScope scope;
    return original(launches, count, flags);
}
#endif

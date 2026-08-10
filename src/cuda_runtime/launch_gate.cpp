#include "hbfsim/coverage.hpp"

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <dlfcn.h>

#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <sstream>
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
    RuntimeGate() : writer_(environment_or("HBFSIM_COVERAGE_PATH", "coverage.json")) {}

    hbfsim::CoverageGate& gate() { return gate_; }

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
    if (get_module == nullptr || get_module(&module, function) != CUDA_SUCCESS) {
        return "cuda-module:unknown";
    }
    std::ostringstream output;
    output << "cuda-module:" << module;
    return output.str();
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
hbfsim::GateDecision inspect_bounded(
    Handle handle, std::string runtime_module_id, std::string kernel,
    void** arguments, GetInfo get_info)
{
    auto& runtime = runtime_gate();
    runtime.refresh_manifests();
    if (arguments == nullptr || get_info == nullptr) {
        return runtime.gate().has_ranges()
                   ? unavailable(std::move(runtime_module_id), std::move(kernel))
                   : hbfsim::GateDecision{.allowed = true,
                                          .module_id = std::move(runtime_module_id),
                                          .kernel = std::move(kernel)};
    }

    hbfsim::KernelLaunch launch{.kernel = kernel};
    for (std::size_t index = 0;; ++index) {
        std::size_t offset = 0;
        std::size_t width = 0;
        const auto status = get_info(handle, index, &offset, &width);
        if (status == CUDA_ERROR_INVALID_VALUE) {
            break;
        }
        if (status != CUDA_SUCCESS) {
            return runtime.gate().has_ranges()
                       ? unavailable(std::move(runtime_module_id), std::move(kernel))
                       : hbfsim::GateDecision{.allowed = true,
                                              .module_id = std::move(runtime_module_id),
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
        // fail closed while any HBF range is active; no aggregate slot guessing.
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

hbfsim::GateDecision inspect_function_launch(
    CUfunction function, void** arguments, void** extra)
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
    static auto get_info = reinterpret_cast<get_info_type>(
        driver_symbol("cuFuncGetParamInfo"));
    return inspect_bounded(function, module_id, kernel, arguments, get_info);
}

hbfsim::GateDecision inspect_kernel_launch(cudaKernel_t kernel, void** arguments)
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
    static auto get_info = reinterpret_cast<get_info_type>(
        driver_symbol("cuKernelGetParamInfo"));
    const char* raw_name = nullptr;
    const std::string name = get_name != nullptr &&
                                     get_name(&raw_name, reinterpret_cast<CUkernel>(kernel)) ==
                                         CUDA_SUCCESS &&
                                     raw_name != nullptr
                                 ? raw_name
                                 : "unknown_cukernel";
    return inspect_bounded(reinterpret_cast<CUkernel>(kernel), "cuda-module:unknown",
                           name, arguments, get_info);
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
                   ? unavailable("cuda-module:unknown", "unknown_runtime_kernel")
                   : hbfsim::GateDecision{.allowed = true,
                                          .module_id = "cuda-module:unknown",
                                          .kernel = "unknown_runtime_kernel"};
    }
    return inspect_function_launch(reinterpret_cast<CUfunction>(runtime_function),
                                   arguments, nullptr);
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

using driver_launch_type = CUresult (*)(
    CUfunction, unsigned int, unsigned int, unsigned int, unsigned int,
    unsigned int, unsigned int, unsigned int, CUstream, void**, void**);

CUresult driver_launch(
    const char* symbol, CUfunction function, unsigned int grid_x,
    unsigned int grid_y, unsigned int grid_z, unsigned int block_x,
    unsigned int block_y, unsigned int block_z, unsigned int shared_memory,
    CUstream stream, void** parameters, void** extra)
{
    auto original = reinterpret_cast<driver_launch_type>(dlsym(RTLD_NEXT, symbol));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }
    if (runtime_launch_in_progress) {
        return original(function, grid_x, grid_y, grid_z, block_x, block_y,
                        block_z, shared_memory, stream, parameters, extra);
    }
    const auto decision = inspect_function_launch(function, parameters, extra);
    if (!approve(decision)) {
        return CUDA_ERROR_NOT_SUPPORTED;
    }
    return original(function, grid_x, grid_y, grid_z, block_x, block_y, block_z,
                    shared_memory, stream, parameters, extra);
}

using runtime_launch_type = cudaError_t (*)(
    const void*, dim3, dim3, void**, std::size_t, cudaStream_t);

cudaError_t runtime_launch(
    const char* symbol, const void* function, dim3 grid, dim3 block,
    void** arguments, std::size_t shared_memory, cudaStream_t stream)
{
    auto original = reinterpret_cast<runtime_launch_type>(dlsym(RTLD_NEXT, symbol));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    const auto decision = inspect_symbol_launch(function, arguments);
    if (!approve(decision)) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(function, grid, block, arguments, shared_memory, stream);
}

using kernel_launch_type = cudaError_t (*)(
    cudaKernel_t, dim3, dim3, void**, std::size_t, cudaStream_t);

cudaError_t kernel_launch(
    const char* symbol, cudaKernel_t kernel, dim3 grid, dim3 block,
    void** arguments, std::size_t shared_memory, cudaStream_t stream)
{
    auto original = reinterpret_cast<kernel_launch_type>(dlsym(RTLD_NEXT, symbol));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    const auto decision = inspect_kernel_launch(kernel, arguments);
    if (!approve(decision)) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(kernel, grid, block, arguments, shared_memory, stream);
}

}  // namespace

extern "C" int hbfsim_coverage_add_range(
    std::uintptr_t begin, std::uintptr_t end) noexcept
{
    try {
        runtime_gate().gate().add_range(begin, end);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "hbfsim range registration: " << error.what() << '\n';
        return -1;
    }
}

#define HBFSIM_DRIVER_LAUNCH(name)                                              \
    extern "C" CUresult name(                                                  \
        CUfunction function, unsigned int grid_x, unsigned int grid_y,          \
        unsigned int grid_z, unsigned int block_x, unsigned int block_y,        \
        unsigned int block_z, unsigned int shared_memory, CUstream stream,      \
        void** parameters, void** extra)                                        \
    {                                                                            \
        return driver_launch(#name, function, grid_x, grid_y, grid_z, block_x,  \
                             block_y, block_z, shared_memory, stream, parameters,\
                             extra);                                             \
    }

HBFSIM_DRIVER_LAUNCH(cuLaunchKernel)
HBFSIM_DRIVER_LAUNCH(cuLaunchKernel_ptsz)

#define HBFSIM_RUNTIME_LAUNCH(name)                                             \
    extern "C" cudaError_t name(                                                \
        const void* function, dim3 grid, dim3 block, void** arguments,          \
        std::size_t shared_memory, cudaStream_t stream)                         \
    {                                                                            \
        return runtime_launch(#name, function, grid, block, arguments,          \
                              shared_memory, stream);                            \
    }

HBFSIM_RUNTIME_LAUNCH(cudaLaunchKernel)
HBFSIM_RUNTIME_LAUNCH(cudaLaunchKernel_ptsz)

#define HBFSIM_KERNEL_LAUNCH(name)                                              \
    extern "C" cudaError_t name(                                                \
        cudaKernel_t kernel, dim3 grid, dim3 block, void** arguments,           \
        std::size_t shared_memory, cudaStream_t stream)                         \
    {                                                                            \
        return kernel_launch(#name, kernel, grid, block, arguments,             \
                             shared_memory, stream);                             \
    }

HBFSIM_KERNEL_LAUNCH(__cudaLaunchKernel)
HBFSIM_KERNEL_LAUNCH(__cudaLaunchKernel_ptsz)

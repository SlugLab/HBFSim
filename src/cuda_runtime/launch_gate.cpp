#include "hbfsim/coverage.hpp"

#include <cuda.h>
#include <cuda_runtime_api.h>
#include <dlfcn.h>
#include <json.hpp>

#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

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
                const auto json = nlohmann::json::parse(line);
                hbfsim::ModuleManifest manifest{
                    .name = json.at("name").get<std::string>(),
                    .instrumented = json.value("instrumented", false),
                    .pointer_parameters =
                        json.value("pointer_parameters", std::vector<std::size_t>{}),
                };
                for (const auto& unsupported :
                     json.value("unsupported_parameters", nlohmann::json::array())) {
                    manifest.unsupported_parameters.push_back({
                        .index = unsupported.at("index").get<std::size_t>(),
                        .operation = unsupported.at("operation").get<std::string>(),
                    });
                }
                gate_.add_module(std::move(manifest));
            } catch (const nlohmann::json::exception&) {
                // A pass may still be appending its final line. The next launch retries.
            }
        }
    }

    void record(const hbfsim::GateDecision& decision) noexcept
    {
        try {
            writer_.append(decision);
        } catch (const std::exception& error) {
            std::cerr << "hbfsim coverage writer: " << error.what() << '\n';
        }
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

std::string driver_kernel_name(CUfunction function)
{
    using function_type = CUresult (*)(const char**, CUfunction);
    static auto get_name =
        reinterpret_cast<function_type>(driver_symbol("cuFuncGetName"));
    const char* name = nullptr;
    if (get_name != nullptr && get_name(&name, function) == CUDA_SUCCESS &&
        name != nullptr) {
        return name;
    }
    return "unknown_cufunction";
}

std::string driver_kernel_name(CUkernel kernel)
{
    using function_type = CUresult (*)(const char**, CUkernel);
    static auto get_name =
        reinterpret_cast<function_type>(driver_symbol("cuKernelGetName"));
    const char* name = nullptr;
    if (get_name != nullptr && get_name(&name, kernel) == CUDA_SUCCESS &&
        name != nullptr) {
        return name;
    }
    return "unknown_cukernel";
}

hbfsim::GateDecision metadata_unavailable(std::string kernel)
{
    return {
        .allowed = false,
        .module = kernel,
        .kernel = std::move(kernel),
        .reason = "launch_metadata_unavailable",
        .operation = "opaque_pointer_access",
    };
}

hbfsim::GateDecision inspect_driver_launch(
    CUfunction function, void** kernel_parameters, void** extra)
{
    auto& runtime = runtime_gate();
    runtime.refresh_manifests();
    const auto kernel = driver_kernel_name(function);

    // The driver API exposes an exact parameter count and byte size. Iterate only
    // entries accepted by cuFuncGetParamInfo; never probe kernel_parameters past
    // that boundary. cudaLaunchKernel is forwarded to this driver interception
    // because its host-stub API does not expose a safe argument-count query.
    using get_parameter_info_type =
        CUresult (*)(CUfunction, std::size_t, std::size_t*, std::size_t*);
    static auto get_parameter_info = reinterpret_cast<get_parameter_info_type>(
        driver_symbol("cuFuncGetParamInfo"));
    if (get_parameter_info == nullptr || kernel_parameters == nullptr || extra != nullptr) {
        return runtime.gate().has_ranges()
                   ? metadata_unavailable(kernel)
                   : hbfsim::GateDecision{
                         .allowed = true, .module = kernel, .kernel = kernel};
    }

    hbfsim::KernelLaunch launch{.module = kernel, .kernel = kernel};
    for (std::size_t index = 0;; ++index) {
        std::size_t offset = 0;
        std::size_t size = 0;
        const auto status =
            get_parameter_info(function, index, &offset, &size);
        if (status == CUDA_ERROR_INVALID_VALUE) {
            break;
        }
        if (status != CUDA_SUCCESS) {
            return runtime.gate().has_ranges()
                       ? metadata_unavailable(kernel)
                       : hbfsim::GateDecision{
                             .allowed = true, .module = kernel, .kernel = kernel};
        }
        launch.arguments.resize(index + 1, 0);
        if (size == sizeof(std::uintptr_t) && kernel_parameters[index] != nullptr) {
            std::memcpy(&launch.arguments[index], kernel_parameters[index], size);
        }
    }
    return runtime.gate().check_launch(launch);
}

hbfsim::GateDecision inspect_runtime_launch(const void* function, void** arguments)
{
    using get_function_type = cudaError_t (*)(cudaFunction_t*, const void*);
    static auto get_function = reinterpret_cast<get_function_type>(
        dlsym(RTLD_NEXT, "cudaGetFuncBySymbol"));
    if (get_function == nullptr) {
        return runtime_gate().gate().has_ranges()
                   ? metadata_unavailable("unknown_runtime_kernel")
                   : hbfsim::GateDecision{.allowed = true,
                                          .module = "unknown_runtime_kernel",
                                          .kernel = "unknown_runtime_kernel"};
    }
    cudaFunction_t runtime_function = nullptr;
    if (get_function(&runtime_function, function) != cudaSuccess ||
        runtime_function == nullptr) {
        return runtime_gate().gate().has_ranges()
                   ? metadata_unavailable("unknown_runtime_kernel")
                   : hbfsim::GateDecision{.allowed = true,
                                          .module = "unknown_runtime_kernel",
                                          .kernel = "unknown_runtime_kernel"};
    }
    return inspect_driver_launch(
        reinterpret_cast<CUfunction>(runtime_function), arguments, nullptr);
}

hbfsim::GateDecision inspect_runtime_launch(cudaKernel_t kernel, void** arguments)
{
    auto& runtime = runtime_gate();
    runtime.refresh_manifests();
    const auto kernel_name = driver_kernel_name(reinterpret_cast<CUkernel>(kernel));
    using get_parameter_info_type =
        CUresult (*)(CUkernel, std::size_t, std::size_t*, std::size_t*);
    static auto get_parameter_info = reinterpret_cast<get_parameter_info_type>(
        driver_symbol("cuKernelGetParamInfo"));
    if (get_parameter_info == nullptr || arguments == nullptr) {
        return runtime.gate().has_ranges()
                   ? metadata_unavailable(kernel_name)
                   : hbfsim::GateDecision{.allowed = true,
                                          .module = kernel_name,
                                          .kernel = kernel_name};
    }

    hbfsim::KernelLaunch launch{.module = kernel_name, .kernel = kernel_name};
    for (std::size_t index = 0;; ++index) {
        std::size_t offset = 0;
        std::size_t size = 0;
        const auto status = get_parameter_info(
            reinterpret_cast<CUkernel>(kernel), index, &offset, &size);
        if (status == CUDA_ERROR_INVALID_VALUE) {
            break;
        }
        if (status != CUDA_SUCCESS) {
            return runtime.gate().has_ranges()
                       ? metadata_unavailable(kernel_name)
                       : hbfsim::GateDecision{.allowed = true,
                                              .module = kernel_name,
                                              .kernel = kernel_name};
        }
        launch.arguments.resize(index + 1, 0);
        if (size == sizeof(std::uintptr_t) && arguments[index] != nullptr) {
            std::memcpy(&launch.arguments[index], arguments[index], size);
        }
    }
    return runtime.gate().check_launch(launch);
}

thread_local bool runtime_launch_in_progress = false;

class RuntimeLaunchScope {
  public:
    RuntimeLaunchScope() { runtime_launch_in_progress = true; }
    ~RuntimeLaunchScope() { runtime_launch_in_progress = false; }
};

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

extern "C" CUresult cuLaunchKernel(
    CUfunction function, unsigned int grid_x, unsigned int grid_y,
    unsigned int grid_z, unsigned int block_x, unsigned int block_y,
    unsigned int block_z, unsigned int shared_memory, CUstream stream,
    void** kernel_parameters, void** extra)
{
    using launch_type = CUresult (*)(
        CUfunction, unsigned int, unsigned int, unsigned int, unsigned int,
        unsigned int, unsigned int, unsigned int, CUstream, void**, void**);
    static auto original =
        reinterpret_cast<launch_type>(dlsym(RTLD_NEXT, "cuLaunchKernel"));
    if (original == nullptr) {
        return CUDA_ERROR_NOT_INITIALIZED;
    }

    if (runtime_launch_in_progress) {
        return original(function, grid_x, grid_y, grid_z, block_x, block_y,
                        block_z, shared_memory, stream, kernel_parameters, extra);
    }

    auto decision = inspect_driver_launch(function, kernel_parameters, extra);
    runtime_gate().record(decision);
    if (!decision.allowed) {
        return CUDA_ERROR_NOT_SUPPORTED;
    }
    return original(function, grid_x, grid_y, grid_z, block_x, block_y, block_z,
                    shared_memory, stream, kernel_parameters, extra);
}

extern "C" cudaError_t cudaLaunchKernel(
    const void* function, dim3 grid, dim3 block, void** arguments,
    std::size_t shared_memory, cudaStream_t stream)
{
    using launch_type = cudaError_t (*)(
        const void*, dim3, dim3, void**, std::size_t, cudaStream_t);
    static auto original =
        reinterpret_cast<launch_type>(dlsym(RTLD_NEXT, "cudaLaunchKernel"));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    const auto decision = inspect_runtime_launch(function, arguments);
    runtime_gate().record(decision);
    if (!decision.allowed) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(function, grid, block, arguments, shared_memory, stream);
}

extern "C" cudaError_t __cudaLaunchKernel(
    cudaKernel_t kernel, dim3 grid, dim3 block, void** arguments,
    std::size_t shared_memory, cudaStream_t stream)
{
    using launch_type = cudaError_t (*)(
        cudaKernel_t, dim3, dim3, void**, std::size_t, cudaStream_t);
    static auto original =
        reinterpret_cast<launch_type>(dlsym(RTLD_NEXT, "__cudaLaunchKernel"));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    const auto decision = inspect_runtime_launch(kernel, arguments);
    runtime_gate().record(decision);
    if (!decision.allowed) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(kernel, grid, block, arguments, shared_memory, stream);
}

extern "C" cudaError_t __cudaLaunchKernel_ptsz(
    cudaKernel_t kernel, dim3 grid, dim3 block, void** arguments,
    std::size_t shared_memory, cudaStream_t stream)
{
    using launch_type = cudaError_t (*)(
        cudaKernel_t, dim3, dim3, void**, std::size_t, cudaStream_t);
    static auto original = reinterpret_cast<launch_type>(
        dlsym(RTLD_NEXT, "__cudaLaunchKernel_ptsz"));
    if (original == nullptr) {
        return cudaErrorInitializationError;
    }
    const auto decision = inspect_runtime_launch(kernel, arguments);
    runtime_gate().record(decision);
    if (!decision.allowed) {
        return cudaErrorNotSupported;
    }
    RuntimeLaunchScope scope;
    return original(kernel, grid, block, arguments, shared_memory, stream);
}

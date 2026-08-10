#include <array>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <mutex>

extern "C" {

namespace {

constexpr std::uintptr_t fake_module_value = 0x7000;
std::array<std::uint8_t, 32> module_identity = {0x42};
bool module_loaded = false;
bool marker_available = true;
bool unload_fails = false;
bool lifecycle_fails = false;
int launch_count = 0;
std::mutex concurrent_load_mutex;
std::condition_variable concurrent_load_ready;
int concurrent_load_arrivals = 0;

}  // namespace

static int lookup(const char* symbol, void** function)
{
    *function = reinterpret_cast<void*>(static_cast<std::uintptr_t>(0x1234));
    return symbol != nullptr && std::strncmp(symbol, "cu", 2) == 0 &&
                   std::strncmp(symbol, "cuda", 4) != 0
               ? 0
               : 1;
}

int cuGetProcAddress(const char* symbol, void** function, int version,
                     std::uint64_t flags)
{
    (void)version;
    (void)flags;
    return lookup(symbol, function);
}

int cuGetProcAddress_v2(const char* symbol, void** function, int version,
                        std::uint64_t flags, int* status)
{
    (void)version;
    (void)flags;
    const int result = lookup(symbol, function);
    if (status != nullptr) {
        *status = result == 0 ? 0 : 1;
    }
    return result;
}

int cudaGetDriverEntryPoint(const char* symbol, void** function,
                            std::uint64_t flags, int* status)
{
    (void)flags;
    const int result = lookup(symbol, function);
    if (status != nullptr) {
        *status = result == 0 ? 0 : 1;
    }
    return result;
}

int cudaGetDriverEntryPoint_ptsz(const char* symbol, void** function,
                                 std::uint64_t flags, int* status)
{
    return cudaGetDriverEntryPoint(symbol, function, flags, status);
}

int cudaGetDriverEntryPointByVersion(const char* symbol, void** function,
                                     unsigned int version, std::uint64_t flags,
                                     int* status)
{
    (void)version;
    return cudaGetDriverEntryPoint(symbol, function, flags, status);
}

int cudaGetDriverEntryPointByVersion_ptsz(const char* symbol, void** function,
                                          unsigned int version,
                                          std::uint64_t flags, int* status)
{
    return cudaGetDriverEntryPointByVersion(symbol, function, version, flags,
                                            status);
}

int cuModuleLoadDataEx(void** module, const void* image, unsigned int, void*,
                       void**)
{
    if (module == nullptr || image == nullptr) {
        return 1;
    }
    const auto* image_name = static_cast<const char*>(image);
    if (std::strcmp(image_name, "fail-a") == 0 ||
        std::strcmp(image_name, "success-b") == 0) {
        std::unique_lock lock(concurrent_load_mutex);
        ++concurrent_load_arrivals;
        concurrent_load_ready.notify_all();
        concurrent_load_ready.wait(
            lock, [] { return concurrent_load_arrivals >= 2; });
    }
    if (std::strcmp(image_name, "fail") == 0 ||
        std::strcmp(image_name, "fail-a") == 0) {
        return 1;
    }
    module_loaded = true;
    *module = reinterpret_cast<void*>(fake_module_value);
    return 0;
}

int cuModuleUnload(void* module)
{
    if (module != reinterpret_cast<void*>(fake_module_value) ||
        !module_loaded || unload_fails) {
        return 1;
    }
    module_loaded = false;
    return 0;
}

int destroy_context()
{
    if (lifecycle_fails) {
        return 1;
    }
    module_loaded = false;
    return 0;
}

int cuCtxDestroy(void*)
{
    return destroy_context();
}

int cuCtxDestroy_v2(void*)
{
    return destroy_context();
}

int cuCtxDetach(void*)
{
    return destroy_context();
}

int cuDevicePrimaryCtxReset(int)
{
    return destroy_context();
}

int cuDevicePrimaryCtxReset_v2(int)
{
    return destroy_context();
}

int cuDevicePrimaryCtxRelease(int)
{
    return destroy_context();
}

int cuDevicePrimaryCtxRelease_v2(int)
{
    return destroy_context();
}

int cuGreenCtxDestroy(void*)
{
    return destroy_context();
}

int cudaDeviceReset()
{
    return destroy_context();
}

int cudaThreadExit()
{
    return destroy_context();
}

int cuFuncGetModule(void** module, void*)
{
    if (!module_loaded || module == nullptr) {
        return 1;
    }
    *module = reinterpret_cast<void*>(fake_module_value);
    return 0;
}

int cuFuncGetName(const char** name, void*)
{
    if (!module_loaded || name == nullptr) {
        return 1;
    }
    *name = "kernel";
    return 0;
}

int cuFuncGetParamInfo(void*, std::size_t index, std::size_t* offset,
                       std::size_t* size)
{
    if (!module_loaded || index != 0 || offset == nullptr || size == nullptr) {
        return 1;
    }
    *offset = 0;
    *size = sizeof(std::uintptr_t);
    return 0;
}

int cuModuleGetGlobal_v2(std::uintptr_t* address, std::size_t* size, void*,
                         const char* name)
{
    if (!module_loaded || !marker_available || address == nullptr ||
        size == nullptr || name == nullptr ||
        std::strcmp(name, "__hbfsim_module_identity") != 0) {
        return 1;
    }
    *address = reinterpret_cast<std::uintptr_t>(module_identity.data());
    *size = module_identity.size();
    return 0;
}

int cuMemcpyDtoH_v2(void* destination, std::uintptr_t source, std::size_t size)
{
    if (destination == nullptr || source == 0 ||
        size != module_identity.size()) {
        return 1;
    }
    std::memcpy(destination, reinterpret_cast<const void*>(source), size);
    return 0;
}

int cuLaunchKernel(void*, unsigned int, unsigned int, unsigned int,
                   unsigned int, unsigned int, unsigned int, unsigned int,
                   void*, void**, void**)
{
    ++launch_count;
    return 0;
}

void fakeCudaSetUnloadFailure(int fail)
{
    unload_fails = fail != 0;
}

void fakeCudaSetLifecycleFailure(int fail)
{
    lifecycle_fails = fail != 0;
}

void fakeCudaSetMarkerAvailable(int available)
{
    marker_available = available != 0;
}

void fakeCudaResetConcurrentLoads()
{
    std::lock_guard lock(concurrent_load_mutex);
    concurrent_load_arrivals = 0;
}

int fakeCudaLaunchCount()
{
    return launch_count;
}

int fakeCudaSetModuleIdentity(const std::uint8_t* identity, std::size_t size)
{
    if (identity == nullptr || size != module_identity.size()) {
        return 1;
    }
    std::memcpy(module_identity.data(), identity, module_identity.size());
    return 0;
}
}

#include <algorithm>
#include <array>
#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <memory>
#include <mutex>
#include <regex>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
#include "../../include/hbfsim/timing_future_abi.hpp"

extern "C" {

namespace {

constexpr std::uintptr_t fake_module_value = 0x7000;
std::array<std::uint8_t, 32> module_identity = {0x42};
bool module_loaded = false;
bool marker_available = true;
bool control_symbols_available = true;
bool control_copy_fails = false;
int control_copy_fail_position = 0;
int control_copy_calls = 0;
bool unload_fails = false;
bool lifecycle_fails = false;
std::atomic_int launch_count{0};
int launch_error=0;
int synchronize_count = 0;
bool synchronize_fails = false;
int unregister_count = 0;
bool unregister_fails = false;
bool nested_runtime_reset = false;
thread_local std::uintptr_t current_context = 0xCA00;
thread_local int current_device = 3;
std::mutex domain_mutex;
std::unordered_map<std::uintptr_t, int> live_domains{{0xCA00, 3}};
std::uint64_t control_alias = 0;
std::uint64_t control_generation = 0;
int future_contract_mode=0;
hbfsim::timing_future::ModuleRequirements future_requirements{};
hbfsim::timing_future::ModuleRequirements future_helper_abi{};
hbfsim::timing_future::ModuleConfig future_config{};
hbfsim::timing_future::Counters future_counters{};
hbfsim::timing_future::Trace future_trace[hbfsim::timing_future::kTraceCapacity];
std::array<unsigned char,32> future_helper_hash{};
std::array<unsigned char,32> future_kernel_hash{};
std::vector<int> future_copy_events;
// TEST_ONLY: opt-in image fixtures retain independent module-owned storage and
// extract kernel constants from the actual plugin PTX passed to the loader.
struct FutureImage {
    std::uintptr_t context{current_context};
    std::array<std::uint8_t,32> identity{module_identity};
    hbfsim::timing_future::ModuleRequirements requirements{future_requirements};
    hbfsim::timing_future::ModuleRequirements helper{future_helper_abi};
    hbfsim::timing_future::ModuleConfig config{};
    hbfsim::timing_future::Counters counters{};
    hbfsim::timing_future::Trace trace[hbfsim::timing_future::kTraceCapacity]{};
    std::array<unsigned char,32> helper_hash{future_helper_hash};
    std::uint64_t alias{0}, generation{0};
    std::unordered_map<std::string,std::vector<unsigned char>> kernels;
};
struct FutureFunction { void* module; std::string name; };
bool future_image_mode=false;
std::uintptr_t next_image_handle=0x8000,next_function_handle=0x10000;
std::unordered_map<void*,std::unique_ptr<FutureImage>> future_images;
std::unordered_map<void*,FutureFunction> future_functions;
std::unordered_map<void*,void*> kernel_function_fixtures;
int future_kernel_lookup_failure=0;

bool future_image_memory(std::uintptr_t address,std::size_t size,bool write)
{
    const auto within=[&](const void* data,std::size_t bytes) {
        const auto begin=reinterpret_cast<std::uintptr_t>(data);
        return address>=begin && size<=bytes && address-begin<=bytes-size;
    };
    for(const auto& [_,image]:future_images) {
        const auto& m=*image;
        if(within(&m.config,sizeof(m.config)) || within(&m.counters,sizeof(m.counters)) ||
           within(&m.alias,sizeof(m.alias)) || within(&m.generation,sizeof(m.generation)))return true;
        if(!write) {
            if(within(m.identity.data(),m.identity.size()) || within(&m.requirements,sizeof(m.requirements)) ||
               within(&m.helper,sizeof(m.helper)) || within(m.helper_hash.data(),m.helper_hash.size()))return true;
            for(const auto& [_,bytes]:m.kernels)if(within(bytes.data(),bytes.size()))return true;
        }
    }
    return false;
}
std::uintptr_t pointer_context = 0xCA00;
int pointer_device = 3;
unsigned int pointer_memory_type = 2;
unsigned int pointer_is_managed = 0;
std::uintptr_t allocation_base = 0x1000;
std::size_t allocation_bytes = 0x1000;
std::mutex pointer_pause_mutex;
std::condition_variable pointer_pause_ready;
bool pointer_pause_enabled = false;
bool pointer_pause_entered = false;
bool pointer_pause_released = false;
std::mutex lifecycle_pause_mutex;
std::condition_variable lifecycle_pause_ready;
bool lifecycle_pause_enabled = false;
bool lifecycle_pause_entered = false;
bool lifecycle_pause_released = false;

enum CapacityEvent {
    capacity_context_set = 1,
    capacity_granularity = 2,
    capacity_reserve = 3,
    capacity_create = 4,
    capacity_map = 5,
    capacity_set_access = 6,
    capacity_host_alloc = 7,
    capacity_htod = 8,
    capacity_dtoh = 9,
    capacity_worker_context_exit = 10,
    capacity_host_free = 11,
    capacity_unmap = 12,
    capacity_release = 13,
    capacity_address_free = 14,
};

struct CapacityReservation {
    std::size_t bytes{0};
    std::size_t alignment{0};
    std::uint64_t handle{0};
    int device{-1};
    int access_device{-1};
    bool mapped{false};
    bool accessible{false};
};

struct CapacityHandle {
    std::size_t bytes{0};
    int device{-1};
};

std::mutex capacity_mutex;
std::unordered_map<std::uintptr_t, CapacityReservation>
    capacity_reservations;
std::unordered_map<std::uint64_t, CapacityHandle> capacity_handles;
std::unordered_map<void*, std::size_t> capacity_pinned;
std::unordered_map<std::string, int> capacity_fail_at;
std::unordered_map<std::string, int> capacity_calls;
std::vector<int> capacity_events;
std::uint64_t capacity_next_handle = 1;
std::size_t capacity_htod_calls = 0;
std::size_t capacity_dtoh_calls = 0;
std::size_t capacity_worker_contexts = 0;
std::size_t capacity_explicit_context_clears = 0;
std::uintptr_t capacity_last_context = 0;
int capacity_last_device = -1;

bool capacity_should_fail_locked(const char* operation)
{
    const auto call = ++capacity_calls[operation];
    const auto failure = capacity_fail_at.find(operation);
    return failure != capacity_fail_at.end() && failure->second == call;
}

bool capacity_valid_current_locked(int device)
{
    if (device < 0 || current_context == 0 || current_device != device) {
        return false;
    }
    std::lock_guard domain_lock(domain_mutex);
    const auto live = live_domains.find(current_context);
    return live != live_domains.end() && live->second == device;
}

auto capacity_reservation_for_locked(std::uintptr_t address,
                                     std::size_t bytes)
{
    return std::find_if(
        capacity_reservations.begin(), capacity_reservations.end(),
        [&](const auto& item) {
            const auto base = item.first;
            const auto size = item.second.bytes;
            return address >= base && bytes <= size &&
                   address - base <= size - bytes;
        });
}

bool capacity_device_range_locked(std::uintptr_t address,
                                  std::size_t bytes)
{
    const auto found = capacity_reservation_for_locked(address, bytes);
    return found != capacity_reservations.end() && found->second.mapped &&
           found->second.accessible &&
           found->second.device == found->second.access_device &&
           capacity_valid_current_locked(found->second.device);
}

struct CapacityThreadBinding {
    bool counted{false};
};

thread_local CapacityThreadBinding capacity_thread_binding;

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
    if (std::strcmp(image_name, "fail") == 0 ||
        std::strcmp(image_name, "fail-a") == 0) {
        return 1;
    }
    if(future_image_mode) {
        auto state=std::make_unique<FutureImage>();
        const std::string ptx(image_name);
        const std::regex constant(R"(\.b8\s+(__hbfsim_timing_future_kernel_[a-f0-9]{64}_v1)\[(\d+)\]\s*=\s*\{([^}]*)\})");
        for(std::sregex_iterator it(ptx.begin(),ptx.end(),constant),end;it!=end;++it) {
            const auto count=std::stoul((*it)[2]);
            if(count>64)return 1;
            auto& bytes=state->kernels[(*it)[1]];bytes.resize(count);
            std::istringstream values((*it)[3]);std::string value;std::size_t index=0;
            while(std::getline(values,value,',')) {
                const auto byte=std::stoul(value,nullptr,0);
                if(index>=count || byte>255)return 1;
                bytes[index++]=static_cast<unsigned char>(byte);
            }
        }
        *module=reinterpret_cast<void*>(next_image_handle++);
        future_images.emplace(*module,std::move(state));module_loaded=true;
        return 0;
    }
    module_loaded = true;
    control_alias = 0;
    control_generation = 0;
    *module = reinterpret_cast<void*>(fake_module_value);
    return 0;
}

int cuModuleUnload(void* module)
{
    if(future_image_mode) {
        if(unload_fails || future_images.erase(module)!=1)return 1;
        module_loaded=!future_images.empty();return 0;
    }
    if (module != reinterpret_cast<void*>(fake_module_value) ||
        !module_loaded || unload_fails) {
        return 1;
    }
    module_loaded = false;
    return 0;
}

int destroy_context(std::uintptr_t target_context)
{
    {
        std::unique_lock lock(lifecycle_pause_mutex);
        if (lifecycle_pause_enabled) {
            lifecycle_pause_entered = true;
            lifecycle_pause_ready.notify_all();
            lifecycle_pause_ready.wait(
                lock, [] { return lifecycle_pause_released; });
        }
    }
    if (lifecycle_fails) {
        return 1;
    }
    if (target_context != 0) {
        std::lock_guard lock(domain_mutex);
        live_domains.erase(target_context);
    }
    if (target_context == current_context) {
        if(future_image_mode) {
            for(auto it=future_images.begin();it!=future_images.end();) {
                if(it->second->context==target_context)it=future_images.erase(it);else ++it;
            }
        }
        module_loaded = false;
        current_context = 0;
        current_device = -1;
    }
    return 0;
}

int cuCtxDestroy(void* context)
{
    return destroy_context(reinterpret_cast<std::uintptr_t>(context));
}

int cuCtxDestroy_v2(void* context)
{
    return destroy_context(reinterpret_cast<std::uintptr_t>(context));
}

int cuCtxDetach(void* context)
{
    return destroy_context(reinterpret_cast<std::uintptr_t>(context));
}

int destroy_device(int device)
{
    return destroy_context(device == current_device ? current_context : 0);
}

int cuDevicePrimaryCtxReset(int device)
{
    return destroy_device(device);
}

int cuDevicePrimaryCtxReset_v2(int device)
{
    return destroy_device(device);
}

int cuDevicePrimaryCtxRelease(int device)
{
    return destroy_device(device);
}

int cuDevicePrimaryCtxRelease_v2(int device)
{
    return destroy_device(device);
}

int cuCtxFromGreenCtx(void** context, void* green_context)
{
    if (context == nullptr) {
        return 1;
    }
    const auto green = reinterpret_cast<std::uintptr_t>(green_context);
    if (green == 0xC100) {
        *context = reinterpret_cast<void*>(0xCA00);
        return 0;
    }
    if (green == 0xC200) {
        *context = reinterpret_cast<void*>(0xCB00);
        return 0;
    }
    return 1;
}

int cuGreenCtxDestroy(void* context)
{
    void* mapped = nullptr;
    return cuCtxFromGreenCtx(&mapped, context) == 0
               ? destroy_context(reinterpret_cast<std::uintptr_t>(mapped))
               : 1;
}

int cudaDeviceReset()
{
    if (nested_runtime_reset) {
        using destroy_type = int (*)(void*);
        auto destroy = reinterpret_cast<destroy_type>(
            ::dlsym(RTLD_DEFAULT, "cuCtxDestroy"));
        return destroy == nullptr
                   ? 1
                   : destroy(reinterpret_cast<void*>(current_context));
    }
    return destroy_context(current_context);
}

int cudaHostRegister(void*, std::size_t, unsigned int)
{
    return 0;
}

int cudaHostGetDevicePointer(void** device, void* host, unsigned int)
{
    if (device == nullptr || host == nullptr) {
        return 1;
    }
    *device = host;
    return 0;
}

int cudaDeviceSynchronize()
{
    ++synchronize_count;
    return synchronize_fails ? 1 : 0;
}

int cuCtxSynchronize() { return cudaDeviceSynchronize(); }

int cudaHostUnregister(void*)
{
    ++unregister_count;
    return unregister_fails ? 1 : 0;
}

int cudaThreadExit()
{
    return destroy_context(current_context);
}

int cuFuncGetModule(void** module, void* function)
{
    if(future_image_mode) {
        const auto it=future_functions.find(function);
        if(!module || it==future_functions.end() || !future_images.count(it->second.module))return 1;
        *module=it->second.module;return 0;
    }
    if (!module_loaded || module == nullptr) {
        return 1;
    }
    *module = reinterpret_cast<void*>(fake_module_value);
    return 0;
}

int cuKernelGetFunction(void** function,void* kernel)
{
    const auto it=kernel_function_fixtures.find(kernel);
    if(!function || it==kernel_function_fixtures.end())return 1;
    *function=it->second;return 0;
}

void fakeCudaSetKernelFunction(void* kernel,void* function)
{
    kernel_function_fixtures.insert_or_assign(kernel,function);
}

int cuFuncGetName(const char** name, void* function)
{
    if(future_image_mode) {
        const auto it=future_functions.find(function);
        if(!name || it==future_functions.end() || !future_images.count(it->second.module))return 1;
        *name=it->second.name.c_str();return 0;
    }
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

int fakeCudaImplCtxSetCurrent(std::uintptr_t value)
{
    std::lock_guard capacity_lock(capacity_mutex);
    if (capacity_should_fail_locked("cuCtxSetCurrent")) {
        return 1;
    }
    if (value == 0) {
        ++capacity_explicit_context_clears;
        if (capacity_thread_binding.counted) {
            capacity_thread_binding.counted = false;
            --capacity_worker_contexts;
            capacity_events.push_back(capacity_worker_context_exit);
        }
        current_context = 0;
        current_device = -1;
        return 0;
    }
    int device = -1;
    {
        std::lock_guard domain_lock(domain_mutex);
        const auto live = live_domains.find(value);
        if (live == live_domains.end()) {
            return 1;
        }
        device = live->second;
    }
    current_context = value;
    current_device = device;
    capacity_last_context = value;
    capacity_last_device = device;
    if (!capacity_thread_binding.counted) {
        capacity_thread_binding.counted = true;
        ++capacity_worker_contexts;
    }
    capacity_events.push_back(capacity_context_set);
    return 0;
}

int fakeCudaImplCtxGetCurrent(std::uintptr_t* context)
{
    {
        std::lock_guard lock(capacity_mutex);
        if (capacity_should_fail_locked("cuCtxGetCurrent")) {
            return 1;
        }
    }
    if (context == nullptr || current_context == 0) {
        return 1;
    }
    std::lock_guard lock(domain_mutex);
    const auto live = live_domains.find(current_context);
    if (live == live_domains.end() || live->second != current_device) {
        return 1;
    }
    *context = current_context;
    return 0;
}

int fakeCudaImplCtxGetDevice(int* device)
{
    {
        std::lock_guard lock(capacity_mutex);
        if (capacity_should_fail_locked("cuCtxGetDevice")) {
            return 1;
        }
    }
    if (device == nullptr || current_device < 0) {
        return 1;
    }
    std::lock_guard lock(domain_mutex);
    const auto live = live_domains.find(current_context);
    if (live == live_domains.end() || live->second != current_device) {
        return 1;
    }
    *device = current_device;
    return 0;
}

int cuPointerGetAttribute(void* data, int attribute, std::uintptr_t)
{
    if (data == nullptr) {
        return 1;
    }
    {
        std::unique_lock lock(pointer_pause_mutex);
        if (pointer_pause_enabled && !pointer_pause_entered) {
            pointer_pause_entered = true;
            pointer_pause_ready.notify_all();
            pointer_pause_ready.wait(lock,
                                     [] { return pointer_pause_released; });
        }
    }
    switch (attribute) {
    case 1:
        *static_cast<void**>(data) =
            reinterpret_cast<void*>(pointer_context);
        return 0;
    case 2:
        *static_cast<unsigned int*>(data) = pointer_memory_type;
        return 0;
    case 8:
        *static_cast<unsigned int*>(data) = pointer_is_managed;
        return 0;
    case 9:
        *static_cast<int*>(data) = pointer_device;
        return 0;
    default:
        return 1;
    }
}

int fake_mem_get_address_range(std::uintptr_t* base, std::size_t* size)
{
    if (base == nullptr || size == nullptr || allocation_base == 0 ||
        allocation_bytes == 0) {
        return 1;
    }
    *base = allocation_base;
    *size = allocation_bytes;
    return 0;
}

int cuMemGetAddressRange(std::uintptr_t* base, std::size_t* size,
                         std::uintptr_t)
{
    return fake_mem_get_address_range(base, size);
}

int cuMemGetAddressRange_v2(std::uintptr_t* base, std::size_t* size,
                            std::uintptr_t)
{
    return fake_mem_get_address_range(base, size);
}

int cuModuleGetGlobal_v2(std::uintptr_t* address, std::size_t* size, void* module,
                         const char* name)
{
    if (!module_loaded || address == nullptr || size == nullptr ||
        name == nullptr) {
        return 1;
    }
    if(future_image_mode) {
        const auto it=future_images.find(module);
        if(it==future_images.end())return 1;
        auto& m=*it->second;
        const auto value=[&](void* data,std::size_t bytes) {
            *address=reinterpret_cast<std::uintptr_t>(data);*size=bytes;return 0;
        };
        if(std::strcmp(name,"__hbfsim_module_identity")==0)return value(m.identity.data(),m.identity.size());
        if(std::strcmp(name,"__hbfsim_control")==0)return value(&m.alias,sizeof(m.alias));
        if(std::strcmp(name,"__hbfsim_control_generation")==0)return value(&m.generation,sizeof(m.generation));
        if(std::strcmp(name,"__hbfsim_timing_future_requirements_v1")==0)return value(&m.requirements,sizeof(m.requirements));
        if(std::strcmp(name,"__hbfsim_timing_future_helper_abi_v1")==0)return value(&m.helper,sizeof(m.helper));
        if(std::strcmp(name,"__hbfsim_timing_future_config_v1")==0)return value(&m.config,sizeof(m.config));
        if(std::strcmp(name,"__hbfsim_timing_future_counters_v1")==0)return value(&m.counters,sizeof(m.counters));
        if(std::strcmp(name,"__hbfsim_timing_future_trace_v1")==0)return value(m.trace,sizeof(m.trace));
        if(std::strcmp(name,"__hbfsim_timing_future_helper_sha256_v1")==0)return value(m.helper_hash.data(),m.helper_hash.size());
        if(std::strncmp(name,"__hbfsim_timing_future_kernel_",30)==0 && future_kernel_lookup_failure)
            return future_kernel_lookup_failure;
        const auto kernel=m.kernels.find(name);
        if(kernel!=m.kernels.end())return value(kernel->second.data(),kernel->second.size());
        return 500; // Exact CUDA_ERROR_NOT_FOUND, distinct from inaccessible.
    }
    if (std::strcmp(name, "__hbfsim_module_identity") == 0) {
        if (!marker_available) {
            return 1;
        }
        *address = reinterpret_cast<std::uintptr_t>(module_identity.data());
        *size = module_identity.size();
        return 0;
    }
    if (std::strcmp(name,"__hbfsim_timing_future_requirements_v1")==0) {
        if (!future_contract_mode) return 500; // CUDA_ERROR_NOT_FOUND
        if (future_contract_mode==6) return 999; // Inaccessible is not absent.
        *address=reinterpret_cast<std::uintptr_t>(&future_requirements);*size=sizeof(future_requirements);return 0;
    }
    if (future_contract_mode && future_contract_mode!=13 && std::strcmp(name,"__hbfsim_timing_future_helper_abi_v1")==0) {
        *address=reinterpret_cast<std::uintptr_t>(&future_helper_abi);*size=sizeof(future_helper_abi)-(future_contract_mode==14?8:0);return 0;
    }
    if (future_contract_mode && future_contract_mode!=3 && std::strcmp(name,"__hbfsim_timing_future_config_v1")==0) {
        *address=reinterpret_cast<std::uintptr_t>(&future_config);
        *size=future_contract_mode==4 ? 12 : sizeof(future_config);return 0;
    }
    if(future_contract_mode && future_contract_mode!=7 && std::strcmp(name,"__hbfsim_timing_future_trace_v1")==0) {
        *address=reinterpret_cast<std::uintptr_t>(future_trace)+(future_contract_mode==11?1:0);
        *size=sizeof(future_trace)-(future_contract_mode==8?64:0);return 0;
    }
    if(future_contract_mode && std::strcmp(name,"__hbfsim_timing_future_counters_v1")==0) {
        *address=reinterpret_cast<std::uintptr_t>(&future_counters);
        *size=sizeof(future_counters)-(future_contract_mode==9?8:0);return 0;
    }
    if(future_contract_mode && std::strcmp(name,"__hbfsim_timing_future_helper_sha256_v1")==0) {
        *address=reinterpret_cast<std::uintptr_t>(future_helper_hash.data());*size=future_helper_hash.size();return 0;
    }
    if(future_contract_mode && future_contract_mode!=12 && std::strncmp(name,"__hbfsim_timing_future_kernel_",30)==0) {
        *address=reinterpret_cast<std::uintptr_t>(future_kernel_hash.data());*size=future_kernel_hash.size();return 0;
    }
    if (!control_symbols_available) {
        return 1;
    }
    if (std::strcmp(name, "__hbfsim_control") == 0) {
        *address = reinterpret_cast<std::uintptr_t>(&control_alias);
        *size = sizeof(control_alias);
        return 0;
    }
    if (std::strcmp(name, "__hbfsim_control_generation") == 0) {
        *address = reinterpret_cast<std::uintptr_t>(&control_generation);
        *size = sizeof(control_generation);
        return 0;
    }
    return 1;
}

int fakeCudaImplMemGetAllocationGranularity(std::size_t* granularity,
                                            int device)
{
    std::lock_guard lock(capacity_mutex);
    if (granularity == nullptr || !capacity_valid_current_locked(device) ||
        capacity_should_fail_locked("cuMemGetAllocationGranularity")) {
        return 1;
    }
    *granularity = 16 * 1024;
    capacity_events.push_back(capacity_granularity);
    return 0;
}

int fakeCudaImplMemAddressReserve(std::uintptr_t* address, std::size_t bytes,
                                  std::size_t alignment,
                                  std::uintptr_t requested,
                                  std::uint64_t flags)
{
    std::lock_guard lock(capacity_mutex);
    if (address == nullptr || bytes == 0 || alignment == 0 || requested != 0 ||
        flags != 0 || !capacity_valid_current_locked(current_device) ||
        capacity_should_fail_locked("cuMemAddressReserve")) {
        return 1;
    }
    void* storage = nullptr;
    if (::posix_memalign(&storage, alignment, bytes) != 0 || storage == nullptr) {
        return 1;
    }
    std::memset(storage, 0, bytes);
    const auto base = reinterpret_cast<std::uintptr_t>(storage);
    capacity_reservations.emplace(
        base, CapacityReservation{.bytes = bytes,
                                  .alignment = alignment,
                                  .device = current_device});
    *address = base;
    capacity_events.push_back(capacity_reserve);
    return 0;
}

int fakeCudaImplMemAddressFree(std::uintptr_t address, std::size_t bytes)
{
    std::lock_guard lock(capacity_mutex);
    const auto found = capacity_reservations.find(address);
    if (found == capacity_reservations.end() || found->second.bytes != bytes ||
        found->second.mapped ||
        !capacity_valid_current_locked(found->second.device) ||
        capacity_should_fail_locked("cuMemAddressFree")) {
        return 1;
    }
    ::free(reinterpret_cast<void*>(address));
    capacity_reservations.erase(found);
    capacity_events.push_back(capacity_address_free);
    return 0;
}

int fakeCudaImplMemCreate(std::uint64_t* handle, std::size_t bytes,
                          std::uint64_t flags, int device)
{
    std::lock_guard lock(capacity_mutex);
    if (handle == nullptr || bytes == 0 || flags != 0 ||
        !capacity_valid_current_locked(device) ||
        capacity_should_fail_locked("cuMemCreate")) {
        return 1;
    }
    const auto value = capacity_next_handle++;
    capacity_handles.emplace(value,
                             CapacityHandle{.bytes = bytes, .device = device});
    *handle = value;
    capacity_events.push_back(capacity_create);
    return 0;
}

int fakeCudaImplMemRelease(std::uint64_t handle)
{
    std::lock_guard lock(capacity_mutex);
    const auto found = capacity_handles.find(handle);
    const auto still_mapped = std::any_of(
        capacity_reservations.begin(), capacity_reservations.end(),
        [&](const auto& item) {
            return item.second.mapped && item.second.handle == handle;
        });
    if (found == capacity_handles.end() || still_mapped ||
        !capacity_valid_current_locked(found->second.device) ||
        capacity_should_fail_locked("cuMemRelease")) {
        return 1;
    }
    capacity_handles.erase(found);
    capacity_events.push_back(capacity_release);
    return 0;
}

int fakeCudaImplMemMap(std::uintptr_t address, std::size_t bytes,
                       std::size_t offset, std::uint64_t handle,
                       std::uint64_t flags)
{
    std::lock_guard lock(capacity_mutex);
    const auto reservation = capacity_reservations.find(address);
    const auto allocation = capacity_handles.find(handle);
    if (reservation == capacity_reservations.end() ||
        allocation == capacity_handles.end() || reservation->second.mapped ||
        reservation->second.bytes != bytes ||
        allocation->second.bytes != bytes ||
        reservation->second.device != allocation->second.device ||
        !capacity_valid_current_locked(reservation->second.device) ||
        offset != 0 || flags != 0 ||
        capacity_should_fail_locked("cuMemMap")) {
        return 1;
    }
    reservation->second.mapped = true;
    reservation->second.handle = handle;
    capacity_events.push_back(capacity_map);
    return 0;
}

int fakeCudaImplMemUnmap(std::uintptr_t address, std::size_t bytes)
{
    std::lock_guard lock(capacity_mutex);
    const auto reservation = capacity_reservations.find(address);
    if (reservation == capacity_reservations.end() ||
        reservation->second.bytes != bytes || !reservation->second.mapped ||
        !capacity_valid_current_locked(reservation->second.device) ||
        capacity_should_fail_locked("cuMemUnmap")) {
        return 1;
    }
    reservation->second.mapped = false;
    reservation->second.accessible = false;
    reservation->second.access_device = -1;
    reservation->second.handle = 0;
    capacity_events.push_back(capacity_unmap);
    return 0;
}

int fakeCudaImplMemSetAccess(std::uintptr_t address, std::size_t bytes,
                             std::size_t count, int device)
{
    std::lock_guard lock(capacity_mutex);
    const auto reservation = capacity_reservations.find(address);
    if (reservation == capacity_reservations.end() ||
        reservation->second.bytes != bytes || !reservation->second.mapped ||
        reservation->second.device != device ||
        !capacity_valid_current_locked(device) || count != 1 ||
        capacity_should_fail_locked("cuMemSetAccess")) {
        return 1;
    }
    reservation->second.accessible = true;
    reservation->second.access_device = device;
    capacity_events.push_back(capacity_set_access);
    return 0;
}

int fakeCudaImplHostAlloc(void** data, std::size_t bytes)
{
    std::lock_guard lock(capacity_mutex);
    if (data == nullptr || bytes == 0 ||
        capacity_should_fail_locked("cudaHostAlloc")) {
        return 1;
    }
    void* storage = std::malloc(bytes);
    if (storage == nullptr) {
        return 1;
    }
    capacity_pinned.emplace(storage, bytes);
    *data = storage;
    capacity_events.push_back(capacity_host_alloc);
    return 0;
}

int fakeCudaImplFreeHost(void* data)
{
    std::lock_guard lock(capacity_mutex);
    const auto found = capacity_pinned.find(data);
    if (found == capacity_pinned.end() ||
        capacity_should_fail_locked("cudaFreeHost")) {
        return 1;
    }
    std::free(data);
    capacity_pinned.erase(found);
    capacity_events.push_back(capacity_host_free);
    return 0;
}

int fakeCudaImplMemcpyHtoD(std::uintptr_t destination, const void* source,
                           std::size_t size)
{
    {
        std::lock_guard lock(capacity_mutex);
        if (source != nullptr &&
            capacity_device_range_locked(destination, size)) {
            if (capacity_should_fail_locked("cuMemcpyHtoD_v2")) {
                return 1;
            }
            std::memcpy(reinterpret_cast<void*>(destination), source, size);
            ++capacity_htod_calls;
            capacity_events.push_back(capacity_htod);
            return 0;
        }
    }
    ++control_copy_calls;
    if(future_image_mode) {
        if(control_copy_fails || (control_copy_fail_position && control_copy_calls==control_copy_fail_position) ||
           !source || !future_image_memory(destination,size,true))return 1;
        std::memcpy(reinterpret_cast<void*>(destination),source,size);return 0;
    }
    if(future_contract_mode) {
        int event=0;
        if(destination==reinterpret_cast<std::uintptr_t>(&future_config)+offsetof(hbfsim::timing_future::ModuleConfig,enabled) && size==4)
            event=*static_cast<const std::uint32_t*>(source)?6:1;
        else if(destination==reinterpret_cast<std::uintptr_t>(&future_counters))event=2;
        else if(destination==reinterpret_cast<std::uintptr_t>(&future_config))event=3;
        else if(destination==reinterpret_cast<std::uintptr_t>(&control_generation))event=4;
        else if(destination==reinterpret_cast<std::uintptr_t>(&control_alias))event=5;
        future_copy_events.push_back(event);
    }
    if (control_copy_fails ||
        (control_copy_fail_position != 0 &&
         control_copy_calls == control_copy_fail_position) ||
        destination == 0 || source == nullptr ||
        (size != sizeof(std::uint64_t) && !(destination==reinterpret_cast<std::uintptr_t>(&future_counters) && size==sizeof(future_counters)) &&
         !(future_contract_mode && destination>=reinterpret_cast<std::uintptr_t>(&future_config) &&
           destination+size<=reinterpret_cast<std::uintptr_t>(&future_config)+sizeof(future_config)))) {
        return 1;
    }
    std::memcpy(reinterpret_cast<void*>(destination), source, size);
    return 0;
}

int fakeCudaImplMemcpyDtoH(void* destination, std::uintptr_t source,
                           std::size_t size)
{
    {
        std::lock_guard lock(capacity_mutex);
        if (destination != nullptr &&
            capacity_device_range_locked(source, size)) {
            if (capacity_should_fail_locked("cuMemcpyDtoH_v2")) {
                return 1;
            }
            std::memcpy(destination, reinterpret_cast<const void*>(source),
                        size);
            ++capacity_dtoh_calls;
            capacity_events.push_back(capacity_dtoh);
            return 0;
        }
    }
    if(future_image_mode) {
        if(!destination || !future_image_memory(source,size,false))return 1;
        std::memcpy(destination,reinterpret_cast<const void*>(source),size);return 0;
    }
    if (destination == nullptr || source == 0 ||
        (size != module_identity.size() && !(source==reinterpret_cast<std::uintptr_t>(&future_counters) && size==sizeof(future_counters)) &&
         !(future_contract_mode && ((source==reinterpret_cast<std::uintptr_t>(&future_requirements) && size==sizeof(future_requirements)) ||
           (source==reinterpret_cast<std::uintptr_t>(&future_helper_abi) && size==sizeof(future_helper_abi)) ||
           (source==reinterpret_cast<std::uintptr_t>(&future_config) && size==sizeof(future_config)))))) {
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
    return launch_error;
}

int cuLaunchKernelEx(const void*, void*, void**, void**)
{
    ++launch_count;
    return 0;
}

int cuLaunchKernelEx_ptsz(const void* config, void* function,
                          void** parameters, void** extra)
{
    return cuLaunchKernelEx(config, function, parameters, extra);
}

int cuLaunch(void*) { ++launch_count; return 0; }
int cuLaunchGrid(void*,int,int) { ++launch_count; return 0; }
int cuLaunchGridAsync(void*,int,int,void*) { ++launch_count; return 0; }
int cuGraphLaunch(void*,void*) { ++launch_count; return 0; }
int cudaGraphLaunch(void*,void*) { ++launch_count; return 0; }

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

void fakeCudaSetCurrentDomain(std::uintptr_t context, int device)
{
    current_context = context;
    current_device = device;
    if (context != 0 && device >= 0) {
        std::lock_guard lock(domain_mutex);
        live_domains[context] = device;
    }
}

void fakeCudaPausePointerValidation()
{
    std::lock_guard lock(pointer_pause_mutex);
    pointer_pause_enabled = true;
    pointer_pause_entered = false;
    pointer_pause_released = false;
}

void fakeCudaWaitPointerValidationEntered()
{
    std::unique_lock lock(pointer_pause_mutex);
    pointer_pause_ready.wait(lock, [] { return pointer_pause_entered; });
}

void fakeCudaReleasePointerValidation()
{
    std::lock_guard lock(pointer_pause_mutex);
    pointer_pause_released = true;
    pointer_pause_ready.notify_all();
}

void fakeCudaPauseLifecycle()
{
    std::lock_guard lock(lifecycle_pause_mutex);
    lifecycle_pause_enabled = true;
    lifecycle_pause_entered = false;
    lifecycle_pause_released = false;
}

void fakeCudaWaitLifecycleEntered()
{
    std::unique_lock lock(lifecycle_pause_mutex);
    lifecycle_pause_ready.wait(lock, [] { return lifecycle_pause_entered; });
}

void fakeCudaReleaseLifecycle()
{
    std::lock_guard lock(lifecycle_pause_mutex);
    lifecycle_pause_released = true;
    lifecycle_pause_ready.notify_all();
}

void fakeCudaSetNestedRuntimeReset(int enabled)
{
    nested_runtime_reset = enabled != 0;
}

void fakeCudaSetPointerMetadata(std::uintptr_t context, int device,
                                unsigned int memory_type,
                                unsigned int is_managed,
                                std::uintptr_t base, std::size_t bytes)
{
    pointer_context = context;
    pointer_device = device;
    pointer_memory_type = memory_type;
    pointer_is_managed = is_managed;
    allocation_base = base;
    allocation_bytes = bytes;
}

void fakeCudaSetControlSymbolsAvailable(int available)
{
    control_symbols_available = available != 0;
}

void fakeCudaSetControlCopyFailure(int fail)
{
    control_copy_fails = fail != 0;
    control_copy_calls = 0;
}

void fakeCudaSetFutureContract(int mode)
{
    future_contract_mode=mode;future_requirements={};future_helper_abi={};future_config={};future_counters={};future_copy_events.clear();
    if(mode==2)future_requirements.token_bytes=80;
    if(mode==5)future_helper_abi.metadata_version=2;
}
int fakeCudaSetFutureHelperHash(const void* data,std::size_t bytes) {
    if(!data || bytes!=future_helper_hash.size())return -1;
    std::memcpy(future_helper_hash.data(),data,bytes);return 0;
}
int fakeCudaSetFutureKernelHash(const void* data,std::size_t bytes) {
    if(!data || bytes!=future_kernel_hash.size())return -1;
    std::memcpy(future_kernel_hash.data(),data,bytes);return 0;
}
int fakeCudaFutureCopyCount(){return static_cast<int>(future_copy_events.size());}
int fakeCudaFutureCopyEvent(unsigned index){return index<future_copy_events.size()?future_copy_events[index]:-1;}
std::uint32_t fakeCudaFutureEnabled() { return future_config.enabled; }

void fakeCudaEnableImageFixtures() { future_image_mode=true; }
void* fakeCudaImageFunction(void* module,const char* name) {
    if(!future_image_mode || !future_images.count(module) || !name)return nullptr;
    auto function=reinterpret_cast<void*>(next_function_handle++);
    future_functions.emplace(function,FutureFunction{module,name});return function;
}
std::uint32_t fakeCudaImageEnabled(void* module) {
    auto it=future_images.find(module);return it==future_images.end()?0:it->second->config.enabled;
}
void fakeCudaImageKernelLookupFailure(int code) { future_kernel_lookup_failure=code; }

void fakeCudaSetControlCopyFailurePosition(int position)
{
    control_copy_fail_position = position;
    control_copy_calls = 0;
}

std::uint64_t fakeCudaControlAlias()
{
    return control_alias;
}

std::uint64_t fakeCudaControlGeneration()
{
    return control_generation;
}

int fakeCudaLaunchCount()
{
    return launch_count;
}
void fakeCudaSetLaunchFailure(int error){launch_error=error;}

void fakeCudaSetHostUnregisterFailure(int fail)
{
    unregister_fails = fail != 0;
}

void fakeCudaSetSynchronizeFailure(int fail)
{
    synchronize_fails = fail != 0;
}

void fakeCudaResetLifecycleCounts()
{
    synchronize_count = 0;
    unregister_count = 0;
}

int fakeCudaSynchronizeCount()
{
    return synchronize_count;
}

int fakeCudaUnregisterCount()
{
    return unregister_count;
}

int fakeCudaSetModuleIdentity(const std::uint8_t* identity, std::size_t size)
{
    if (identity == nullptr || size != module_identity.size()) {
        return 1;
    }
    std::memcpy(module_identity.data(), identity, module_identity.size());
    return 0;
}

void fakeCudaCapacityReset()
{
    std::lock_guard lock(capacity_mutex);
    for (const auto& [address, reservation] : capacity_reservations) {
        (void)reservation;
        ::free(reinterpret_cast<void*>(address));
    }
    for (const auto& [data, bytes] : capacity_pinned) {
        (void)bytes;
        ::free(data);
    }
    capacity_reservations.clear();
    capacity_handles.clear();
    capacity_pinned.clear();
    capacity_fail_at.clear();
    capacity_calls.clear();
    capacity_events.clear();
    capacity_next_handle = 1;
    capacity_htod_calls = 0;
    capacity_dtoh_calls = 0;
    capacity_worker_contexts = 0;
    capacity_explicit_context_clears = 0;
    capacity_last_context = 0;
    capacity_last_device = -1;
}

void fakeCudaCapacityFail(const char* operation, int call)
{
    std::lock_guard lock(capacity_mutex);
    if (operation == nullptr || call <= 0) {
        return;
    }
    capacity_fail_at[operation] = call;
    capacity_calls[operation] = 0;
}

std::size_t fakeCudaCapacityLiveReservations()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_reservations.size();
}

std::size_t fakeCudaCapacityLiveHandles()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_handles.size();
}

std::size_t fakeCudaCapacityLivePinnedBuffers()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_pinned.size();
}

std::size_t fakeCudaCapacityLiveWorkerContexts()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_worker_contexts;
}

std::size_t fakeCudaCapacityExplicitContextClears()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_explicit_context_clears;
}

std::size_t fakeCudaCapacityReservedBytes()
{
    std::lock_guard lock(capacity_mutex);
    std::size_t result = 0;
    for (const auto& [address, reservation] : capacity_reservations) {
        (void)address;
        result += reservation.bytes;
    }
    return result;
}

std::size_t fakeCudaCapacityPinnedBytes()
{
    std::lock_guard lock(capacity_mutex);
    std::size_t result = 0;
    for (const auto& [data, bytes] : capacity_pinned) {
        (void)data;
        result += bytes;
    }
    return result;
}

std::size_t fakeCudaCapacityHtoDCalls()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_htod_calls;
}

std::size_t fakeCudaCapacityDtoHCalls()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_dtoh_calls;
}

std::uintptr_t fakeCudaCapacityLastContext()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_last_context;
}

int fakeCudaCapacityLastDevice()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_last_device;
}

int fakeCudaCapacityReadDevice(std::uintptr_t address, void* bytes,
                               std::size_t size)
{
    std::lock_guard lock(capacity_mutex);
    if (bytes == nullptr || !capacity_device_range_locked(address, size)) {
        return 1;
    }
    std::memcpy(bytes, reinterpret_cast<const void*>(address), size);
    return 0;
}

int fakeCudaCapacityWriteDevice(std::uintptr_t address, const void* bytes,
                                std::size_t size)
{
    std::lock_guard lock(capacity_mutex);
    if (bytes == nullptr || !capacity_device_range_locked(address, size)) {
        return 1;
    }
    std::memcpy(reinterpret_cast<void*>(address), bytes, size);
    return 0;
}

std::size_t fakeCudaCapacityEventCount()
{
    std::lock_guard lock(capacity_mutex);
    return capacity_events.size();
}

int fakeCudaCapacityEvent(std::size_t index)
{
    std::lock_guard lock(capacity_mutex);
    return index < capacity_events.size() ? capacity_events[index] : 0;
}
}

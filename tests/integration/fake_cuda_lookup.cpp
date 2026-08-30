#include <algorithm>
#include <array>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

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
int launch_count = 0;
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
std::unordered_map<std::uintptr_t, CapacityReservation> capacity_reservations;
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

bool capacity_should_fail_locked(const char *operation) {
    const auto call = ++capacity_calls[operation];
    const auto failure = capacity_fail_at.find(operation);
    return failure != capacity_fail_at.end() && failure->second == call;
}

bool capacity_valid_current_locked(int device) {
    if (device < 0 || current_context == 0 || current_device != device) {
        return false;
    }
    std::lock_guard domain_lock(domain_mutex);
    const auto live = live_domains.find(current_context);
    return live != live_domains.end() && live->second == device;
}

auto capacity_reservation_for_locked(std::uintptr_t address,
                                     std::size_t bytes) {
  return std::find_if(capacity_reservations.begin(),
                      capacity_reservations.end(), [&](const auto &item) {
            const auto base = item.first;
            const auto size = item.second.bytes;
            return address >= base && bytes <= size &&
                   address - base <= size - bytes;
        });
}

bool capacity_device_range_locked(std::uintptr_t address, std::size_t bytes) {
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

static int lookup(const char *symbol, void **function) {
    *function = reinterpret_cast<void*>(static_cast<std::uintptr_t>(0x1234));
    return symbol != nullptr && std::strncmp(symbol, "cu", 2) == 0 &&
                   std::strncmp(symbol, "cuda", 4) != 0
               ? 0
               : 1;
}

int cuDeviceGetAttribute(int *value, int attribute, int device) {
  if (value == nullptr || device < 0) {
    return 1;
  }
  *value = attribute == 86 ? 1 : 0;
  return 0;
}

int cuGetProcAddress(const char* symbol, void** function, int version,
                     std::uint64_t flags) {
    (void)version;
    (void)flags;
    return lookup(symbol, function);
}

int cuGetProcAddress_v2(const char* symbol, void** function, int version,
                        std::uint64_t flags, int *status) {
    (void)version;
    (void)flags;
    const int result = lookup(symbol, function);
    if (status != nullptr) {
        *status = result == 0 ? 0 : 1;
    }
    return result;
}

int cudaGetDriverEntryPoint(const char* symbol, void** function,
                            std::uint64_t flags, int *status) {
    (void)flags;
    const int result = lookup(symbol, function);
    if (status != nullptr) {
        *status = result == 0 ? 0 : 1;
    }
    return result;
}

int cudaGetDriverEntryPoint_ptsz(const char* symbol, void** function,
                                 std::uint64_t flags, int *status) {
    return cudaGetDriverEntryPoint(symbol, function, flags, status);
}

int cudaGetDriverEntryPointByVersion(const char* symbol, void** function,
                                     unsigned int version, std::uint64_t flags,
                                     int *status) {
    (void)version;
    return cudaGetDriverEntryPoint(symbol, function, flags, status);
}

int cudaGetDriverEntryPointByVersion_ptsz(const char* symbol, void** function,
                                          unsigned int version,
                                          std::uint64_t flags, int *status) {
    return cudaGetDriverEntryPointByVersion(symbol, function, version, flags,
                                            status);
}

int cuModuleLoadDataEx(void** module, const void* image, unsigned int, void*,
                       void **) {
    if (module == nullptr || image == nullptr) {
        return 1;
    }
    const auto* image_name = static_cast<const char*>(image);
    if (std::strcmp(image_name, "fail") == 0 ||
        std::strcmp(image_name, "fail-a") == 0) {
        return 1;
    }
    module_loaded = true;
    control_alias = 0;
    control_generation = 0;
    *module = reinterpret_cast<void*>(fake_module_value);
    return 0;
}

int cuModuleUnload(void *module) {
  if (module != reinterpret_cast<void *>(fake_module_value) || !module_loaded ||
      unload_fails) {
        return 1;
    }
    module_loaded = false;
    return 0;
}

int destroy_context(std::uintptr_t target_context) {
    {
        std::unique_lock lock(lifecycle_pause_mutex);
        if (lifecycle_pause_enabled) {
            lifecycle_pause_entered = true;
            lifecycle_pause_ready.notify_all();
      lifecycle_pause_ready.wait(lock, [] { return lifecycle_pause_released; });
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
        module_loaded = false;
        current_context = 0;
        current_device = -1;
    }
    return 0;
}

int cuCtxDestroy(void *context) {
    return destroy_context(reinterpret_cast<std::uintptr_t>(context));
}

int cuCtxDestroy_v2(void *context) {
    return destroy_context(reinterpret_cast<std::uintptr_t>(context));
}

int cuCtxDetach(void *context) {
    return destroy_context(reinterpret_cast<std::uintptr_t>(context));
}

int destroy_device(int device) {
    return destroy_context(device == current_device ? current_context : 0);
}

int cuDevicePrimaryCtxReset(int device) { return destroy_device(device); }

int cuDevicePrimaryCtxReset_v2(int device) { return destroy_device(device); }

int cuDevicePrimaryCtxRelease(int device) { return destroy_device(device); }

int cuDevicePrimaryCtxRelease_v2(int device) { return destroy_device(device); }

int cuCtxFromGreenCtx(void **context, void *green_context) {
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

int cuGreenCtxDestroy(void *context) {
    void* mapped = nullptr;
    return cuCtxFromGreenCtx(&mapped, context) == 0
               ? destroy_context(reinterpret_cast<std::uintptr_t>(mapped))
               : 1;
}

int cudaDeviceReset() {
    if (nested_runtime_reset) {
        using destroy_type = int (*)(void*);
    auto destroy =
        reinterpret_cast<destroy_type>(::dlsym(RTLD_DEFAULT, "cuCtxDestroy"));
        return destroy == nullptr
                   ? 1
                   : destroy(reinterpret_cast<void*>(current_context));
    }
    return destroy_context(current_context);
}

int cudaHostRegister(void *, std::size_t, unsigned int) { return 0; }

int cudaHostGetDevicePointer(void **device, void *host, unsigned int) {
    if (device == nullptr || host == nullptr) {
        return 1;
    }
    *device = host;
    return 0;
}

int cudaDeviceSynchronize() {
    ++synchronize_count;
    return synchronize_fails ? 1 : 0;
}

int cudaHostUnregister(void *) {
    ++unregister_count;
    return unregister_fails ? 1 : 0;
}

int cudaThreadExit() { return destroy_context(current_context); }

int cuFuncGetModule(void **module, void *) {
    if (!module_loaded || module == nullptr) {
        return 1;
    }
    *module = reinterpret_cast<void*>(fake_module_value);
    return 0;
}

int cuFuncGetName(const char **name, void *) {
    if (!module_loaded || name == nullptr) {
        return 1;
    }
    *name = "kernel";
    return 0;
}

int cuFuncGetParamInfo(void*, std::size_t index, std::size_t* offset,
                       std::size_t *size) {
    if (!module_loaded || index != 0 || offset == nullptr || size == nullptr) {
        return 1;
    }
    *offset = 0;
    *size = sizeof(std::uintptr_t);
    return 0;
}

int fakeCudaImplCtxSetCurrent(std::uintptr_t value) {
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

int fakeCudaImplCtxGetCurrent(std::uintptr_t *context) {
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

int fakeCudaImplCtxGetDevice(int *device) {
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

int cuPointerGetAttribute(void *data, int attribute, std::uintptr_t) {
    if (data == nullptr) {
        return 1;
    }
    {
        std::unique_lock lock(pointer_pause_mutex);
        if (pointer_pause_enabled && !pointer_pause_entered) {
            pointer_pause_entered = true;
            pointer_pause_ready.notify_all();
      pointer_pause_ready.wait(lock, [] { return pointer_pause_released; });
        }
    }
    switch (attribute) {
    case 1:
    *static_cast<void **>(data) = reinterpret_cast<void *>(pointer_context);
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

int fake_mem_get_address_range(std::uintptr_t *base, std::size_t *size) {
    if (base == nullptr || size == nullptr || allocation_base == 0 ||
        allocation_bytes == 0) {
        return 1;
    }
    *base = allocation_base;
    *size = allocation_bytes;
    return 0;
}

int cuMemGetAddressRange(std::uintptr_t* base, std::size_t* size,
                         std::uintptr_t) {
    return fake_mem_get_address_range(base, size);
}

int cuMemGetAddressRange_v2(std::uintptr_t* base, std::size_t* size,
                            std::uintptr_t) {
    return fake_mem_get_address_range(base, size);
}

int cuModuleGetGlobal_v2(std::uintptr_t* address, std::size_t* size, void*,
                         const char *name) {
    if (!module_loaded || address == nullptr || size == nullptr ||
        name == nullptr) {
        return 1;
    }
    if (std::strcmp(name, "__hbfsim_module_identity") == 0) {
        if (!marker_available) {
            return 1;
        }
        *address = reinterpret_cast<std::uintptr_t>(module_identity.data());
        *size = module_identity.size();
        return 0;
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
                                            int device) {
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
                                  std::uint64_t flags) {
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
  capacity_reservations.emplace(base,
                                CapacityReservation{.bytes = bytes,
                                  .alignment = alignment,
                                  .device = current_device});
    *address = base;
    capacity_events.push_back(capacity_reserve);
    return 0;
}

int fakeCudaImplMemAddressFree(std::uintptr_t address, std::size_t bytes) {
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
                          std::uint64_t flags, int device) {
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

int fakeCudaImplMemRelease(std::uint64_t handle) {
    std::lock_guard lock(capacity_mutex);
    const auto found = capacity_handles.find(handle);
  const auto still_mapped =
      std::any_of(capacity_reservations.begin(), capacity_reservations.end(),
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
                       std::uint64_t flags) {
    std::lock_guard lock(capacity_mutex);
    const auto reservation = capacity_reservations.find(address);
    const auto allocation = capacity_handles.find(handle);
    if (reservation == capacity_reservations.end() ||
        allocation == capacity_handles.end() || reservation->second.mapped ||
      reservation->second.bytes != bytes || allocation->second.bytes != bytes ||
        reservation->second.device != allocation->second.device ||
        !capacity_valid_current_locked(reservation->second.device) ||
      offset != 0 || flags != 0 || capacity_should_fail_locked("cuMemMap")) {
        return 1;
    }
    reservation->second.mapped = true;
    reservation->second.handle = handle;
    capacity_events.push_back(capacity_map);
    return 0;
}

int fakeCudaImplMemUnmap(std::uintptr_t address, std::size_t bytes) {
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
                             std::size_t count, int device) {
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

int fakeCudaImplHostAlloc(void **data, std::size_t bytes) {
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

int fakeCudaImplFreeHost(void *data) {
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
                           std::size_t size) {
    {
        std::lock_guard lock(capacity_mutex);
    if (source != nullptr && capacity_device_range_locked(destination, size)) {
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
    if (control_copy_fails ||
        (control_copy_fail_position != 0 &&
         control_copy_calls == control_copy_fail_position) ||
      destination == 0 || source == nullptr || size != sizeof(std::uint64_t)) {
        return 1;
    }
    std::memcpy(reinterpret_cast<void*>(destination), source, size);
    return 0;
}

int fakeCudaImplMemcpyDtoH(void* destination, std::uintptr_t source,
                           std::size_t size) {
    {
        std::lock_guard lock(capacity_mutex);
    if (destination != nullptr && capacity_device_range_locked(source, size)) {
            if (capacity_should_fail_locked("cuMemcpyDtoH_v2")) {
                return 1;
            }
      std::memcpy(destination, reinterpret_cast<const void *>(source), size);
            ++capacity_dtoh_calls;
            capacity_events.push_back(capacity_dtoh);
            return 0;
        }
    }
  if (destination == nullptr || source == 0 || size != module_identity.size()) {
        return 1;
    }
    std::memcpy(destination, reinterpret_cast<const void*>(source), size);
    return 0;
}

int cuLaunchKernel(void*, unsigned int, unsigned int, unsigned int,
                   unsigned int, unsigned int, unsigned int, unsigned int,
                   void *, void **, void **) {
    ++launch_count;
    return 0;
}

int cuLaunchKernelEx(const void *, void *, void **, void **) {
    ++launch_count;
    return 0;
}

int cuLaunchKernelEx_ptsz(const void *config, void *function, void **parameters,
                          void **extra) {
    return cuLaunchKernelEx(config, function, parameters, extra);
}

void fakeCudaSetUnloadFailure(int fail) { unload_fails = fail != 0; }

void fakeCudaSetLifecycleFailure(int fail) { lifecycle_fails = fail != 0; }

void fakeCudaSetMarkerAvailable(int available) {
    marker_available = available != 0;
}

void fakeCudaSetCurrentDomain(std::uintptr_t context, int device) {
    current_context = context;
    current_device = device;
    if (context != 0 && device >= 0) {
        std::lock_guard lock(domain_mutex);
        live_domains[context] = device;
    }
}

void fakeCudaPausePointerValidation() {
    std::lock_guard lock(pointer_pause_mutex);
    pointer_pause_enabled = true;
    pointer_pause_entered = false;
    pointer_pause_released = false;
}

void fakeCudaWaitPointerValidationEntered() {
    std::unique_lock lock(pointer_pause_mutex);
    pointer_pause_ready.wait(lock, [] { return pointer_pause_entered; });
}

void fakeCudaReleasePointerValidation() {
    std::lock_guard lock(pointer_pause_mutex);
    pointer_pause_released = true;
    pointer_pause_ready.notify_all();
}

void fakeCudaPauseLifecycle() {
    std::lock_guard lock(lifecycle_pause_mutex);
    lifecycle_pause_enabled = true;
    lifecycle_pause_entered = false;
    lifecycle_pause_released = false;
}

void fakeCudaWaitLifecycleEntered() {
    std::unique_lock lock(lifecycle_pause_mutex);
    lifecycle_pause_ready.wait(lock, [] { return lifecycle_pause_entered; });
}

void fakeCudaReleaseLifecycle() {
    std::lock_guard lock(lifecycle_pause_mutex);
    lifecycle_pause_released = true;
    lifecycle_pause_ready.notify_all();
}

void fakeCudaSetNestedRuntimeReset(int enabled) {
    nested_runtime_reset = enabled != 0;
}

void fakeCudaSetPointerMetadata(std::uintptr_t context, int device,
                                unsigned int memory_type,
                                unsigned int is_managed, std::uintptr_t base,
                                std::size_t bytes) {
    pointer_context = context;
    pointer_device = device;
    pointer_memory_type = memory_type;
    pointer_is_managed = is_managed;
    allocation_base = base;
    allocation_bytes = bytes;
}

void fakeCudaSetControlSymbolsAvailable(int available) {
    control_symbols_available = available != 0;
}

void fakeCudaSetControlCopyFailure(int fail) {
    control_copy_fails = fail != 0;
    control_copy_calls = 0;
}

void fakeCudaSetControlCopyFailurePosition(int position) {
    control_copy_fail_position = position;
    control_copy_calls = 0;
}

std::uint64_t fakeCudaControlAlias() { return control_alias; }

std::uint64_t fakeCudaControlGeneration() { return control_generation; }

int fakeCudaLaunchCount() { return launch_count; }

void fakeCudaSetHostUnregisterFailure(int fail) {
    unregister_fails = fail != 0;
}

void fakeCudaSetSynchronizeFailure(int fail) { synchronize_fails = fail != 0; }

void fakeCudaResetLifecycleCounts() {
    synchronize_count = 0;
    unregister_count = 0;
}

int fakeCudaSynchronizeCount() { return synchronize_count; }

int fakeCudaUnregisterCount() { return unregister_count; }

int fakeCudaSetModuleIdentity(const std::uint8_t *identity, std::size_t size) {
    if (identity == nullptr || size != module_identity.size()) {
        return 1;
    }
    std::memcpy(module_identity.data(), identity, module_identity.size());
    return 0;
}

void fakeCudaCapacityReset() {
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

void fakeCudaCapacityFail(const char *operation, int call) {
    std::lock_guard lock(capacity_mutex);
    if (operation == nullptr || call <= 0) {
        return;
    }
    capacity_fail_at[operation] = call;
    capacity_calls[operation] = 0;
}

std::size_t fakeCudaCapacityLiveReservations() {
    std::lock_guard lock(capacity_mutex);
    return capacity_reservations.size();
}

std::size_t fakeCudaCapacityLiveHandles() {
    std::lock_guard lock(capacity_mutex);
    return capacity_handles.size();
}

std::size_t fakeCudaCapacityLivePinnedBuffers() {
    std::lock_guard lock(capacity_mutex);
    return capacity_pinned.size();
}

std::size_t fakeCudaCapacityLiveWorkerContexts() {
    std::lock_guard lock(capacity_mutex);
    return capacity_worker_contexts;
}

std::size_t fakeCudaCapacityExplicitContextClears() {
    std::lock_guard lock(capacity_mutex);
    return capacity_explicit_context_clears;
}

std::size_t fakeCudaCapacityReservedBytes() {
    std::lock_guard lock(capacity_mutex);
    std::size_t result = 0;
    for (const auto& [address, reservation] : capacity_reservations) {
        (void)address;
        result += reservation.bytes;
    }
    return result;
}

std::size_t fakeCudaCapacityPinnedBytes() {
    std::lock_guard lock(capacity_mutex);
    std::size_t result = 0;
    for (const auto& [data, bytes] : capacity_pinned) {
        (void)data;
        result += bytes;
    }
    return result;
}

std::size_t fakeCudaCapacityHtoDCalls() {
    std::lock_guard lock(capacity_mutex);
    return capacity_htod_calls;
}

std::size_t fakeCudaCapacityDtoHCalls() {
    std::lock_guard lock(capacity_mutex);
    return capacity_dtoh_calls;
}

std::uintptr_t fakeCudaCapacityLastContext() {
    std::lock_guard lock(capacity_mutex);
    return capacity_last_context;
}

int fakeCudaCapacityLastDevice() {
    std::lock_guard lock(capacity_mutex);
    return capacity_last_device;
}

int fakeCudaCapacityReadDevice(std::uintptr_t address, void* bytes,
                               std::size_t size) {
    std::lock_guard lock(capacity_mutex);
    if (bytes == nullptr || !capacity_device_range_locked(address, size)) {
        return 1;
    }
    std::memcpy(bytes, reinterpret_cast<const void*>(address), size);
    return 0;
}

int fakeCudaCapacityWriteDevice(std::uintptr_t address, const void* bytes,
                                std::size_t size) {
    std::lock_guard lock(capacity_mutex);
    if (bytes == nullptr || !capacity_device_range_locked(address, size)) {
        return 1;
    }
    std::memcpy(reinterpret_cast<void*>(address), bytes, size);
    return 0;
}

std::size_t fakeCudaCapacityEventCount() {
    std::lock_guard lock(capacity_mutex);
    return capacity_events.size();
}

int fakeCudaCapacityEvent(std::size_t index) {
    std::lock_guard lock(capacity_mutex);
    return index < capacity_events.size() ? capacity_events[index] : 0;
}
}

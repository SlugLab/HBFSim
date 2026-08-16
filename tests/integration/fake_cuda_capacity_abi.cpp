#include <cuda.h>
#include <cuda_runtime_api.h>

#include <cstddef>
#include <cstdint>

extern "C" {

int fakeCudaImplCtxSetCurrent(std::uintptr_t context);
int fakeCudaImplCtxGetCurrent(std::uintptr_t* context);
int fakeCudaImplCtxGetDevice(int* device);
int fakeCudaImplMemGetAllocationGranularity(std::size_t* granularity,
                                            int device);
int fakeCudaImplMemAddressReserve(std::uintptr_t* address, std::size_t bytes,
                                  std::size_t alignment,
                                  std::uintptr_t requested,
                                  std::uint64_t flags);
int fakeCudaImplMemAddressFree(std::uintptr_t address, std::size_t bytes);
int fakeCudaImplMemCreate(std::uint64_t* handle, std::size_t bytes,
                          std::uint64_t flags, int device);
int fakeCudaImplMemRelease(std::uint64_t handle);
int fakeCudaImplMemMap(std::uintptr_t address, std::size_t bytes,
                       std::size_t offset, std::uint64_t handle,
                       std::uint64_t flags);
int fakeCudaImplMemUnmap(std::uintptr_t address, std::size_t bytes);
int fakeCudaImplMemSetAccess(std::uintptr_t address, std::size_t bytes,
                             std::size_t count, int device);
int fakeCudaImplHostAlloc(void** data, std::size_t bytes);
int fakeCudaImplFreeHost(void* data);
int fakeCudaImplMemcpyHtoD(std::uintptr_t destination, const void* source,
                           std::size_t size);
int fakeCudaImplMemcpyDtoH(void* destination, std::uintptr_t source,
                           std::size_t size);

CUresult CUDAAPI cuCtxSetCurrent(CUcontext context)
{
    return fakeCudaImplCtxSetCurrent(
               reinterpret_cast<std::uintptr_t>(context)) == 0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_CONTEXT;
}

CUresult CUDAAPI cuCtxGetCurrent(CUcontext* context)
{
    if (context == nullptr) {
        return CUDA_ERROR_INVALID_VALUE;
    }
    std::uintptr_t value = 0;
    if (fakeCudaImplCtxGetCurrent(&value) != 0) {
        return CUDA_ERROR_INVALID_CONTEXT;
    }
    *context = reinterpret_cast<CUcontext>(value);
    return CUDA_SUCCESS;
}

CUresult CUDAAPI cuCtxGetDevice(CUdevice* device)
{
    return device != nullptr && fakeCudaImplCtxGetDevice(device) == 0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_CONTEXT;
}

CUresult CUDAAPI cuDeviceGetName(char*, int, CUdevice)
{
    // Exact launch tests intentionally provide no calibrated device snapshot.
    // A normal driver error lets the runtime exercise its fail-closed path.
    return CUDA_ERROR_NOT_SUPPORTED;
}

CUresult CUDAAPI cuDeviceGetPCIBusId(char*, int, CUdevice)
{
    return CUDA_ERROR_NOT_SUPPORTED;
}

CUresult CUDAAPI cuDeviceGetUuid_v2(CUuuid*, CUdevice)
{
    return CUDA_ERROR_NOT_SUPPORTED;
}

CUresult CUDAAPI cuDeviceGetAttribute(int*, CUdevice_attribute, CUdevice)
{
    return CUDA_ERROR_NOT_SUPPORTED;
}

CUresult CUDAAPI cuDriverGetVersion(int*)
{
    return CUDA_ERROR_NOT_SUPPORTED;
}

CUresult CUDAAPI cuMemGetAllocationGranularity(
    std::size_t* granularity, const CUmemAllocationProp* properties,
    CUmemAllocationGranularity_flags option)
{
    if (properties == nullptr ||
        properties->type != CU_MEM_ALLOCATION_TYPE_PINNED ||
        properties->location.type != CU_MEM_LOCATION_TYPE_DEVICE ||
        option != CU_MEM_ALLOC_GRANULARITY_MINIMUM) {
        return CUDA_ERROR_INVALID_VALUE;
    }
    return fakeCudaImplMemGetAllocationGranularity(
               granularity, properties->location.id) == 0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuMemAddressReserve(CUdeviceptr* address, std::size_t bytes,
                                     std::size_t alignment,
                                     CUdeviceptr requested,
                                     unsigned long long flags)
{
    if (address == nullptr) {
        return CUDA_ERROR_INVALID_VALUE;
    }
    std::uintptr_t value = 0;
    if (fakeCudaImplMemAddressReserve(
            &value, bytes, alignment, static_cast<std::uintptr_t>(requested),
            static_cast<std::uint64_t>(flags)) != 0) {
        return CUDA_ERROR_INVALID_VALUE;
    }
    *address = static_cast<CUdeviceptr>(value);
    return CUDA_SUCCESS;
}

CUresult CUDAAPI cuMemAddressFree(CUdeviceptr address, std::size_t bytes)
{
    return fakeCudaImplMemAddressFree(
               static_cast<std::uintptr_t>(address), bytes) == 0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuMemCreate(CUmemGenericAllocationHandle* handle,
                             std::size_t bytes,
                             const CUmemAllocationProp* properties,
                             unsigned long long flags)
{
    if (handle == nullptr || properties == nullptr ||
        properties->type != CU_MEM_ALLOCATION_TYPE_PINNED ||
        properties->location.type != CU_MEM_LOCATION_TYPE_DEVICE) {
        return CUDA_ERROR_INVALID_VALUE;
    }
    std::uint64_t value = 0;
    if (fakeCudaImplMemCreate(&value, bytes,
                              static_cast<std::uint64_t>(flags),
                              properties->location.id) != 0) {
        return CUDA_ERROR_INVALID_VALUE;
    }
    *handle = static_cast<CUmemGenericAllocationHandle>(value);
    return CUDA_SUCCESS;
}

CUresult CUDAAPI cuMemRelease(CUmemGenericAllocationHandle handle)
{
    return fakeCudaImplMemRelease(static_cast<std::uint64_t>(handle)) == 0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuMemMap(CUdeviceptr address, std::size_t bytes,
                          std::size_t offset,
                          CUmemGenericAllocationHandle handle,
                          unsigned long long flags)
{
    return fakeCudaImplMemMap(
               static_cast<std::uintptr_t>(address), bytes, offset,
               static_cast<std::uint64_t>(handle),
               static_cast<std::uint64_t>(flags)) == 0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuMemUnmap(CUdeviceptr address, std::size_t bytes)
{
    return fakeCudaImplMemUnmap(static_cast<std::uintptr_t>(address), bytes) ==
                   0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuMemSetAccess(CUdeviceptr address, std::size_t bytes,
                                const CUmemAccessDesc* access,
                                std::size_t count)
{
    if (access == nullptr || count != 1 ||
        access->location.type != CU_MEM_LOCATION_TYPE_DEVICE ||
        access->flags != CU_MEM_ACCESS_FLAGS_PROT_READWRITE) {
        return CUDA_ERROR_INVALID_VALUE;
    }
    return fakeCudaImplMemSetAccess(static_cast<std::uintptr_t>(address),
                                    bytes, count, access->location.id) == 0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

cudaError_t CUDARTAPI cudaHostAlloc(void** data, std::size_t bytes,
                                    unsigned int flags)
{
    if (flags != cudaHostAllocDefault) {
        return cudaErrorInvalidValue;
    }
    return fakeCudaImplHostAlloc(data, bytes) == 0 ? cudaSuccess
                                                    : cudaErrorMemoryAllocation;
}

cudaError_t CUDARTAPI cudaFreeHost(void* data)
{
    return fakeCudaImplFreeHost(data) == 0 ? cudaSuccess
                                           : cudaErrorInvalidValue;
}

CUresult CUDAAPI cuMemcpyHtoD_v2(CUdeviceptr destination, const void* source,
                                 std::size_t bytes)
{
    return fakeCudaImplMemcpyHtoD(
               static_cast<std::uintptr_t>(destination), source, bytes) == 0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuMemcpyDtoH_v2(void* destination, CUdeviceptr source,
                                 std::size_t bytes)
{
    return fakeCudaImplMemcpyDtoH(
               destination, static_cast<std::uintptr_t>(source), bytes) == 0
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuStreamCreate(CUstream* stream, unsigned int flags)
{
    if (stream == nullptr || flags != CU_STREAM_NON_BLOCKING) {
        return CUDA_ERROR_INVALID_VALUE;
    }
    *stream = reinterpret_cast<CUstream>(static_cast<std::uintptr_t>(0x5a00));
    return CUDA_SUCCESS;
}

CUresult CUDAAPI cuStreamDestroy_v2(CUstream stream)
{
    return stream == reinterpret_cast<CUstream>(
                         static_cast<std::uintptr_t>(0x5a00))
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuStreamSynchronize(CUstream stream)
{
    return stream == reinterpret_cast<CUstream>(
                         static_cast<std::uintptr_t>(0x5a00))
               ? CUDA_SUCCESS
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuMemcpyHtoDAsync_v2(CUdeviceptr destination,
                                      const void* source,
                                      std::size_t bytes, CUstream stream)
{
    return cuStreamSynchronize(stream) == CUDA_SUCCESS
               ? cuMemcpyHtoD_v2(destination, source, bytes)
               : CUDA_ERROR_INVALID_VALUE;
}

CUresult CUDAAPI cuMemcpyDtoHAsync_v2(void* destination, CUdeviceptr source,
                                      std::size_t bytes, CUstream stream)
{
    return cuStreamSynchronize(stream) == CUDA_SUCCESS
               ? cuMemcpyDtoH_v2(destination, source, bytes)
               : CUDA_ERROR_INVALID_VALUE;
}

}  // extern "C"

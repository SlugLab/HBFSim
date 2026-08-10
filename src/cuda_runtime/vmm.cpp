#include "vmm.hpp"

#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
#include <cuda.h>
#endif

#include <algorithm>
#include <limits>
#include <utility>

namespace hbfsim::runtime {
namespace {

std::size_t round_up(std::size_t value, std::size_t alignment)
{
    if (value == 0 || alignment == 0 ||
        value > std::numeric_limits<std::size_t>::max() - (alignment - 1)) {
        throw VmmError("invalid or overflowing VMM size");
    }
    return ((value + alignment - 1) / alignment) * alignment;
}

bool power_of_two(std::size_t value) noexcept
{
    return value != 0 && (value & (value - 1)) == 0;
}

}  // namespace

std::size_t CudaVmmDriver::granularity(int device_ordinal)
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    CUmemAllocationProp properties{};
    properties.type = CU_MEM_ALLOCATION_TYPE_PINNED;
    properties.location.type = CU_MEM_LOCATION_TYPE_DEVICE;
    properties.location.id = device_ordinal;
    std::size_t result = 0;
    return ::cuMemGetAllocationGranularity(
               &result, &properties, CU_MEM_ALLOC_GRANULARITY_MINIMUM) ==
                   CUDA_SUCCESS
               ? result
               : 0;
#else
    (void)device_ordinal;
    return 0;
#endif
}

std::uintptr_t CudaVmmDriver::reserve(std::size_t bytes,
                                      std::size_t alignment)
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    CUdeviceptr address = 0;
    return ::cuMemAddressReserve(&address, bytes, alignment, 0, 0) ==
                   CUDA_SUCCESS
               ? static_cast<std::uintptr_t>(address)
               : 0;
#else
    (void)bytes;
    (void)alignment;
    return 0;
#endif
}

bool CudaVmmDriver::free_address(std::uintptr_t address, std::size_t bytes)
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    return ::cuMemAddressFree(static_cast<CUdeviceptr>(address), bytes) ==
           CUDA_SUCCESS;
#else
    (void)address;
    (void)bytes;
    return false;
#endif
}

std::uint64_t CudaVmmDriver::create(std::size_t bytes, int device_ordinal)
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    CUmemAllocationProp properties{};
    properties.type = CU_MEM_ALLOCATION_TYPE_PINNED;
    properties.location.type = CU_MEM_LOCATION_TYPE_DEVICE;
    properties.location.id = device_ordinal;
    CUmemGenericAllocationHandle handle = 0;
    return ::cuMemCreate(&handle, bytes, &properties, 0) == CUDA_SUCCESS
               ? static_cast<std::uint64_t>(handle)
               : 0;
#else
    (void)bytes;
    (void)device_ordinal;
    return 0;
#endif
}

bool CudaVmmDriver::release(std::uint64_t handle)
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    return ::cuMemRelease(
               static_cast<CUmemGenericAllocationHandle>(handle)) ==
           CUDA_SUCCESS;
#else
    (void)handle;
    return false;
#endif
}

bool CudaVmmDriver::map(std::uintptr_t address, std::size_t bytes,
                        std::uint64_t handle)
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    return ::cuMemMap(static_cast<CUdeviceptr>(address), bytes, 0,
                      static_cast<CUmemGenericAllocationHandle>(handle), 0) ==
           CUDA_SUCCESS;
#else
    (void)address;
    (void)bytes;
    (void)handle;
    return false;
#endif
}

bool CudaVmmDriver::unmap(std::uintptr_t address, std::size_t bytes)
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    return ::cuMemUnmap(static_cast<CUdeviceptr>(address), bytes) ==
           CUDA_SUCCESS;
#else
    (void)address;
    (void)bytes;
    return false;
#endif
}

bool CudaVmmDriver::set_access(std::uintptr_t address, std::size_t bytes,
                               int device_ordinal)
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    CUmemAccessDesc access{};
    access.location.type = CU_MEM_LOCATION_TYPE_DEVICE;
    access.location.id = device_ordinal;
    access.flags = CU_MEM_ACCESS_FLAGS_PROT_READWRITE;
    return ::cuMemSetAccess(static_cast<CUdeviceptr>(address), bytes, &access,
                            1) == CUDA_SUCCESS;
#else
    (void)address;
    (void)bytes;
    (void)device_ordinal;
    return false;
#endif
}

VmmRange::~VmmRange()
{
    reset();
}

VmmRange::VmmRange(VmmRange&& other) noexcept
    : driver_(std::exchange(other.driver_, nullptr)),
      base_(std::exchange(other.base_, 0)),
      logical_bytes_(std::exchange(other.logical_bytes_, 0)),
      reserved_bytes_(std::exchange(other.reserved_bytes_, 0))
{
}

VmmRange& VmmRange::operator=(VmmRange&& other) noexcept
{
    if (this != &other) {
        reset();
        driver_ = std::exchange(other.driver_, nullptr);
        base_ = std::exchange(other.base_, 0);
        logical_bytes_ = std::exchange(other.logical_bytes_, 0);
        reserved_bytes_ = std::exchange(other.reserved_bytes_, 0);
    }
    return *this;
}

VmmRange VmmRange::reserve_logical(VmmDriver& driver,
                                   std::size_t logical_bytes,
                                   std::size_t alignment,
                                   int device_ordinal)
{
    const auto minimum = driver.granularity(device_ordinal);
    if (!power_of_two(minimum) ||
        (alignment != 0 &&
         (!power_of_two(alignment) || alignment < minimum))) {
        throw VmmError("invalid VMM allocation granularity or alignment");
    }
    const auto effective_alignment = alignment == 0 ? minimum : alignment;
    const auto reserved = round_up(logical_bytes, minimum);
    const auto base = driver.reserve(reserved, effective_alignment);
    if (base == 0) {
        throw VmmError("failed to reserve CUDA virtual address range");
    }
    VmmRange result;
    result.driver_ = &driver;
    result.base_ = base;
    result.logical_bytes_ = logical_bytes;
    result.reserved_bytes_ = reserved;
    return result;
}

void VmmRange::reset() noexcept
{
    if (driver_ != nullptr && base_ != 0 && reserved_bytes_ != 0) {
        (void)driver_->free_address(base_, reserved_bytes_);
    }
    driver_ = nullptr;
    base_ = 0;
    logical_bytes_ = 0;
    reserved_bytes_ = 0;
}

VmmFramePool::~VmmFramePool()
{
    reset();
}

VmmFramePool::VmmFramePool(VmmFramePool&& other) noexcept
    : driver_(std::exchange(other.driver_, nullptr)),
      base_(std::exchange(other.base_, 0)),
      reserved_bytes_(std::exchange(other.reserved_bytes_, 0)),
      handle_(std::exchange(other.handle_, 0)),
      mapped_(std::exchange(other.mapped_, false)),
      frame_addresses_(std::move(other.frame_addresses_))
{
}

VmmFramePool& VmmFramePool::operator=(VmmFramePool&& other) noexcept
{
    if (this != &other) {
        reset();
        driver_ = std::exchange(other.driver_, nullptr);
        base_ = std::exchange(other.base_, 0);
        reserved_bytes_ = std::exchange(other.reserved_bytes_, 0);
        handle_ = std::exchange(other.handle_, 0);
        mapped_ = std::exchange(other.mapped_, false);
        frame_addresses_ = std::move(other.frame_addresses_);
    }
    return *this;
}

VmmFramePool VmmFramePool::create(VmmDriver& driver,
                                  std::size_t frame_count,
                                  std::size_t page_bytes,
                                  int device_ordinal)
{
    const auto minimum = driver.granularity(device_ordinal);
    if (!power_of_two(minimum) || page_bytes == 0 || frame_count == 0 ||
        frame_count > std::numeric_limits<std::size_t>::max() / page_bytes) {
        throw VmmError("invalid VMM frame pool geometry");
    }
    const auto logical_bytes = frame_count * page_bytes;
    const auto reserved = round_up(logical_bytes, minimum);
    VmmFramePool result;
    result.driver_ = &driver;
    result.base_ = driver.reserve(reserved, minimum);
    result.reserved_bytes_ = reserved;
    if (result.base_ == 0) {
        throw VmmError("failed to reserve HBM frame interval");
    }
    result.handle_ = driver.create(reserved, device_ordinal);
    if (result.handle_ == 0) {
        throw VmmError("failed to allocate HBM frame storage");
    }
    if (!driver.map(result.base_, reserved, result.handle_)) {
        throw VmmError("failed to map HBM frame storage");
    }
    result.mapped_ = true;
    if (!driver.set_access(result.base_, reserved, device_ordinal)) {
        throw VmmError("failed to grant HBM frame access");
    }
    result.frame_addresses_.reserve(frame_count);
    for (std::size_t index = 0; index < frame_count; ++index) {
        result.frame_addresses_.push_back(result.base_ + index * page_bytes);
    }
    return result;
}

void VmmFramePool::reset() noexcept
{
    if (driver_ != nullptr && mapped_) {
        (void)driver_->unmap(base_, reserved_bytes_);
    }
    if (driver_ != nullptr && handle_ != 0) {
        (void)driver_->release(handle_);
    }
    if (driver_ != nullptr && base_ != 0 && reserved_bytes_ != 0) {
        (void)driver_->free_address(base_, reserved_bytes_);
    }
    driver_ = nullptr;
    base_ = 0;
    reserved_bytes_ = 0;
    handle_ = 0;
    mapped_ = false;
    frame_addresses_.clear();
}

}  // namespace hbfsim::runtime

#include "capacity_runtime.hpp"

#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
#include <cuda.h>
#include <cuda_runtime_api.h>
#endif

#include <chrono>
#include <cstring>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hbfsim::runtime {
namespace {

std::size_t frame_count(const Profile& profile)
{
    validate_profile(profile);
    if (profile.hbm_cache_bytes == 0 ||
        profile.hbm_cache_bytes % profile.page_bytes != 0 ||
        profile.hbm_cache_bytes / profile.page_bytes >
            std::numeric_limits<std::size_t>::max()) {
        throw std::invalid_argument("invalid CUDA capacity cache geometry");
    }
    return static_cast<std::size_t>(profile.hbm_cache_bytes /
                                    profile.page_bytes);
}

}  // namespace

CapacityRuntime::PinnedPage::~PinnedPage()
{
    (void)release();
}

CapacityRuntime::PinnedPage::PinnedPage(PinnedPage&& other) noexcept
    : data_(std::exchange(other.data_, nullptr)),
      bytes_(std::exchange(other.bytes_, 0))
{}

CapacityRuntime::PinnedPage& CapacityRuntime::PinnedPage::operator=(
    PinnedPage&& other) noexcept
{
    if (this != &other) {
        if (release()) {
            data_ = std::exchange(other.data_, nullptr);
            bytes_ = std::exchange(other.bytes_, 0);
        }
    }
    return *this;
}

CapacityRuntime::PinnedPage CapacityRuntime::PinnedPage::allocate(
    std::size_t bytes)
{
    if (bytes == 0) {
        throw std::invalid_argument("invalid CUDA capacity bounce size");
    }
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    void* data = nullptr;
    if (::cudaHostAlloc(&data, bytes, cudaHostAllocDefault) != cudaSuccess ||
        data == nullptr) {
        throw std::runtime_error("failed to allocate CUDA capacity bounce page");
    }
    PinnedPage result;
    result.data_ = static_cast<std::byte*>(data);
    result.bytes_ = bytes;
    return result;
#else
    throw std::runtime_error("CUDA capacity runtime is disabled");
#endif
}

bool CapacityRuntime::PinnedPage::release() noexcept
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    if (data_ != nullptr) {
        if (::cudaFreeHost(data_) != cudaSuccess) {
            return false;
        }
    }
#endif
    data_ = nullptr;
    bytes_ = 0;
    return true;
}

std::unique_ptr<CapacityRuntime> CapacityRuntime::create(
    const Profile& profile, host_service::ControlView control,
    std::uintptr_t cuda_context, int device_ordinal)
{
    if (!control.valid() || cuda_context == 0 || device_ordinal < 0) {
        return nullptr;
    }
    try {
        return std::unique_ptr<CapacityRuntime>(new CapacityRuntime(
            profile, control, cuda_context, device_ordinal));
    } catch (...) {
        return nullptr;
    }
}

CapacityRuntime::CapacityRuntime(const Profile& profile,
                                 host_service::ControlView control,
                                 std::uintptr_t cuda_context,
                                 int device_ordinal)
    : page_bytes_(profile.page_bytes), cuda_context_(cuda_context),
      device_ordinal_(device_ordinal), driver_(),
      vmm_(VmmFramePool::create(driver_, frame_count(profile),
                                profile.page_bytes, device_ordinal)),
      cache_(vmm_.frame_addresses()), router_(),
      bounce_(PinnedPage::allocate(profile.page_bytes)),
      service_(
          {
              .read_page = [this](std::uint64_t page, std::size_t bytes) {
                  return router_.read_page(page, bytes);
              },
              .write_page = [this](std::uint64_t page, std::size_t bytes,
                                   std::span<const std::byte> data) {
                  return router_.write_page(page, bytes, data);
              },
              .flush = [this] { return router_.flush(); },
          },
          cache_, profile.page_bytes,
          {
              .host_to_frame =
                  [this](std::uint64_t frame,
                         std::span<const std::byte> bytes) {
                      return host_to_frame(frame, bytes);
                  },
              .frame_to_host =
                  [this](std::uint64_t frame, std::span<std::byte> bytes) {
                      return frame_to_host(frame, bytes);
                  },
          }),
      worker_(control, service_, std::chrono::microseconds(50),
              &CapacityRuntime::start_worker, this,
              &CapacityRuntime::stop_worker, this)
{}

CapacityRuntime::~CapacityRuntime() = default;

bool CapacityRuntime::start_worker(void* opaque) noexcept
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    const auto* runtime = static_cast<const CapacityRuntime*>(opaque);
    const auto expected = reinterpret_cast<CUcontext>(runtime->cuda_context_);
    if (::cuCtxSetCurrent(expected) != CUDA_SUCCESS) {
        return false;
    }
    CUcontext current = nullptr;
    CUdevice device = -1;
    return ::cuCtxGetCurrent(&current) == CUDA_SUCCESS &&
           current == expected && ::cuCtxGetDevice(&device) == CUDA_SUCCESS &&
           device == runtime->device_ordinal_;
#else
    (void)opaque;
    return false;
#endif
}

void CapacityRuntime::stop_worker(void*) noexcept
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    (void)::cuCtxSetCurrent(nullptr);
#endif
}

bool CapacityRuntime::host_to_frame(
    std::uint64_t frame, std::span<const std::byte> bytes) noexcept
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    if (bytes.size() != page_bytes_) {
        return false;
    }
    std::memcpy(bounce_.data(), bytes.data(), bytes.size());
    return ::cuMemcpyHtoD(static_cast<CUdeviceptr>(frame), bounce_.data(),
                          bytes.size()) == CUDA_SUCCESS;
#else
    (void)frame;
    (void)bytes;
    return false;
#endif
}

bool CapacityRuntime::frame_to_host(
    std::uint64_t frame, std::span<std::byte> bytes) noexcept
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    if (bytes.size() != page_bytes_ ||
        ::cuMemcpyDtoH(bounce_.data(), static_cast<CUdeviceptr>(frame),
                       bytes.size()) != CUDA_SUCCESS) {
        return false;
    }
    std::memcpy(bytes.data(), bounce_.data(), bytes.size());
    return true;
#else
    (void)frame;
    (void)bytes;
    return false;
#endif
}

RequestStatus CapacityRuntime::flush(
    host_service::CapacityPageService::ModelProgram model_program,
    std::optional<std::uint32_t> range_id)
{
    return worker_.flush(model_program, range_id);
}

void CapacityRuntime::stop()
{
    worker_.stop();
}

bool CapacityRuntime::release_cuda_resources() noexcept
{
    const auto bounce_released = bounce_.release();
    const auto frames_released = vmm_.release();
    return bounce_released && frames_released;
}

}  // namespace hbfsim::runtime

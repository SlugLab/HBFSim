#pragma once

#include "backing_store.hpp"

#include "../cuda_runtime/hbm_cache.hpp"

#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>
#include <span>

namespace hbfsim::host_service {

struct CapacityFrameIo {
    std::function<bool(std::uint64_t, std::span<const std::byte>)>
        host_to_frame;
    std::function<bool(std::uint64_t, std::span<std::byte>)>
        frame_to_host;
};

struct CapacityResolveResult {
    RequestStatus status{RequestStatus::IoError};
    std::uint64_t frame_address{0};
};

class CapacityPageService {
  public:
    CapacityPageService(BackingStore& backing, runtime::HbmCache& cache,
                        std::size_t page_bytes, CapacityFrameIo frame_io);

    CapacityResolveResult resolve(std::uint64_t logical_page,
                                  std::uint32_t operation);
    RequestStatus flush();

  private:
    RequestStatus writeback(const runtime::CacheEviction& eviction);

    BackingStore& backing_;
    runtime::HbmCache& cache_;
    std::size_t page_bytes_;
    CapacityFrameIo frame_io_;
    std::mutex mutex_;
};

}  // namespace hbfsim::host_service

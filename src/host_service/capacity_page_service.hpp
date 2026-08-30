#pragma once

#include "backing_store.hpp"
#include "capacity_backing_router.hpp"
#include "control_layout.hpp"

#include "../cuda_runtime/hbm_cache.hpp"

#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <mutex>
#include <optional>
#include <span>
#include <unordered_map>
#include <vector>

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
    CapacityMediaPlan media;
};

struct CapacityBackingIo {
    std::function<RoutedPage(std::uint64_t, std::size_t)> read_page;
    std::function<RequestStatus(std::uint64_t, std::size_t,
                                std::span<const std::byte>)>
        write_page;
    std::function<RequestStatus()> flush;
};

class CapacityPageService {
  public:
    using ModelProgram =
        std::function<RequestStatus(std::uint32_t, std::uint64_t)>;

    CapacityPageService(CapacityBackingIo backing, runtime::HbmCache& cache,
                        std::size_t page_bytes, CapacityFrameIo frame_io);
    CapacityPageService(BackingStore& backing, runtime::HbmCache& cache,
                        std::size_t page_bytes, CapacityFrameIo frame_io);

    CapacityResolveResult resolve(std::uint64_t logical_page,
                                  std::uint32_t operation);
    RequestStatus flush();
    RequestStatus flush(
        const ModelProgram& model_program,
        std::optional<std::uint32_t> range_id = std::nullopt);
    RequestStatus flush(std::uint64_t first_page,
                        std::uint64_t page_count);

  private:
    RequestStatus writeback(const runtime::CacheEviction& eviction);
    RequestStatus flush_range(std::uint64_t first_page,
                              std::uint64_t page_count);

    CapacityBackingIo backing_;
    runtime::HbmCache& cache_;
    std::size_t page_bytes_;
    CapacityFrameIo frame_io_;
    std::unordered_map<std::uint64_t, std::uint32_t> resident_range_ids_;
    std::mutex mutex_;
};

}  // namespace hbfsim::host_service

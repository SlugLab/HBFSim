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

// Additive capacity observability.  Raw counters are exported instead of
// embedding ratios so callers can serialize a zero denominator as JSON null.
// Fields that are unavailable in this synchronous baseline remain absent from
// this snapshot and are represented by the public v2 validity mask.
struct CapacityStatsV2Snapshot {
    std::uint64_t demand_requests{0};
    std::uint64_t cache_hits{0};
    std::uint64_t cache_misses{0};
    std::uint64_t coalesced_misses{0};
    std::uint64_t clean_evictions{0};
    std::uint64_t dirty_evictions{0};
    std::uint64_t hbf_read_bytes{0};
    std::uint64_t hbf_program_bytes{0};
    std::uint64_t writeback_bytes{0};
    std::uint64_t host_service_time_ns{0};
    std::uint64_t backing_io_wall_time_ns{0};
    std::uint64_t h2d_copy_time_ns{0};
    std::uint64_t dtoh_copy_time_ns{0};
    std::uint64_t page_residence_time_ns{0};
    std::uint64_t completed_residences{0};
    std::uint64_t frame_count{0};
    std::uint64_t resident_pages_current{0};
    std::uint64_t resident_pages_peak{0};
    std::uint64_t free_frames{0};
    std::uint64_t evicting_pages{0};
    std::uint64_t dirty_pages{0};
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
    [[nodiscard]] CapacityStatsV2Snapshot stats_v2() const;

  private:
    RequestStatus writeback(const runtime::CacheEviction& eviction);
    RequestStatus flush_backing();
    RequestStatus flush_range(std::uint64_t first_page,
                              std::uint64_t page_count);
    void update_residency_stats_locked();
    void record_residence_end_locked(std::uint64_t logical_page);

    CapacityBackingIo backing_;
    runtime::HbmCache& cache_;
    std::size_t page_bytes_;
    CapacityFrameIo frame_io_;
    std::unordered_map<std::uint64_t, std::uint32_t> resident_range_ids_;
    std::unordered_map<std::uint64_t, std::uint64_t> resident_since_ns_;
    CapacityStatsV2Snapshot stats_v2_;
    mutable std::mutex mutex_;
};

}  // namespace hbfsim::host_service

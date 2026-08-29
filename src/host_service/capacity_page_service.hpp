#pragma once

#include "backing_store.hpp"
#include "capacity_backing_router.hpp"
#include "control_layout.hpp"

#include "../cuda_runtime/hbm_cache.hpp"

#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <cstdint>
#include <deque>
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

    // Readahead. This is a system-side prefetcher: on a demand miss for page
    // N it queues N+1 through N+pages, and the worker drains the queue when it
    // has nothing else to do. The application neither asks for it nor knows it
    // happened, and the only thing the policy has to go on is which pages have
    // already been demanded.
    //
    // Zero, the default, switches it off, so a run that does not ask for
    // readahead behaves exactly as before.
    void set_readahead_pages(std::uint32_t pages);

    // Fetches at most one queued page. Returns true when it did work, so the
    // caller can keep draining before going back to sleep. A queued page is
    // skipped rather than fetched when it is already resident, when the
    // backing store has nothing at that address, or when no frame is free --
    // readahead never evicts, so it can never push out a page a demand still
    // needs.
    bool run_one_readahead();

    [[nodiscard]] std::uint64_t readahead_pages_fetched() const;
    [[nodiscard]] std::uint64_t readahead_pages_skipped() const;

  private:
    RequestStatus writeback(const runtime::CacheEviction& eviction);
    RequestStatus flush_range(std::uint64_t first_page,
                              std::uint64_t page_count);
    void queue_readahead_locked(std::uint64_t demanded_page);
    bool fill_free_frame_locked(std::uint64_t logical_page);

    CapacityBackingIo backing_;
    runtime::HbmCache& cache_;
    std::size_t page_bytes_;
    CapacityFrameIo frame_io_;
    std::unordered_map<std::uint64_t, std::uint32_t> resident_range_ids_;
    std::uint32_t readahead_pages_{0};
    // Bounded so a burst of misses cannot grow the queue without limit.
    std::deque<std::uint64_t> readahead_queue_;
    std::uint64_t readahead_fetched_{0};
    std::uint64_t readahead_skipped_{0};
    mutable std::mutex mutex_;
};

}  // namespace hbfsim::host_service

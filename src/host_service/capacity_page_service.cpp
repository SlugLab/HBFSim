#include "capacity_page_service.hpp"

#include <algorithm>
#include <chrono>
#include <limits>
#include <utility>
#include <vector>

namespace hbfsim::host_service {

namespace {

RequestStatus checked_status(RequestStatus status)
{
    return status == RequestStatus::Pending ||
                   status > RequestStatus::DaemonLost
               ? RequestStatus::IoError
               : status;
}

std::uint64_t monotonic_ns()
{
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch())
            .count());
}

void saturating_add(std::uint64_t& target, std::uint64_t value)
{
    target = value > std::numeric_limits<std::uint64_t>::max() - target
                 ? std::numeric_limits<std::uint64_t>::max()
                 : target + value;
}

class ScopedDuration {
  public:
    explicit ScopedDuration(std::uint64_t& destination)
        : destination_(destination), started_(monotonic_ns())
    {}

    ~ScopedDuration() { pause(); }

    void pause()
    {
        if (!running_) {
            return;
        }
        const auto finished = monotonic_ns();
        saturating_add(destination_,
                       finished >= started_ ? finished - started_ : 0);
        running_ = false;
    }

    void resume()
    {
        if (running_) {
            return;
        }
        started_ = monotonic_ns();
        running_ = true;
    }

  private:
    std::uint64_t& destination_;
    std::uint64_t started_;
    bool running_{true};
};

}  // namespace

CapacityPageService::CapacityPageService(CapacityBackingIo backing,
                                         runtime::HbmCache& cache,
                                         std::size_t page_bytes,
                                         CapacityFrameIo frame_io)
    : backing_(std::move(backing)), cache_(cache), page_bytes_(page_bytes),
      frame_io_(std::move(frame_io))
{
    if (page_bytes_ == 0 || !backing_.read_page || !backing_.write_page ||
        !backing_.flush || !frame_io_.host_to_frame ||
        !frame_io_.frame_to_host) {
        throw std::invalid_argument(
            "invalid capacity page service configuration");
    }
}

CapacityPageService::CapacityPageService(BackingStore& backing,
                                         runtime::HbmCache& cache,
                                         std::size_t page_bytes,
                                         CapacityFrameIo frame_io)
    : CapacityPageService(
          {
              .read_page = [&backing](std::uint64_t page,
                                      std::size_t bytes) {
                  return RoutedPage{.status = RequestStatus::Ready,
                                    .range_id = 1,
                                    .bytes = backing.read_page(page, bytes)};
              },
              .write_page = [&backing](std::uint64_t page,
                                       std::size_t bytes,
                                       std::span<const std::byte> data) {
                  backing.write_page(
                      page, bytes,
                      std::vector<std::byte>(data.begin(), data.end()));
                  return RequestStatus::Ready;
              },
              .flush = [&backing] {
                  backing.flush();
                  return RequestStatus::Ready;
              },
          },
          cache, page_bytes, std::move(frame_io))
{}

RequestStatus CapacityPageService::writeback(
    const runtime::CacheEviction& eviction)
{
    std::vector<std::byte> page;
    try {
        page.resize(page_bytes_);
    } catch (...) {
        return RequestStatus::IoError;
    }
    {
        ScopedDuration timer(stats_v2_.dtoh_copy_time_ns);
        try {
            if (!frame_io_.frame_to_host(eviction.frame_address, page)) {
                return RequestStatus::CopyError;
            }
        } catch (...) {
            return RequestStatus::CopyError;
        }
    }
    RequestStatus status = RequestStatus::IoError;
    {
        ScopedDuration timer(stats_v2_.backing_io_wall_time_ns);
        try {
            status = checked_status(
                backing_.write_page(eviction.logical_page, page_bytes_, page));
        } catch (...) {
            return RequestStatus::IoError;
        }
    }
    if (status == RequestStatus::Ready) {
        saturating_add(stats_v2_.writeback_bytes, page_bytes_);
    }
    return status;
}

RequestStatus CapacityPageService::flush_backing()
{
    ScopedDuration timer(stats_v2_.backing_io_wall_time_ns);
    try {
        return checked_status(backing_.flush());
    } catch (...) {
        return RequestStatus::IoError;
    }
}

void CapacityPageService::update_residency_stats_locked()
{
    const auto snapshot = cache_.snapshot();
    stats_v2_.frame_count = snapshot.frame_count;
    stats_v2_.resident_pages_current = snapshot.resident_pages;
    stats_v2_.resident_pages_peak = std::max(
        stats_v2_.resident_pages_peak, stats_v2_.resident_pages_current);
    stats_v2_.free_frames = snapshot.free_frames;
    stats_v2_.evicting_pages = snapshot.evicting_pages;
    stats_v2_.dirty_pages = snapshot.dirty_pages;
}

void CapacityPageService::record_residence_end_locked(
    std::uint64_t logical_page)
{
    const auto found = resident_since_ns_.find(logical_page);
    if (found == resident_since_ns_.end()) {
        return;
    }
    const auto now = monotonic_ns();
    saturating_add(stats_v2_.page_residence_time_ns,
                   now >= found->second ? now - found->second : 0);
    saturating_add(stats_v2_.completed_residences, 1);
    resident_since_ns_.erase(found);
}

CapacityStatsV2Snapshot CapacityPageService::stats_v2() const
{
    std::lock_guard lock(mutex_);
    auto result = stats_v2_;
    const auto snapshot = cache_.snapshot();
    result.frame_count = snapshot.frame_count;
    result.resident_pages_current = snapshot.resident_pages;
    result.free_frames = snapshot.free_frames;
    result.evicting_pages = snapshot.evicting_pages;
    result.dirty_pages = snapshot.dirty_pages;
    return result;
}

CapacityResolveResult CapacityPageService::resolve(
    std::uint64_t logical_page, std::uint32_t operation)
{
    if (operation > 1) {
        return {.status = RequestStatus::Unsupported};
    }
    std::lock_guard lock(mutex_);
    ScopedDuration service_timer(stats_v2_.host_service_time_ns);
    saturating_add(stats_v2_.demand_requests, 1);
    if (const auto resident = cache_.resolve(logical_page); resident) {
        saturating_add(stats_v2_.cache_hits, 1);
        if (operation == 1 && !cache_.mark_dirty(logical_page)) {
            return {.status = RequestStatus::IoError};
        }
        return {.status = RequestStatus::Ready,
                .frame_address = *resident};
    }
    if (const auto reclaimed = cache_.reclaim_eviction(logical_page);
        reclaimed) {
        saturating_add(stats_v2_.cache_hits, 1);
        saturating_add(stats_v2_.coalesced_misses, 1);
        if (operation == 1 && !cache_.mark_dirty(logical_page)) {
            return {.status = RequestStatus::IoError};
        }
        return {.status = RequestStatus::Ready,
                .frame_address = *reclaimed};
    }

    saturating_add(stats_v2_.cache_misses, 1);

    CapacityMediaPlan media{.flags = CapacityMediaRead};
    auto frame = cache_.free_frame();
    if (!frame.has_value()) {
        auto eviction = cache_.begin_eviction();
        if (!eviction.has_value()) {
            return {.status = RequestStatus::IoError};
        }
        if (eviction->dirty) {
            const auto victim_range =
                resident_range_ids_.find(eviction->logical_page);
            if (victim_range == resident_range_ids_.end() ||
                victim_range->second == 0) {
                (void)cache_.cancel_eviction(*eviction);
                return {.status = RequestStatus::IoError};
            }
            const auto status = writeback(*eviction);
            if (status != RequestStatus::Ready) {
                (void)cache_.cancel_eviction(*eviction);
                return {.status = status};
            }
            media.flags |= CapacityMediaProgram;
            media.program_page = eviction->logical_page;
            media.program_range_id = victim_range->second;
        }
        if (!cache_.complete_eviction(*eviction)) {
            return {.status = RequestStatus::IoError};
        }
        record_residence_end_locked(eviction->logical_page);
        saturating_add(eviction->dirty ? stats_v2_.dirty_evictions
                                       : stats_v2_.clean_evictions,
                       1);
        if (eviction->dirty) {
            saturating_add(stats_v2_.hbf_program_bytes, page_bytes_);
        }
        resident_range_ids_.erase(eviction->logical_page);
        update_residency_stats_locked();
        frame = eviction->frame_address;
    }

    std::vector<std::byte> page;
    std::uint32_t range_id = 0;
    try {
        RoutedPage routed;
        {
            ScopedDuration timer(stats_v2_.backing_io_wall_time_ns);
            routed = backing_.read_page(logical_page, page_bytes_);
        }
        if (routed.status != RequestStatus::Ready) {
            return {.status = checked_status(routed.status)};
        }
        if (routed.bytes.size() != page_bytes_) {
            return {.status = RequestStatus::IoError};
        }
        if (routed.range_id == 0) {
            return {.status = RequestStatus::IoError};
        }
        range_id = routed.range_id;
        page = std::move(routed.bytes);
    } catch (...) {
        return {.status = RequestStatus::IoError};
    }
    {
        ScopedDuration timer(stats_v2_.h2d_copy_time_ns);
        try {
            if (!frame_io_.host_to_frame(*frame, page)) {
                return {.status = RequestStatus::CopyError};
            }
        } catch (...) {
            return {.status = RequestStatus::CopyError};
        }
    }
    try {
        const auto [entry, inserted] =
            resident_range_ids_.emplace(logical_page, range_id);
        (void)entry;
        if (!inserted) {
            return {.status = RequestStatus::IoError};
        }
    } catch (...) {
        return {.status = RequestStatus::IoError};
    }
    if (!cache_.publish(logical_page, *frame)) {
        resident_range_ids_.erase(logical_page);
        return {.status = RequestStatus::IoError};
    }
    resident_since_ns_[logical_page] = monotonic_ns();
    update_residency_stats_locked();
    if (operation == 1 && !cache_.mark_dirty(logical_page)) {
        return {.status = RequestStatus::IoError};
    }
    saturating_add(stats_v2_.hbf_read_bytes, page_bytes_);
    return {.status = RequestStatus::Ready,
            .frame_address = *frame,
            .media = media};
}

RequestStatus CapacityPageService::flush()
{
    std::lock_guard lock(mutex_);
    ScopedDuration service_timer(stats_v2_.host_service_time_ns);
    bool synchronized = false;
    while (cache_.dirty_pages() != 0) {
        auto eviction = cache_.begin_eviction();
        if (!eviction.has_value()) {
            return RequestStatus::IoError;
        }
        if (eviction->dirty) {
            const auto status = writeback(*eviction);
            if (status != RequestStatus::Ready) {
                (void)cache_.cancel_eviction(*eviction);
                return status;
            }
            const auto flush_status = flush_backing();
            if (flush_status != RequestStatus::Ready) {
                (void)cache_.cancel_eviction(*eviction);
                return flush_status;
            }
            synchronized = true;
        }
        if (!cache_.complete_eviction(*eviction)) {
            return RequestStatus::IoError;
        }
        record_residence_end_locked(eviction->logical_page);
        saturating_add(eviction->dirty ? stats_v2_.dirty_evictions
                                       : stats_v2_.clean_evictions,
                       1);
        if (eviction->dirty) {
            saturating_add(stats_v2_.hbf_program_bytes, page_bytes_);
        }
        resident_range_ids_.erase(eviction->logical_page);
        update_residency_stats_locked();
    }
    if (!synchronized) {
        return flush_backing();
    }
    return RequestStatus::Ready;
}

RequestStatus CapacityPageService::flush(
    const ModelProgram& model_program,
    std::optional<std::uint32_t> range_id)
{
    if (!model_program || (range_id.has_value() && *range_id == 0)) {
        return RequestStatus::IoError;
    }
    std::unique_lock lock(mutex_);
    ScopedDuration service_timer(stats_v2_.host_service_time_ns);
    std::vector<std::uint64_t> candidates;
    try {
        candidates.reserve(resident_range_ids_.size());
        for (const auto& [page, resident_range] : resident_range_ids_) {
            if (!range_id.has_value() || resident_range == *range_id) {
                candidates.push_back(page);
            }
        }
    } catch (...) {
        return RequestStatus::IoError;
    }

    bool synchronized = false;
    for (const auto page : candidates) {
        auto eviction = cache_.begin_eviction_in_range(page, 1, true);
        if (!eviction.has_value()) {
            continue;
        }
        const auto resident = resident_range_ids_.find(page);
        if (resident == resident_range_ids_.end() || resident->second == 0) {
            (void)cache_.cancel_eviction(*eviction);
            return RequestStatus::IoError;
        }
        const auto resident_range = resident->second;
        auto status = writeback(*eviction);
        if (status != RequestStatus::Ready) {
            (void)cache_.cancel_eviction(*eviction);
            return status;
        }
        service_timer.pause();
        lock.unlock();
        try {
            status = checked_status(model_program(resident_range, page));
        } catch (...) {
            status = RequestStatus::IoError;
        }
        lock.lock();
        service_timer.resume();
        if (status != RequestStatus::Ready) {
            (void)cache_.cancel_eviction(*eviction);
            return status;
        }
        status = flush_backing();
        if (status != RequestStatus::Ready) {
            (void)cache_.cancel_eviction(*eviction);
            return status;
        }
        const auto current_resident = resident_range_ids_.find(page);
        if (current_resident == resident_range_ids_.end() ||
            current_resident->second != resident_range) {
            (void)cache_.cancel_eviction(*eviction);
            return RequestStatus::IoError;
        }
        if (!cache_.complete_eviction(*eviction)) {
            return RequestStatus::IoError;
        }
        record_residence_end_locked(eviction->logical_page);
        saturating_add(stats_v2_.dirty_evictions, 1);
        saturating_add(stats_v2_.hbf_program_bytes, page_bytes_);
        resident_range_ids_.erase(current_resident);
        update_residency_stats_locked();
        synchronized = true;
    }
    if (synchronized) {
        return RequestStatus::Ready;
    }
    return flush_backing();
}

RequestStatus CapacityPageService::flush(std::uint64_t first_page,
                                         std::uint64_t page_count)
{
    if (page_count == 0 ||
        first_page > std::numeric_limits<std::uint64_t>::max() - page_count) {
        return RequestStatus::IoError;
    }
    std::lock_guard lock(mutex_);
    ScopedDuration service_timer(stats_v2_.host_service_time_ns);
    return flush_range(first_page, page_count);
}

RequestStatus CapacityPageService::flush_range(std::uint64_t first_page,
                                               std::uint64_t page_count)
{
    bool synchronized = false;
    for (;;) {
        auto eviction =
            cache_.begin_eviction_in_range(first_page, page_count, true);
        if (!eviction.has_value()) {
            break;
        }
        const auto status = writeback(*eviction);
        if (status != RequestStatus::Ready) {
            (void)cache_.cancel_eviction(*eviction);
            return status;
        }
        const auto flush_status = flush_backing();
        if (flush_status != RequestStatus::Ready) {
            (void)cache_.cancel_eviction(*eviction);
            return flush_status;
        }
        synchronized = true;
        if (!cache_.complete_eviction(*eviction)) {
            return RequestStatus::IoError;
        }
        record_residence_end_locked(eviction->logical_page);
        saturating_add(stats_v2_.dirty_evictions, 1);
        saturating_add(stats_v2_.hbf_program_bytes, page_bytes_);
        resident_range_ids_.erase(eviction->logical_page);
        update_residency_stats_locked();
    }
    if (synchronized) {
        return RequestStatus::Ready;
    }
    return flush_backing();
}

}  // namespace hbfsim::host_service

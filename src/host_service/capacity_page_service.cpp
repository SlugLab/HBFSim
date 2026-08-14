#include "capacity_page_service.hpp"

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
    try {
        if (!frame_io_.frame_to_host(eviction.frame_address, page)) {
            return RequestStatus::CopyError;
        }
    } catch (...) {
        return RequestStatus::CopyError;
    }
    try {
        const auto status = checked_status(
            backing_.write_page(eviction.logical_page, page_bytes_, page));
        if (status == RequestStatus::Ready) {
            dirty_writebacks_.fetch_add(1, std::memory_order_relaxed);
        }
        return status;
    } catch (...) {
        return RequestStatus::IoError;
    }
}

CapacityResolveResult CapacityPageService::resolve(
    std::uint64_t logical_page, std::uint32_t operation)
{
    if (operation > 1) {
        return {.status = RequestStatus::Unsupported};
    }
    std::lock_guard lock(mutex_);
    if (const auto resident = cache_.resolve(logical_page); resident) {
        cache_hits_.fetch_add(1, std::memory_order_relaxed);
        if (operation == 1 && !cache_.mark_dirty(logical_page)) {
            return {.status = RequestStatus::IoError};
        }
        return {.status = RequestStatus::Ready,
                .frame_address = *resident};
    }
    if (const auto reclaimed = cache_.reclaim_eviction(logical_page);
        reclaimed) {
        cache_hits_.fetch_add(1, std::memory_order_relaxed);
        if (operation == 1 && !cache_.mark_dirty(logical_page)) {
            return {.status = RequestStatus::IoError};
        }
        return {.status = RequestStatus::Ready,
                .frame_address = *reclaimed};
    }

    cache_misses_.fetch_add(1, std::memory_order_relaxed);

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
        resident_range_ids_.erase(eviction->logical_page);
        frame = eviction->frame_address;
    }

    std::vector<std::byte> page;
    std::uint32_t range_id = 0;
    try {
        auto routed = backing_.read_page(logical_page, page_bytes_);
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
    try {
        if (!frame_io_.host_to_frame(*frame, page)) {
            return {.status = RequestStatus::CopyError};
        }
    } catch (...) {
        return {.status = RequestStatus::CopyError};
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
    if (operation == 1 && !cache_.mark_dirty(logical_page)) {
        return {.status = RequestStatus::IoError};
    }
    return {.status = RequestStatus::Ready,
            .frame_address = *frame,
            .media = media};
}

CapacityPageStats CapacityPageService::stats() const noexcept
{
    return {
        .cache_hits = cache_hits_.load(std::memory_order_relaxed),
        .cache_misses = cache_misses_.load(std::memory_order_relaxed),
        .dirty_writebacks = dirty_writebacks_.load(std::memory_order_relaxed),
    };
}

RequestStatus CapacityPageService::flush()
{
    std::lock_guard lock(mutex_);
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
            try {
                const auto status = checked_status(backing_.flush());
                if (status != RequestStatus::Ready) {
                    (void)cache_.cancel_eviction(*eviction);
                    return status;
                }
                synchronized = true;
            } catch (...) {
                (void)cache_.cancel_eviction(*eviction);
                return RequestStatus::IoError;
            }
        }
        if (!cache_.complete_eviction(*eviction)) {
            return RequestStatus::IoError;
        }
        resident_range_ids_.erase(eviction->logical_page);
    }
    try {
        if (!synchronized) {
            return checked_status(backing_.flush());
        }
        return RequestStatus::Ready;
    } catch (...) {
        return RequestStatus::IoError;
    }
}

RequestStatus CapacityPageService::flush(
    const ModelProgram& model_program,
    std::optional<std::uint32_t> range_id)
{
    if (!model_program || (range_id.has_value() && *range_id == 0)) {
        return RequestStatus::IoError;
    }
    std::unique_lock lock(mutex_);
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
        lock.unlock();
        try {
            status = checked_status(model_program(resident_range, page));
        } catch (...) {
            status = RequestStatus::IoError;
        }
        lock.lock();
        if (status != RequestStatus::Ready) {
            (void)cache_.cancel_eviction(*eviction);
            return status;
        }
        try {
            status = checked_status(backing_.flush());
        } catch (...) {
            status = RequestStatus::IoError;
        }
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
        resident_range_ids_.erase(current_resident);
        synchronized = true;
    }
    if (synchronized) {
        return RequestStatus::Ready;
    }
    try {
        return checked_status(backing_.flush());
    } catch (...) {
        return RequestStatus::IoError;
    }
}

RequestStatus CapacityPageService::flush(std::uint64_t first_page,
                                         std::uint64_t page_count)
{
    if (page_count == 0 ||
        first_page > std::numeric_limits<std::uint64_t>::max() - page_count) {
        return RequestStatus::IoError;
    }
    std::lock_guard lock(mutex_);
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
        try {
            const auto flush_status = checked_status(backing_.flush());
            if (flush_status != RequestStatus::Ready) {
                (void)cache_.cancel_eviction(*eviction);
                return flush_status;
            }
            synchronized = true;
        } catch (...) {
            (void)cache_.cancel_eviction(*eviction);
            return RequestStatus::IoError;
        }
        if (!cache_.complete_eviction(*eviction)) {
            return RequestStatus::IoError;
        }
        resident_range_ids_.erase(eviction->logical_page);
    }
    if (synchronized) {
        return RequestStatus::Ready;
    }
    try {
        return checked_status(backing_.flush());
    } catch (...) {
        return RequestStatus::IoError;
    }
}

}  // namespace hbfsim::host_service

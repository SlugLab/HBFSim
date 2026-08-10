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
        return checked_status(
            backing_.write_page(eviction.logical_page, page_bytes_, page));
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
        if (operation == 1 && !cache_.mark_dirty(logical_page)) {
            return {.status = RequestStatus::IoError};
        }
        return {.status = RequestStatus::Ready,
                .frame_address = *resident};
    }

    auto frame = cache_.free_frame();
    if (!frame.has_value()) {
        auto eviction = cache_.begin_eviction();
        if (!eviction.has_value()) {
            return {.status = RequestStatus::IoError};
        }
        if (eviction->dirty) {
            const auto status = writeback(*eviction);
            if (status != RequestStatus::Ready) {
                (void)cache_.cancel_eviction(*eviction);
                return {.status = status};
            }
        }
        if (!cache_.complete_eviction(*eviction)) {
            return {.status = RequestStatus::IoError};
        }
        frame = eviction->frame_address;
    }

    std::vector<std::byte> page;
    try {
        auto routed = backing_.read_page(logical_page, page_bytes_);
        if (routed.status != RequestStatus::Ready) {
            return {.status = checked_status(routed.status)};
        }
        if (routed.bytes.size() != page_bytes_) {
            return {.status = RequestStatus::IoError};
        }
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
    if (!cache_.publish(logical_page, *frame) ||
        (operation == 1 && !cache_.mark_dirty(logical_page))) {
        return {.status = RequestStatus::IoError};
    }
    return {.status = RequestStatus::Ready, .frame_address = *frame};
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

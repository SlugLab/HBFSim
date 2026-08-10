#include "capacity_page_service.hpp"

#include <utility>
#include <vector>

namespace hbfsim::host_service {

CapacityPageService::CapacityPageService(BackingStore& backing,
                                         runtime::HbmCache& cache,
                                         std::size_t page_bytes,
                                         CapacityFrameIo frame_io)
    : backing_(backing), cache_(cache), page_bytes_(page_bytes),
      frame_io_(std::move(frame_io))
{
    if (page_bytes_ == 0 || !frame_io_.host_to_frame ||
        !frame_io_.frame_to_host) {
        throw std::invalid_argument(
            "invalid capacity page service configuration");
    }
}

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
        backing_.write_page(eviction.logical_page, page_bytes_, page);
        return RequestStatus::Ready;
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
        page = backing_.read_page(logical_page, page_bytes_);
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
                backing_.flush();
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
            backing_.flush();
        }
        return RequestStatus::Ready;
    } catch (...) {
        return RequestStatus::IoError;
    }
}

}  // namespace hbfsim::host_service

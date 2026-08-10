#include "hbm_cache.hpp"

#include <stdexcept>

namespace hbfsim::runtime {

HbmCache::HbmCache(std::vector<std::uint64_t> frame_addresses)
{
    frames_.reserve(frame_addresses.size());
    for (const auto address : frame_addresses) {
        if (address == 0 || address_to_frame_.contains(address)) {
            throw std::invalid_argument(
                "HBM cache frames must be nonzero and unique");
        }
        address_to_frame_.emplace(address, frames_.size());
        frames_.push_back(Frame{.address = address});
    }
}

bool HbmCache::publish(std::uint64_t logical_page,
                       std::uint64_t frame_address)
{
    std::lock_guard lock(mutex_);
    const auto frame = address_to_frame_.find(frame_address);
    if (frame == address_to_frame_.end() ||
        frames_[frame->second].logical_page.has_value() ||
        frames_[frame->second].evicting ||
        page_to_frame_.contains(logical_page) ||
        evicting_pages_.contains(logical_page)) {
        return false;
    }
    auto& state = frames_[frame->second];
    state.logical_page = logical_page;
    state.referenced = true;
    state.dirty = false;
    page_to_frame_.emplace(logical_page, frame->second);
    return true;
}

std::optional<std::uint64_t> HbmCache::resolve(
    std::uint64_t logical_page)
{
    std::lock_guard lock(mutex_);
    const auto found = page_to_frame_.find(logical_page);
    if (found == page_to_frame_.end()) {
        return std::nullopt;
    }
    auto& frame = frames_[found->second];
    frame.referenced = true;
    return frame.address;
}

bool HbmCache::mark_dirty(std::uint64_t logical_page)
{
    std::lock_guard lock(mutex_);
    const auto found = page_to_frame_.find(logical_page);
    if (found == page_to_frame_.end()) {
        return false;
    }
    auto& frame = frames_[found->second];
    frame.referenced = true;
    if (!frame.dirty) {
        frame.dirty = true;
        ++dirty_pages_;
    }
    return true;
}

std::size_t HbmCache::dirty_pages() const
{
    std::lock_guard lock(mutex_);
    return dirty_pages_;
}

std::optional<std::uint64_t> HbmCache::free_frame() const
{
    std::lock_guard lock(mutex_);
    for (const auto& frame : frames_) {
        if (!frame.logical_page.has_value() && !frame.evicting) {
            return frame.address;
        }
    }
    return std::nullopt;
}

std::optional<CacheEviction> HbmCache::begin_eviction()
{
    std::lock_guard lock(mutex_);
    if (frames_.empty() || page_to_frame_.empty()) {
        return std::nullopt;
    }
    const auto limit = frames_.size() * 2;
    for (std::size_t scanned = 0; scanned < limit; ++scanned) {
        const auto index = clock_hand_;
        clock_hand_ = (clock_hand_ + 1) % frames_.size();
        auto& frame = frames_[index];
        if (!frame.logical_page.has_value() || frame.evicting) {
            continue;
        }
        if (frame.referenced) {
            frame.referenced = false;
            continue;
        }
        const CacheEviction result{
            .logical_page = *frame.logical_page,
            .frame_address = frame.address,
            .generation = frame.generation,
            .dirty = frame.dirty,
        };
        const auto [reservation, inserted] =
            evicting_pages_.emplace(*frame.logical_page, index);
        (void)reservation;
        if (!inserted) {
            continue;
        }
        page_to_frame_.erase(*frame.logical_page);
        frame.evicting = true;
        frame.referenced = false;
        return result;
    }
    return std::nullopt;
}

bool HbmCache::cancel_eviction(const CacheEviction& eviction)
{
    std::lock_guard lock(mutex_);
    const auto found = address_to_frame_.find(eviction.frame_address);
    if (found == address_to_frame_.end()) {
        return false;
    }
    auto& frame = frames_[found->second];
    if (!frame.evicting || frame.generation != eviction.generation ||
        !frame.logical_page.has_value() ||
        *frame.logical_page != eviction.logical_page ||
        frame.dirty != eviction.dirty ||
        evicting_pages_.find(eviction.logical_page) ==
            evicting_pages_.end() ||
        evicting_pages_.at(eviction.logical_page) != found->second ||
        page_to_frame_.contains(eviction.logical_page)) {
        return false;
    }
    const auto [resident, inserted] =
        page_to_frame_.emplace(eviction.logical_page, found->second);
    (void)resident;
    if (!inserted) {
        return false;
    }
    evicting_pages_.erase(eviction.logical_page);
    frame.evicting = false;
    frame.referenced = true;
    return true;
}

bool HbmCache::complete_eviction(const CacheEviction& eviction)
{
    std::lock_guard lock(mutex_);
    const auto found = address_to_frame_.find(eviction.frame_address);
    if (found == address_to_frame_.end()) {
        return false;
    }
    auto& frame = frames_[found->second];
    if (!frame.evicting || frame.generation != eviction.generation ||
        !frame.logical_page.has_value() ||
        *frame.logical_page != eviction.logical_page ||
        frame.dirty != eviction.dirty ||
        evicting_pages_.find(eviction.logical_page) ==
            evicting_pages_.end() ||
        evicting_pages_.at(eviction.logical_page) != found->second) {
        return false;
    }
    evicting_pages_.erase(eviction.logical_page);
    if (frame.dirty) {
        --dirty_pages_;
    }
    frame.logical_page.reset();
    frame.referenced = false;
    frame.dirty = false;
    frame.evicting = false;
    ++frame.generation;
    if (frame.generation == 0) {
        ++frame.generation;
    }
    return true;
}

}  // namespace hbfsim::runtime

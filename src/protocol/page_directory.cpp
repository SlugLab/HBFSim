#include <hbfsim/page_directory.hpp>

#include <limits>

namespace hbfsim {

PageReservation PageDirectory::lookup_or_reserve(std::uint64_t logical_page,
                                                 std::uint64_t request_id)
{
    std::lock_guard lock(mutex_);
    auto& entry = entries_[logical_page];
    if (entry.state == PageState::Invalid) {
        entry.owner_request_id = request_id;
        entry.waiter_count = 0;
        entry.state = PageState::Fetching;
        return {.owner = true, .generation = entry.generation};
    }

    if (entry.waiter_count != std::numeric_limits<std::uint32_t>::max()) {
        ++entry.waiter_count;
    }
    return {.owner = false, .generation = entry.generation};
}

bool PageDirectory::publish(std::uint64_t logical_page,
                            std::uint64_t generation,
                            std::uint64_t frame)
{
    std::lock_guard lock(mutex_);
    const auto iterator = entries_.find(logical_page);
    if (iterator == entries_.end() ||
        iterator->second.generation != generation ||
        iterator->second.state != PageState::Fetching) {
        return false;
    }

    iterator->second.frame = frame;
    iterator->second.state = PageState::Valid;
    return true;
}

std::optional<ResolvedPage> PageDirectory::resolve(
    std::uint64_t logical_page) const
{
    std::lock_guard lock(mutex_);
    const auto iterator = entries_.find(logical_page);
    if (iterator == entries_.end() ||
        (iterator->second.state != PageState::Valid &&
         iterator->second.state != PageState::Dirty)) {
        return std::nullopt;
    }
    return ResolvedPage{
        .frame = iterator->second.frame,
        .generation = iterator->second.generation,
        .state = iterator->second.state,
    };
}

bool PageDirectory::mark_dirty(std::uint64_t logical_page,
                               std::uint64_t generation)
{
    std::lock_guard lock(mutex_);
    const auto iterator = entries_.find(logical_page);
    if (iterator == entries_.end() ||
        iterator->second.generation != generation ||
        iterator->second.state != PageState::Valid) {
        return false;
    }
    iterator->second.state = PageState::Dirty;
    return true;
}

bool PageDirectory::begin_writeback(std::uint64_t logical_page,
                                    std::uint64_t generation)
{
    std::lock_guard lock(mutex_);
    const auto iterator = entries_.find(logical_page);
    if (iterator == entries_.end() ||
        iterator->second.generation != generation ||
        iterator->second.state != PageState::Dirty) {
        return false;
    }
    iterator->second.state = PageState::Writeback;
    return true;
}

bool PageDirectory::evict(std::uint64_t logical_page,
                          std::uint64_t generation)
{
    std::lock_guard lock(mutex_);
    const auto iterator = entries_.find(logical_page);
    if (iterator == entries_.end() ||
        iterator->second.generation != generation ||
        (iterator->second.state != PageState::Valid &&
         iterator->second.state != PageState::Writeback)) {
        return false;
    }

    iterator->second.frame = 0;
    iterator->second.owner_request_id = 0;
    iterator->second.waiter_count = 0;
    iterator->second.state = PageState::Invalid;
    ++iterator->second.generation;
    if (iterator->second.generation == 0) {
        ++iterator->second.generation;
    }
    return true;
}

}  // namespace hbfsim

#include "capacity_backing_router.hpp"

#include <limits>
#include <utility>

namespace hbfsim::host_service {

CapacityBackingRouter::CapacityBackingRouter(
    AdmissionHook admission_hook, DeactivationHook deactivation_hook)
    : admission_hook_(std::move(admission_hook)),
      deactivation_hook_(std::move(deactivation_hook))
{}

CapacityBackingRouter::Token CapacityBackingRouter::next_token_locked() noexcept
{
    if (tokens_exhausted_) {
        return 0;
    }
    const auto token = next_token_;
    if (next_token_ == std::numeric_limits<Token>::max()) {
        tokens_exhausted_ = true;
    } else {
        ++next_token_;
    }
    return token;
}

CapacityBackingRouter::Token CapacityBackingRouter::stage(
    std::uint32_t range_id, std::uint64_t first_page,
    std::uint64_t page_count, bool writable,
    std::shared_ptr<BackingStore> backing)
{
    if (range_id == 0 || page_count == 0 || !backing ||
        first_page > std::numeric_limits<std::uint64_t>::max() - page_count) {
        return 0;
    }
    const auto last_page = first_page + page_count;
    std::lock_guard lock(mutex_);
    Entry* vacant = nullptr;
    for (auto& entry : entries_) {
        if (entry.state == EntryState::Empty) {
            if (vacant == nullptr) {
                vacant = &entry;
            }
            continue;
        }
        if (entry.range_id == range_id) {
            return 0;
        }
        const auto entry_last = entry.first_page + entry.page_count;
        if (first_page < entry_last && entry.first_page < last_page) {
            return 0;
        }
    }
    if (vacant == nullptr) {
        return 0;
    }
    const auto token = next_token_locked();
    if (token == 0) {
        return 0;
    }
    *vacant = {
        .state = EntryState::Staged,
        .token = token,
        .range_id = range_id,
        .first_page = first_page,
        .page_count = page_count,
        .writable = writable,
        .backing = std::move(backing),
    };
    return token;
}

bool CapacityBackingRouter::activate(Token token) noexcept
{
    if (token == 0) {
        return false;
    }
    try {
        std::lock_guard lock(mutex_);
        for (auto& entry : entries_) {
            if (entry.state == EntryState::Staged && entry.token == token) {
                entry.state = EntryState::Active;
                return true;
            }
        }
    } catch (...) {
    }
    return false;
}

void CapacityBackingRouter::cancel(Token token) noexcept
{
    if (token == 0) {
        return;
    }
    try {
        std::lock_guard lock(mutex_);
        for (auto& entry : entries_) {
            if (entry.state == EntryState::Staged && entry.token == token) {
                entry = {};
                return;
            }
        }
    } catch (...) {
    }
}

RequestStatus CapacityBackingRouter::deactivate(std::uint32_t range_id)
{
    if (range_id == 0) {
        return RequestStatus::IoError;
    }
    std::unique_lock lock(mutex_);
    for (auto& entry : entries_) {
        if (entry.state != EntryState::Active || entry.range_id != range_id) {
            continue;
        }
        entry.state = EntryState::Deactivating;
        if (deactivation_hook_) {
            try {
                deactivation_hook_();
            } catch (...) {
            }
        }
        drained_.wait(lock, [&] { return entry.admissions == 0; });
        entry = {};
        return RequestStatus::Ready;
    }
    return RequestStatus::IoError;
}

std::optional<std::size_t> CapacityBackingRouter::admit(
    std::uint64_t global_page)
{
    std::lock_guard lock(mutex_);
    for (std::size_t index = 0; index < entries_.size(); ++index) {
        auto& entry = entries_[index];
        if (entry.state == EntryState::Active &&
            global_page >= entry.first_page &&
            global_page < entry.first_page + entry.page_count) {
            ++entry.admissions;
            return index;
        }
    }
    return std::nullopt;
}

void CapacityBackingRouter::release(std::size_t index) noexcept
{
    try {
        std::lock_guard lock(mutex_);
        auto& entry = entries_[index];
        if (entry.admissions != 0) {
            --entry.admissions;
            if (entry.admissions == 0) {
                drained_.notify_all();
            }
        }
    } catch (...) {
    }
}

void CapacityBackingRouter::invoke_admission_hook()
{
    if (admission_hook_) {
        admission_hook_();
    }
}

RoutedPage CapacityBackingRouter::read_page(std::uint64_t global_page,
                                             std::size_t page_bytes)
{
    const auto admitted = admit(global_page);
    if (!admitted.has_value()) {
        return {};
    }
    std::shared_ptr<BackingStore> backing;
    std::uint64_t local_page = 0;
    std::uint32_t range_id = 0;
    {
        std::lock_guard lock(mutex_);
        const auto& entry = entries_[*admitted];
        backing = entry.backing;
        local_page = global_page - entry.first_page;
        range_id = entry.range_id;
    }
    try {
        invoke_admission_hook();
        auto bytes = backing->read_page(local_page, page_bytes);
        release(*admitted);
        return {.status = RequestStatus::Ready,
                .range_id = range_id,
                .bytes = std::move(bytes)};
    } catch (...) {
        release(*admitted);
        return {};
    }
}

RequestStatus CapacityBackingRouter::write_page(
    std::uint64_t global_page, std::size_t page_bytes,
    std::span<const std::byte> bytes)
{
    const auto admitted = admit(global_page);
    if (!admitted.has_value()) {
        return RequestStatus::IoError;
    }
    std::shared_ptr<BackingStore> backing;
    std::uint64_t local_page = 0;
    bool writable = false;
    {
        std::lock_guard lock(mutex_);
        const auto& entry = entries_[*admitted];
        backing = entry.backing;
        local_page = global_page - entry.first_page;
        writable = entry.writable;
    }
    if (!writable) {
        release(*admitted);
        return RequestStatus::Unsupported;
    }
    try {
        invoke_admission_hook();
        backing->write_page(local_page, page_bytes,
                            std::vector<std::byte>(bytes.begin(), bytes.end()));
        release(*admitted);
        return RequestStatus::Ready;
    } catch (...) {
        release(*admitted);
        return RequestStatus::IoError;
    }
}

RequestStatus CapacityBackingRouter::flush(
    std::optional<std::uint32_t> range_id)
{
    if (range_id.has_value() && *range_id == 0) {
        return RequestStatus::IoError;
    }
    std::array<std::size_t, kCapacity> admitted{};
    std::size_t admitted_count = 0;
    {
        std::lock_guard lock(mutex_);
        for (std::size_t index = 0; index < entries_.size(); ++index) {
            auto& entry = entries_[index];
            if (entry.state != EntryState::Active ||
                (range_id.has_value() && entry.range_id != *range_id)) {
                continue;
            }
            ++entry.admissions;
            admitted[admitted_count++] = index;
        }
    }
    if (range_id.has_value() && admitted_count == 0) {
        return RequestStatus::IoError;
    }

    auto status = RequestStatus::Ready;
    for (std::size_t offset = 0; offset < admitted_count; ++offset) {
        const auto index = admitted[offset];
        std::shared_ptr<BackingStore> backing;
        {
            std::lock_guard lock(mutex_);
            backing = entries_[index].backing;
        }
        if (status == RequestStatus::Ready) {
            try {
                invoke_admission_hook();
                backing->flush();
            } catch (...) {
                status = RequestStatus::IoError;
            }
        }
        release(index);
    }
    return status;
}

}  // namespace hbfsim::host_service

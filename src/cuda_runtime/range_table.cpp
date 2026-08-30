#include "range_table.hpp"

#include <algorithm>
#include <limits>

namespace hbfsim::runtime {

RangeTable::RangeTable(host_service::ControlView control,
                       std::uint32_t page_bytes,
                       std::uint64_t media_capacity_bytes) noexcept
    : control_(control), page_bytes_(page_bytes),
      media_capacity_bytes_(media_capacity_bytes),
      records_(host_service::kRangeCapacity)
{
}

int RangeTable::add(std::uintptr_t base, std::size_t length,
                    const hbfsim_range_options& options,
                    RangePublishTransaction transaction,
                    void* transaction_state) noexcept
{
    std::scoped_lock lock(mutex_);
    constexpr auto valid_permissions =
        static_cast<std::uint32_t>(HBFSIM_RANGE_READ_WRITE);
    if (!control_.valid() || base == 0 || length == 0 || page_bytes_ == 0 ||
        length > std::numeric_limits<std::uintptr_t>::max() - base ||
        options.permissions == 0 ||
        (options.permissions & ~valid_permissions) != 0 ||
        options.cache_policy != HBFSIM_CACHE_POLICY_NONE) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (options.mode != HBFSIM_RANGE_MODE_TIMING &&
        options.mode != HBFSIM_RANGE_MODE_CAPACITY) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (count_ == records_.size()) {
        return HBFSIM_UNSUPPORTED;
    }
    const auto length64 = static_cast<std::uint64_t>(length);
    if (length64 > std::numeric_limits<std::uint64_t>::max() -
                       (page_bytes_ - 1)) {
        return HBFSIM_UNSUPPORTED;
    }
    const auto media_extent =
        ((length64 + page_bytes_ - 1) / page_bytes_) * page_bytes_;
    if (next_file_offset_ > media_capacity_bytes_ ||
        media_extent > media_capacity_bytes_ - next_file_offset_) {
        return HBFSIM_UNSUPPORTED;
    }

    const auto end = base + length;
    const auto insertion = std::lower_bound(
        records_.begin(), records_.begin() + count_, base,
        [](const host_service::SharedRangeRecord& record,
           std::uintptr_t candidate) { return record.base < candidate; });
    const auto index =
        static_cast<std::size_t>(insertion - records_.begin());
    if ((index != 0 &&
         records_[index - 1].base + records_[index - 1].length > base) ||
        (index != count_ && end > records_[index].base)) {
        return HBFSIM_INVALID_ARGUMENT;
    }

    const host_service::SharedRangeRecord record{
        .base = base,
        .length = length,
        .file_offset = next_file_offset_,
        .range_id = next_range_id_,
        .mode = options.mode,
        .permissions = options.permissions,
        .cache_policy = options.cache_policy,
        .stream_id = options.stream_id,
        .flags = 0,
        .page_bytes = page_bytes_,
        .reserved1 = 0,
    };
    if (transaction == nullptr) {
        return HBFSIM_IO_ERROR;
    }
    struct PendingPublication {
        RangeTable* table;
        host_service::SharedRangeRecord record;
        std::size_t index;
        std::uint64_t media_extent;
        bool published;
    } pending{this, record, index, media_extent, false};
    const auto publish = +[](void* opaque) noexcept {
        auto& item = *static_cast<PendingPublication*>(opaque);
        if (item.published) {
            return;
        }
        auto& table = *item.table;
        std::move_backward(table.records_.begin() + item.index,
                           table.records_.begin() + table.count_,
                           table.records_.begin() + table.count_ + 1);
        table.records_[item.index] = item.record;
        ++table.count_;
        ++table.next_range_id_;
        table.next_file_offset_ += item.media_extent;
        std::copy_n(table.records_.begin(), table.count_,
                    table.control_.ranges());
        host_service::atomic_store(
            table.control_.header()->range_count,
            static_cast<std::uint32_t>(table.count_),
            std::memory_order_release);
        item.published = true;
    };
    const auto status =
        transaction(record, publish, &pending, transaction_state);
    if (pending.published) {
        // Publication is the transaction's no-fail point. A gate-owned
        // callback must stage its range before invoking publish and perform no
        // fallible work afterward; normalize a hostile post-publish status so
        // callers cannot observe a committed range as rejected.
        return HBFSIM_OK;
    }
    return status == HBFSIM_OK ? HBFSIM_IO_ERROR : status;
}

int RangeTable::remove(std::uintptr_t base,
                       RangePublishTransaction transaction,
                       void* transaction_state) noexcept
{
    std::scoped_lock lock(mutex_);
    if (!control_.valid() || base == 0) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    const auto found = std::lower_bound(
        records_.begin(), records_.begin() + count_, base,
        [](const host_service::SharedRangeRecord& record,
           std::uintptr_t candidate) { return record.base < candidate; });
    if (found == records_.begin() + count_ || found->base != base) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (transaction == nullptr) {
        return HBFSIM_IO_ERROR;
    }
    struct PendingRemoval {
        RangeTable* table;
        host_service::SharedRangeRecord record;
        std::size_t index;
        bool published;
    } pending{this, *found,
              static_cast<std::size_t>(found - records_.begin()), false};
    const auto publish = +[](void* opaque) noexcept {
        auto& item = *static_cast<PendingRemoval*>(opaque);
        if (item.published) {
            return;
        }
        auto& table = *item.table;
        std::move(table.records_.begin() + item.index + 1,
                  table.records_.begin() + table.count_,
                  table.records_.begin() + item.index);
        --table.count_;
        table.records_[table.count_] = {};
        std::copy_n(table.records_.begin(), table.count_,
                    table.control_.ranges());
        table.control_.ranges()[table.count_] = {};
        host_service::atomic_store(
            table.control_.header()->range_count,
            static_cast<std::uint32_t>(table.count_),
            std::memory_order_release);
        item.published = true;
    };
    const auto status =
        transaction(pending.record, publish, &pending, transaction_state);
    if (pending.published) {
        return HBFSIM_OK;
    }
    return status == HBFSIM_OK ? HBFSIM_IO_ERROR : status;
}

RangeLookup RangeTable::lookup(std::uintptr_t address,
                               std::uint32_t bytes) const noexcept
{
    std::scoped_lock lock(mutex_);
    if (bytes == 0 || count_ == 0) {
        return {};
    }
    const auto upper = std::upper_bound(
        records_.begin(), records_.begin() + count_, address,
        [](std::uintptr_t candidate,
           const host_service::SharedRangeRecord& record) {
            return candidate < record.base;
        });
    if (upper == records_.begin()) {
        return {};
    }
    const auto& record = *std::prev(upper);
    if (address < record.base || address - record.base >= record.length) {
        return {};
    }
    if (bytes > std::numeric_limits<std::uintptr_t>::max() - address ||
        address + bytes > record.base + record.length) {
        return {.kind = RangeLookupKind::CrossesBoundary, .record = &record};
    }
    return {.kind = RangeLookupKind::Matched, .record = &record};
}

std::size_t RangeTable::size() const noexcept
{
    std::scoped_lock lock(mutex_);
    return count_;
}

}  // namespace hbfsim::runtime

#include <hbfsim/tma_async.hpp>

#include <algorithm>
#include <limits>
#include <stdexcept>

namespace hbfsim {

std::size_t TmaBarrierKeyHash::operator()(const TmaBarrierKey& key) const noexcept
{
    std::size_t result = std::hash<std::uint32_t>{}(key.cluster_id);
    const auto mix = [&](std::size_t value) {
        result ^= value + 0x9e3779b97f4a7c15ULL + (result << 6) +
                  (result >> 2);
    };
    mix(std::hash<std::uint32_t>{}(key.target_ctarank));
    mix(std::hash<std::uintptr_t>{}(key.barrier));
    mix(std::hash<std::uint64_t>{}(key.phase));
    return result;
}

bool TmaBarrierTracker::initialize(const TmaBarrierKey& key,
                                   std::uint64_t expected_bytes)
{
    if (key.barrier == 0 || expected_bytes == 0 || records_.contains(key)) {
        return false;
    }
    records_.emplace(key, Record{.expected_bytes = expected_bytes});
    return true;
}

bool TmaBarrierTracker::mark_native_complete(const TmaBarrierKey& key)
{
    const auto found = records_.find(key);
    if (found == records_.end() || found->second.faulted ||
        found->second.consumed || found->second.native_complete) {
        return false;
    }
    found->second.native_complete = true;
    return true;
}

bool TmaBarrierTracker::mark_shadow_complete(
    const TmaBarrierKey& key, std::uint64_t completed_bytes)
{
    const auto found = records_.find(key);
    if (found == records_.end() || found->second.faulted ||
        found->second.consumed || completed_bytes == 0 ||
        completed_bytes > found->second.expected_bytes -
                              found->second.shadow_bytes) {
        return false;
    }
    found->second.shadow_bytes += completed_bytes;
    return true;
}

bool TmaBarrierTracker::fault(const TmaBarrierKey& key)
{
    const auto found = records_.find(key);
    if (found == records_.end() || found->second.consumed ||
        found->second.faulted) {
        return false;
    }
    found->second.faulted = true;
    return true;
}

bool TmaBarrierTracker::consume(const TmaBarrierKey& key)
{
    const auto found = records_.find(key);
    if (found == records_.end() || state(key) != AsyncCompletionState::Ready) {
        return false;
    }
    found->second.consumed = true;
    return true;
}

bool TmaBarrierTracker::invalidate(const TmaBarrierKey& key)
{
    const auto found = records_.find(key);
    if (found == records_.end() ||
        (!found->second.consumed && !found->second.faulted)) {
        return false;
    }
    records_.erase(found);
    return true;
}

AsyncCompletionState TmaBarrierTracker::state(const TmaBarrierKey& key) const
{
    const auto found = records_.find(key);
    if (found == records_.end() || found->second.faulted) {
        return AsyncCompletionState::Faulted;
    }
    const auto& record = found->second;
    if (record.consumed) return AsyncCompletionState::Consumed;
    const bool shadow_complete = record.shadow_bytes == record.expected_bytes;
    if (record.native_complete && shadow_complete) {
        return AsyncCompletionState::Ready;
    }
    return record.native_complete ? AsyncCompletionState::ShadowPending
                                  : AsyncCompletionState::NativePending;
}

bool TmaBarrierTracker::ready(const TmaBarrierKey& key) const
{
    return state(key) == AsyncCompletionState::Ready;
}

std::size_t TmaBarrierTracker::live() const noexcept
{
    return std::count_if(records_.begin(), records_.end(), [](const auto& item) {
        return !item.second.consumed && !item.second.faulted;
    });
}

BulkGroupTracker::BulkGroupTracker(std::size_t staging_limit_bytes)
    : staging_limit_bytes_(staging_limit_bytes)
{
}

BulkOperationId BulkGroupTracker::issue_store(
    std::span<const std::byte> source)
{
    if (source.empty() || source.size() > staging_limit_bytes_ -
                                           std::min(staged_bytes_,
                                                    staging_limit_bytes_) ||
        next_id_ == 0) {
        return 0;
    }
    const auto id = next_id_++;
    Operation operation;
    operation.source.assign(source.begin(), source.end());
    staged_bytes_ += source.size();
    operations_.emplace(id, std::move(operation));
    uncommitted_.push_back(id);
    return id;
}

bool BulkGroupTracker::mark_source_read(BulkOperationId operation)
{
    const auto found = operations_.find(operation);
    if (found == operations_.end() || found->second.source_read ||
        found->second.faulted || found->second.consumed) {
        return false;
    }
    found->second.source_read = true;
    return true;
}

bool BulkGroupTracker::mark_destination_complete(BulkOperationId operation)
{
    const auto found = operations_.find(operation);
    if (found == operations_.end() || !found->second.source_read ||
        found->second.destination_complete || found->second.faulted ||
        found->second.consumed) {
        return false;
    }
    found->second.destination_complete = true;
    return true;
}

bool BulkGroupTracker::fault(BulkOperationId operation)
{
    const auto found = operations_.find(operation);
    if (found == operations_.end() || found->second.faulted ||
        found->second.consumed) {
        return false;
    }
    found->second.faulted = true;
    return true;
}

bool BulkGroupTracker::commit_group()
{
    if (uncommitted_.empty()) return false;
    groups_.push_back(std::move(uncommitted_));
    uncommitted_.clear();
    return true;
}

bool BulkGroupTracker::wait_read(std::uint32_t pending_limit) const
{
    const auto completed = groups_.size() > pending_limit
                               ? groups_.size() - pending_limit
                               : 0;
    for (std::size_t group = 0; group < completed; ++group) {
        for (const auto operation : groups_[group]) {
            const auto& item = operations_.at(operation);
            if (!item.source_read && !item.faulted) return false;
        }
    }
    return true;
}

bool BulkGroupTracker::wait_full(std::uint32_t pending_limit) const
{
    const auto completed = groups_.size() > pending_limit
                               ? groups_.size() - pending_limit
                               : 0;
    for (std::size_t group = 0; group < completed; ++group) {
        for (const auto operation : groups_[group]) {
            const auto& item = operations_.at(operation);
            if ((!item.destination_complete || !item.source_read) &&
                !item.faulted) {
                return false;
            }
        }
    }
    return true;
}

bool BulkGroupTracker::retire_completed(std::uint32_t pending_limit)
{
    if (!wait_full(pending_limit)) return false;
    const auto retire = groups_.size() > pending_limit
                            ? groups_.size() - pending_limit
                            : 0;
    for (std::size_t group = 0; group < retire; ++group) {
        for (const auto operation : groups_[group]) {
            auto& item = operations_.at(operation);
            staged_bytes_ -= item.source.size();
            item.source.clear();
            item.consumed = true;
        }
    }
    groups_.erase(groups_.begin(), groups_.begin() + retire);
    return true;
}

AsyncCompletionState BulkGroupTracker::state(BulkOperationId operation) const
{
    const auto found = operations_.find(operation);
    if (found == operations_.end() || found->second.faulted) {
        return AsyncCompletionState::Faulted;
    }
    if (found->second.consumed) return AsyncCompletionState::Consumed;
    if (found->second.source_read && found->second.destination_complete) {
        return AsyncCompletionState::Ready;
    }
    return found->second.source_read ? AsyncCompletionState::ShadowPending
                                     : AsyncCompletionState::NativePending;
}

std::span<const std::byte> BulkGroupTracker::snapshot(
    BulkOperationId operation) const
{
    const auto found = operations_.find(operation);
    return found == operations_.end()
               ? std::span<const std::byte>{}
               : std::span<const std::byte>{found->second.source};
}

std::size_t BulkGroupTracker::staged_bytes() const noexcept
{
    return staged_bytes_;
}

std::size_t BulkGroupTracker::pending_groups() const noexcept
{
    return groups_.size();
}

MulticastIssue TmaMulticastTracker::issue(
    std::uint32_t cluster_id, std::uint16_t mask, std::uintptr_t barrier,
    std::uint64_t phase, std::uint64_t bytes)
{
    if (mask == 0 || barrier == 0 || bytes == 0 ||
        bytes > std::numeric_limits<std::uint64_t>::max() - source_bytes_) {
        throw std::invalid_argument("invalid TMA multicast issue");
    }
    MulticastIssue result{.source_bytes = bytes};
    for (std::uint32_t target = 0; target < 16; ++target) {
        if ((mask & (1U << target)) == 0) continue;
        TmaBarrierKey key{.cluster_id = cluster_id,
                          .target_ctarank = target,
                          .barrier = barrier,
                          .phase = phase};
        if (!barriers_.initialize(key, bytes)) {
            throw std::logic_error("duplicate TMA multicast target key");
        }
        result.targets.push_back(key);
    }
    source_bytes_ += bytes;
    return result;
}

bool TmaMulticastTracker::mark_native_complete(const TmaBarrierKey& key)
{
    return barriers_.mark_native_complete(key);
}

bool TmaMulticastTracker::mark_shadow_complete(const TmaBarrierKey& key,
                                               std::uint64_t bytes)
{
    if (!barriers_.mark_shadow_complete(key, bytes)) return false;
    if (bytes > std::numeric_limits<std::uint64_t>::max() -
                    materialized_bytes_) {
        (void)barriers_.fault(key);
        return false;
    }
    materialized_bytes_ += bytes;
    return true;
}

bool TmaMulticastTracker::fault(const TmaBarrierKey& key)
{
    return barriers_.fault(key);
}

bool TmaMulticastTracker::ready(const TmaBarrierKey& key) const
{
    return barriers_.ready(key);
}

std::uint64_t TmaMulticastTracker::source_bytes() const noexcept
{
    return source_bytes_;
}

std::uint64_t TmaMulticastTracker::materialized_bytes() const noexcept
{
    return materialized_bytes_;
}

}  // namespace hbfsim

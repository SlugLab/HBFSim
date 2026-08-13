#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <unordered_map>
#include <vector>

namespace hbfsim {

enum class AsyncCompletionState : std::uint32_t {
    NativePending,
    ShadowPending,
    Ready,
    Faulted,
    Consumed,
};

struct TmaBarrierKey {
    std::uint32_t cluster_id{0};
    std::uint32_t target_ctarank{0};
    std::uintptr_t barrier{0};
    std::uint64_t phase{0};
    bool operator==(const TmaBarrierKey&) const = default;
};

struct TmaBarrierKeyHash {
    std::size_t operator()(const TmaBarrierKey& key) const noexcept;
};

class TmaBarrierTracker {
  public:
    bool initialize(const TmaBarrierKey& key, std::uint64_t expected_bytes);
    bool mark_native_complete(const TmaBarrierKey& key);
    bool mark_shadow_complete(const TmaBarrierKey& key,
                              std::uint64_t completed_bytes);
    bool fault(const TmaBarrierKey& key);
    bool consume(const TmaBarrierKey& key);
    bool invalidate(const TmaBarrierKey& key);
    [[nodiscard]] AsyncCompletionState state(
        const TmaBarrierKey& key) const;
    [[nodiscard]] bool ready(const TmaBarrierKey& key) const;
    [[nodiscard]] std::size_t live() const noexcept;

  private:
    struct Record {
        std::uint64_t expected_bytes{0};
        std::uint64_t shadow_bytes{0};
        bool native_complete{false};
        bool faulted{false};
        bool consumed{false};
    };
    std::unordered_map<TmaBarrierKey, Record, TmaBarrierKeyHash> records_;
};

using BulkOperationId = std::uint64_t;

class BulkGroupTracker {
  public:
    explicit BulkGroupTracker(std::size_t staging_limit_bytes);
    BulkOperationId issue_store(std::span<const std::byte> source);
    bool mark_source_read(BulkOperationId operation);
    bool mark_destination_complete(BulkOperationId operation);
    bool fault(BulkOperationId operation);
    bool commit_group();
    [[nodiscard]] bool wait_read(std::uint32_t pending_limit) const;
    [[nodiscard]] bool wait_full(std::uint32_t pending_limit) const;
    bool retire_completed(std::uint32_t pending_limit);
    [[nodiscard]] AsyncCompletionState state(BulkOperationId operation) const;
    [[nodiscard]] std::span<const std::byte> snapshot(
        BulkOperationId operation) const;
    [[nodiscard]] std::size_t staged_bytes() const noexcept;
    [[nodiscard]] std::size_t pending_groups() const noexcept;

  private:
    struct Operation {
        std::vector<std::byte> source;
        bool source_read{false};
        bool destination_complete{false};
        bool faulted{false};
        bool consumed{false};
    };
    std::size_t staging_limit_bytes_{0};
    std::size_t staged_bytes_{0};
    BulkOperationId next_id_{1};
    std::unordered_map<BulkOperationId, Operation> operations_;
    std::vector<BulkOperationId> uncommitted_;
    std::vector<std::vector<BulkOperationId>> groups_;
};

struct MulticastIssue {
    std::uint64_t source_bytes{0};
    std::vector<TmaBarrierKey> targets;
};

class TmaMulticastTracker {
  public:
    MulticastIssue issue(std::uint32_t cluster_id, std::uint16_t mask,
                         std::uintptr_t barrier, std::uint64_t phase,
                         std::uint64_t bytes);
    bool mark_native_complete(const TmaBarrierKey& key);
    bool mark_shadow_complete(const TmaBarrierKey& key,
                              std::uint64_t bytes);
    bool fault(const TmaBarrierKey& key);
    [[nodiscard]] bool ready(const TmaBarrierKey& key) const;
    [[nodiscard]] std::uint64_t source_bytes() const noexcept;
    [[nodiscard]] std::uint64_t materialized_bytes() const noexcept;

  private:
    TmaBarrierTracker barriers_;
    std::uint64_t source_bytes_{0};
    std::uint64_t materialized_bytes_{0};
};

}  // namespace hbfsim

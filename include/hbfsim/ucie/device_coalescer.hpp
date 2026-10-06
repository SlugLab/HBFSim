#pragma once

#include <hbfsim/ucie/bank_layout.hpp>
#include <hbfsim/ucie/backing_registry.hpp>

#include <array>
#include <compare>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <map>
#include <optional>
#include <set>
#include <vector>

namespace hbfsim::ucie {

struct DevicePageKey {
    std::uint64_t canonical_id{};
    std::uint64_t generation{};
    std::uint32_t stack_id{};
    std::uint32_t module_id{};
    std::uint64_t endpoint_id{};
    std::uint64_t canonical_page{};
    std::uint32_t native_channel{};
    std::uint32_t native_chip{};
    auto operator<=>(const DevicePageKey&) const = default;
};

struct DeviceMediaStart {
    std::uint64_t token{};
    DevicePageKey key;
};

struct DeviceArrival {
    std::uint64_t token{};
    bool buffer_hit{};
};

struct DeviceCoalescerCounters {
    std::uint64_t media_misses{};
    std::uint64_t inflight_joins{};
    std::uint64_t buffer_hits{};
    std::uint64_t peak_groups{};
    std::uint64_t peak_waiters{};
    std::uint64_t peak_slots_in_one_bank{};
};

struct DeviceMediaReady {
    std::uint64_t token{};
    std::vector<std::uint64_t> waiters;
    bool success{};
};

// Device-side in-flight grouping. Every submitted group has reserved one of
// exactly two slots in its bank before the caller starts a NAND read.
class DeviceReadCoalescer {
public:
    DeviceReadCoalescer(std::size_t max_groups, std::size_t max_waiters,
                        bool buffer_cache=false,
                        std::uint32_t max_channels=16,
                        std::uint32_t max_chips_per_channel=256,
                        bool inflight_coalescing=true);
    DeviceReadCoalescer(const DeviceReadCoalescer&)=default;
    DeviceReadCoalescer& operator=(const DeviceReadCoalescer&);
    DeviceReadCoalescer(DeviceReadCoalescer&&) noexcept;
    DeviceReadCoalescer& operator=(DeviceReadCoalescer&&) noexcept;
    // Nullopt means bounded backpressure; no waiter/group was installed.
    [[nodiscard]] std::optional<DeviceArrival> arrive(
        std::uint64_t waiter_id, const DevicePageKey& key);
    [[nodiscard]] std::vector<DeviceMediaStart> take_submittable();
    [[nodiscard]] DeviceMediaReady complete(std::uint64_t token, bool success);
    // detach is for an unqueued response; queued R must use consume after
    // physical delivery, including when the caller was cancelled.
    // True means a submitted NAND read still needs its real callback/drain.
    [[nodiscard]] bool detach(std::uint64_t waiter_id);
    void consume(std::uint64_t waiter_id);
    [[nodiscard]] std::size_t group_count() const noexcept { return groups_.size(); }
    [[nodiscard]] std::size_t waiter_count() const noexcept { return waiter_to_group_.size(); }
    [[nodiscard]] std::size_t occupied_slots(std::uint32_t channel,
                                              std::uint32_t chip) const;
    [[nodiscard]] const DeviceCoalescerCounters& counters() const noexcept
    { return counters_; }
private:
    void swap_state(DeviceReadCoalescer&) noexcept;
    std::size_t waiting_group_count_{};
    bool empty_wait_fastpath_{};
    enum class Phase { Waiting, Submitted, Ready };
    struct Group {
        DevicePageKey key;
        Phase phase{Phase::Waiting};
        std::set<std::uint64_t> waiters;
        int slot{-1};
        bool success{};
    };
    struct Slot {
        std::uint64_t token{};
        std::optional<DevicePageKey> cached_key;
    };
    struct Bank {
        std::array<Slot,2> slots{};
        std::deque<std::uint64_t> waiting;
    };
    using BankId=std::pair<std::uint32_t,std::uint32_t>;
    void release_if_unreferenced(std::uint64_t token);
    void prune_empty_bank(const BankId& id);
    std::size_t max_groups_;
    std::size_t max_waiters_;
    bool buffer_cache_;
    bool inflight_coalescing_;
    std::uint32_t max_channels_;
    std::uint32_t max_chips_per_channel_;
    std::uint64_t next_token_{1};
    std::map<std::uint64_t,Group> groups_;
    std::map<DevicePageKey,std::uint64_t> joinable_;
    std::map<DevicePageKey,std::uint64_t> ready_;
    std::map<std::uint64_t,std::uint64_t> waiter_to_group_;
    std::map<BankId,Bank> banks_;
    DeviceCoalescerCounters counters_;
};

} // namespace hbfsim::ucie

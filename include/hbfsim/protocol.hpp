#pragma once

#include <array>
#include <atomic>
#include <concepts>
#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace hbfsim {

inline constexpr std::uint64_t kControlMagic = 0x48424653494d3031ULL;

enum class RequestStatus : std::uint32_t {
    Pending = 0,
    Ready = 1,
    IoError = 2,
    CopyError = 3,
    ChecksumError = 4,
    Timeout = 5,
    Unsupported = 6,
    DaemonLost = 7,
};

enum class PageState : std::uint32_t {
    Invalid = 0,
    Fetching = 1,
    Valid = 2,
    Dirty = 3,
    Writeback = 4,
};

enum class RequestOperation : std::uint32_t {
    Read = 0,
    Write = 1,
};

struct alignas(64) HbfRequest {
    std::uint64_t request_id;
    std::uint64_t sequence;
    std::uint64_t arrival_ns;
    std::uint64_t logical_address;
    std::uint64_t deadline_ns;
    std::uint32_t bytes;
    std::uint32_t range_id;
    std::uint32_t stream_id;
    std::uint32_t operation;
    std::uint32_t page_generation;
    std::uint32_t flags;
};

struct alignas(64) HbfCompletion {
    std::uint64_t request_id;
    std::uint64_t modeled_completion_ns;
    std::uint64_t modeled_ns;
    std::uint64_t service_ns;
    std::uint64_t cache_frame_address;
    std::uint32_t page_generation;
    std::uint32_t status;
    std::uint64_t checksum;
    std::uint64_t reserved;
};

struct alignas(64) ControlHeader {
    std::uint64_t magic;
    std::uint32_t abi_version;
    std::uint32_t flags;
    std::uint64_t request_capacity;
    std::uint64_t completion_capacity;
    std::uint64_t request_producer_sequence;
    std::uint64_t request_consumer_sequence;
    std::uint64_t completion_producer_sequence;
    std::uint64_t completion_consumer_sequence;
};

struct alignas(64) PageEntry {
    std::uint64_t logical_page;
    std::uint64_t frame_address;
    std::uint64_t owner_request_id;
    std::uint64_t generation;
    std::uint32_t state;
    std::uint32_t waiter_count;
    std::uint64_t checksum;
    std::uint64_t reserved0;
    std::uint64_t reserved1;
};

template <typename T, std::size_t Capacity>
    requires std::is_trivially_copyable_v<T>
class SequenceRing {
    static_assert(Capacity >= 2, "ring capacity must be at least two");
    static_assert((Capacity & (Capacity - 1)) == 0,
                  "ring capacity must be a power of two");
    static_assert(std::atomic<std::uint64_t>::is_always_lock_free,
                  "shared sequence counters must be lock-free");

    struct alignas(64) Slot {
        std::atomic<std::uint64_t> sequence;
        T value{};
    };

public:
    SequenceRing()
    {
        for (std::uint64_t index = 0; index < Capacity; ++index) {
            slots_[index].sequence.store(index, std::memory_order_relaxed);
        }
    }

    SequenceRing(const SequenceRing&) = delete;
    SequenceRing& operator=(const SequenceRing&) = delete;

    bool try_push(const T& value) noexcept
    {
        auto position = producer_.load(std::memory_order_relaxed);
        for (;;) {
            auto& slot = slots_[position & (Capacity - 1)];
            const auto sequence = slot.sequence.load(std::memory_order_acquire);
            const auto difference = static_cast<std::int64_t>(sequence - position);
            if (difference == 0) {
                if (producer_.compare_exchange_weak(
                        position, position + 1, std::memory_order_relaxed)) {
                    slot.value = value;
                    slot.sequence.store(position + 1, std::memory_order_release);
                    return true;
                }
            } else if (difference < 0) {
                return false;
            } else {
                position = producer_.load(std::memory_order_relaxed);
            }
        }
    }

    bool try_pop(T& value) noexcept
    {
        auto position = consumer_.load(std::memory_order_relaxed);
        for (;;) {
            auto& slot = slots_[position & (Capacity - 1)];
            const auto sequence = slot.sequence.load(std::memory_order_acquire);
            const auto difference =
                static_cast<std::int64_t>(sequence - (position + 1));
            if (difference == 0) {
                if (consumer_.compare_exchange_weak(
                        position, position + 1, std::memory_order_relaxed)) {
                    value = slot.value;
                    slot.sequence.store(position + Capacity,
                                        std::memory_order_release);
                    return true;
                }
            } else if (difference < 0) {
                return false;
            } else {
                position = consumer_.load(std::memory_order_relaxed);
            }
        }
    }

    [[nodiscard]] std::uint64_t producer_sequence() const noexcept
    {
        return producer_.load(std::memory_order_acquire);
    }

    [[nodiscard]] std::uint64_t consumer_sequence() const noexcept
    {
        return consumer_.load(std::memory_order_acquire);
    }

private:
    alignas(64) std::atomic<std::uint64_t> producer_{0};
    alignas(64) std::atomic<std::uint64_t> consumer_{0};
    std::array<Slot, Capacity> slots_{};
};

static_assert(sizeof(HbfRequest) == 64);
static_assert(sizeof(HbfCompletion) == 64);
static_assert(sizeof(ControlHeader) == 64);
static_assert(sizeof(PageEntry) == 64);

}  // namespace hbfsim

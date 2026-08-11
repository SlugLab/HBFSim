#pragma once

#include <cstddef>
#include <cstdint>

namespace hbfsim::device {

#if defined(__CUDACC__)
#define HBFSIM_HOST_DEVICE __host__ __device__
#else
#define HBFSIM_HOST_DEVICE
#endif

inline constexpr std::uint64_t kControlMagic = 0x48424653494d3031ULL;
inline constexpr std::uint32_t kControlAbiVersion = 2;
inline constexpr std::uint32_t kRangeCapacity = 32'768;
inline constexpr std::uint32_t kMinimumRingCapacity = 2;
inline constexpr std::uint32_t kMaximumRingCapacity = 4096;
inline constexpr std::uint64_t kAdmissionClosedBit = 1ULL << 63;
inline constexpr std::uint64_t kAdmissionCountMask = ~kAdmissionClosedBit;

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

struct alignas(64) SharedControlHeader {
    std::uint64_t magic;
    std::uint32_t abi_version;
    std::uint32_t header_bytes;
    std::uint64_t region_bytes;
    std::uint32_t ring_capacity;
    std::uint32_t range_capacity;
    std::uint32_t page_capacity;
    alignas(4) std::uint32_t range_count;
    std::uint64_t range_offset;
    std::uint64_t request_offset;
    std::uint64_t completion_offset;
    std::uint64_t page_offset;
    alignas(8) std::uint64_t request_producer;
    alignas(8) std::uint64_t request_consumer;
    alignas(8) std::uint64_t completion_producer;
    alignas(8) std::uint64_t completion_consumer;
    alignas(8) std::uint64_t heartbeat_ns;
    alignas(8) std::uint64_t shutdown;
    alignas(8) std::uint64_t fault;
    alignas(8) std::uint64_t daemon_pid;
    alignas(8) std::uint64_t admission_state;
    alignas(8) std::uint64_t request_timeout_ns;
    alignas(8) std::uint64_t heartbeat_timeout_ns;
    alignas(4) std::uint32_t time_scale;
    std::uint32_t reserved0;
    alignas(8) std::uint64_t control_generation;
};

struct alignas(64) SharedRangeRecord {
    std::uint64_t base;
    std::uint64_t length;
    std::uint64_t file_offset;
    std::uint32_t range_id;
    std::uint32_t mode;
    std::uint32_t permissions;
    std::uint32_t cache_policy;
    std::uint32_t stream_id;
    std::uint32_t flags;
    std::uint64_t page_bytes;
    std::uint64_t reserved1;
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

template <typename T>
struct alignas(64) SharedRingSlot {
    alignas(8) std::uint64_t sequence;
    std::byte sequence_padding[56];
    T value;
};

using SharedRequestSlot = SharedRingSlot<HbfRequest>;
using SharedCompletionSlot = SharedRingSlot<HbfCompletion>;

struct alignas(8) ResolveResult {
    std::uint64_t address;
    std::uint32_t status;
    std::uint32_t reserved;
};

struct MediaDescriptor {
    std::uint64_t logical_address;
    std::uint32_t bytes;
    bool valid;
};

static_assert(sizeof(SharedControlHeader) == 192);
static_assert(sizeof(SharedRangeRecord) == 64);
static_assert(sizeof(HbfRequest) == 64);
static_assert(sizeof(HbfCompletion) == 64);
static_assert(sizeof(PageEntry) == 64);
static_assert(sizeof(SharedRequestSlot) == 128);
static_assert(sizeof(SharedCompletionSlot) == 128);
static_assert(sizeof(ResolveResult) == 16);
static_assert(offsetof(SharedControlHeader, range_count) == 36);
static_assert(offsetof(SharedControlHeader, range_offset) == 40);
static_assert(offsetof(SharedControlHeader, heartbeat_ns) == 104);
static_assert(offsetof(SharedControlHeader, request_timeout_ns) == 144);
static_assert(offsetof(SharedControlHeader, control_generation) == 168);

HBFSIM_HOST_DEVICE constexpr bool valid_ring_capacity(
    std::uint32_t capacity) noexcept
{
    return capacity >= kMinimumRingCapacity &&
           capacity <= kMaximumRingCapacity &&
           (capacity & (capacity - 1)) == 0;
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t find_range_index(
    const SharedRangeRecord* ranges, std::uint32_t count,
    std::uint64_t address) noexcept
{
    std::uint32_t first = 0;
    std::uint32_t last = count;
    while (first < last) {
        const auto middle = first + (last - first) / 2;
        if (ranges[middle].base <= address) {
            first = middle + 1;
        } else {
            last = middle;
        }
    }
    return first == 0 ? count : first - 1;
}

HBFSIM_HOST_DEVICE constexpr bool access_supported(
    const SharedRangeRecord& range, std::uint64_t address,
    std::uint32_t bytes, std::uint32_t operation) noexcept
{
    if (bytes == 0 || operation > 1 ||
        (range.mode != 1 && range.mode != 2) ||
        range.page_bytes == 0 || range.page_bytes > UINT32_MAX ||
        range.length > UINT64_MAX - range.base || address < range.base ||
        address - range.base >= range.length ||
        bytes > UINT64_MAX - address ||
        address + bytes > range.base + range.length ||
        (range.permissions & (1U << operation)) == 0) {
        return false;
    }
    const auto offset = address - range.base;
    const auto last_offset = offset + bytes - 1;
    return offset / range.page_bytes == last_offset / range.page_bytes;
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t resolved_address(
    const SharedRangeRecord& range, std::uint64_t original_address,
    std::uint64_t cache_frame_address) noexcept
{
    if (range.mode == 1) {
        return original_address;
    }
    if (range.mode != 2 || range.page_bytes == 0 ||
        original_address < range.base || cache_frame_address == 0) {
        return 0;
    }
    const auto page_offset =
        (original_address - range.base) % range.page_bytes;
    return page_offset > UINT64_MAX - cache_frame_address
               ? 0
               : cache_frame_address + page_offset;
}

HBFSIM_HOST_DEVICE constexpr MediaDescriptor media_descriptor(
    const SharedRangeRecord& range, std::uint64_t address,
    std::uint32_t bytes, std::uint32_t operation) noexcept
{
    if (!access_supported(range, address, bytes, operation)) {
        return {};
    }
    const auto page_offset =
        ((address - range.base) / range.page_bytes) * range.page_bytes;
    if (page_offset > UINT64_MAX - range.file_offset) {
        return {};
    }
    return {.logical_address = range.file_offset + page_offset,
            .bytes = static_cast<std::uint32_t>(range.page_bytes),
            .valid = true};
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t saturating_add(
    std::uint64_t left, std::uint64_t right) noexcept
{
    return right > UINT64_MAX - left ? UINT64_MAX : left + right;
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t saturating_multiply(
    std::uint64_t left, std::uint32_t right) noexcept
{
    return right != 0 && left > UINT64_MAX / right ? UINT64_MAX
                                                    : left * right;
}

#undef HBFSIM_HOST_DEVICE

}  // namespace hbfsim::device

#pragma once

#include <hbfsim/protocol.hpp>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <thread>
#include <type_traits>

namespace hbfsim::host_service {

inline constexpr std::uint32_t kControlAbiVersion = 10;
inline constexpr std::uint32_t kRangeCapacity = 32'768;
inline constexpr std::uint32_t kTensorMapCapacity = 256;
inline constexpr std::uint32_t kSm120StateCapacity = 256;
inline constexpr std::uint32_t kSm120RoutingLutCapacity = 64;
inline constexpr std::uint64_t kSm120ChannelConfigMagic =
    0x534d313230434846ULL;
inline constexpr std::uint32_t kControlCapabilityCapacityMedia = 1U << 0;
inline constexpr std::uint32_t kMinimumRingCapacity = 2;
inline constexpr std::uint32_t kMaximumRingCapacity = 4096;
inline constexpr std::uint64_t kAdmissionClosedBit = 1ULL << 63;
inline constexpr std::uint64_t kAdmissionCountMask = ~kAdmissionClosedBit;
using RequestAdmissionHook = void (*)(void*) noexcept;
using CapacityHandoffHook = void (*)(void*) noexcept;
inline constexpr std::uint64_t kCapacityCompletionClaimed = UINT64_MAX;
inline constexpr std::uint64_t kMaximumCapacityTicket =
    (UINT64_MAX >> 8) - 1;

inline constexpr std::uint64_t capacity_pending_token(
    std::uint64_t ticket) noexcept
{
    return ((ticket + 1) << 8) | 0x80U;
}

enum class CapacityHandoffState : std::uint32_t {
    Empty = 0,
    Available = 1,
    Claiming = 2,
    Reading = 3,
    Copied = 4,
    Reclaiming = 5,
};

struct CapacityHandoff {
    std::uint64_t ticket{0};
    std::uint64_t request_id{0};
    std::uint64_t logical_page{0};
    std::uint32_t operation{0};
};

enum CapacityMediaFlags : std::uint64_t {
    CapacityMediaNone = 0,
    CapacityMediaRead = 1ULL << 0,
    CapacityMediaProgram = 1ULL << 1,
};

struct CapacityMediaPlan {
    std::uint64_t flags{CapacityMediaNone};
    std::uint64_t program_page{0};
    std::uint32_t program_range_id{0};
};

inline constexpr bool valid_capacity_media_plan(
    const CapacityMediaPlan& media) noexcept
{
    constexpr auto known = CapacityMediaRead | CapacityMediaProgram;
    if ((media.flags & ~known) != 0) {
        return false;
    }
    const auto programs = (media.flags & CapacityMediaProgram) != 0;
    return programs ? media.program_range_id != 0
                    : media.program_page == 0 &&
                          media.program_range_id == 0;
}

struct CapacityHandoffResult {
    RequestStatus status{RequestStatus::Pending};
    std::uint64_t frame_address{0};
    CapacityMediaPlan media;
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
    std::uint64_t read_latency_ns;
    std::uint64_t program_latency_ns;
    std::uint64_t aggregate_bandwidth_bytes_per_s;
    alignas(8) std::uint64_t fast_request_sequence;
    alignas(8) std::uint64_t fast_channel_tail_ns;
    alignas(8) std::uint64_t fast_requests;
    alignas(8) std::uint64_t reference_requests;
    alignas(8) std::uint64_t fast_modeled_ns;
    std::uint64_t reference_sample_threshold;
    std::uint32_t reference_warmup_requests;
    std::uint32_t timing_model;
    alignas(8) std::uint64_t empirical_burst_state;
    std::uint64_t empirical_cumulative_ns[6];
    std::uint32_t empirical_breakpoint_pages[6];
    std::uint32_t empirical_point_count;
    std::uint32_t empirical_flags;
    alignas(8) std::uint64_t future_issued;
    alignas(8) std::uint64_t future_issue_throttle_ns;
    alignas(8) std::uint64_t future_dependency_wait_ns;
    alignas(8) std::uint64_t future_ordering_wait_ns;
    alignas(8) std::uint64_t future_faults;
    alignas(8) std::uint64_t future_drained;
    std::uint32_t tensormap_capacity;
    alignas(4) std::uint32_t tensormap_count;
    std::uint64_t tensormap_offset;
    alignas(8) std::uint64_t tensormap_publication_generation;
    alignas(8) std::uint64_t tma_issued;
    alignas(8) std::uint64_t tma_hbm_bytes;
    alignas(8) std::uint64_t tma_hbf_bytes;
    alignas(8) std::uint64_t tma_oob_bytes;
    alignas(8) std::uint64_t tma_fanout_targets;
    alignas(8) std::uint64_t tma_barrier_wait_ns;
    alignas(8) std::uint64_t tma_group_wait_ns;
    alignas(8) std::uint64_t tma_stale_generations;
    alignas(8) std::uint64_t tma_faults;
    alignas(8) std::uint64_t tma_leaked;
    std::uint64_t sm120_channel_config_offset;
    std::uint64_t sm120_channel_state_offset;
    std::uint32_t sm120_channel_state_capacity;
    alignas(4) std::uint32_t sm120_channel_state_count;
    alignas(8) std::uint64_t sm120_channel_profile_generation;
    alignas(8) std::uint64_t sm120_channel_saturated;
    alignas(8) std::uint64_t telemetry_generation;
    std::uint64_t telemetry_host_ns;
    std::int64_t telemetry_gpu_millic;
    std::uint64_t telemetry_gpu_power_mw;
    std::uint32_t telemetry_status;
    std::uint32_t thermal_mode;
    alignas(8) std::uint64_t thermal_generation;
    std::int64_t thermal_junction_millic;
    std::uint32_t thermal_service_ppm;
    std::uint32_t thermal_admission_open;
    alignas(8) std::uint64_t thermal_read_bytes;
    std::uint64_t thermal_write_bytes;
    std::uint64_t thermal_refresh_read_bytes;
    std::uint64_t thermal_refresh_write_bytes;
    alignas(8) std::uint64_t refresh_debt_bytes;
    std::uint64_t refresh_debt_generation;
    std::uint64_t thermal_transitions;
    std::uint64_t thermal_inflight_completed;
    std::uint64_t thermal_completed_refresh_blocks;
    std::uint64_t thermal_max_pec;
    std::uint64_t thermal_average_pec_millionths;
    std::uint64_t thermal_refresh_claimed_bytes;
    std::uint64_t thermal_refresh_background_drained_bytes;
    std::uint64_t thermal_summary_generation;
    std::uint32_t thermal_summary_status;
    std::uint32_t reserved1;
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

struct alignas(64) SharedTensorMapSlot {
    alignas(8) std::uint64_t publication_generation;
    std::uint64_t descriptor_generation;
    std::byte descriptor_sha256[32];
    std::byte descriptor[128];
    std::uint64_t base_address;
    std::uint64_t global_dim[5];
    std::uint64_t global_stride[5];
    std::uint32_t box_dim[5];
    std::uint32_t element_stride[5];
    std::int32_t lower_corner[5];
    std::int32_t upper_corner[5];
    std::uint32_t channels_per_pixel;
    std::uint32_t pixels_per_column;
    std::uint32_t wide_mode;
    std::uint32_t rank;
    std::uint32_t mode;
    std::uint32_t element_type;
    std::uint32_t interleave;
    std::uint32_t swizzle;
    std::uint32_t swizzle_atomicity;
    std::uint32_t l2_promotion;
    std::uint32_t oob_fill;
    std::uint32_t fenced;
};

struct alignas(64) SharedSm120ChannelConfig {
    std::uint64_t magic;
    std::uint32_t routing_version;
    std::uint32_t enabled;
    std::uint32_t gnic_count;
    std::uint32_t gpc_count;
    std::uint32_t gnic_depth;
    std::uint32_t gpc_depth;
    std::uint32_t gnic_arbitration;
    std::uint32_t gpc_arbitration;
    std::uint32_t smsp_proxy_lut_count;
    std::uint32_t gnic_lut_count;
    std::uint32_t gpc_lut_count;
    std::byte routing_program_sha256[32];
    std::byte reserved0[44];
    std::uint64_t gnic_service_ns_by_class[7];
    std::uint64_t gpc_service_ns_by_class[7];
    std::uint32_t smsp_proxy_lut[kSm120RoutingLutCapacity];
    std::uint32_t gnic_lut[kSm120RoutingLutCapacity];
    std::uint32_t gpc_lut[kSm120RoutingLutCapacity];
};

struct alignas(64) SharedSm120ChannelState {
    alignas(8) std::uint64_t gnic_tail_ns[4];
    std::uint64_t gpc_tail_ns[2];
    std::uint64_t gnic_bytes[4];
    std::uint64_t gpc_bytes[2];
    std::uint64_t gnic_service_ns[4];
    std::uint64_t gpc_service_ns[2];
    std::uint64_t gnic_requests[4];
    std::uint64_t gpc_requests[2];
    alignas(4) std::uint32_t gnic_outstanding[4];
    std::uint32_t gpc_outstanding[2];
    alignas(8) std::uint64_t saturated_requests;
    alignas(8) std::uint64_t gnic_round_robin;
    std::uint64_t gpc_round_robin;
    alignas(4) std::uint32_t lock;
    std::uint32_t maximum_gnic_outstanding;
    std::uint32_t maximum_gpc_outstanding;
    std::uint32_t migration_visible_sm_mismatch;
};

template <typename T>
struct alignas(64) SharedRingSlot {
    alignas(8) std::uint64_t sequence;
    std::byte sequence_padding[56];
    T value;
};

using SharedRequestSlot = SharedRingSlot<HbfRequest>;
using SharedCompletionSlot = SharedRingSlot<HbfCompletion>;

static_assert(std::atomic<std::uint64_t>::is_always_lock_free,
              "process-shared counters require lock-free 64-bit atomics");
static_assert(std::atomic<std::uint32_t>::is_always_lock_free,
              "process-shared counters require lock-free 32-bit atomics");
static_assert(std::is_standard_layout_v<SharedControlHeader>);
static_assert(std::is_trivially_copyable_v<SharedControlHeader>);
static_assert(std::is_trivially_copyable_v<SharedRangeRecord>);
static_assert(std::is_trivially_copyable_v<SharedTensorMapSlot>);
static_assert(std::is_trivially_copyable_v<SharedSm120ChannelConfig>);
static_assert(std::is_trivially_copyable_v<SharedSm120ChannelState>);
static_assert(std::is_trivially_copyable_v<SharedRequestSlot>);
static_assert(std::is_trivially_copyable_v<SharedCompletionSlot>);
static_assert(sizeof(SharedControlHeader) == 768);
static_assert(sizeof(SharedRangeRecord) == 64);
static_assert(sizeof(SharedTensorMapSlot) == 448);
static_assert(sizeof(SharedSm120ChannelConfig) == 1024);
static_assert(sizeof(SharedSm120ChannelState) == 256);
static_assert(sizeof(SharedRequestSlot) == 192);
static_assert(sizeof(SharedCompletionSlot) == 128);

inline bool valid_ring_capacity(std::uint32_t capacity) noexcept
{
    return capacity >= kMinimumRingCapacity &&
           capacity <= kMaximumRingCapacity &&
           (capacity & (capacity - 1)) == 0;
}

inline std::size_t align_control(std::size_t value) noexcept
{
    return (value + 63U) & ~std::size_t{63U};
}

inline std::size_t control_region_bytes(std::uint32_t capacity) noexcept
{
    if (!valid_ring_capacity(capacity)) {
        return 0;
    }
    std::size_t bytes = sizeof(SharedControlHeader);
    bytes = align_control(bytes) + sizeof(SharedRangeRecord) * kRangeCapacity;
    bytes = align_control(bytes) + sizeof(SharedRequestSlot) * capacity;
    bytes = align_control(bytes) + sizeof(SharedCompletionSlot) * capacity;
    bytes = align_control(bytes) + sizeof(PageEntry) * capacity;
    bytes = align_control(bytes) +
            sizeof(SharedTensorMapSlot) * kTensorMapCapacity;
    bytes = align_control(bytes) + sizeof(SharedSm120ChannelConfig);
    bytes = align_control(bytes) +
            sizeof(SharedSm120ChannelState) * kSm120StateCapacity;
    return align_control(bytes);
}

inline std::uint64_t atomic_load(const std::uint64_t& value,
                                 std::memory_order order) noexcept
{
    return std::atomic_ref<const std::uint64_t>(value).load(order);
}

inline std::uint32_t atomic_load(const std::uint32_t& value,
                                 std::memory_order order) noexcept
{
    return std::atomic_ref<const std::uint32_t>(value).load(order);
}

inline std::int64_t atomic_load(const std::int64_t& value,
                                std::memory_order order) noexcept
{
    return std::atomic_ref<const std::int64_t>(value).load(order);
}

inline void atomic_store(std::uint64_t& target, std::uint64_t value,
                         std::memory_order order) noexcept
{
    std::atomic_ref<std::uint64_t>(target).store(value, order);
}

inline void atomic_store(std::uint32_t& target, std::uint32_t value,
                         std::memory_order order) noexcept
{
    std::atomic_ref<std::uint32_t>(target).store(value, order);
}

inline bool atomic_compare_exchange_weak(
    std::uint64_t& target, std::uint64_t& expected,
    std::uint64_t desired,
    std::memory_order success = std::memory_order_relaxed,
    std::memory_order failure = std::memory_order_relaxed) noexcept
{
    return std::atomic_ref<std::uint64_t>(target).compare_exchange_weak(
        expected, desired, success, failure);
}

inline std::uint64_t atomic_fetch_add(std::uint64_t& target,
                                      std::uint64_t increment) noexcept
{
    return std::atomic_ref<std::uint64_t>(target).fetch_add(
        increment, std::memory_order_relaxed);
}

inline std::uint64_t atomic_fetch_sub(
    std::uint64_t& target, std::uint64_t decrement,
    std::memory_order order = std::memory_order_relaxed) noexcept
{
    return std::atomic_ref<std::uint64_t>(target).fetch_sub(decrement, order);
}

inline std::uint64_t atomic_fetch_or(
    std::uint64_t& target, std::uint64_t bits,
    std::memory_order order = std::memory_order_relaxed) noexcept
{
    return std::atomic_ref<std::uint64_t>(target).fetch_or(bits, order);
}

class ControlView {
public:
    ControlView() = default;
    ControlView(void* address, std::size_t bytes) noexcept
        : base_(static_cast<std::byte*>(address)), bytes_(bytes)
    {
    }

    [[nodiscard]] SharedControlHeader* header() const noexcept
    {
        return reinterpret_cast<SharedControlHeader*>(base_);
    }

    [[nodiscard]] bool valid() const noexcept
    {
        if (base_ == nullptr || bytes_ < sizeof(SharedControlHeader)) {
            return false;
        }
        const auto* h = header();
        if (h->magic != kControlMagic ||
            h->abi_version != kControlAbiVersion ||
            h->header_bytes != sizeof(SharedControlHeader) ||
            !valid_ring_capacity(h->ring_capacity) ||
            h->range_capacity != kRangeCapacity ||
            h->tensormap_capacity != kTensorMapCapacity ||
            h->sm120_channel_state_capacity != kSm120StateCapacity ||
            h->page_capacity != h->ring_capacity ||
            h->region_bytes != bytes_ ||
            control_region_bytes(h->ring_capacity) != bytes_) {
            return false;
        }
        return h->range_offset == sizeof(SharedControlHeader) &&
               h->request_offset == h->range_offset +
                                        sizeof(SharedRangeRecord) *
                                            kRangeCapacity &&
               h->completion_offset ==
                   h->request_offset +
                       sizeof(SharedRequestSlot) * h->ring_capacity &&
               h->page_offset == h->completion_offset +
                                      sizeof(SharedCompletionSlot) *
                                          h->ring_capacity &&
               h->tensormap_offset ==
                   align_control(h->page_offset +
                                 sizeof(PageEntry) * h->page_capacity) &&
               h->tensormap_offset +
                       sizeof(SharedTensorMapSlot) * kTensorMapCapacity ==
                   h->sm120_channel_config_offset &&
               h->sm120_channel_state_offset ==
                   h->sm120_channel_config_offset +
                       sizeof(SharedSm120ChannelConfig) &&
               h->sm120_channel_state_offset +
                       sizeof(SharedSm120ChannelState) *
                           kSm120StateCapacity ==
                   bytes_;
    }

    bool initialize(std::uint32_t capacity) noexcept
    {
        const auto required = control_region_bytes(capacity);
        if (base_ == nullptr || required == 0 || bytes_ != required) {
            return false;
        }
        std::memset(base_, 0, bytes_);
        auto* h = header();
        h->magic = kControlMagic;
        h->abi_version = kControlAbiVersion;
        h->header_bytes = sizeof(SharedControlHeader);
        h->region_bytes = bytes_;
        h->ring_capacity = capacity;
        h->range_capacity = kRangeCapacity;
        h->page_capacity = capacity;
        h->tensormap_capacity = kTensorMapCapacity;
        h->sm120_channel_state_capacity = kSm120StateCapacity;
        h->thermal_service_ppm = 1'000'000;
        h->thermal_admission_open = 1;
        h->range_offset = sizeof(SharedControlHeader);
        h->request_offset =
            h->range_offset + sizeof(SharedRangeRecord) * kRangeCapacity;
        h->completion_offset =
            h->request_offset + sizeof(SharedRequestSlot) * capacity;
        h->page_offset =
            h->completion_offset + sizeof(SharedCompletionSlot) * capacity;
        h->tensormap_offset =
            align_control(h->page_offset + sizeof(PageEntry) * capacity);
        h->sm120_channel_config_offset =
            h->tensormap_offset +
            sizeof(SharedTensorMapSlot) * kTensorMapCapacity;
        h->sm120_channel_state_offset =
            h->sm120_channel_config_offset +
            sizeof(SharedSm120ChannelConfig);
        for (std::uint64_t index = 0; index < capacity; ++index) {
            request_slots()[index].sequence = index;
            completion_slots()[index].sequence = index;
        }
        return valid();
    }

    [[nodiscard]] SharedRangeRecord* ranges() const noexcept
    {
        return reinterpret_cast<SharedRangeRecord*>(base_ +
                                                    header()->range_offset);
    }
    [[nodiscard]] SharedRequestSlot* request_slots() const noexcept
    {
        return reinterpret_cast<SharedRequestSlot*>(base_ +
                                                    header()->request_offset);
    }
    [[nodiscard]] SharedCompletionSlot* completion_slots() const noexcept
    {
        return reinterpret_cast<SharedCompletionSlot*>(
            base_ + header()->completion_offset);
    }
    [[nodiscard]] PageEntry* pages() const noexcept
    {
        return reinterpret_cast<PageEntry*>(base_ + header()->page_offset);
    }
    [[nodiscard]] SharedTensorMapSlot* tensormap_slots() const noexcept
    {
        return reinterpret_cast<SharedTensorMapSlot*>(
            base_ + header()->tensormap_offset);
    }
    [[nodiscard]] SharedSm120ChannelConfig* sm120_channel_config() const noexcept
    {
        return reinterpret_cast<SharedSm120ChannelConfig*>(
            base_ + header()->sm120_channel_config_offset);
    }
    [[nodiscard]] SharedSm120ChannelState* sm120_channel_states() const noexcept
    {
        return reinterpret_cast<SharedSm120ChannelState*>(
            base_ + header()->sm120_channel_state_offset);
    }

    bool try_push_request(const HbfRequest& request,
                          std::uint64_t& ticket) noexcept
    {
        return try_push_request_with_hooks_for_test(
            request, ticket, nullptr, nullptr, nullptr);
    }

    bool try_push_request_with_hooks_for_test(
        const HbfRequest& request, std::uint64_t& ticket,
        RequestAdmissionHook after_gate_check,
        RequestAdmissionHook after_reservation, void* hook_state) noexcept
    {
        auto* h = header();
        auto admission =
            atomic_load(h->admission_state, std::memory_order_acquire);
        if ((admission & kAdmissionClosedBit) != 0) {
            return false;
        }
        if (after_gate_check != nullptr) {
            after_gate_check(hook_state);
        }
        for (;;) {
            if ((admission & kAdmissionClosedBit) != 0 ||
                (admission & kAdmissionCountMask) == kAdmissionCountMask) {
                return false;
            }
            if (atomic_compare_exchange_weak(
                    h->admission_state, admission, admission + 1,
                    std::memory_order_acq_rel, std::memory_order_acquire)) {
                break;
            }
        }

        auto position =
            atomic_load(h->request_producer, std::memory_order_relaxed);
        for (;;) {
            auto& slot = request_slots()[position & (h->ring_capacity - 1)];
            auto& completion_slot =
                completion_slots()[position & (h->ring_capacity - 1)];
            const auto sequence =
                atomic_load(slot.sequence, std::memory_order_acquire);
            const auto completion_sequence = atomic_load(
                completion_slot.sequence, std::memory_order_acquire);
            const auto difference =
                static_cast<std::int64_t>(sequence - position);
            const auto completion_difference = static_cast<std::int64_t>(
                completion_sequence - position);
            if (difference == 0 && completion_difference == 0) {
                if (atomic_compare_exchange_weak(
                        h->request_producer, position, position + 1)) {
                    if (after_reservation != nullptr) {
                        after_reservation(hook_state);
                    }
                    auto stamped = request;
                    stamped.sequence = position;
                    slot.value = stamped;
                    atomic_store(slot.sequence, position + 1,
                                 std::memory_order_release);
                    (void)atomic_fetch_sub(h->admission_state, 1,
                                           std::memory_order_release);
                    ticket = position;
                    return true;
                }
            } else if (difference < 0 || completion_difference < 0) {
                (void)atomic_fetch_sub(h->admission_state, 1,
                                       std::memory_order_release);
                return false;
            } else {
                position = atomic_load(h->request_producer,
                                       std::memory_order_relaxed);
            }
        }
    }

    bool try_push_request(const HbfRequest& request) noexcept
    {
        std::uint64_t ignored_ticket = 0;
        return try_push_request(request, ignored_ticket);
    }

    bool try_pop_request(HbfRequest& request) noexcept
    {
        auto* h = header();
        auto position =
            atomic_load(h->request_consumer, std::memory_order_relaxed);
        for (;;) {
            auto& slot = request_slots()[position & (h->ring_capacity - 1)];
            const auto sequence =
                atomic_load(slot.sequence, std::memory_order_acquire);
            const auto difference =
                static_cast<std::int64_t>(sequence - (position + 1));
            if (difference == 0) {
                if (atomic_compare_exchange_weak(
                        h->request_consumer, position, position + 1)) {
                    request = slot.value;
                    atomic_store(slot.sequence,
                                 position + h->ring_capacity,
                                 std::memory_order_release);
                    return true;
                }
            } else if (difference < 0) {
                return false;
            } else {
                position = atomic_load(h->request_consumer,
                                       std::memory_order_relaxed);
            }
        }
    }

    bool try_publish_completion(std::uint64_t ticket,
                                const HbfCompletion& completion) noexcept
    {
        auto* h = header();
        auto& slot = completion_slots()[ticket & (h->ring_capacity - 1)];
        if (atomic_load(slot.sequence, std::memory_order_acquire) != ticket) {
            return false;
        }
        slot.value = completion;
        atomic_store(slot.sequence, ticket + 1, std::memory_order_release);
        (void)atomic_fetch_add(h->completion_producer, 1);
        return true;
    }

    bool try_consume_completion(std::uint64_t ticket,
                                HbfCompletion& completion) noexcept
    {
        auto* h = header();
        auto& slot = completion_slots()[ticket & (h->ring_capacity - 1)];
        if (atomic_load(slot.sequence, std::memory_order_acquire) !=
            ticket + 1) {
            return false;
        }
        completion = slot.value;
        atomic_store(slot.sequence, ticket + h->ring_capacity,
                     std::memory_order_release);
        (void)atomic_fetch_add(h->completion_consumer, 1);
        return true;
    }

    bool begin_capacity_handoff(const HbfRequest& request) noexcept
    {
        auto* h = header();
        if (!valid() || request.request_id == 0 || request.bytes == 0 ||
            request.logical_address % request.bytes != 0 ||
            request.operation >
                static_cast<std::uint32_t>(RequestOperation::Write) ||
            request.sequence > kMaximumCapacityTicket) {
            return false;
        }
        auto& page = pages()[request.sequence & (h->page_capacity - 1)];
        if (atomic_load(page.state, std::memory_order_acquire) !=
                static_cast<std::uint32_t>(CapacityHandoffState::Empty)) {
            return false;
        }
        page.logical_page = request.logical_address / request.bytes;
        page.frame_address = 0;
        atomic_store(page.generation, request.sequence + 1,
                     std::memory_order_relaxed);
        page.waiter_count = request.operation;
        page.checksum = 0;
        atomic_store(page.reserved0, capacity_pending_token(request.sequence),
                     std::memory_order_relaxed);
        page.reserved1 = 0;
        atomic_store(page.owner_request_id, request.request_id,
                     std::memory_order_relaxed);
        atomic_store(
            page.state,
            static_cast<std::uint32_t>(CapacityHandoffState::Available),
            std::memory_order_release);
        return true;
    }

    bool try_capacity_handoff(std::uint32_t slot_index,
                              CapacityHandoff& handoff) const noexcept
    {
        return try_capacity_handoff_with_hook_for_test(
            slot_index, handoff, nullptr, nullptr);
    }

    bool try_capacity_handoff_with_hook_for_test(
        std::uint32_t slot_index, CapacityHandoff& handoff,
        CapacityHandoffHook after_claim, void* hook_state) const noexcept
    {
        auto* h = header();
        if (!valid() || slot_index >= h->page_capacity) {
            return false;
        }
        const auto& page = pages()[slot_index];
        auto expected_state =
            static_cast<std::uint32_t>(CapacityHandoffState::Available);
        auto state = std::atomic_ref<std::uint32_t>(
            const_cast<std::uint32_t&>(page.state));
        if (!state.compare_exchange_strong(
                expected_state,
                static_cast<std::uint32_t>(CapacityHandoffState::Claiming),
                std::memory_order_acq_rel, std::memory_order_acquire)) {
            return false;
        }
        if (after_claim != nullptr) {
            after_claim(hook_state);
        }
        expected_state =
            static_cast<std::uint32_t>(CapacityHandoffState::Claiming);
        if (!state.compare_exchange_strong(
                expected_state,
                static_cast<std::uint32_t>(CapacityHandoffState::Reading),
                std::memory_order_acq_rel, std::memory_order_acquire)) {
            return false;
        }
        const auto owner =
            atomic_load(page.owner_request_id, std::memory_order_acquire);
        const auto generation =
            atomic_load(page.generation, std::memory_order_acquire);
        if (owner == 0 || generation == 0 ||
            atomic_load(page.reserved0, std::memory_order_acquire) !=
                capacity_pending_token(generation - 1) ||
            page.waiter_count >
                static_cast<std::uint32_t>(RequestOperation::Write)) {
            state.store(
                static_cast<std::uint32_t>(CapacityHandoffState::Copied),
                std::memory_order_release);
            return false;
        }
        const CapacityHandoff candidate{
            .ticket = generation - 1,
            .request_id = owner,
            .logical_page = page.logical_page,
            .operation = page.waiter_count,
        };
        handoff = candidate;
        state.store(
            static_cast<std::uint32_t>(CapacityHandoffState::Copied),
            std::memory_order_release);
        return true;
    }

    bool complete_capacity_handoff(std::uint64_t ticket,
                                   std::uint64_t request_id,
                                   std::uint64_t frame_address,
                                   RequestStatus status) noexcept
    {
        return complete_capacity_handoff_with_hook_for_test(
            ticket, request_id, frame_address, status, nullptr, nullptr);
    }

    bool complete_capacity_handoff(std::uint64_t ticket,
                                   std::uint64_t request_id,
                                   std::uint64_t frame_address,
                                   RequestStatus status,
                                   CapacityMediaPlan media) noexcept
    {
        return complete_capacity_handoff_with_hook_for_test(
            ticket, request_id, frame_address, status, nullptr, nullptr,
            media);
    }

    bool complete_capacity_handoff_with_hook_for_test(
        std::uint64_t ticket, std::uint64_t request_id,
        std::uint64_t frame_address, RequestStatus status,
        CapacityHandoffHook before_claim, void* hook_state,
        CapacityMediaPlan media = {}) noexcept
    {
        auto* h = header();
        if (!valid() || request_id == 0 ||
            ticket > kMaximumCapacityTicket ||
            status == RequestStatus::Pending ||
            status > RequestStatus::DaemonLost ||
            (status == RequestStatus::Ready) != (frame_address != 0) ||
            !valid_capacity_media_plan(media) ||
            (status != RequestStatus::Ready &&
             media.flags != CapacityMediaNone)) {
            return false;
        }
        auto& page = pages()[ticket & (h->page_capacity - 1)];
        if (atomic_load(page.owner_request_id, std::memory_order_acquire) !=
                request_id ||
            atomic_load(page.generation, std::memory_order_acquire) !=
                ticket + 1) {
            return false;
        }
        if (before_claim != nullptr) {
            before_claim(hook_state);
        }
        auto expected_status = capacity_pending_token(ticket);
        auto completion = std::atomic_ref<std::uint64_t>(page.reserved0);
        if (!completion.compare_exchange_strong(
                expected_status, kCapacityCompletionClaimed,
                std::memory_order_acq_rel, std::memory_order_acquire)) {
            return false;
        }
        if (atomic_load(page.owner_request_id, std::memory_order_acquire) !=
                request_id ||
            atomic_load(page.generation, std::memory_order_acquire) !=
                ticket + 1) {
            completion.store(
                capacity_pending_token(ticket),
                std::memory_order_release);
            return false;
        }
        page.frame_address = frame_address;
        page.checksum = media.program_page;
        page.reserved1 =
            (static_cast<std::uint64_t>(media.program_range_id) << 32) |
            static_cast<std::uint32_t>(media.flags);
        completion.store(static_cast<std::uint64_t>(status),
                         std::memory_order_release);
        return true;
    }

    bool capacity_handoff_result(const HbfRequest& request,
                                 CapacityHandoffResult& result) const noexcept
    {
        auto* h = header();
        if (!valid() || request.request_id == 0 ||
            request.sequence > kMaximumCapacityTicket) {
            return false;
        }
        const auto& page =
            pages()[request.sequence & (h->page_capacity - 1)];
        if (atomic_load(page.owner_request_id, std::memory_order_acquire) !=
                request.request_id ||
            atomic_load(page.generation, std::memory_order_acquire) !=
                request.sequence + 1) {
            return false;
        }
        const auto raw_status =
            atomic_load(page.reserved0, std::memory_order_acquire);
        if (raw_status == capacity_pending_token(request.sequence) ||
            raw_status == kCapacityCompletionClaimed ||
            raw_status >
                static_cast<std::uint64_t>(RequestStatus::DaemonLost)) {
            return false;
        }
        const auto status = static_cast<RequestStatus>(raw_status);
        const auto frame = page.frame_address;
        if ((status == RequestStatus::Ready) != (frame != 0)) {
            return false;
        }
        const CapacityMediaPlan media{
            .flags = static_cast<std::uint32_t>(page.reserved1),
            .program_page = page.checksum,
            .program_range_id = static_cast<std::uint32_t>(
                page.reserved1 >> 32),
        };
        if (!valid_capacity_media_plan(media) ||
            (status != RequestStatus::Ready &&
             media.flags != CapacityMediaNone)) {
            return false;
        }
        result = {.status = status, .frame_address = frame, .media = media};
        return true;
    }

    bool release_capacity_handoff(const HbfRequest& request) noexcept
    {
        auto* h = header();
        if (!valid() || request.request_id == 0 ||
            request.sequence > kMaximumCapacityTicket) {
            return false;
        }
        auto& page = pages()[request.sequence & (h->page_capacity - 1)];
        if (atomic_load(page.owner_request_id, std::memory_order_acquire) !=
                request.request_id ||
            atomic_load(page.generation, std::memory_order_acquire) !=
                request.sequence + 1) {
            return false;
        }
        const auto status =
            atomic_load(page.reserved0, std::memory_order_acquire);
        if (status < static_cast<std::uint64_t>(RequestStatus::Ready) ||
            status > static_cast<std::uint64_t>(RequestStatus::DaemonLost)) {
            return false;
        }
        auto state = std::atomic_ref<std::uint32_t>(page.state);
        for (;;) {
            auto expected_state = state.load(std::memory_order_acquire);
            if (expected_state == static_cast<std::uint32_t>(
                                      CapacityHandoffState::Reading)) {
                std::this_thread::yield();
                continue;
            }
            if (expected_state != static_cast<std::uint32_t>(
                                      CapacityHandoffState::Available) &&
                expected_state != static_cast<std::uint32_t>(
                                      CapacityHandoffState::Claiming) &&
                expected_state != static_cast<std::uint32_t>(
                                      CapacityHandoffState::Copied)) {
                return false;
            }
            if (state.compare_exchange_weak(
                    expected_state,
                    static_cast<std::uint32_t>(
                        CapacityHandoffState::Reclaiming),
                    std::memory_order_acq_rel, std::memory_order_acquire)) {
                break;
            }
        }
        page.logical_page = 0;
        page.frame_address = 0;
        atomic_store(page.generation, 0, std::memory_order_relaxed);
        page.waiter_count = 0;
        page.checksum = 0;
        atomic_store(page.reserved0, 0, std::memory_order_relaxed);
        page.reserved1 = 0;
        atomic_store(page.owner_request_id, 0, std::memory_order_relaxed);
        state.store(
            static_cast<std::uint32_t>(CapacityHandoffState::Empty),
            std::memory_order_release);
        return true;
    }

private:
    std::byte* base_{nullptr};
    std::size_t bytes_{0};
};

}  // namespace hbfsim::host_service

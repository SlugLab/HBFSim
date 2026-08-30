#include "hbf_device.cuh"

#include <cuda/atomic>
#include <cuda_runtime.h>

extern "C" __device__ unsigned long long __hbfsim_control = 0;
extern "C" __device__ unsigned long long __hbfsim_control_generation = 0;
extern "C" __device__ __constant__ unsigned int
    __hbfsim_device_helper_marker = 0x48424632U;

namespace {

using hbfsim::device::RequestStatus;
using hbfsim::device::ResolveResult;
using hbfsim::device::SharedControlHeader;
using hbfsim::device::SharedCompletionSlot;
using hbfsim::device::SharedRangeRecord;
using hbfsim::device::SharedRequestSlot;

template <typename T>
__device__ T system_acquire(const T* address)
{
    cuda::atomic_ref<T, cuda::thread_scope_system> value(
        *const_cast<T*>(address));
    return value.load(cuda::memory_order_acquire);
}

template <typename T>
__device__ void system_release(T* address, T desired)
{
    cuda::atomic_ref<T, cuda::thread_scope_system> value(*address);
    value.store(desired, cuda::memory_order_release);
}

__device__ bool system_compare_exchange(std::uint64_t* address,
                                        std::uint64_t& expected,
                                        std::uint64_t desired)
{
    cuda::atomic_ref<std::uint64_t, cuda::thread_scope_system> value(*address);
    return value.compare_exchange_weak(expected, desired,
                                       cuda::memory_order_relaxed,
                                       cuda::memory_order_relaxed);
}

__device__ std::uint64_t system_fetch_add(std::uint64_t* address,
                                          std::uint64_t increment)
{
    cuda::atomic_ref<std::uint64_t, cuda::thread_scope_system> value(*address);
    return value.fetch_add(increment, cuda::memory_order_relaxed);
}

__device__ void system_fetch_sub_release(std::uint64_t* address,
                                         std::uint64_t decrement)
{
    cuda::atomic_ref<std::uint64_t, cuda::thread_scope_system> value(*address);
    (void)value.fetch_sub(decrement, cuda::memory_order_release);
}

__device__ std::uint64_t gpu_time_ns()
{
    std::uint64_t now;
    asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(now));
    return now;
}

__device__ void bounded_sleep(std::uint32_t& delay_ns)
{
    __nanosleep(delay_ns);
    delay_ns = delay_ns < 524288U ? delay_ns * 2U : 1048576U;
}

__device__ std::uint32_t lane_id()
{
    std::uint32_t lane;
    asm volatile("mov.u32 %0, %%laneid;" : "=r"(lane));
    return lane;
}

__device__ ResolveResult fail(std::uint64_t address,
                              RequestStatus status)
{
    return {.address = address,
            .status = static_cast<std::uint32_t>(status),
            .reserved = 0};
}

__device__ const SharedRangeRecord* find_range(
    const SharedRangeRecord* ranges, std::uint32_t count,
    std::uint64_t address)
{
    const auto index = hbfsim::device::find_range_index(
        ranges, count, address);
    return index == count ? nullptr : &ranges[index];
}

struct WaitState {
    std::uint64_t deadline_ns;
    std::uint64_t heartbeat_value;
    std::uint64_t heartbeat_observed_ns;
    std::uint32_t sleep_ns{64};
};

struct CompletionResult {
    RequestStatus status{RequestStatus::IoError};
    std::uint64_t frame_address{0};
};

struct ThermalGateSnapshot {
    std::uint32_t scale_ppm{hbfsim::device::kThermalScaleOnePpm};
    std::uint64_t generation{0};
    bool active{false};
    bool admission_open{true};
};

__device__ RequestStatus poll_liveness(const SharedControlHeader* header,
                                       WaitState& wait)
{
    const auto now = gpu_time_ns();
    if (now >= wait.deadline_ns) {
        return RequestStatus::Timeout;
    }
    if (system_acquire(&header->shutdown) != 0 ||
        system_acquire(&header->fault) != 0) {
        return RequestStatus::DaemonLost;
    }
    const auto heartbeat = system_acquire(&header->heartbeat_ns);
    if (heartbeat == 0) {
        return RequestStatus::DaemonLost;
    }
    if (heartbeat != wait.heartbeat_value) {
        wait.heartbeat_value = heartbeat;
        wait.heartbeat_observed_ns = now;
    } else if (header->heartbeat_timeout_ns == 0 ||
               now - wait.heartbeat_observed_ns >=
                   header->heartbeat_timeout_ns) {
        return RequestStatus::DaemonLost;
    }
    bounded_sleep(wait.sleep_ns);
    return RequestStatus::Pending;
}

__device__ RequestStatus read_thermal_gate(
    const SharedControlHeader* header, ThermalGateSnapshot& snapshot)
{
    constexpr std::uint32_t kModeOff = 0;
    constexpr std::uint32_t kModePackageRc = 1;
    constexpr std::uint32_t kStageOff = 0;
    constexpr std::uint32_t kStageReadOnly = 1;
    constexpr std::uint32_t kStageShadow = 2;
    constexpr std::uint32_t kStageActive = 3;
    constexpr std::uint32_t kPolicyShutdown = 3;

    const auto before = system_acquire(&header->thermal_generation);
    if ((before & 1U) != 0) {
        return RequestStatus::Pending;
    }
    const auto mode = system_acquire(&header->thermal_mode);
    const auto stage = system_acquire(&header->thermal_stage);
    const auto policy = system_acquire(&header->thermal_policy_state);
    const auto scale = system_acquire(&header->thermal_scale_ppm);
    const auto admission = system_acquire(&header->thermal_admission_open);
    const auto flags = system_acquire(&header->thermal_flags);
    const auto after = system_acquire(&header->thermal_generation);
    if (before != after || (after & 1U) != 0) {
        return RequestStatus::Pending;
    }
    const auto off_valid = mode == kModeOff && stage == kStageOff &&
                           policy == 0 &&
                           scale == hbfsim::device::kThermalScaleOnePpm &&
                           admission == 1 && flags == 0 && after == 0;
    const auto package_valid =
        mode == kModePackageRc &&
        (stage == kStageReadOnly || stage == kStageShadow ||
         stage == kStageActive) &&
        policy <= kPolicyShutdown &&
        ((scale != 0 && scale <= hbfsim::device::kThermalScaleOnePpm) ||
         (policy == kPolicyShutdown && scale == 0 && admission == 0)) &&
        admission <= 1 &&
        (flags == 0 ||
         (flags == hbfsim::device::kThermalFlagTerminalFault &&
          stage == kStageActive && policy == kPolicyShutdown && scale == 0 &&
          admission == 0)) &&
        after != 0;
    if (!off_valid && !package_valid) {
        return RequestStatus::Unsupported;
    }
    snapshot.generation = after;
    snapshot.active = mode == kModePackageRc && stage == kStageActive;
    snapshot.scale_ppm = snapshot.active
                             ? scale
                             : hbfsim::device::kThermalScaleOnePpm;
    snapshot.admission_open = !snapshot.active || admission != 0;
    return RequestStatus::Ready;
}

__device__ RequestStatus wait_thermal_admission(
    SharedControlHeader* header, WaitState& wait,
    ThermalGateSnapshot& snapshot)
{
    bool counted = false;
    for (;;) {
        const auto read = read_thermal_gate(header, snapshot);
        if (read == RequestStatus::Unsupported) {
            return read;
        }
        if (read == RequestStatus::Ready && snapshot.admission_open) {
            return RequestStatus::Ready;
        }
        if (read == RequestStatus::Ready && !counted) {
            (void)system_fetch_add(&header->thermal_blocked_requests, 1);
            counted = true;
        }
        const auto liveness = poll_liveness(header, wait);
        if (liveness != RequestStatus::Pending) {
            return liveness;
        }
    }
}

__device__ RequestStatus reserve_request(
    SharedControlHeader* header, SharedRequestSlot* requests,
    SharedCompletionSlot* completions,
    const hbfsim::device::HbfRequest& request, WaitState& wait,
    ThermalGateSnapshot& thermal, std::uint64_t& ticket)
{
retry_after_thermal_gate:
    const auto gate = wait_thermal_admission(header, wait, thermal);
    if (gate != RequestStatus::Ready) {
        return gate;
    }
    auto admission = system_acquire(&header->admission_state);
    for (;;) {
        if ((admission & hbfsim::device::kAdmissionClosedBit) != 0 ||
            (admission & hbfsim::device::kAdmissionCountMask) ==
                hbfsim::device::kAdmissionCountMask) {
            return RequestStatus::IoError;
        }
        auto expected = admission;
        if (system_compare_exchange(&header->admission_state, expected,
                                    admission + 1)) {
            break;
        }
        admission = expected;
    }
    auto position = system_acquire(&header->request_producer);
    for (;;) {
        if ((system_acquire(&header->admission_state) &
             hbfsim::device::kAdmissionClosedBit) != 0) {
            system_fetch_sub_release(&header->admission_state, 1);
            return RequestStatus::IoError;
        }
        ThermalGateSnapshot current{};
        const auto thermal_status = read_thermal_gate(header, current);
        if (thermal_status == RequestStatus::Unsupported) {
            system_fetch_sub_release(&header->admission_state, 1);
            return thermal_status;
        }
        if (thermal_status == RequestStatus::Pending ||
            !current.admission_open) {
            system_fetch_sub_release(&header->admission_state, 1);
            goto retry_after_thermal_gate;
        }
        auto& request_slot =
            requests[position & (header->ring_capacity - 1)];
        auto& completion_slot =
            completions[position & (header->ring_capacity - 1)];
        const auto request_sequence = system_acquire(&request_slot.sequence);
        const auto completion_sequence =
            system_acquire(&completion_slot.sequence);
        const auto request_difference =
            static_cast<std::int64_t>(request_sequence - position);
        const auto completion_difference =
            static_cast<std::int64_t>(completion_sequence - position);
        if (request_difference == 0 && completion_difference == 0) {
            auto expected = position;
            if (system_compare_exchange(&header->request_producer, expected,
                                        position + 1)) {
                thermal = current;
                request_slot.value = request;
                request_slot.value.request_id = position + 1;
                request_slot.value.sequence = position;
                system_release(&request_slot.sequence, position + 1);
                system_fetch_sub_release(&header->admission_state, 1);
                ticket = position;
                return RequestStatus::Ready;
            }
            position = expected;
            continue;
        }
        if (request_difference > 0 && completion_difference > 0) {
            position = system_acquire(&header->request_producer);
            continue;
        }
        const auto liveness = poll_liveness(header, wait);
        if (liveness != RequestStatus::Pending) {
            system_fetch_sub_release(&header->admission_state, 1);
            return liveness;
        }
        position = system_acquire(&header->request_producer);
    }
}

__device__ CompletionResult wait_for_completion(
    SharedControlHeader* header, SharedCompletionSlot* completions,
    std::uint64_t ticket, WaitState& wait, std::uint64_t arrival_ns,
    std::uint32_t thermal_scale_ppm)
{
    auto& slot = completions[ticket & (header->ring_capacity - 1)];
    while (system_acquire(&slot.sequence) != ticket + 1) {
        const auto liveness = poll_liveness(header, wait);
        if (liveness != RequestStatus::Pending) {
            // fail_all publishes every exact terminal completion before its
            // release-store to the global fault word. Recheck the paired slot
            // after observing liveness failure so IoError/CopyError/etc. are
            // not collapsed into a synthesized DaemonLost status.
            if (system_acquire(&slot.sequence) != ticket + 1) {
                return {.status = liveness};
            }
            break;
        }
    }
    const auto completion = slot.value;
    system_release(&slot.sequence, ticket + header->ring_capacity);
    system_fetch_add(&header->completion_consumer, 1);
    if (completion.request_id != ticket + 1 ||
        completion.status == static_cast<std::uint32_t>(
                                 RequestStatus::Pending) ||
        completion.status > static_cast<std::uint32_t>(
                                RequestStatus::DaemonLost)) {
        return {.status = RequestStatus::IoError};
    }
    const auto status = static_cast<RequestStatus>(completion.status);
    if (status != RequestStatus::Ready) {
        return {.status = status};
    }
    const auto thermally_scaled = hbfsim::device::thermal_scaled_service_ns(
        completion.modeled_ns, thermal_scale_ppm);
    const auto scaled = hbfsim::device::saturating_multiply(
        thermally_scaled, header->time_scale);
    const auto target = hbfsim::device::saturating_add(arrival_ns, scaled);
    while (gpu_time_ns() < target) {
        if (gpu_time_ns() >= wait.deadline_ns) {
            return {.status = RequestStatus::Timeout};
        }
        bounded_sleep(wait.sleep_ns);
    }
    return {.status = RequestStatus::Ready,
            .frame_address = completion.cache_frame_address};
}

__device__ CompletionResult resolve_leader(
    SharedControlHeader* header, const SharedRangeRecord& range,
    const hbfsim::device::MediaDescriptor& media,
    std::uint32_t operation)
{
    const auto capacity = header->ring_capacity;
    if (capacity < hbfsim::device::kMinimumRingCapacity ||
        capacity > hbfsim::device::kMaximumRingCapacity ||
        (capacity & (capacity - 1)) != 0 ||
        header->request_timeout_ns == 0 ||
        header->heartbeat_timeout_ns == 0 || header->time_scale == 0) {
        return {.status = RequestStatus::Unsupported};
    }
    const auto arrival = gpu_time_ns();
    WaitState wait{.deadline_ns = hbfsim::device::saturating_add(
                       arrival, header->request_timeout_ns),
                   .heartbeat_value = system_acquire(&header->heartbeat_ns),
                   .heartbeat_observed_ns = arrival};
    if (wait.heartbeat_value == 0) {
        return {.status = RequestStatus::DaemonLost};
    }
    ThermalGateSnapshot thermal{};
    const auto gate = wait_thermal_admission(header, wait, thermal);
    if (gate != RequestStatus::Ready) {
        return {.status = gate};
    }
    auto* base = reinterpret_cast<std::byte*>(header);
    auto* requests = reinterpret_cast<SharedRequestSlot*>(
        base + header->request_offset);
    auto* completions = reinterpret_cast<SharedCompletionSlot*>(
        base + header->completion_offset);
    const hbfsim::device::HbfRequest request{
        .request_id = 0,
        .sequence = 0,
        .arrival_ns = arrival,
        .logical_address = media.logical_address,
        .deadline_ns = wait.deadline_ns,
        .bytes = media.bytes,
        .range_id = range.range_id,
        .stream_id = range.stream_id,
        .operation = operation,
        .page_generation = 0,
        .flags = 0,
    };
    std::uint64_t ticket = 0;
    const auto reserved = reserve_request(header, requests, completions,
                                          request, wait, thermal, ticket);
    return reserved == RequestStatus::Ready
               ? wait_for_completion(header, completions, ticket, wait,
                                     arrival, thermal.scale_ppm)
               : CompletionResult{.status = reserved};
}

__device__ CompletionResult resolve_fast_or_hybrid(
    SharedControlHeader* header, const SharedRangeRecord& range,
    const hbfsim::device::MediaDescriptor& media,
    std::uint32_t operation)
{
    constexpr std::uint32_t kFast = 1;
    constexpr std::uint32_t kHybrid = 2;
    const auto arrival = gpu_time_ns();
    WaitState wait{.deadline_ns = hbfsim::device::saturating_add(
                       arrival, header->request_timeout_ns),
                   .heartbeat_value = system_acquire(&header->heartbeat_ns),
                   .heartbeat_observed_ns = arrival};
    if (header->request_timeout_ns == 0 ||
        header->heartbeat_timeout_ns == 0 || wait.heartbeat_value == 0) {
        return {.status = wait.heartbeat_value == 0
                              ? RequestStatus::DaemonLost
                              : RequestStatus::Unsupported};
    }
    ThermalGateSnapshot thermal{};
    const auto gate = wait_thermal_admission(header, wait, thermal);
    if (gate != RequestStatus::Ready) {
        return {.status = gate};
    }
    const auto empirical_enabled = header->empirical_flags != 0;
    if (empirical_enabled &&
        !hbfsim::device::empirical_control_valid(*header)) {
        return {.status = RequestStatus::Unsupported};
    }
    const auto sequence = system_fetch_add(&header->fast_request_sequence, 1);
    const auto sample_key = media.logical_address ^
                            (static_cast<std::uint64_t>(range.range_id) << 32) ^
                            operation;
    if (header->timing_model == kHybrid &&
        hbfsim::device::hybrid_reference_sample(
            sequence, header->reference_warmup_requests,
            header->reference_sample_threshold, sample_key)) {
        (void)system_fetch_add(&header->reference_requests, 1);
        return resolve_leader(header, range, media, operation);
    }
    if (header->timing_model != kFast && header->timing_model != kHybrid) {
        return {.status = RequestStatus::Unsupported};
    }

    if (empirical_enabled) {
        if (media.bytes != 4096 || range.page_bytes != 4096 ||
            media.logical_address % media.bytes != 0 ||
            header->time_scale == 0 || header->request_timeout_ns == 0) {
            return {.status = RequestStatus::Unsupported};
        }
        const auto page = media.logical_address / media.bytes;
        auto previous_state =
            system_acquire(&header->empirical_burst_state);
        hbfsim::device::EmpiricalRequestService request{};
        for (;;) {
            request = hbfsim::device::empirical_request_service(
                *header, previous_state, page, operation);
            if (!request.valid) {
                return {.status = RequestStatus::Unsupported};
            }
            auto expected = previous_state;
            if (system_compare_exchange(&header->empirical_burst_state,
                                        expected,
                                        request.packed_state)) {
                break;
            }
            previous_state = expected;
        }

        const auto thermal_service =
            hbfsim::device::thermal_scaled_service_ns(
                request.service_ns, thermal.scale_ppm);
        const auto scaled_service = hbfsim::device::saturating_multiply(
            thermal_service, header->time_scale);
        auto tail = system_acquire(&header->fast_channel_tail_ns);
        std::uint64_t target = 0;
        for (;;) {
            const auto start = tail > arrival ? tail : arrival;
            target = hbfsim::device::saturating_add(start, scaled_service);
            auto expected = tail;
            if (system_compare_exchange(&header->fast_channel_tail_ns,
                                        expected, target)) {
                break;
            }
            tail = expected;
        }
        while (gpu_time_ns() < target) {
            const auto liveness = poll_liveness(header, wait);
            if (liveness != RequestStatus::Pending) {
                return {.status = liveness};
            }
        }
        (void)system_fetch_add(&header->fast_requests, 1);
        (void)system_fetch_add(&header->fast_modeled_ns,
                               request.service_ns);
        return {.status = RequestStatus::Ready};
    }

    const auto base_latency = operation == 0 ? header->read_latency_ns
                                             : header->program_latency_ns;
    const auto transfer_ns = hbfsim::device::fast_transfer_ns(
        media.bytes, header->aggregate_bandwidth_bytes_per_s);
    if (base_latency == 0 || transfer_ns == 0 || header->time_scale == 0) {
        return {.status = RequestStatus::Unsupported};
    }
    const auto thermal_base = hbfsim::device::thermal_scaled_service_ns(
        base_latency, thermal.scale_ppm);
    const auto thermal_transfer = hbfsim::device::thermal_scaled_service_ns(
        transfer_ns, thermal.scale_ppm);
    const auto base_scaled = hbfsim::device::saturating_multiply(
        thermal_base, header->time_scale);
    const auto transfer_scaled = hbfsim::device::saturating_multiply(
        thermal_transfer, header->time_scale);
    auto tail = system_acquire(&header->fast_channel_tail_ns);
    std::uint64_t transfer_target = 0;
    for (;;) {
        const auto transfer_start = tail > arrival ? tail : arrival;
        transfer_target = hbfsim::device::saturating_add(
            transfer_start, transfer_scaled);
        auto expected = tail;
        if (system_compare_exchange(&header->fast_channel_tail_ns, expected,
                                    transfer_target)) {
            break;
        }
        tail = expected;
    }
    const auto latency_target = hbfsim::device::saturating_add(
        arrival, base_scaled);
    const auto target = latency_target > transfer_target ? latency_target
                                                          : transfer_target;
    while (gpu_time_ns() < target) {
        const auto liveness = poll_liveness(header, wait);
        if (liveness != RequestStatus::Pending) {
            return {.status = liveness};
        }
    }
    (void)system_fetch_add(&header->fast_requests, 1);
    (void)system_fetch_add(
        &header->fast_modeled_ns,
        hbfsim::device::fast_service_ns(
            base_latency, media.bytes,
            header->aggregate_bandwidth_bytes_per_s));
    return {.status = RequestStatus::Ready};
}

}  // namespace

extern "C" __device__ hbfsim::device::ResolveResult
__hbfsim_resolve(std::uint64_t address, std::uint32_t bytes,
                 std::uint32_t operation)
{
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    // An unbound module must preserve ordinary HBM semantics. The launch gate
    // rejects registered HBF pointers before such a module can execute, while
    // an all-zero alias is the intentional fast-path state for non-HBF work.
    if (control_address == 0) {
        return fail(address, bytes == 0 ? RequestStatus::Unsupported
                                        : RequestStatus::Ready);
    }
    if (expected_generation == 0 || bytes == 0) {
        return fail(address, RequestStatus::Unsupported);
    }

    const auto* header = reinterpret_cast<const SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    const auto capacity = header->ring_capacity;
    const auto expected_range_offset = sizeof(SharedControlHeader);
    const auto expected_request_offset =
        expected_range_offset +
        sizeof(SharedRangeRecord) * hbfsim::device::kRangeCapacity;
    const auto expected_completion_offset =
        expected_request_offset + sizeof(SharedRequestSlot) * capacity;
    const auto expected_page_offset =
        expected_completion_offset + sizeof(SharedCompletionSlot) * capacity;
    const auto expected_region_bytes =
        expected_page_offset + sizeof(hbfsim::device::PageEntry) * capacity;
    if (header->magic != hbfsim::device::kControlMagic ||
        header->abi_version != hbfsim::device::kControlAbiVersion ||
        header->header_bytes != sizeof(SharedControlHeader) ||
        header->range_capacity != hbfsim::device::kRangeCapacity ||
        !hbfsim::device::valid_ring_capacity(capacity) ||
        header->page_capacity != capacity ||
        header->range_offset != expected_range_offset ||
        header->request_offset != expected_request_offset ||
        header->completion_offset != expected_completion_offset ||
        header->page_offset != expected_page_offset ||
        header->region_bytes != expected_region_bytes ||
        header->timing_model > 2 || header->read_latency_ns == 0 ||
        header->program_latency_ns == 0 ||
        header->aggregate_bandwidth_bytes_per_s == 0 ||
        system_acquire(&header->control_generation) != expected_generation) {
        return fail(address, RequestStatus::Unsupported);
    }
    const auto count = system_acquire(&header->range_count);
    if (count > hbfsim::device::kRangeCapacity) {
        return fail(address, RequestStatus::Unsupported);
    }
    const auto* ranges = reinterpret_cast<const SharedRangeRecord*>(
        reinterpret_cast<const std::byte*>(header) + header->range_offset);
    const auto* range = find_range(ranges, count, address);
    if (range == nullptr || address < range->base ||
        address - range->base >= range->length) {
        return fail(address, RequestStatus::Ready);
    }
    const auto media = hbfsim::device::media_descriptor(
        *range, address, bytes, operation);
    if (!media.valid) {
        return fail(address, RequestStatus::Unsupported);
    }

    const auto logical_page = media.logical_address / media.bytes;
    const auto active = __activemask();
    const auto same_range = __match_any_sync(active, range->range_id);
    const auto same_page_low = __match_any_sync(
        active, static_cast<std::uint32_t>(logical_page));
    const auto same_page_high = __match_any_sync(
        active, static_cast<std::uint32_t>(logical_page >> 32));
    const auto group = same_range & same_page_low & same_page_high;
    const auto leader = __ffs(static_cast<int>(group)) - 1;
    CompletionResult resolution{.status = RequestStatus::Ready};
    if (static_cast<int>(lane_id()) == leader) {
        auto* mutable_header = const_cast<SharedControlHeader*>(header);
        resolution = range->mode == 1 && header->timing_model != 0
                         ? resolve_fast_or_hybrid(mutable_header, *range,
                                                  media, operation)
                         : resolve_leader(mutable_header, *range, media,
                                          operation);
    }
    auto status = __shfl_sync(
        group, static_cast<std::uint32_t>(resolution.status), leader);
    const auto frame = __shfl_sync(group, resolution.frame_address, leader);
    if (status != static_cast<std::uint32_t>(RequestStatus::Ready)) {
        return {.address = address, .status = status, .reserved = 0};
    }
    const auto translated =
        hbfsim::device::resolved_address(*range, address, frame);
    if (translated == 0) {
        return fail(address, RequestStatus::CopyError);
    }
    return {.address = translated, .status = status, .reserved = 0};
}

extern "C" __device__ void __hbfsim_fault(std::uint32_t)
{
    asm volatile("trap;");
}

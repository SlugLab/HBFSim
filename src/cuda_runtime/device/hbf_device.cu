#include "hbf_device.cuh"

#include <cuda/atomic>
#include <cuda_runtime.h>

extern "C" __device__ unsigned long long __hbfsim_control = 0;
extern "C" __device__ unsigned long long __hbfsim_control_generation = 0;
extern "C" __device__ __constant__ unsigned int
    __hbfsim_device_helper_marker = 0x48424632U;

namespace {

using hbfsim::device::RequestStatus;
using hbfsim::device::DeviceFuture;
using hbfsim::device::DeviceFutureState;
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

__device__ bool system_compare_exchange(std::uint32_t* address,
                                        std::uint32_t& expected,
                                        std::uint32_t desired)
{
    cuda::atomic_ref<std::uint32_t, cuda::thread_scope_system> value(*address);
    return value.compare_exchange_weak(expected, desired,
                                       cuda::memory_order_acquire,
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

__device__ RequestStatus reserve_request(
    SharedControlHeader* header, SharedRequestSlot* requests,
    SharedCompletionSlot* completions,
    const hbfsim::device::HbfRequest& request, WaitState& wait,
    std::uint64_t& ticket)
{
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
    std::uint64_t ticket, WaitState& wait, std::uint64_t arrival_ns)
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
    const auto scaled = hbfsim::device::saturating_multiply(
        completion.modeled_ns, header->time_scale);
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
                                          request, wait, ticket);
    return reserved == RequestStatus::Ready
               ? wait_for_completion(header, completions, ticket, wait,
                                     arrival)
               : CompletionResult{.status = reserved};
}

__device__ CompletionResult resolve_fast_or_hybrid(
    SharedControlHeader* header, const SharedRangeRecord& range,
    const hbfsim::device::MediaDescriptor& media,
    std::uint32_t operation)
{
    constexpr std::uint32_t kFast = 1;
    constexpr std::uint32_t kHybrid = 2;
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

        const auto arrival = gpu_time_ns();
        const auto scaled_service = hbfsim::device::saturating_multiply(
            request.service_ns, header->time_scale);
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
        const auto deadline = hbfsim::device::saturating_add(
            arrival, header->request_timeout_ns);
        std::uint32_t sleep_ns = 64;
        while (gpu_time_ns() < target) {
            const auto now = gpu_time_ns();
            if (now >= deadline) {
                return {.status = RequestStatus::Timeout};
            }
            if (system_acquire(&header->shutdown) != 0 ||
                system_acquire(&header->fault) != 0) {
                return {.status = RequestStatus::DaemonLost};
            }
            bounded_sleep(sleep_ns);
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
    const auto arrival = gpu_time_ns();
    const auto base_scaled = hbfsim::device::saturating_multiply(
        base_latency, header->time_scale);
    const auto transfer_scaled = hbfsim::device::saturating_multiply(
        transfer_ns, header->time_scale);
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
    const auto deadline = hbfsim::device::saturating_add(
        arrival, header->request_timeout_ns);
    std::uint32_t sleep_ns = 64;
    while (gpu_time_ns() < target) {
        const auto now = gpu_time_ns();
        if (now >= deadline) {
            return {.status = RequestStatus::Timeout};
        }
        if (system_acquire(&header->shutdown) != 0 ||
            system_acquire(&header->fault) != 0) {
            return {.status = RequestStatus::DaemonLost};
        }
        bounded_sleep(sleep_ns);
    }
    (void)system_fetch_add(&header->fast_requests, 1);
    (void)system_fetch_add(
        &header->fast_modeled_ns,
        hbfsim::device::fast_service_ns(
            base_latency, media.bytes,
            header->aggregate_bandwidth_bytes_per_s));
    return {.status = RequestStatus::Ready};
}

__device__ DeviceFuture native_future(std::uint64_t address,
                                      std::uint32_t bytes,
                                      std::uint32_t instruction_id)
{
    return {.ticket = 0,
            .original_address = address,
            .resolved_address = address,
            .ready_ns = gpu_time_ns(),
            .bytes = bytes,
            .instruction_id = instruction_id,
            .channel = 0,
            .flags = hbfsim::device::DeviceFutureNative,
            .state = DeviceFutureState::Native,
            .status = bytes == 0 ? RequestStatus::Unsupported
                                 : RequestStatus::Ready};
}

__device__ DeviceFuture failed_future(std::uint64_t address,
                                      std::uint32_t bytes,
                                      std::uint32_t instruction_id,
                                      RequestStatus status,
                                      std::uint64_t ready_ns = 0)
{
    return {.ticket = 0,
            .original_address = address,
            .resolved_address = address,
            .ready_ns = ready_ns == 0 ? gpu_time_ns() : ready_ns,
            .bytes = bytes,
            .instruction_id = instruction_id,
            .channel = 0,
            .flags = hbfsim::device::DeviceFutureNone,
            .state = DeviceFutureState::TerminalError,
            .status = status};
}

__device__ bool valid_control(const SharedControlHeader* header,
                              std::uint64_t expected_generation)
{
    if (header == nullptr) {
        return false;
    }
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
        expected_page_offset + sizeof(hbfsim::device::PageEntry) * capacity +
        sizeof(hbfsim::device::SharedTensorMapSlot) *
            hbfsim::device::kTensorMapCapacity +
        sizeof(hbfsim::device::SharedSm120ChannelConfig) +
        sizeof(hbfsim::device::SharedSm120ChannelState) *
            hbfsim::device::kSm120StateCapacity;
    const auto expected_tensormap_offset =
        expected_page_offset + sizeof(hbfsim::device::PageEntry) * capacity;
    const auto expected_channel_config_offset =
        expected_tensormap_offset +
        sizeof(hbfsim::device::SharedTensorMapSlot) *
            hbfsim::device::kTensorMapCapacity;
    const auto expected_channel_state_offset =
        expected_channel_config_offset +
        sizeof(hbfsim::device::SharedSm120ChannelConfig);
    return expected_generation != 0 &&
           header->magic == hbfsim::device::kControlMagic &&
           header->abi_version == hbfsim::device::kControlAbiVersion &&
           header->header_bytes == sizeof(SharedControlHeader) &&
           header->range_capacity == hbfsim::device::kRangeCapacity &&
           hbfsim::device::valid_ring_capacity(capacity) &&
           header->page_capacity == capacity &&
           header->tensormap_capacity ==
               hbfsim::device::kTensorMapCapacity &&
           header->range_offset == expected_range_offset &&
           header->request_offset == expected_request_offset &&
           header->completion_offset == expected_completion_offset &&
           header->page_offset == expected_page_offset &&
           header->tensormap_offset == expected_tensormap_offset &&
           header->sm120_channel_config_offset ==
               expected_channel_config_offset &&
           header->sm120_channel_state_offset ==
               expected_channel_state_offset &&
           header->sm120_channel_state_capacity ==
               hbfsim::device::kSm120StateCapacity &&
           header->sm120_channel_state_count <=
               hbfsim::device::kSm120StateCapacity &&
           header->region_bytes == expected_region_bytes &&
           header->timing_model <= 2 && header->read_latency_ns != 0 &&
           header->program_latency_ns != 0 &&
           header->aggregate_bandwidth_bytes_per_s != 0 &&
           system_acquire(&header->control_generation) ==
               expected_generation;
}

struct DeviceChannelReservation {
    std::uint64_t ready_ns{0};
    std::uint32_t packed_channels{0};
    bool valid{false};
    bool saturated{false};
};

__device__ DeviceChannelReservation reserve_sm120_channels(
    SharedControlHeader* header, std::uint32_t operation,
    std::uint32_t bytes, std::uint64_t arrival_ns,
    std::uint64_t base_ready_ns, std::uint64_t media_ready_ns = 0,
    std::uint64_t capacity_ready_ns = 0,
    std::uint64_t native_ready_ns = 0)
{
    if (system_acquire(&header->sm120_channel_profile_generation) == 0) {
        return {.ready_ns = hbfsim::device::sm120_ready_max(
                    base_ready_ns, 0, 0, media_ready_ns,
                    capacity_ready_ns, native_ready_ns),
                .valid = true};
    }
    auto* base = reinterpret_cast<std::byte*>(header);
    const auto* config =
        reinterpret_cast<const hbfsim::device::SharedSm120ChannelConfig*>(
            base + header->sm120_channel_config_offset);
    std::uint32_t smid = 0;
    std::uint32_t warpid = 0;
    std::uint32_t cluster_ctarank = 0;
    asm volatile("mov.u32 %0, %%smid;" : "=r"(smid));
    asm volatile("mov.u32 %0, %%warpid;" : "=r"(warpid));
    asm volatile("mov.u32 %0, %%cluster_ctarank;"
                 : "=r"(cluster_ctarank));
    const auto cta_threads = blockDim.x * blockDim.y * blockDim.z;
    const hbfsim::device::Sm120DeviceRoutingInput input{
        .smid = smid,
        .warpid = warpid,
        .cta_x = blockDim.x,
        .cta_y = blockDim.y,
        .cta_z = blockDim.z,
        .resident_warps = (cta_threads + 31U) / 32U,
        .cluster_ctarank = cluster_ctarank,
        .operation = operation,
    };
    auto selection = hbfsim::device::route_sm120_channel(*config, input);
    if (!selection.valid || bytes == 0 ||
        smid >= system_acquire(&header->sm120_channel_state_count)) {
        return {};
    }
    auto* states =
        reinterpret_cast<hbfsim::device::SharedSm120ChannelState*>(
            base + header->sm120_channel_state_offset);
    auto& state = states[smid];
    auto* lock = &state.lock;
    std::uint32_t delay = 64;
    for (;;) {
        std::uint32_t expected = 0;
        if (system_compare_exchange(lock, expected, 1)) break;
        bounded_sleep(delay);
    }
    if (config->gnic_arbitration == 1) {
        selection.gnic = static_cast<std::uint32_t>(
            (selection.gnic + state.gnic_round_robin++) % 4);
    }
    if (config->gpc_arbitration == 1) {
        selection.gpc = static_cast<std::uint32_t>(
            (selection.gpc + state.gpc_round_robin++) % 2);
    }
    const auto take_gnic =
        hbfsim::device::sm120_operation_uses_gnic(operation);
    const auto take_gpc =
        hbfsim::device::sm120_operation_uses_gpc(operation);
    const auto gnic_service = take_gnic
        ? hbfsim::device::saturating_multiply(
              config->gnic_service_ns_by_class[operation],
              header->time_scale) : 0;
    const auto gpc_service = take_gpc
        ? hbfsim::device::saturating_multiply(
              config->gpc_service_ns_by_class[operation],
              header->time_scale) : 0;
    const auto gnic_tail = take_gnic
        ? system_acquire(&state.gnic_tail_ns[selection.gnic]) : 0;
    const auto gpc_tail = take_gpc
        ? system_acquire(&state.gpc_tail_ns[selection.gpc]) : 0;
    const auto gnic_window = take_gnic
        ? hbfsim::device::saturating_multiply(
              gnic_service, config->gnic_depth) : 0;
    const auto gpc_window = take_gpc
        ? hbfsim::device::saturating_multiply(
              gpc_service, config->gpc_depth) : 0;
    const auto saturated =
        (take_gnic && gnic_tail > arrival_ns &&
         gnic_tail - arrival_ns >= gnic_window) ||
        (take_gpc && gpc_tail > arrival_ns &&
         gpc_tail - arrival_ns >= gpc_window);
    const auto queued = [](std::uint64_t tail, std::uint64_t arrival,
                           std::uint64_t service) {
        if (service == 0 || tail <= arrival) return std::uint32_t{1};
        const auto pending = (tail - arrival + service - 1) / service;
        return pending >= UINT32_MAX ? UINT32_MAX
                                     : static_cast<std::uint32_t>(pending + 1);
    };
    if (take_gnic) {
        const auto value = queued(gnic_tail, arrival_ns, gnic_service);
        if (value > state.maximum_gnic_outstanding)
            state.maximum_gnic_outstanding = value;
    }
    if (take_gpc) {
        const auto value = queued(gpc_tail, arrival_ns, gpc_service);
        if (value > state.maximum_gpc_outstanding)
            state.maximum_gpc_outstanding = value;
    }
    if (saturated) {
        (void)system_fetch_add(&state.saturated_requests, 1);
        (void)system_fetch_add(&header->sm120_channel_saturated, 1);
        system_release(lock, 0U);
        return {.ready_ns = UINT64_MAX,
                .packed_channels = selection.gnic |
                    (selection.gpc << 2) | (take_gnic ? 1U << 3 : 0) |
                    (take_gpc ? 1U << 4 : 0),
                .valid = true,
                .saturated = true};
    }
    std::uint64_t gnic_ready = 0;
    std::uint64_t gpc_ready = 0;
    if (take_gnic) {
        gnic_ready = hbfsim::device::saturating_add(
            gnic_tail > arrival_ns ? gnic_tail : arrival_ns,
            gnic_service);
        system_release(&state.gnic_tail_ns[selection.gnic], gnic_ready);
        (void)system_fetch_add(&state.gnic_bytes[selection.gnic], bytes);
        (void)system_fetch_add(&state.gnic_service_ns[selection.gnic],
                               gnic_service);
        (void)system_fetch_add(&state.gnic_requests[selection.gnic], 1);
    }
    if (take_gpc) {
        gpc_ready = hbfsim::device::saturating_add(
            gpc_tail > arrival_ns ? gpc_tail : arrival_ns, gpc_service);
        system_release(&state.gpc_tail_ns[selection.gpc], gpc_ready);
        (void)system_fetch_add(&state.gpc_bytes[selection.gpc], bytes);
        (void)system_fetch_add(&state.gpc_service_ns[selection.gpc],
                               gpc_service);
        (void)system_fetch_add(&state.gpc_requests[selection.gpc], 1);
    }
    system_release(lock, 0U);
    return {.ready_ns = hbfsim::device::sm120_ready_max(
                base_ready_ns, gnic_ready, gpc_ready, media_ready_ns,
                capacity_ready_ns, native_ready_ns),
            .packed_channels = selection.gnic | (selection.gpc << 2) |
                (take_gnic ? 1U << 3 : 0) | (take_gpc ? 1U << 4 : 0) |
                (smid << 8),
            .valid = true};
}

__device__ DeviceFuture issue_reference(
    SharedControlHeader* header, const SharedRangeRecord& range,
    const hbfsim::device::MediaDescriptor& media, std::uint64_t address,
    std::uint32_t bytes, std::uint32_t operation,
    std::uint32_t instruction_id)
{
    const auto arrival = gpu_time_ns();
    WaitState wait{.deadline_ns = hbfsim::device::saturating_add(
                       arrival, header->request_timeout_ns),
                   .heartbeat_value = system_acquire(&header->heartbeat_ns),
                   .heartbeat_observed_ns = arrival};
    if (header->request_timeout_ns == 0 ||
        header->heartbeat_timeout_ns == 0 || header->time_scale == 0) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }
    if (wait.heartbeat_value == 0) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::DaemonLost);
    }
    auto* base = reinterpret_cast<std::byte*>(header);
    auto* requests = reinterpret_cast<SharedRequestSlot*>(
        base + header->request_offset);
    auto* completions = reinterpret_cast<SharedCompletionSlot*>(
        base + header->completion_offset);
    const auto request_operation = operation == 2 ? 0U : operation;
    const hbfsim::device::HbfRequest request{
        .request_id = 0,
        .sequence = 0,
        .arrival_ns = arrival,
        .logical_address = media.logical_address,
        .deadline_ns = wait.deadline_ns,
        .bytes = media.bytes,
        .range_id = range.range_id,
        .stream_id = range.stream_id,
        .operation = request_operation,
        .page_generation = 0,
        .flags = 0,
        .instruction_id = instruction_id,
        .future_flags = hbfsim::device::DeviceFutureReference |
                        (range.mode == 2
                             ? hbfsim::device::DeviceFutureCapacity
                             : 0U) |
                        (operation == 2
                             ? hbfsim::device::DeviceFutureAtomic
                             : 0U),
        .issue_timestamp_ns = arrival,
    };
    std::uint64_t ticket = 0;
    const auto reserve_begin = gpu_time_ns();
    const auto reserved = reserve_request(header, requests, completions,
                                          request, wait, ticket);
    const auto reserve_end = gpu_time_ns();
    (void)system_fetch_add(&header->future_issue_throttle_ns,
                           reserve_end - reserve_begin);
    if (reserved != RequestStatus::Ready) {
        return failed_future(address, bytes, instruction_id, reserved);
    }
    (void)system_fetch_add(&header->future_issued, 1);
    const auto flags = hbfsim::device::DeviceFutureReference |
                       (range.mode == 2
                            ? hbfsim::device::DeviceFutureCapacity
                            : 0U) |
                       (operation == 2
                            ? hbfsim::device::DeviceFutureAtomic
                            : 0U);
    return {.ticket = ticket,
            .original_address = address,
            .resolved_address = range.mode == 1 ? address : 0,
            .ready_ns = arrival,
            .bytes = bytes,
            .instruction_id = instruction_id,
            .channel = 0,
            .flags = flags,
            .state = DeviceFutureState::Issued,
            .status = RequestStatus::Pending};
}

__device__ DeviceFuture issue_fast(
    SharedControlHeader* header, const SharedRangeRecord& range,
    const hbfsim::device::MediaDescriptor& media, std::uint64_t address,
    std::uint32_t bytes, std::uint32_t operation,
    std::uint32_t instruction_id)
{
    constexpr std::uint32_t kFast = 1;
    constexpr std::uint32_t kHybrid = 2;
    const auto empirical_enabled = header->empirical_flags != 0;
    if (empirical_enabled &&
        !hbfsim::device::empirical_control_valid(*header)) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
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
        return issue_reference(header, range, media, address, bytes,
                               operation, instruction_id);
    }
    if (header->timing_model != kFast &&
        header->timing_model != kHybrid) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }

    const auto arrival = gpu_time_ns();
    std::uint64_t modeled_service = 0;
    std::uint64_t target = 0;
    if (empirical_enabled) {
        if (media.bytes != 4096 || range.page_bytes != 4096 ||
            media.logical_address % media.bytes != 0 ||
            header->time_scale == 0 || header->request_timeout_ns == 0) {
            return failed_future(address, bytes, instruction_id,
                                 RequestStatus::Unsupported);
        }
        const auto page = media.logical_address / media.bytes;
        auto previous_state = system_acquire(&header->empirical_burst_state);
        hbfsim::device::EmpiricalRequestService request{};
        for (;;) {
            request = hbfsim::device::empirical_request_service(
                *header, previous_state, page, operation == 2 ? 0 : operation);
            if (!request.valid) {
                return failed_future(address, bytes, instruction_id,
                                     RequestStatus::Unsupported);
            }
            auto expected = previous_state;
            if (system_compare_exchange(&header->empirical_burst_state,
                                        expected, request.packed_state)) {
                break;
            }
            previous_state = expected;
        }
        modeled_service = request.service_ns;
        const auto scaled_service = hbfsim::device::saturating_multiply(
            modeled_service, header->time_scale);
        if (system_acquire(
                &header->sm120_channel_profile_generation) != 0) {
            target = hbfsim::device::saturating_add(arrival,
                                                     scaled_service);
        } else {
            auto tail = system_acquire(&header->fast_channel_tail_ns);
            for (;;) {
                target = hbfsim::device::fast_future_ready_ns(
                    arrival, tail, 0, scaled_service);
                auto expected = tail;
                if (system_compare_exchange(&header->fast_channel_tail_ns,
                                            expected, target)) {
                    break;
                }
                tail = expected;
            }
        }
    } else {
        const auto request_operation = operation == 2 ? 0U : operation;
        const auto base_latency = request_operation == 0
                                      ? header->read_latency_ns
                                      : header->program_latency_ns;
        const auto transfer_ns = hbfsim::device::fast_transfer_ns(
            media.bytes, header->aggregate_bandwidth_bytes_per_s);
        if (base_latency == 0 || transfer_ns == 0 ||
            header->time_scale == 0 || header->request_timeout_ns == 0) {
            return failed_future(address, bytes, instruction_id,
                                 RequestStatus::Unsupported);
        }
        const auto latency_scaled = hbfsim::device::saturating_multiply(
            base_latency, header->time_scale);
        const auto transfer_scaled = hbfsim::device::saturating_multiply(
            transfer_ns, header->time_scale);
        if (system_acquire(
                &header->sm120_channel_profile_generation) != 0) {
            target = hbfsim::device::fast_future_ready_ns(
                arrival, arrival, latency_scaled, transfer_scaled);
        } else {
            auto tail = system_acquire(&header->fast_channel_tail_ns);
            for (;;) {
                target = hbfsim::device::fast_future_ready_ns(
                    arrival, tail, latency_scaled, transfer_scaled);
                auto expected = tail;
                const auto next_tail = hbfsim::device::saturating_add(
                    tail > arrival ? tail : arrival, transfer_scaled);
                if (system_compare_exchange(&header->fast_channel_tail_ns,
                                            expected, next_tail)) {
                    break;
                }
                tail = expected;
            }
        }
        modeled_service = hbfsim::device::fast_service_ns(
            base_latency, media.bytes,
            header->aggregate_bandwidth_bytes_per_s);
    }
    const auto channel_operation = operation == 0 ? 0U
                                   : operation == 1 ? 1U : 6U;
    const auto channel = reserve_sm120_channels(
        header, channel_operation, bytes, arrival, target, target,
        range.mode == 2 ? target : 0, 0);
    if (!channel.valid || channel.saturated) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }
    target = channel.ready_ns;
    const auto deadline = hbfsim::device::saturating_add(
        arrival, header->request_timeout_ns);
    if (target > deadline) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Timeout, deadline);
    }
    (void)system_fetch_add(&header->fast_requests, 1);
    (void)system_fetch_add(&header->fast_modeled_ns, modeled_service);
    (void)system_fetch_add(&header->future_issued, 1);
    return {.ticket = 0,
            .original_address = address,
            .resolved_address = address,
            .ready_ns = target,
            .bytes = bytes,
            .instruction_id = instruction_id,
            .channel = channel.packed_channels,
            .flags = hbfsim::device::DeviceFutureTiming |
                     (operation == 2
                          ? hbfsim::device::DeviceFutureAtomic
                          : 0U),
            .state = DeviceFutureState::Issued,
            .status = RequestStatus::Pending};
}

}  // namespace

__device__ hbfsim::device::ResolveResult resolve_sync_legacy(
    std::uint64_t address, std::uint32_t bytes, std::uint32_t operation)
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
        expected_page_offset + sizeof(hbfsim::device::PageEntry) * capacity +
        sizeof(hbfsim::device::SharedTensorMapSlot) *
            hbfsim::device::kTensorMapCapacity +
        sizeof(hbfsim::device::SharedSm120ChannelConfig) +
        sizeof(hbfsim::device::SharedSm120ChannelState) *
            hbfsim::device::kSm120StateCapacity;
    const auto expected_tensormap_offset =
        expected_page_offset + sizeof(hbfsim::device::PageEntry) * capacity;
    const auto expected_channel_config_offset =
        expected_tensormap_offset +
        sizeof(hbfsim::device::SharedTensorMapSlot) *
            hbfsim::device::kTensorMapCapacity;
    const auto expected_channel_state_offset =
        expected_channel_config_offset +
        sizeof(hbfsim::device::SharedSm120ChannelConfig);
    if (header->magic != hbfsim::device::kControlMagic ||
        header->abi_version != hbfsim::device::kControlAbiVersion ||
        header->header_bytes != sizeof(SharedControlHeader) ||
        header->range_capacity != hbfsim::device::kRangeCapacity ||
        !hbfsim::device::valid_ring_capacity(capacity) ||
        header->page_capacity != capacity ||
        header->tensormap_capacity != hbfsim::device::kTensorMapCapacity ||
        header->range_offset != expected_range_offset ||
        header->request_offset != expected_request_offset ||
        header->completion_offset != expected_completion_offset ||
        header->page_offset != expected_page_offset ||
        header->tensormap_offset != expected_tensormap_offset ||
        header->sm120_channel_config_offset !=
            expected_channel_config_offset ||
        header->sm120_channel_state_offset != expected_channel_state_offset ||
        header->sm120_channel_state_capacity !=
            hbfsim::device::kSm120StateCapacity ||
        header->sm120_channel_state_count >
            hbfsim::device::kSm120StateCapacity ||
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

extern "C" __device__ hbfsim::device::DeviceFuture
__hbfsim_future_issue(std::uint64_t address, std::uint32_t bytes,
                      std::uint32_t operation,
                      std::uint32_t instruction_id)
{
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    if (control_address == 0) {
        return native_future(address, bytes, instruction_id);
    }
    if (bytes == 0 || operation > 2) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }
    auto* header = reinterpret_cast<SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (!valid_control(header, expected_generation)) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }
    const auto count = system_acquire(&header->range_count);
    if (count > hbfsim::device::kRangeCapacity) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }
    const auto* ranges = reinterpret_cast<const SharedRangeRecord*>(
        reinterpret_cast<const std::byte*>(header) + header->range_offset);
    const auto* range = find_range(ranges, count, address);
    if (range == nullptr || address < range->base ||
        address - range->base >= range->length) {
        return native_future(address, bytes, instruction_id);
    }
    if (operation == 2 && (range->permissions & 3U) != 3U) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }
    const auto media_operation = operation == 2 ? 0U : operation;
    const auto media = hbfsim::device::media_descriptor(
        *range, address, bytes, media_operation);
    if (!media.valid) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }
    return range->mode == 1 && header->timing_model != 0
               ? issue_fast(header, *range, media, address, bytes,
                            operation, instruction_id)
               : issue_reference(header, *range, media, address, bytes,
                                 operation, instruction_id);
}

extern "C" __device__ std::uint32_t __hbfsim_future_poll(
    hbfsim::device::DeviceFuture future)
{
    if (future.state == DeviceFutureState::Native ||
        future.state == DeviceFutureState::Ready ||
        future.state == DeviceFutureState::DeferredMaterialization ||
        future.state == DeviceFutureState::Consumed) {
        return 1;
    }
    const auto now = gpu_time_ns();
    if (future.state == DeviceFutureState::TerminalError) {
        return now >= future.ready_ns ? 1U : 0U;
    }
    if ((future.flags & hbfsim::device::DeviceFutureTiming) != 0) {
        return now >= future.ready_ns ? 1U : 0U;
    }

    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    if (control_address == 0) {
        return 1;
    }
    const auto* header = reinterpret_cast<const SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (!valid_control(header, expected_generation)) {
        return 1;
    }
    const auto deadline = hbfsim::device::saturating_add(
        future.ready_ns, header->request_timeout_ns);
    if (hbfsim::device::future_deadline_expired(now, deadline)) {
        return 1;
    }
    const auto* base = reinterpret_cast<const std::byte*>(header);
    const auto* completions = reinterpret_cast<const SharedCompletionSlot*>(
        base + header->completion_offset);
    const auto& slot =
        completions[future.ticket & (header->ring_capacity - 1)];
    if (system_acquire(&slot.sequence) != future.ticket + 1) {
        return 0;
    }
    const auto completion = slot.value;
    if (completion.request_id != future.ticket + 1 ||
        completion.status !=
            static_cast<std::uint32_t>(RequestStatus::Ready)) {
        return 1;
    }
    const auto scaled = hbfsim::device::saturating_multiply(
        completion.modeled_ns, header->time_scale);
    const auto target = hbfsim::device::saturating_add(
        future.ready_ns, scaled);
    return now >= target ? 1U : 0U;
}

extern "C" __device__ std::uint32_t __hbfsim_tensormap_lookup(
    const std::byte* descriptor_sha256, std::uint64_t descriptor_generation,
    std::uint64_t* base_address)
{
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    if (control_address == 0 || descriptor_sha256 == nullptr ||
        base_address == nullptr) {
        return UINT32_MAX;
    }
    const auto* header = reinterpret_cast<const SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (!valid_control(header, expected_generation)) return UINT32_MAX;
    const auto count = system_acquire(&header->tensormap_count);
    if (count > hbfsim::device::kTensorMapCapacity) return UINT32_MAX;
    const auto* slots =
        reinterpret_cast<const hbfsim::device::SharedTensorMapSlot*>(
            reinterpret_cast<const std::byte*>(header) +
            header->tensormap_offset);
    for (std::uint32_t index = count; index != 0; --index) {
        const auto& slot = slots[index - 1];
        if (system_acquire(&slot.publication_generation) == 0 ||
            slot.descriptor_generation != descriptor_generation ||
            !hbfsim::device::tensormap_sha_equal(
                slot.descriptor_sha256, descriptor_sha256)) {
            continue;
        }
        *base_address = slot.base_address;
        return index - 1;
    }
    (void)system_fetch_add(
        const_cast<std::uint64_t*>(&header->tma_stale_generations), 1);
    return UINT32_MAX;
}

extern "C" __device__ std::uint64_t __hbfsim_tma_issue(
    std::uint64_t descriptor_address, std::uint32_t instruction_id,
    std::uint32_t direction, std::uint64_t barrier,
    std::uint32_t multicast_mask)
{
    (void)barrier;
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    if (control_address == 0 || descriptor_address == 0 || direction > 1) {
        return 0;
    }
    auto* header = reinterpret_cast<SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (!valid_control(header, expected_generation)) return 0;
    const auto count = system_acquire(&header->tensormap_count);
    if (count > hbfsim::device::kTensorMapCapacity) return 0;
    const auto* descriptor = reinterpret_cast<const std::byte*>(
        static_cast<std::uintptr_t>(descriptor_address));
    const auto* slots =
        reinterpret_cast<const hbfsim::device::SharedTensorMapSlot*>(
            reinterpret_cast<const std::byte*>(header) +
            header->tensormap_offset);
    const hbfsim::device::SharedTensorMapSlot* selected = nullptr;
    for (std::uint32_t index = count; index != 0 && selected == nullptr;
         --index) {
        const auto& slot = slots[index - 1];
        if (system_acquire(&slot.publication_generation) == 0) continue;
        bool equal = true;
        for (std::uint32_t byte = 0; byte < 128; ++byte) {
            if (slot.descriptor[byte] != descriptor[byte]) {
                equal = false;
                break;
            }
        }
        if (equal) selected = &slot;
    }
    if (selected == nullptr || selected->rank == 0 || selected->rank > 5) {
        (void)system_fetch_add(&header->tma_stale_generations, 1);
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    const std::uint32_t element_sizes[13]{
        1, 2, 4, 4, 8, 8, 2, 4, 8, 2, 4, 4, 4};
    if (selected->element_type >= 13) {
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    std::uint64_t tile_bytes = element_sizes[selected->element_type];
    for (std::uint32_t dimension = 0; dimension < selected->rank;
         ++dimension) {
        const auto extent = selected->box_dim[dimension] == 0
                                ? 1U
                                : selected->box_dim[dimension];
        if (tile_bytes > UINT32_MAX / extent) {
            (void)system_fetch_add(&header->tma_faults, 1);
            return 0;
        }
        tile_bytes *= extent;
    }
    (void)system_fetch_add(&header->tma_issued, 1);
    (void)system_fetch_add(&header->tma_fanout_targets,
                           multicast_mask == 0 ? 1 : __popc(multicast_mask));
    if (selected->base_address > UINT64_MAX - tile_bytes) {
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    const auto tile_end = selected->base_address + tile_bytes;
    const auto range_count = system_acquire(&header->range_count);
    const auto* ranges = reinterpret_cast<const SharedRangeRecord*>(
        reinterpret_cast<const std::byte*>(header) + header->range_offset);
    const auto* range = range_count <= hbfsim::device::kRangeCapacity
                            ? find_range(ranges, range_count,
                                         selected->base_address)
                            : nullptr;
    if (range == nullptr) {
        for (std::uint32_t index = 0;
             index < range_count && index < hbfsim::device::kRangeCapacity;
             ++index) {
            const auto& candidate = ranges[index];
            if (candidate.length == 0 ||
                candidate.base > UINT64_MAX - candidate.length) {
                (void)system_fetch_add(&header->tma_faults, 1);
                return 0;
            }
            const auto candidate_end = candidate.base + candidate.length;
            if (selected->base_address < candidate_end &&
                tile_end > candidate.base) {
                // The descriptor begins in native HBM but its tile crosses an
                // HBF range.  Native TMA cannot be partially redirected.
                (void)system_fetch_add(&header->tma_faults, 1);
                return 0;
            }
        }
        (void)system_fetch_add(&header->tma_hbm_bytes, tile_bytes);
        return 1;
    }
    const auto offset = selected->base_address - range->base;
    if (range->mode != 1 || offset > range->length ||
        tile_bytes > range->length - offset) {
        // Capacity descriptors and mixed tiles are never allowed to execute
        // natively until software materialization has proved every segment.
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    (void)system_fetch_add(&header->tma_hbf_bytes, tile_bytes);
    const auto arrival = gpu_time_ns();
    const auto latency = direction == 0 ? header->read_latency_ns
                                        : header->program_latency_ns;
    const auto transfer = hbfsim::device::fast_transfer_ns(
        static_cast<std::uint32_t>(tile_bytes),
        header->aggregate_bandwidth_bytes_per_s);
    if (latency == 0 || transfer == 0 || header->time_scale == 0) {
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    const auto scaled_latency = hbfsim::device::saturating_multiply(
        latency, header->time_scale);
    const auto scaled_transfer = hbfsim::device::saturating_multiply(
        transfer, header->time_scale);
    std::uint64_t ready = 0;
    if (system_acquire(
            &header->sm120_channel_profile_generation) != 0) {
        ready = hbfsim::device::fast_future_ready_ns(
            arrival, arrival, scaled_latency, scaled_transfer);
    } else {
        auto tail = system_acquire(&header->fast_channel_tail_ns);
        for (;;) {
            ready = hbfsim::device::fast_future_ready_ns(
                arrival, tail, scaled_latency, scaled_transfer);
            auto expected = tail;
            const auto next_tail = hbfsim::device::saturating_add(
                tail > arrival ? tail : arrival, scaled_transfer);
            if (system_compare_exchange(&header->fast_channel_tail_ns,
                                        expected, next_tail)) {
                break;
            }
            tail = expected;
        }
    }
    const auto operation_class = direction != 0 ? 3U
        : multicast_mask == 0 ? 2U
        : __popc(multicast_mask) > 1 ? 5U : 4U;
    const auto channel = reserve_sm120_channels(
        header, operation_class, static_cast<std::uint32_t>(tile_bytes),
        arrival, ready, ready, 0, 0);
    if (!channel.valid || channel.saturated) {
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    ready = channel.ready_ns;
    return ready < 2 ? 2 : ready;
}

extern "C" __device__ std::uint32_t __hbfsim_tma_barrier_poll(
    std::uint64_t token)
{
    return token == 1 || (token >= 2 && gpu_time_ns() >= token) ? 1U : 0U;
}

extern "C" __device__ void __hbfsim_tma_barrier_wait(std::uint64_t token)
{
    std::uint32_t delay = 64;
    while (__hbfsim_tma_barrier_poll(token) == 0) bounded_sleep(delay);
}

extern "C" __device__ void __hbfsim_tma_commit_group()
{
}

extern "C" __device__ void __hbfsim_tma_wait_group(
    std::uint64_t token, std::uint32_t read_only)
{
    (void)read_only;
    __hbfsim_tma_barrier_wait(token);
}

extern "C" __device__ hbfsim::device::DeviceFuture
__hbfsim_future_wait(hbfsim::device::DeviceFuture future,
                     std::uint32_t wait_kind)
{
    if (future.state == DeviceFutureState::Native ||
        future.state == DeviceFutureState::Ready ||
        future.state == DeviceFutureState::DeferredMaterialization ||
        future.state == DeviceFutureState::Consumed) {
        return future;
    }
    const bool counted_issue = future.state == DeviceFutureState::Issued;
    const auto wait_begin = gpu_time_ns();
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    auto* header = reinterpret_cast<SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    const bool binding_valid =
        control_address != 0 && valid_control(header, expected_generation);
    if (binding_valid && counted_issue &&
        (future.flags & hbfsim::device::DeviceFutureTiming) != 0) {
        const auto issue_smid = future.channel >> 8;
        std::uint32_t wait_smid = 0;
        asm volatile("mov.u32 %0, %%smid;" : "=r"(wait_smid));
        const auto state_count =
            system_acquire(&header->sm120_channel_state_count);
        if (issue_smid < state_count && wait_smid != issue_smid) {
            auto* base = reinterpret_cast<std::byte*>(header);
            auto* states = reinterpret_cast<
                hbfsim::device::SharedSm120ChannelState*>(
                base + header->sm120_channel_state_offset);
            system_release(&states[issue_smid].migration_visible_sm_mismatch,
                           1U);
        }
    }
    if (future.state == DeviceFutureState::TerminalError ||
        (future.flags & hbfsim::device::DeviceFutureTiming) != 0) {
        if (future.state != DeviceFutureState::TerminalError &&
            !binding_valid) {
            future.ready_ns = wait_begin;
            future.state = DeviceFutureState::TerminalError;
            future.status = RequestStatus::Unsupported;
        }
        std::uint32_t sleep_ns = 64;
        while (gpu_time_ns() < future.ready_ns) {
            if (binding_valid &&
                (system_acquire(&header->shutdown) != 0 ||
                 system_acquire(&header->fault) != 0)) {
                future.state = DeviceFutureState::TerminalError;
                future.status = RequestStatus::DaemonLost;
                break;
            }
            bounded_sleep(sleep_ns);
        }
        if (future.state != DeviceFutureState::TerminalError) {
            future.state = DeviceFutureState::Ready;
            future.status = RequestStatus::Ready;
        }
    } else {
        if (!binding_valid) {
            future.state = DeviceFutureState::TerminalError;
            future.status = RequestStatus::Unsupported;
        } else {
            WaitState wait{.deadline_ns = hbfsim::device::saturating_add(
                               future.ready_ns,
                               header->request_timeout_ns),
                           .heartbeat_value =
                               system_acquire(&header->heartbeat_ns),
                           .heartbeat_observed_ns = gpu_time_ns()};
            auto* base = reinterpret_cast<std::byte*>(header);
            auto* completions = reinterpret_cast<SharedCompletionSlot*>(
                base + header->completion_offset);
            const auto completion = wait_for_completion(
                header, completions, future.ticket, wait, future.ready_ns);
            if (completion.status != RequestStatus::Ready) {
                future.state = DeviceFutureState::TerminalError;
                future.status = completion.status;
            } else {
                const auto count = system_acquire(&header->range_count);
                const auto* ranges =
                    reinterpret_cast<const SharedRangeRecord*>(
                        base + header->range_offset);
                const auto* range = count <= hbfsim::device::kRangeCapacity
                                        ? find_range(ranges, count,
                                                     future.original_address)
                                        : nullptr;
                const auto translated =
                    range == nullptr
                        ? 0
                        : hbfsim::device::resolved_address(
                              *range, future.original_address,
                              completion.frame_address);
                if (translated == 0) {
                    future.state = DeviceFutureState::TerminalError;
                    future.status = RequestStatus::CopyError;
                } else {
                    future.resolved_address = translated;
                    future.state =
                        (future.flags &
                         hbfsim::device::DeviceFutureCapacity) != 0
                            ? DeviceFutureState::DeferredMaterialization
                            : DeviceFutureState::Ready;
                    future.status = RequestStatus::Ready;
                }
            }
        }
    }
    future.ready_ns = gpu_time_ns();
    if (binding_valid) {
        auto* counter = wait_kind == 0
                            ? &header->future_dependency_wait_ns
                            : &header->future_ordering_wait_ns;
        (void)system_fetch_add(counter, future.ready_ns - wait_begin);
        if (counted_issue) {
            (void)system_fetch_add(&header->future_drained, 1);
        }
    }
    return future;
}

extern "C" __device__ void __hbfsim_future_fault(
    std::uint32_t, std::uint32_t)
{
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    auto* header = reinterpret_cast<SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (control_address != 0 &&
        valid_control(header, expected_generation)) {
        (void)system_fetch_add(&header->future_faults, 1);
    }
    asm volatile("trap;");
}

extern "C" __device__ hbfsim::device::ResolveResult
__hbfsim_resolve(std::uint64_t address, std::uint32_t bytes,
                 std::uint32_t operation)
{
    auto future = __hbfsim_future_issue(address, bytes, operation, 0);
    future = __hbfsim_future_wait(future, 0);
    return {.address = future.resolved_address == 0
                           ? future.original_address
                           : future.resolved_address,
            .status = static_cast<std::uint32_t>(future.status),
            .reserved = 0};
}

extern "C" __device__ void __hbfsim_fault(std::uint32_t status)
{
    __hbfsim_future_fault(status, 0);
}

#include "hbf_device.cuh"

#include <cuda/atomic>
#include <cuda_bf16.h>
#include <cuda_fp16.h>
#include <cuda_runtime.h>

extern "C" __device__ unsigned long long __hbfsim_control = 0;
extern "C" __device__ unsigned long long __hbfsim_control_generation = 0;
extern "C" __device__ __constant__ unsigned int
    __hbfsim_device_helper_marker = 0x48424632U;

extern "C" __device__ hbfsim::device::DeviceFuture
__hbfsim_future_wait(hbfsim::device::DeviceFuture future,
                     std::uint32_t wait_kind);

namespace {

using hbfsim::device::RequestStatus;
using hbfsim::device::DeviceFuture;
using hbfsim::device::DeviceFutureState;
using hbfsim::device::ResolveResult;
using hbfsim::device::SharedControlHeader;
using hbfsim::device::SharedCompletionSlot;
using hbfsim::device::SharedRangeRecord;
using hbfsim::device::SharedRequestSlot;

inline constexpr std::uint32_t kTmaDeferredCapacity = 64;
inline constexpr std::uint32_t kTmaDeferredPageCapacity = 32;
inline constexpr std::uint32_t kTmaMaterializationBytes = 32 * 1024;

struct alignas(64) TmaDeferredState {
    std::uint32_t status;
    std::uint32_t generation;
    std::uint32_t tensormap_index;
    std::uint32_t future_count;
    std::uint32_t direction;
    std::uint32_t access_mode;
    std::uint32_t reduction_operation;
    std::uint32_t shared_scope;
    std::uint32_t cta_group;
    std::uint32_t force_software;
    std::uint32_t instruction_id;
    std::uint32_t data_target_mask;
    std::uint32_t barrier_target_mask;
    std::uint32_t materialized_mask;
    std::uint32_t copying_mask;
    std::uint32_t barrier_completed_mask;
    std::uint32_t software_mbarrier;
    std::uint32_t completion_bytes;
    std::uint32_t issuer_rank;
    std::uint32_t cluster_id[3];
    std::int32_t coordinates[5];
    std::int32_t im2col_offsets[3];
    std::uint64_t shared_address;
    std::uint64_t barrier;
    std::uint64_t ready_ns;
    DeviceFuture futures[kTmaDeferredPageCapacity];
    std::uint64_t page_bases[kTmaDeferredPageCapacity];
    std::byte store_snapshot[kTmaMaterializationBytes];
};

alignas(64) __device__ TmaDeferredState
    hbfsim_tma_deferred_states[kTmaDeferredCapacity];
__device__ std::uint32_t hbfsim_tma_deferred_generation = 0;
__device__ std::uint32_t hbfsim_tma_barrier_completion_lock = 0;

__device__ bool try_acquire_tma_barrier_completion_lock()
{
    std::uint32_t previous = 0;
    const auto address = reinterpret_cast<std::uint64_t>(
        &hbfsim_tma_barrier_completion_lock);
    asm volatile("atom.global.cas.b32 %0, [%1], 0, 1;"
                 : "=r"(previous) : "l"(address) : "memory");
    if (previous != 0) return false;
    __threadfence();
    return true;
}

__device__ void release_tma_barrier_completion_lock()
{
    __threadfence();
    std::uint32_t previous = 0;
    const auto address = reinterpret_cast<std::uint64_t>(
        &hbfsim_tma_barrier_completion_lock);
    asm volatile("atom.global.exch.b32 %0, [%1], 0;"
                 : "=r"(previous) : "l"(address) : "memory");
    if (previous != 1U) asm volatile("trap;");
}

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

__device__ hbfsim::device::DeviceThermalSnapshot read_thermal_snapshot(
    const SharedControlHeader* header)
{
    for (std::uint32_t attempt = 0; attempt < 16; ++attempt) {
        const auto before = system_acquire(&header->thermal_generation);
        if (before == 0) return {};
        if ((before & 1U) != 0) continue;
        hbfsim::device::DeviceThermalSnapshot snapshot{
            .generation = before,
            .mode = static_cast<hbfsim::device::ThermalMode>(
                header->thermal_mode),
            .service_ppm = header->thermal_service_ppm,
            .admission_open = header->thermal_admission_open != 0,
            .valid = true,
        };
        const auto after = system_acquire(&header->thermal_generation);
        if (before != after || (after & 1U) != 0) continue;
        if (static_cast<std::uint32_t>(snapshot.mode) > 3 ||
            snapshot.service_ppm > 1'000'000 ||
            ((snapshot.mode == hbfsim::device::ThermalMode::Normal ||
              snapshot.mode == hbfsim::device::ThermalMode::Light) !=
             snapshot.admission_open) ||
            ((snapshot.mode == hbfsim::device::ThermalMode::Normal ||
              snapshot.mode == hbfsim::device::ThermalMode::Light) &&
             snapshot.service_ppm == 0)) {
            snapshot.valid = false;
        }
        return snapshot;
    }
    return {.valid = false};
}

__device__ RequestStatus await_thermal_admission(
    SharedControlHeader* header, WaitState& wait,
    hbfsim::device::DeviceThermalSnapshot& accepted)
{
    for (;;) {
        const auto snapshot = read_thermal_snapshot(header);
        if (!snapshot.valid) return RequestStatus::IoError;
        const auto admission = hbfsim::device::thermal_admission(
            snapshot.mode, false);
        if (admission == hbfsim::device::ThermalAdmission::Admit) {
            accepted = snapshot;
            return RequestStatus::Ready;
        }
        if (admission == hbfsim::device::ThermalAdmission::Shutdown) {
            return RequestStatus::ThermalShutdown;
        }
        const auto liveness = poll_liveness(header, wait);
        if (liveness != RequestStatus::Pending) return liveness;
    }
}

__device__ std::uint64_t claim_refresh_service(
    SharedControlHeader* header, std::uint32_t service_ppm)
{
    const auto quantum = header->thermal_refresh_quantum_bytes;
    if (quantum == 0) return 0;
    auto debt = system_acquire(&header->refresh_debt_bytes);
    std::uint64_t claimed = 0;
    for (;;) {
        const auto update = hbfsim::device::claim_refresh_debt(debt, quantum);
        if (update.claimed == 0) return 0;
        auto expected = debt;
        if (system_compare_exchange(&header->refresh_debt_bytes, expected,
                                    update.remaining)) {
            claimed = update.claimed;
            break;
        }
        debt = expected;
    }
    (void)system_fetch_add(&header->thermal_refresh_claimed_bytes, claimed);
    const auto bytes = claimed > UINT32_MAX
                           ? UINT32_MAX
                           : static_cast<std::uint32_t>(claimed);
    const auto base = header->program_latency_ns > header->read_latency_ns
                          ? header->program_latency_ns
                          : header->read_latency_ns;
    const auto service = hbfsim::device::fast_service_ns(
        base, bytes, header->aggregate_bandwidth_bytes_per_s);
    return hbfsim::device::saturating_multiply(
        hbfsim::device::scale_thermal_service_ns(service, service_ppm),
        header->time_scale);
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
    std::uint64_t ticket, WaitState& wait, std::uint64_t arrival_ns,
    std::uint32_t channel_delay_ns = 0,
    std::uint32_t thermal_service_ppm = 1'000'000)
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
                                RequestStatus::ThermalShutdown)) {
        return {.status = RequestStatus::IoError};
    }
    const auto status = static_cast<RequestStatus>(completion.status);
    if (status != RequestStatus::Ready) {
        return {.status = status};
    }
    const auto effective_service_ppm = completion.reserved == 0
                                           ? thermal_service_ppm
                                           : static_cast<std::uint32_t>(
                                                 completion.reserved);
    const auto thermal_scaled = hbfsim::device::scale_thermal_service_ns(
        completion.modeled_ns, effective_service_ppm);
    const auto scaled = hbfsim::device::saturating_multiply(
        thermal_scaled, header->time_scale);
    const auto target = hbfsim::device::sm120_reference_ready_ns(
        arrival_ns, channel_delay_ns,
        hbfsim::device::saturating_add(arrival_ns, scaled));
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
    std::uint32_t operation, std::uint32_t thermal_service_ppm)
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
        .future_flags = thermal_service_ppm << 8,
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
    std::uint32_t operation, std::uint32_t thermal_service_ppm)
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
        return resolve_leader(header, range, media, operation,
                              thermal_service_ppm);
    }
    if (header->timing_model != kFast && header->timing_model != kHybrid) {
        return {.status = RequestStatus::Unsupported};
    }
    const auto refresh_service = claim_refresh_service(
        header, thermal_service_ppm);

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
        const auto thermal_service =
            hbfsim::device::scale_thermal_service_ns(
                request.service_ns, thermal_service_ppm);
        const auto scaled_service = hbfsim::device::saturating_add(
            refresh_service, hbfsim::device::saturating_multiply(
                                 thermal_service, header->time_scale));
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
    const auto base_scaled = hbfsim::device::saturating_add(
        refresh_service, hbfsim::device::saturating_multiply(
        hbfsim::device::scale_thermal_service_ns(base_latency,
                                                  thermal_service_ppm),
        header->time_scale));
    const auto transfer_scaled = hbfsim::device::saturating_multiply(
        hbfsim::device::scale_thermal_service_ns(transfer_ns,
                                                  thermal_service_ppm),
        header->time_scale);
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
    std::uint64_t native_ready_ns = 0,
    std::uint32_t thermal_service_ppm = 1'000'000)
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
              hbfsim::device::scale_thermal_service_ns(
                  config->gnic_service_ns_by_class[operation],
                  thermal_service_ppm),
              header->time_scale) : 0;
    const auto gpc_service = take_gpc
        ? hbfsim::device::saturating_multiply(
              hbfsim::device::scale_thermal_service_ns(
                  config->gpc_service_ns_by_class[operation],
                  thermal_service_ppm),
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

__device__ DeviceChannelReservation reserve_sm120_channels_warp(
    SharedControlHeader* header, std::uint32_t operation,
    std::uint32_t bytes, std::uint64_t arrival_ns,
    std::uint64_t base_ready_ns, std::uint64_t media_ready_ns = 0,
    std::uint64_t capacity_ready_ns = 0,
    std::uint64_t native_ready_ns = 0,
    std::uint32_t thermal_service_ppm = 1'000'000)
{
    // A transformed ordinary memory instruction creates one future per active
    // lane, but GNIC2TEX/GPCARB arbitrate the issuing warp instruction.  Let a
    // single active lane mutate the per-SM queues and broadcast the resulting
    // reservation without changing the per-lane future accounting.
    const auto active = __activemask();
    const auto leader = __ffs(active) - 1;
    std::uint32_t lane = 0;
    asm volatile("mov.u32 %0, %%laneid;" : "=r"(lane));
    DeviceChannelReservation reservation{};
    if (lane == static_cast<std::uint32_t>(leader)) {
        const auto aggregate_bytes = hbfsim::device::saturating_multiply(
            bytes, static_cast<std::uint64_t>(__popc(active)));
        reservation = reserve_sm120_channels(
            header, operation,
            aggregate_bytes > UINT32_MAX
                ? UINT32_MAX
                : static_cast<std::uint32_t>(aggregate_bytes),
            arrival_ns, base_ready_ns, media_ready_ns, capacity_ready_ns,
            native_ready_ns, thermal_service_ppm);
    }
    auto ready_low = static_cast<std::uint32_t>(reservation.ready_ns);
    auto ready_high = static_cast<std::uint32_t>(reservation.ready_ns >> 32);
    ready_low = __shfl_sync(active, ready_low, leader);
    ready_high = __shfl_sync(active, ready_high, leader);
    reservation.ready_ns = static_cast<std::uint64_t>(ready_low) |
                           (static_cast<std::uint64_t>(ready_high) << 32);
    reservation.packed_channels = __shfl_sync(
        active, reservation.packed_channels, leader);
    auto valid = __shfl_sync(active,
                             reservation.valid ? 1U : 0U, leader);
    auto saturated = __shfl_sync(active,
                                 reservation.saturated ? 1U : 0U, leader);
    reservation.valid = valid != 0;
    reservation.saturated = saturated != 0;
    return reservation;
}

struct WarpHybridDecision {
    std::uint64_t sequence_base{0};
    bool reference{false};
};

__device__ WarpHybridDecision warp_hybrid_reference_decision(
    SharedControlHeader* header, std::uint64_t sample_key)
{
    const auto active = __activemask();
    const auto leader = __ffs(active) - 1;
    const auto active_count = static_cast<std::uint64_t>(__popc(active));
    std::uint32_t lane = 0;
    asm volatile("mov.u32 %0, %%laneid;" : "=r"(lane));
    WarpHybridDecision decision{};
    if (lane == static_cast<std::uint32_t>(leader)) {
        decision.sequence_base = system_fetch_add(
            &header->fast_request_sequence, active_count);
        decision.reference = hbfsim::device::warp_hybrid_reference_sample(
            decision.sequence_base, header->reference_warmup_requests,
            header->reference_sample_threshold, sample_key);
    }
    auto sequence_low = static_cast<std::uint32_t>(decision.sequence_base);
    auto sequence_high =
        static_cast<std::uint32_t>(decision.sequence_base >> 32);
    sequence_low = __shfl_sync(active, sequence_low, leader);
    sequence_high = __shfl_sync(active, sequence_high, leader);
    decision.sequence_base = static_cast<std::uint64_t>(sequence_low) |
                             (static_cast<std::uint64_t>(sequence_high) << 32);
    decision.reference =
        __shfl_sync(active, decision.reference ? 1U : 0U, leader) != 0;
    return decision;
}

__device__ DeviceFuture issue_reference(
    SharedControlHeader* header, const SharedRangeRecord& range,
    const hbfsim::device::MediaDescriptor& media, std::uint64_t address,
    std::uint32_t bytes, std::uint32_t operation,
    std::uint32_t instruction_id, std::uint32_t thermal_service_ppm)
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
    const auto channel_operation = operation == 0 ? 0U
                                   : operation == 1 ? 1U : 6U;
    const auto channel = reserve_sm120_channels_warp(
        header, channel_operation, bytes, arrival, arrival, arrival,
        range.mode == 2 ? arrival : 0, 0, thermal_service_ppm);
    if (!channel.valid || channel.saturated ||
        !hbfsim::device::sm120_reference_channel_delay_valid(
            arrival, channel.ready_ns)) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }
    const auto channel_delay =
        hbfsim::device::sm120_reference_channel_delay(arrival,
                                                      channel.ready_ns);
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
        .flags = thermal_service_ppm << 8,
        .instruction_id = instruction_id,
        .future_flags = (thermal_service_ppm << 8) |
                        hbfsim::device::DeviceFutureReference |
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
            .channel = channel_delay,
            .flags = flags,
            .state = DeviceFutureState::Issued,
            .status = RequestStatus::Pending};
}

__device__ DeviceFuture issue_fast(
    SharedControlHeader* header, const SharedRangeRecord& range,
    const hbfsim::device::MediaDescriptor& media, std::uint64_t address,
    std::uint32_t bytes, std::uint32_t operation,
    std::uint32_t instruction_id, std::uint32_t thermal_service_ppm)
{
    constexpr std::uint32_t kFast = 1;
    constexpr std::uint32_t kHybrid = 2;
    const auto empirical_enabled = header->empirical_flags != 0;
    if (empirical_enabled &&
        !hbfsim::device::empirical_control_valid(*header)) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }
    const auto sample_key = media.logical_address ^
                            (static_cast<std::uint64_t>(range.range_id) << 32) ^
                            operation;
    const auto hybrid = header->timing_model == kHybrid
                            ? warp_hybrid_reference_decision(header, sample_key)
                            : WarpHybridDecision{};
    if (header->timing_model == kHybrid && hybrid.reference) {
        (void)system_fetch_add(&header->reference_requests, 1);
        return issue_reference(header, range, media, address, bytes,
                               operation, instruction_id,
                               thermal_service_ppm);
    }
    if (header->timing_model == kFast) {
        (void)system_fetch_add(&header->fast_request_sequence, 1);
    }
    if (header->timing_model != kFast &&
        header->timing_model != kHybrid) {
        return failed_future(address, bytes, instruction_id,
                             RequestStatus::Unsupported);
    }

    const auto refresh_service = claim_refresh_service(
        header, thermal_service_ppm);

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
        const auto scaled_service = hbfsim::device::saturating_add(
            refresh_service, hbfsim::device::saturating_multiply(
            hbfsim::device::scale_thermal_service_ns(
                modeled_service, thermal_service_ppm),
            header->time_scale));
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
        const auto latency_scaled = hbfsim::device::saturating_add(
            refresh_service, hbfsim::device::saturating_multiply(
            hbfsim::device::scale_thermal_service_ns(
                base_latency, thermal_service_ppm), header->time_scale));
        const auto transfer_scaled = hbfsim::device::saturating_multiply(
            hbfsim::device::scale_thermal_service_ns(
                transfer_ns, thermal_service_ppm), header->time_scale);
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
    const auto channel = reserve_sm120_channels_warp(
        header, channel_operation, bytes, arrival, target, target,
        range.mode == 2 ? target : 0, 0, thermal_service_ppm);
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
        const auto admission_begin = gpu_time_ns();
        WaitState thermal_wait{
            .deadline_ns = hbfsim::device::saturating_add(
                admission_begin, header->request_timeout_ns),
            .heartbeat_value = system_acquire(&header->heartbeat_ns),
            .heartbeat_observed_ns = admission_begin,
        };
        hbfsim::device::DeviceThermalSnapshot thermal{};
        const auto admitted = await_thermal_admission(
            mutable_header, thermal_wait, thermal);
        if (admitted != RequestStatus::Ready) {
            resolution = {.status = admitted};
        } else {
            if (operation == 0 || operation == 2) {
                (void)system_fetch_add(&mutable_header->thermal_read_bytes,
                                       media.bytes);
            }
            if (operation == 1 || operation == 2) {
                (void)system_fetch_add(&mutable_header->thermal_write_bytes,
                                       media.bytes);
            }
            resolution = range->mode == 1 && header->timing_model != 0
                             ? resolve_fast_or_hybrid(
                                   mutable_header, *range, media, operation,
                                   thermal.service_ppm)
                             : resolve_leader(
                                   mutable_header, *range, media, operation,
                                   thermal.service_ppm);
            if (resolution.status == RequestStatus::Ready) {
                (void)system_fetch_add(
                    &mutable_header->thermal_inflight_completed, 1);
            }
        }
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
    const auto admission_begin = gpu_time_ns();
    WaitState thermal_wait{
        .deadline_ns = hbfsim::device::saturating_add(
            admission_begin, header->request_timeout_ns),
        .heartbeat_value = system_acquire(&header->heartbeat_ns),
        .heartbeat_observed_ns = admission_begin,
    };
    hbfsim::device::DeviceThermalSnapshot thermal{};
    const auto admitted = await_thermal_admission(header, thermal_wait,
                                                   thermal);
    if (admitted != RequestStatus::Ready) {
        return failed_future(address, bytes, instruction_id, admitted);
    }
    if (operation == 0 || operation == 2) {
        (void)system_fetch_add(&header->thermal_read_bytes, media.bytes);
    }
    if (operation == 1 || operation == 2) {
        (void)system_fetch_add(&header->thermal_write_bytes, media.bytes);
    }
    return range->mode == 1 && header->timing_model != 0
               ? issue_fast(header, *range, media, address, bytes,
                            operation, instruction_id, thermal.service_ppm)
               : issue_reference(header, *range, media, address, bytes,
                                 operation, instruction_id,
                                 thermal.service_ppm);
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
    const auto service_ppm = completion.reserved == 0
                                 ? 1'000'000U
                                 : static_cast<std::uint32_t>(
                                       completion.reserved);
    const auto scaled = hbfsim::device::saturating_multiply(
        hbfsim::device::scale_thermal_service_ns(
            completion.modeled_ns, service_ppm),
        header->time_scale);
    const auto target = hbfsim::device::saturating_add(
        future.ready_ns, scaled);
    return now >= target ? 1U : 0U;
}

__device__ void current_cluster(std::uint32_t (&id)[3],
                                std::uint32_t& rank)
{
    asm volatile("mov.u32 %0, %%clusterid.x;" : "=r"(id[0]));
    asm volatile("mov.u32 %0, %%clusterid.y;" : "=r"(id[1]));
    asm volatile("mov.u32 %0, %%clusterid.z;" : "=r"(id[2]));
    asm volatile("mov.u32 %0, %%cluster_ctarank;" : "=r"(rank));
}

__device__ bool same_tma_domain(const TmaDeferredState& state,
                                const std::uint32_t (&cluster_id)[3])
{
    return state.cluster_id[0] == cluster_id[0] &&
           state.cluster_id[1] == cluster_id[1] &&
           state.cluster_id[2] == cluster_id[2];
}

__device__ std::uint64_t canonical_tma_shared_address(
    std::uint64_t address, std::uint32_t shared_scope)
{
    if (address == 0 || shared_scope == 0) return address;
    const auto local = static_cast<std::uint32_t>(address);
    std::uint32_t canonical = 0;
    asm volatile("mapa.shared::cluster.u32 %0, %1, 0;"
                 : "=r"(canonical) : "r"(local));
    return canonical;
}

__device__ std::uint64_t tma_state_barrier_address(
    const TmaDeferredState& state, std::uint64_t address)
{
    // Software TMA completion may be issued by every destination CTA, whose
    // local DSM addresses differ by the CTA-rank window.  Native timing
    // shadows, however, must retain the issuer's rank-local key: merging the
    // two native multicast waiters into one shadow state can retire it while
    // another CTA is still conjunctively polling its hardware mbarrier.
    return state.software_mbarrier != 0
               ? canonical_tma_shared_address(address, state.shared_scope)
               : address;
}

__device__ TmaDeferredState* allocate_tma_state(std::uint32_t& slot)
{
    for (std::uint32_t index = 0; index < kTmaDeferredCapacity; ++index) {
        if (atomicCAS(&hbfsim_tma_deferred_states[index].status, 0U, 1U) == 0U) {
            slot = index;
            return &hbfsim_tma_deferred_states[index];
        }
    }
    return nullptr;
}

__device__ bool resolve_tma_state(TmaDeferredState& state)
{
    auto status = atomicAdd(&state.status, 0U);
    if (status == 4U) return true;
    if (status == 5U) asm volatile("trap;");
    if (status != 2U || gpu_time_ns() < state.ready_ns) return false;
    for (std::uint32_t index = 0; index < state.future_count; ++index) {
        if (__hbfsim_future_poll(state.futures[index]) == 0) return false;
    }
    if (atomicCAS(&state.status, 2U, 3U) != 2U) {
        return atomicAdd(&state.status, 0U) == 4U;
    }
    bool valid = true;
    for (std::uint32_t index = 0; index < state.future_count; ++index) {
        auto future = __hbfsim_future_wait(state.futures[index], 0);
        if (future.state == DeviceFutureState::TerminalError ||
            future.status != RequestStatus::Ready ||
            future.resolved_address == 0) {
            valid = false;
            break;
        }
        state.futures[index] = future;
    }
    __threadfence();
    atomicExch(&state.status, valid ? 4U : 5U);
    if (!valid) asm volatile("trap;");
    return true;
}

__device__ std::uint64_t resolved_tma_byte(
    const TmaDeferredState& state, const SharedRangeRecord& range,
    std::uint64_t address)
{
    if (range.mode != 2 || range.page_bytes == 0 || address < range.base) {
        return address;
    }
    const auto page_base = range.base +
        ((address - range.base) / range.page_bytes) * range.page_bytes;
    for (std::uint32_t index = 0; index < state.future_count; ++index) {
        if (state.page_bases[index] != page_base) continue;
        const auto translated = state.futures[index].resolved_address;
        return translated == 0 || address - page_base > UINT64_MAX - translated
                   ? 0
                   : translated + (address - page_base);
    }
    return 0;
}

__device__ std::uint64_t tma_source_bits(const std::byte* source,
                                         std::uint32_t bytes)
{
    std::uint64_t value = 0;
    for (std::uint32_t byte = 0; byte < bytes; ++byte) {
        value |= static_cast<std::uint64_t>(
                     reinterpret_cast<const unsigned char*>(source)[byte])
                 << (byte * 8U);
    }
    return value;
}

__device__ bool atomic_reduce_tma_half(std::byte* destination,
                                      std::uint16_t source_bits,
                                      std::uint32_t element_type,
                                      std::uint32_t operation)
{
    const auto address = reinterpret_cast<std::uintptr_t>(destination);
    if ((address & 1U) != 0) return false;
    auto* word = reinterpret_cast<unsigned int*>(address & ~std::uintptr_t{3});
    const auto shift = static_cast<std::uint32_t>((address & 2U) * 8U);
    const auto mask = 0xffffU << shift;
    auto old = atomicAdd(word, 0U);
    for (;;) {
        const auto current_bits = static_cast<std::uint16_t>(old >> shift);
        float current = 0;
        float source = 0;
        if (element_type == 6) {
            current = __half2float(__ushort_as_half(current_bits));
            source = __half2float(__ushort_as_half(source_bits));
        } else {
            current = __bfloat162float(__ushort_as_bfloat16(current_bits));
            source = __bfloat162float(__ushort_as_bfloat16(source_bits));
        }
        const auto reduced = operation == 0 ? current + source
                           : operation == 6 ? (source < current ? source
                                                               : current)
                                            : (source > current ? source
                                                                : current);
        const auto reduced_bits = element_type == 6
            ? __half_as_ushort(__float2half_rn(reduced))
            : __bfloat16_as_ushort(__float2bfloat16_rn(reduced));
        const auto desired = (old & ~mask) |
                             (static_cast<unsigned int>(reduced_bits) << shift);
        const auto observed = atomicCAS(word, old, desired);
        if (observed == old) return true;
        old = observed;
    }
}

__device__ bool atomic_reduce_tma_element(
    std::byte* destination, const std::byte* source,
    std::uint32_t element_type, std::uint32_t operation)
{
    if (!hbfsim::device::tma_reduction_supported(element_type, operation)) {
        return false;
    }
    if (element_type == 6 || element_type == 10) {
        return atomic_reduce_tma_half(
            destination, static_cast<std::uint16_t>(tma_source_bits(source, 2)),
            element_type, operation);
    }
    if (element_type == 7) {
        if (operation != 0 ||
            (reinterpret_cast<std::uintptr_t>(destination) & 3U) != 0) {
            return false;
        }
        union Bits {
            std::uint32_t bits;
            float value;
        } source_value{static_cast<std::uint32_t>(tma_source_bits(source, 4))};
        atomicAdd(reinterpret_cast<float*>(destination), source_value.value);
        return true;
    }
    if (element_type == 2 || element_type == 3) {
        if ((reinterpret_cast<std::uintptr_t>(destination) & 3U) != 0) {
            return false;
        }
        auto* unsigned_destination =
            reinterpret_cast<unsigned int*>(destination);
        const auto value =
            static_cast<unsigned int>(tma_source_bits(source, 4));
        switch (operation) {
        case 0: atomicAdd(unsigned_destination, value); return true;
        case 1: atomicAnd(unsigned_destination, value); return true;
        case 2: atomicOr(unsigned_destination, value); return true;
        case 3: atomicXor(unsigned_destination, value); return true;
        case 4: atomicInc(unsigned_destination, value); return true;
        case 5: atomicDec(unsigned_destination, value); return true;
        case 6:
            if (element_type == 2) atomicMin(unsigned_destination, value);
            else atomicMin(reinterpret_cast<int*>(destination),
                           static_cast<int>(value));
            return true;
        case 7:
            if (element_type == 2) atomicMax(unsigned_destination, value);
            else atomicMax(reinterpret_cast<int*>(destination),
                           static_cast<int>(value));
            return true;
        default: return false;
        }
    }
    if ((element_type == 4 || element_type == 5) &&
        (reinterpret_cast<std::uintptr_t>(destination) & 7U) == 0) {
        auto* target = reinterpret_cast<unsigned long long*>(destination);
        const auto value = static_cast<unsigned long long>(
            tma_source_bits(source, 8));
        auto old = atomicAdd(target, 0ULL);
        for (;;) {
            auto desired = old;
            switch (operation) {
            case 0: desired = old + value; break;
            case 1: desired = old & value; break;
            case 2: desired = old | value; break;
            case 3: desired = old ^ value; break;
            case 6:
                if (element_type == 4) desired = value < old ? value : old;
                else desired = static_cast<long long>(value) <
                                       static_cast<long long>(old)
                                   ? value
                                   : old;
                break;
            case 7:
                if (element_type == 4) desired = value > old ? value : old;
                else desired = static_cast<long long>(value) >
                                       static_cast<long long>(old)
                                   ? value
                                   : old;
                break;
            default: return false;
            }
            const auto observed = atomicCAS(target, old, desired);
            if (observed == old) return true;
            old = observed;
        }
    }
    return false;
}

__device__ bool materialize_tma_target(
    TmaDeferredState& state, std::uint32_t target_rank,
    SharedControlHeader* header)
{
    if (target_rank >= 16) return false;
    const auto target_bit = 1U << target_rank;
    if ((state.data_target_mask & target_bit) == 0) return true;
    if ((atomicAdd(&state.materialized_mask, 0U) & target_bit) != 0) {
        return true;
    }
    const auto previous = atomicOr(&state.copying_mask, target_bit);
    if ((previous & target_bit) != 0) {
        return (atomicAdd(&state.materialized_mask, 0U) & target_bit) != 0;
    }
    if (state.direction == 2) {
        atomicOr(&state.materialized_mask, target_bit);
        return true;
    }
    auto* base = reinterpret_cast<std::byte*>(header);
    const auto range_count = system_acquire(&header->range_count);
    if (range_count > hbfsim::device::kRangeCapacity ||
        state.tensormap_index >= system_acquire(&header->tensormap_count)) {
        atomicExch(&state.status, 5U);
        return false;
    }
    const auto* ranges = reinterpret_cast<const SharedRangeRecord*>(
        base + header->range_offset);
    const auto* maps = reinterpret_cast<
        const hbfsim::device::SharedTensorMapSlot*>(
        base + header->tensormap_offset);
    const auto& map = maps[state.tensormap_index];
    const auto elements = hbfsim::device::tma_access_element_count(
        map, state.access_mode, state.im2col_offsets);
    if (elements == 0) {
        atomicExch(&state.status, 5U);
        return false;
    }
    std::uint32_t target_shared_address =
        static_cast<std::uint32_t>(state.shared_address);
    if (state.shared_scope == 1) {
        const auto source_shared_address =
            static_cast<std::uint32_t>(state.shared_address);
        asm volatile("mapa.shared::cluster.u32 %0, %1, %2;"
                     : "=r"(target_shared_address)
                     : "r"(source_shared_address), "r"(target_rank));
    }
    for (std::uint64_t linear = 0; linear < elements; ++linear) {
        const auto element = hbfsim::device::tma_element_address(
            map, state.coordinates, state.access_mode, linear,
            state.im2col_offsets, state.shared_address);
        const auto destination_offset = element.destination_offset;
        if (!element.valid ||
            destination_offset >
                kTmaMaterializationBytes - element.shared_bytes ||
            target_shared_address > UINT32_MAX - destination_offset) {
            atomicExch(&state.status, 5U);
            return false;
        }
        auto* shared = reinterpret_cast<std::byte*>(
            __cvta_shared_to_generic(static_cast<std::size_t>(
                target_shared_address + destination_offset)));
        if (element.oob) {
            if (state.direction == 0) {
                for (std::uint32_t byte = 0; byte < element.bytes; ++byte) {
                    const auto fill = map.oob_fill == 0
                                          ? 0U
                                          : hbfsim::device::
                                                tma_oob_nan_fill_byte(
                                                    map.element_type, byte);
                    if (fill == UINT32_MAX) {
                        atomicExch(&state.status, 5U);
                        return false;
                    }
                    shared[byte] = static_cast<std::byte>(fill);
                }
            }
            continue;
        }
        const auto global_address = element.global_address;
        std::uint64_t effective_addresses[16]{};
        for (std::uint32_t byte = 0; byte < element.bytes; ++byte) {
            const auto address = global_address + byte;
            const auto* range = find_range(ranges, range_count, address);
            auto effective = address;
            if (range != nullptr && range->mode == 2) {
                effective = resolved_tma_byte(state, *range, address);
            }
            if (effective == 0) {
                atomicExch(&state.status, 5U);
                return false;
            }
            effective_addresses[byte] = effective;
            auto* global = reinterpret_cast<std::byte*>(
                static_cast<std::uintptr_t>(effective));
            if (state.direction == 0) {
                shared[byte] = *global;
            }
        }
        if (state.direction == 0 &&
            (map.element_type == 11 || map.element_type == 12)) {
            if (element.bytes != 4) {
                atomicExch(&state.status, 5U);
                return false;
            }
            std::uint32_t raw = 0;
            for (std::uint32_t byte = 0; byte < 4; ++byte) {
                raw |= static_cast<std::uint32_t>(
                           static_cast<unsigned char>(shared[byte]))
                       << (byte * 8U);
            }
            const auto converted = hbfsim::device::tma_tf32_load_bits(raw);
            for (std::uint32_t byte = 0; byte < 4; ++byte) {
                shared[byte] = static_cast<std::byte>(
                    (converted >> (byte * 8U)) & 0xffU);
            }
        }
        if (state.direction == 1 &&
            state.reduction_operation == UINT32_MAX) {
            std::byte packed_b6[12]{};
            const std::byte* source =
                &state.store_snapshot[element.destination_offset];
            if (map.element_type == 15 &&
                !hbfsim::device::tma_pack_b6p2x16(source, packed_b6)) {
                atomicExch(&state.status, 5U);
                return false;
            }
            for (std::uint32_t byte = 0; byte < element.bytes; ++byte) {
                auto* global = reinterpret_cast<std::byte*>(
                    static_cast<std::uintptr_t>(effective_addresses[byte]));
                *global = map.element_type == 15 ? packed_b6[byte]
                                                  : source[byte];
            }
        }
        if (state.direction != 0 &&
            state.reduction_operation != UINT32_MAX &&
            state.direction == 1) {
            for (std::uint32_t byte = 1; byte < element.bytes; ++byte) {
                if (effective_addresses[byte] !=
                    effective_addresses[0] + byte) {
                    atomicExch(&state.status, 5U);
                    return false;
                }
            }
            if (!atomic_reduce_tma_element(
                    reinterpret_cast<std::byte*>(static_cast<std::uintptr_t>(
                        effective_addresses[0])),
                    &state.store_snapshot[element.destination_offset],
                    map.element_type, state.reduction_operation)) {
                atomicExch(&state.status, 5U);
                return false;
            }
        }
    }
    __threadfence_block();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    __threadfence();
    atomicOr(&state.materialized_mask, target_bit);
    return true;
}

__device__ std::uint32_t associated_tma_data_mask(
    const TmaDeferredState& state, std::uint32_t barrier_rank)
{
    std::uint32_t associated_data = 0;
    for (std::uint32_t data_rank = 0; data_rank < 16; ++data_rank) {
        const auto data_bit = 1U << data_rank;
        if ((state.data_target_mask & data_bit) == 0) continue;
        const auto target = state.cta_group == 2
                                ? ((data_rank & ~1U) |
                                   (barrier_rank & 1U))
                                : data_rank;
        if (target == barrier_rank) associated_data |= data_bit;
    }
    return associated_data;
}

__device__ bool tma_state_retirable(const TmaDeferredState& state)
{
    const auto materialized = atomicAdd(
        const_cast<std::uint32_t*>(&state.materialized_mask), 0U);
    if ((materialized & state.data_target_mask) != state.data_target_mask) {
        return false;
    }
    if (state.software_mbarrier == 0) return true;
    const auto barriers = atomicAdd(
        const_cast<std::uint32_t*>(&state.barrier_completed_mask), 0U);
    return (barriers & state.barrier_target_mask) ==
           state.barrier_target_mask;
}

__device__ void retire_tma_state(TmaDeferredState& state,
                                 SharedControlHeader* header)
{
    if (!tma_state_retirable(state)) return;
    __threadfence();
    if (atomicExch(&state.status, 0U) != 0U) {
        system_fetch_sub_release(&header->tma_leaked, 1);
    }
}

__device__ bool poll_tma_state(TmaDeferredState& state,
                               std::uint32_t barrier_rank,
                               SharedControlHeader* header,
                               bool materialize)
{
    if (!resolve_tma_state(state)) return false;
    const auto barrier_bit =
        barrier_rank < 16 ? 1U << barrier_rank : 0U;
    if (barrier_bit == 0 ||
        (state.barrier_target_mask & barrier_bit) == 0) return true;
    const auto associated_data =
        associated_tma_data_mask(state, barrier_rank);
    if (associated_data == 0) return true;
    for (std::uint32_t data_rank = 0; data_rank < 16; ++data_rank) {
        const auto data_bit = 1U << data_rank;
        if ((associated_data & data_bit) == 0) continue;
        if (materialize &&
            !materialize_tma_target(state, data_rank, header)) {
            return false;
        }
    }
    // A non-materializing state tracks only modeled timing for one native TMA
    // transaction.  Native multicast completes all destinations as one
    // transaction, so the first destination that observes both native and
    // modeled completion may retire the entire shadow fanout.  Requiring every
    // CTA to win a final shadow-poll race leaks the state when another CTA's
    // native wait is the last one to finish.
    const auto completed = materialize
        ? atomicAdd(&state.materialized_mask, 0U)
        : atomicOr(&state.materialized_mask, state.data_target_mask) |
              state.data_target_mask;
    if ((completed & associated_data) != associated_data) return false;
    retire_tma_state(state, header);
    return true;
}

__device__ bool complete_software_tma_barrier(
    SharedControlHeader* header, std::uint64_t barrier,
    const std::uint32_t (&cluster_id)[3], std::uint32_t rank)
{
    if (rank >= 16 || !try_acquire_tma_barrier_completion_lock()) {
        return false;
    }
    const auto barrier_bit = 1U << rank;
    std::uint64_t completion_bytes = 0;
    bool found = false;
    bool ready = true;
    for (std::uint32_t index = 0; index < kTmaDeferredCapacity; ++index) {
        auto& state = hbfsim_tma_deferred_states[index];
        const auto status = atomicAdd(&state.status, 0U);
        if (status == 0 || state.software_mbarrier == 0 ||
            state.barrier != tma_state_barrier_address(state, barrier) ||
            !same_tma_domain(state, cluster_id) ||
            (state.barrier_target_mask & barrier_bit) == 0 ||
            (atomicAdd(&state.barrier_completed_mask, 0U) & barrier_bit) != 0) {
            continue;
        }
        found = true;
        const auto associated = associated_tma_data_mask(state, rank);
        const auto materialized = atomicAdd(&state.materialized_mask, 0U);
        if (status != 4U || associated == 0 ||
            (materialized & associated) != associated) {
            ready = false;
            break;
        }
        completion_bytes += state.completion_bytes;
        if (completion_bytes > UINT32_MAX) {
            ready = false;
            (void)system_fetch_add(&header->tma_faults, 1);
            break;
        }
    }
    if (!found) {
        release_tma_barrier_completion_lock();
        return true;
    }
    if (!ready || completion_bytes == 0) {
        release_tma_barrier_completion_lock();
        return false;
    }
    const auto bytes = static_cast<std::uint32_t>(completion_bytes);
    __threadfence_block();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    asm volatile(
        "mbarrier.complete_tx.relaxed.cta.shared::cta.b64 [%0], %1;"
        : : "l"(barrier), "r"(bytes) : "memory");
    for (std::uint32_t index = 0; index < kTmaDeferredCapacity; ++index) {
        auto& state = hbfsim_tma_deferred_states[index];
        const auto status = atomicAdd(&state.status, 0U);
        if (status == 0 || state.software_mbarrier == 0 ||
            state.barrier != tma_state_barrier_address(state, barrier) ||
            !same_tma_domain(state, cluster_id) ||
            (state.barrier_target_mask & barrier_bit) == 0) {
            continue;
        }
        atomicOr(&state.barrier_completed_mask, barrier_bit);
        retire_tma_state(state, header);
    }
    release_tma_barrier_completion_lock();
    return true;
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

extern "C" __device__ std::uint64_t
__hbfsim_tensormap_replace_begin(std::uint64_t descriptor_address)
{
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    if (control_address == 0 || descriptor_address == 0) return 0;
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
    for (std::uint32_t index = count; index != 0; --index) {
        const auto& slot = slots[index - 1];
        if (system_acquire(&slot.publication_generation) == 0 ||
            system_acquire(&slot.fenced) == 0 ||
            slot.descriptor_generation == 0 ||
            slot.descriptor_generation > (UINT64_MAX >> 9)) {
            continue;
        }
        bool equal = true;
        for (std::uint32_t byte = 0; byte < 128; ++byte) {
            if (slot.descriptor[byte] != descriptor[byte]) {
                equal = false;
                break;
            }
        }
        if (equal) {
            return (slot.descriptor_generation << 9) |
                   static_cast<std::uint64_t>(index);
        }
    }
    (void)system_fetch_add(&header->tma_stale_generations, 1);
    (void)system_fetch_add(&header->tma_faults, 1);
    return 0;
}

extern "C" __device__ std::uint64_t
__hbfsim_tensormap_copy_begin(std::uint64_t descriptor_address)
{
    return __hbfsim_tensormap_replace_begin(descriptor_address);
}

extern "C" __device__ std::uint32_t
__hbfsim_tensormap_replace_commit(std::uint64_t token,
                                  std::uint64_t descriptor_address,
                                  std::uint32_t field,
                                  std::uint32_t ordinal,
                                  std::uint64_t value)
{
    const auto encoded_index = static_cast<std::uint32_t>(token & 0x1ffU);
    const auto descriptor_generation = token >> 9;
    if (encoded_index == 0 ||
        encoded_index > hbfsim::device::kTensorMapCapacity ||
        descriptor_generation == 0 || descriptor_address == 0 ||
        field > static_cast<std::uint32_t>(
                    hbfsim::device::TensorMapReplaceField::FillMode)) {
        return 0;
    }
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    if (control_address == 0) return 0;
    auto* header = reinterpret_cast<SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (!valid_control(header, expected_generation)) return 0;
    auto* slots = reinterpret_cast<hbfsim::device::SharedTensorMapSlot*>(
        reinterpret_cast<std::byte*>(header) + header->tensormap_offset);
    const auto source_index = encoded_index - 1;
    const auto& source = slots[source_index];
    if (system_acquire(&source.publication_generation) == 0 ||
        source.descriptor_generation != descriptor_generation ||
        descriptor_generation == UINT64_MAX) {
        (void)system_fetch_add(&header->tma_stale_generations, 1);
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    hbfsim::device::SharedTensorMapSlot candidate = source;
    if (!hbfsim::device::apply_tensormap_replace(
            candidate,
            static_cast<hbfsim::device::TensorMapReplaceField>(field),
            ordinal, value)) {
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    auto count = system_acquire(&header->tensormap_count);
    for (;;) {
        if (count >= hbfsim::device::kTensorMapCapacity) {
            (void)system_fetch_add(&header->tma_faults, 1);
            return 0;
        }
        auto expected = count;
        if (system_compare_exchange(&header->tensormap_count, expected,
                                    count + 1)) {
            break;
        }
        count = expected;
    }
    auto& destination = slots[count];
    system_release(&destination.publication_generation, std::uint64_t{0});
    auto* destination_bytes = reinterpret_cast<std::byte*>(&destination);
    const auto* source_bytes = reinterpret_cast<const std::byte*>(&candidate);
    for (std::uint32_t byte = sizeof(destination.publication_generation);
         byte < sizeof(destination); ++byte) {
        destination_bytes[byte] = source_bytes[byte];
    }
    const auto* descriptor = reinterpret_cast<const std::byte*>(
        static_cast<std::uintptr_t>(descriptor_address));
    for (std::uint32_t byte = 0; byte < 128; ++byte) {
        destination.descriptor[byte] = descriptor[byte];
    }
    hbfsim::device::tensormap_sha256_bytes(
        destination.descriptor, destination.descriptor_sha256);
    destination.descriptor_generation = descriptor_generation + 1;
    destination.fenced = 0;
    const auto publication =
        system_fetch_add(&header->tensormap_publication_generation, 1) + 1;
    if (publication == 0) {
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    __threadfence_system();
    system_release(&destination.publication_generation, publication);
    return 1;
}

extern "C" __device__ std::uint32_t
__hbfsim_tensormap_copy_commit(std::uint64_t token,
                               std::uint64_t descriptor_address)
{
    const auto encoded_index = static_cast<std::uint32_t>(token & 0x1ffU);
    const auto descriptor_generation = token >> 9;
    if (encoded_index == 0 ||
        encoded_index > hbfsim::device::kTensorMapCapacity ||
        descriptor_generation == 0 || descriptor_address == 0) {
        return 0;
    }
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    if (control_address == 0) return 0;
    auto* header = reinterpret_cast<SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (!valid_control(header, expected_generation)) return 0;
    auto* slots = reinterpret_cast<hbfsim::device::SharedTensorMapSlot*>(
        reinterpret_cast<std::byte*>(header) + header->tensormap_offset);
    const auto& source = slots[encoded_index - 1];
    if (system_acquire(&source.publication_generation) == 0 ||
        source.descriptor_generation != descriptor_generation) {
        (void)system_fetch_add(&header->tma_stale_generations, 1);
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    const auto* descriptor = reinterpret_cast<const std::byte*>(
        static_cast<std::uintptr_t>(descriptor_address));
    for (std::uint32_t byte = 0; byte < 128; ++byte) {
        if (source.descriptor[byte] != descriptor[byte]) {
            (void)system_fetch_add(&header->tma_faults, 1);
            return 0;
        }
    }
    auto count = system_acquire(&header->tensormap_count);
    for (;;) {
        if (count >= hbfsim::device::kTensorMapCapacity) {
            (void)system_fetch_add(&header->tma_faults, 1);
            return 0;
        }
        auto expected = count;
        if (system_compare_exchange(&header->tensormap_count, expected,
                                    count + 1)) {
            break;
        }
        count = expected;
    }
    auto& destination = slots[count];
    system_release(&destination.publication_generation, std::uint64_t{0});
    auto* destination_bytes = reinterpret_cast<std::byte*>(&destination);
    const auto* source_bytes = reinterpret_cast<const std::byte*>(&source);
    for (std::uint32_t byte = sizeof(destination.publication_generation);
         byte < sizeof(destination); ++byte) {
        destination_bytes[byte] = source_bytes[byte];
    }
    destination.fenced = 0;
    const auto publication =
        system_fetch_add(&header->tensormap_publication_generation, 1) + 1;
    if (publication == 0) {
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    __threadfence_system();
    system_release(&destination.publication_generation, publication);
    return 1;
}

extern "C" __device__ std::uint32_t
__hbfsim_tensormap_acquire(std::uint64_t descriptor_address)
{
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    if (control_address == 0 || descriptor_address == 0) return 0;
    auto* header = reinterpret_cast<SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (!valid_control(header, expected_generation)) return 0;
    const auto count = system_acquire(&header->tensormap_count);
    if (count > hbfsim::device::kTensorMapCapacity) return 0;
    const auto* descriptor = reinterpret_cast<const std::byte*>(
        static_cast<std::uintptr_t>(descriptor_address));
    auto* slots = reinterpret_cast<hbfsim::device::SharedTensorMapSlot*>(
        reinterpret_cast<std::byte*>(header) + header->tensormap_offset);
    for (std::uint32_t index = count; index != 0; --index) {
        auto& slot = slots[index - 1];
        if (system_acquire(&slot.publication_generation) == 0) continue;
        bool equal = true;
        for (std::uint32_t byte = 0; byte < 128; ++byte) {
            if (slot.descriptor[byte] != descriptor[byte]) {
                equal = false;
                break;
            }
        }
        if (equal) {
            system_release(&slot.fenced, 1U);
            return 1;
        }
    }
    (void)system_fetch_add(&header->tma_stale_generations, 1);
    (void)system_fetch_add(&header->tma_faults, 1);
    return 0;
}

extern "C" __device__ std::uint64_t __hbfsim_tma_issue(
    std::uint64_t descriptor_address, std::uint64_t shared_address,
    std::uint32_t shared_scope, std::uint32_t cta_group,
    std::uint32_t instruction_id,
    std::uint32_t direction, std::uint32_t access_mode,
    std::uint32_t reduction_operation,
    std::uint64_t barrier, std::uint32_t multicast_mask,
    std::int32_t coordinate0, std::int32_t coordinate1,
    std::int32_t coordinate2, std::int32_t coordinate3,
    std::int32_t coordinate4, std::int32_t im2col_offset0,
    std::int32_t im2col_offset1, std::int32_t im2col_offset2)
{
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    if (control_address == 0 || descriptor_address == 0 || direction > 2 ||
        access_mode > 4 || shared_scope > 1 ||
        (cta_group != 1 && cta_group != 2) ||
        (reduction_operation > 7 && reduction_operation != UINT32_MAX) ||
        (direction == 2 && (shared_address != 0 || shared_scope != 0 ||
                            barrier != 0 || multicast_mask != 0 ||
                            cta_group != 1)) ||
        (multicast_mask != 0 &&
         (shared_scope != 1 || direction != 0 ||
          (multicast_mask & 0xffff0000U) != 0))) {
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
    std::uint32_t selected_index = count;
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
        if (equal) {
            selected = &slot;
            selected_index = index - 1;
        }
    }
    if (selected == nullptr || system_acquire(&selected->fenced) == 0 ||
        selected->rank == 0 || selected->rank > 5 ||
        (reduction_operation != UINT32_MAX && direction != 1) ||
        (direction != 2 && !hbfsim::device::tma_software_copy_supported(
             *selected, direction, access_mode, shared_scope)) ||
        !hbfsim::device::tma_reduction_supported(selected->element_type,
                                                 reduction_operation)) {
        (void)system_fetch_add(&header->tma_stale_generations, 1);
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    (void)system_fetch_add(&header->tma_issued, 1);
    (void)system_fetch_add(&header->tma_fanout_targets,
                           multicast_mask == 0 ? 1 : __popc(multicast_mask));
    const auto range_count = system_acquire(&header->range_count);
    const auto* ranges = reinterpret_cast<const SharedRangeRecord*>(
        reinterpret_cast<const std::byte*>(header) + header->range_offset);
    const std::int32_t coordinates[5]{coordinate0, coordinate1, coordinate2,
                                      coordinate3, coordinate4};
    const std::int32_t im2col_offsets[3]{im2col_offset0, im2col_offset1,
                                         im2col_offset2};
    const auto access_direction = direction == 2 ? 0U : direction;
    const auto classification = hbfsim::device::classify_tma_access(
        *selected, coordinates, ranges, range_count, access_direction, access_mode,
        im2col_offsets);
    if (!classification.valid || classification.hbf_bytes > UINT32_MAX) {
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    (void)system_fetch_add(&header->tma_hbm_bytes,
                           classification.hbm_bytes);
    (void)system_fetch_add(&header->tma_hbf_bytes,
                           classification.hbf_bytes);
    (void)system_fetch_add(&header->tma_oob_bytes,
                           classification.oob_bytes);
    const bool force_software =
        cta_group == 2 ||
        (direction != 2 && !hbfsim::device::tma_copy_supported(
             *selected, direction, access_mode, shared_scope));
    if (classification.hbf_bytes == 0 && !force_software) {
        return 1;
    }
    const auto arrival = gpu_time_ns();
    std::uint64_t ready = arrival;
    if (classification.hbf_bytes != 0) {
        const auto latency = direction == 1 ? header->program_latency_ns
                                            : header->read_latency_ns;
        const auto transfer = hbfsim::device::fast_transfer_ns(
            static_cast<std::uint32_t>(classification.hbf_bytes),
            header->aggregate_bandwidth_bytes_per_s);
        if (latency == 0 || transfer == 0 || header->time_scale == 0) {
            (void)system_fetch_add(&header->tma_faults, 1);
            return 0;
        }
        const auto scaled_latency = hbfsim::device::saturating_multiply(
            latency, header->time_scale);
        const auto scaled_transfer = hbfsim::device::saturating_multiply(
            transfer, header->time_scale);
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
        const auto operation_class =
            classification.hbm_bytes != 0 && classification.hbf_bytes != 0
                ? 6U
                : direction == 1 ? 3U
                : multicast_mask == 0 ? 2U
                : __popc(multicast_mask) > 1 ? 5U : 4U;
        const auto channel = reserve_sm120_channels(
            header, operation_class,
            static_cast<std::uint32_t>(classification.hbf_bytes),
            arrival, ready, ready, 0, 0);
        if (!channel.valid || channel.saturated) {
            (void)system_fetch_add(&header->tma_faults, 1);
            return 0;
        }
        ready = channel.ready_ns;
    }
    std::uint32_t state_slot = 0;
    auto* state = allocate_tma_state(state_slot);
    if (state == nullptr) {
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    (void)system_fetch_add(&header->tma_leaked, 1);
    auto generation = atomicAdd(&hbfsim_tma_deferred_generation, 1U) + 1U;
    generation &= 0x7fffffffU;
    if (generation == 0) generation = 1;
    state->generation = generation;
    state->tensormap_index = selected_index;
    state->future_count = 0;
    state->direction = direction;
    state->access_mode = access_mode;
    state->reduction_operation = reduction_operation;
    state->shared_scope = shared_scope;
    state->cta_group = cta_group;
    state->force_software = force_software ? 1U : 0U;
    state->software_mbarrier =
        barrier != 0 && (classification.capacity || force_software) ? 1U : 0U;
    state->instruction_id = instruction_id;
    state->materialized_mask = 0;
    state->copying_mask = 0;
    state->barrier_completed_mask = 0;
    state->completion_bytes = 0;
    state->shared_address = shared_address;
    state->barrier = tma_state_barrier_address(*state, barrier);
    state->ready_ns = ready;
    current_cluster(state->cluster_id, state->issuer_rank);
    auto destination_rank = state->issuer_rank;
    if (shared_scope == 1 && shared_address != 0) {
        const auto destination_address =
            static_cast<std::uint32_t>(shared_address);
        asm volatile("getctarank.shared::cluster.u32 %0, %1;"
                     : "=r"(destination_rank)
                     : "r"(destination_address));
    }
    state->data_target_mask = hbfsim::device::tma_data_target_mask(
        multicast_mask, state->issuer_rank, destination_rank, shared_scope);
    auto barrier_owner_rank = state->issuer_rank;
    if (barrier != 0 && shared_scope == 1) {
        const auto barrier_address = static_cast<std::uint32_t>(barrier);
        asm volatile("getctarank.shared::cluster.u32 %0, %1;"
                     : "=r"(barrier_owner_rank)
                     : "r"(barrier_address));
    }
    state->barrier_target_mask = hbfsim::device::tma_barrier_target_mask(
        state->data_target_mask, barrier_owner_rank, cta_group);
    for (std::uint32_t index = 0; index < 5; ++index) {
        state->coordinates[index] = coordinates[index];
    }
    for (std::uint32_t index = 0; index < 3; ++index) {
        state->im2col_offsets[index] = im2col_offsets[index];
    }
    bool state_valid = state->data_target_mask != 0 &&
                       state->barrier_target_mask != 0;
    const auto elements = hbfsim::device::tma_access_element_count(
        *selected, access_mode, im2col_offsets);
    if (elements == 0) state_valid = false;
    std::uint64_t completion_bytes = 0;
    for (std::uint64_t linear = 0;
         state_valid && linear < elements; ++linear) {
        const auto element = hbfsim::device::tma_element_address(
            *selected, coordinates, access_mode, linear, im2col_offsets,
            shared_address);
        if (!element.valid ||
            (direction != 2 &&
             (element.destination_offset >
                  kTmaMaterializationBytes - element.shared_bytes ||
              shared_address > UINT32_MAX - element.destination_offset))) {
            state_valid = false;
            break;
        }
        if (state->software_mbarrier != 0) {
            if (completion_bytes > UINT32_MAX - element.shared_bytes) {
                state_valid = false;
                break;
            }
            completion_bytes += element.shared_bytes;
        }
        if ((classification.capacity || force_software) &&
            direction == 1 && !element.oob) {
            const auto* source = reinterpret_cast<const std::byte*>(
                __cvta_shared_to_generic(static_cast<std::size_t>(
                    shared_address + element.destination_offset)));
            for (std::uint32_t byte = 0; byte < element.shared_bytes; ++byte) {
                state->store_snapshot[element.destination_offset + byte] =
                    source[byte];
            }
        }
        if (!classification.capacity || element.oob) continue;
        for (std::uint32_t byte = 0; byte < element.bytes; ++byte) {
            const auto address = element.global_address + byte;
            const auto* range = find_range(ranges, range_count, address);
            if (range == nullptr || range->mode != 2) continue;
            if (range->page_bytes == 0 || address < range->base) {
                state_valid = false;
                break;
            }
            const auto page_base = range->base +
                ((address - range->base) / range->page_bytes) *
                    range->page_bytes;
            bool known = false;
            for (std::uint32_t page = 0; page < state->future_count; ++page) {
                known = known || state->page_bases[page] == page_base;
            }
            if (known) continue;
            if (state->future_count == kTmaDeferredPageCapacity) {
                state_valid = false;
                break;
            }
            const auto page = state->future_count++;
            state->page_bases[page] = page_base;
            state->futures[page] = __hbfsim_future_issue(
                page_base, 1, access_direction, instruction_id);
            if (state->futures[page].state ==
                DeviceFutureState::TerminalError) {
                state_valid = false;
                break;
            }
        }
    }
    state->completion_bytes = static_cast<std::uint32_t>(completion_bytes);
    if (!state_valid ||
        (state->software_mbarrier != 0 && state->completion_bytes == 0) ||
        (classification.capacity && state->future_count == 0)) {
        atomicExch(&state->status, 0U);
        system_fetch_sub_release(&header->tma_leaked, 1);
        (void)system_fetch_add(&header->tma_faults, 1);
        return 0;
    }
    __threadfence();
    atomicExch(&state->status, 2U);
    if (classification.capacity || force_software) {
        return hbfsim::device::encode_tma_software_token(
            generation, state_slot);
    }
    return hbfsim::device::encode_tma_timing_token(generation, state_slot);
}

extern "C" __device__ std::uint32_t __hbfsim_tma_barrier_poll(
    std::uint64_t token, std::uint64_t barrier)
{
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    auto* header = reinterpret_cast<SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (control_address == 0 ||
        !valid_control(header, expected_generation)) {
        return 0;
    }
    std::uint32_t cluster_id[3]{};
    std::uint32_t rank = 0;
    current_cluster(cluster_id, rank);
    bool found = false;
    bool ready = true;
    bool software = hbfsim::device::is_tma_software_token(token);
    for (std::uint32_t index = 0; index < kTmaDeferredCapacity; ++index) {
        auto& state = hbfsim_tma_deferred_states[index];
        const auto status = atomicAdd(&state.status, 0U);
        if (status == 0 || rank >= 16 ||
            !same_tma_domain(state, cluster_id) ||
            state.barrier != tma_state_barrier_address(state, barrier) ||
            (state.barrier_target_mask & (1U << rank)) == 0) {
            continue;
        }
        found = true;
        const bool deferred = state.future_count != 0 ||
                              state.force_software != 0;
        software = software || deferred;
        if (!poll_tma_state(state, rank, header, deferred)) ready = false;
    }
    if (found && ready && software &&
        !complete_software_tma_barrier(header, barrier, cluster_id, rank)) {
        ready = false;
    }
    if (found) return (ready ? 1U : 0U) | (software ? 2U : 0U);
    if (software) return 3U;
    return token == 1 || (token >= 2 && gpu_time_ns() >= token) ? 1U : 0U;
}

extern "C" __device__ void __hbfsim_tma_barrier_wait(
    std::uint64_t token, std::uint64_t barrier)
{
    std::uint32_t delay = 64;
    while ((__hbfsim_tma_barrier_poll(token, barrier) & 1U) == 0) {
        bounded_sleep(delay);
    }
}

extern "C" __device__ void __hbfsim_tma_commit_group()
{
}

extern "C" __device__ void __hbfsim_tma_wait_group(
    std::uint64_t token, std::uint32_t read_only)
{
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    auto* header = reinterpret_cast<SharedControlHeader*>(
        static_cast<std::uintptr_t>(control_address));
    if (control_address == 0 ||
        !valid_control(header, expected_generation)) {
        asm volatile("trap;");
    }
    if (token == 1) return;
    if (!hbfsim::device::is_tma_software_token(token) &&
        !hbfsim::device::is_tma_timing_token(token)) {
        asm volatile("trap;");
    }
    const auto slot = hbfsim::device::tma_tracked_token_slot(token);
    const auto generation =
        hbfsim::device::tma_tracked_token_generation(token);
    if (slot >= kTmaDeferredCapacity || generation == 0) {
        asm volatile("trap;");
    }
    auto& state = hbfsim_tma_deferred_states[slot];
    std::uint32_t cluster_id[3]{};
    std::uint32_t rank = 0;
    current_cluster(cluster_id, rank);
    if (state.generation != generation ||
        (state.direction != 1 && state.direction != 2) ||
        state.barrier != 0 || !same_tma_domain(state, cluster_id) ||
        state.issuer_rank != rank) {
        asm volatile("trap;");
    }
    if (read_only != 0 && state.direction == 1) return;
    std::uint32_t delay = 64;
    while (atomicAdd(&state.status, 0U) != 0 &&
           !poll_tma_state(state, rank, header,
                           state.future_count != 0 ||
                               state.force_software != 0)) {
        bounded_sleep(delay);
    }
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
                header, completions, future.ticket, wait, future.ready_ns,
                future.channel);
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
            if (future.status == RequestStatus::Ready) {
                (void)system_fetch_add(
                    &header->thermal_inflight_completed, 1);
            }
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

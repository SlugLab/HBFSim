#include "hbf_device.cuh"

#include <cuda/atomic>
#include <cuda_runtime.h>

extern "C" __device__ unsigned long long __hbfsim_control = 0;
extern "C" __device__ unsigned long long __hbfsim_control_generation = 0;
extern "C" __device__ hbfsim::device::EvalDelayConfig
    __hbfsim_eval_delay_config = {};
extern "C" __device__ hbfsim::device::EvalDelayCounters
    __hbfsim_eval_delay_counters = {};
extern "C" __device__ hbfsim::device::AccessAccountingConfig
    __hbfsim_access_accounting_config = {};
extern "C" __device__ hbfsim::device::AccessAccountingCounters
    __hbfsim_access_accounting_counters = {};
extern "C" __device__ hbfsim::device::FirstFaultConfig
    __hbfsim_first_fault_config = {};
extern "C" __device__ unsigned int __hbfsim_first_fault_claimed = 0;
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
extern "C" __device__ hbfsim::device::EvalChainDiagnosticConfig
    __hbfsim_eval_chain_diagnostic_config = {};
#endif
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

__device__ std::uint64_t device_acquire(const std::uint64_t* address)
{
    cuda::atomic_ref<std::uint64_t, cuda::thread_scope_device> value(
        *const_cast<std::uint64_t*>(address));
    return value.load(cuda::memory_order_acquire);
}

__device__ bool device_compare_exchange(std::uint64_t* address,
                                        std::uint64_t& expected,
                                        std::uint64_t desired)
{
    cuda::atomic_ref<std::uint64_t, cuda::thread_scope_device> value(*address);
    return value.compare_exchange_weak(expected, desired,
                                       cuda::memory_order_relaxed,
                                       cuda::memory_order_relaxed);
}

__device__ std::uint64_t device_fetch_add(std::uint64_t* address,
                                          std::uint64_t increment)
{
    cuda::atomic_ref<std::uint64_t, cuda::thread_scope_device> value(*address);
    return value.fetch_add(increment, cuda::memory_order_relaxed);
}

__device__ void device_fetch_sub_release(std::uint64_t* address,
                                         std::uint64_t decrement)
{
    cuda::atomic_ref<std::uint64_t, cuda::thread_scope_device> value(*address);
    (void)value.fetch_sub(decrement, cuda::memory_order_release);
}

__device__ hbfsim::device::DeviceTimingState* device_timing_state(
    SharedControlHeader* header, std::uint64_t expected_generation)
{
    if (header->producer_mode != hbfsim::device::kProducerModeGpuExclusive ||
        header->device_state_bytes !=
            sizeof(hbfsim::device::DeviceTimingState) ||
        header->device_state_address == 0 ||
        header->device_state_generation != expected_generation) {
        return nullptr;
    }
    auto* state = reinterpret_cast<hbfsim::device::DeviceTimingState*>(
        header->device_state_address);
    return device_acquire(&state->magic) ==
                   hbfsim::device::kDeviceTimingStateMagic &&
               device_acquire(&state->generation) == expected_generation &&
               device_acquire(&state->poisoned) == 0
           ? state
           : nullptr;
}

__device__ std::uint64_t gpu_time_ns()
{
    std::uint64_t now;
    asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(now));
    return now;
}

struct FirstFaultObservation {
    hbfsim::device::FirstFaultReason reason{
        hbfsim::device::FirstFaultReason::Other};
    hbfsim::device::FirstFaultPhase phase{
        hbfsim::device::FirstFaultPhase::FaultTrap};
    RequestStatus status{RequestStatus::IoError};
    std::uint64_t valid_bits{0};
    std::uint64_t arrival_ns{0}, deadline_ns{0}, target_ns{0};
    std::uint64_t heartbeat_value{0}, heartbeat_observed_ns{0};
    std::uint64_t ticket{0}, position{0}, slot_index{0};
    std::uint64_t request_slot_sequence{0}, completion_slot_sequence{0};
    std::uint64_t completion_request_id{0}, completion_modeled_ns{0};
    std::uint32_t completion_status{0};
};

__device__ void first_fault_record(const SharedControlHeader* header,
                                   const hbfsim::device::DeviceTimingState* state,
                                   std::uint64_t expected_generation,
                                   const FirstFaultObservation& observation)
{
    const auto config = __hbfsim_first_fault_config;
    if (config.magic != hbfsim::device::kFirstFaultConfigMagic ||
        config.schema_version != hbfsim::device::kFirstFaultSchemaVersion ||
        config.struct_bytes != sizeof(config) || config.enabled != 1 ||
        config.epoch == 0 || config.compact_address == 0 ||
        config.record_address == 0) return;
    const auto reason = static_cast<std::uint32_t>(observation.reason);
    const auto phase = static_cast<std::uint32_t>(observation.phase);
    const auto status = static_cast<std::uint32_t>(observation.status);
    if (config.compact_address != 0 && reason >= 1 && reason <= 5 &&
        phase >= 1 && phase <= 5 && status >= 2 && status <= 7) {
        constexpr std::uint64_t compact_magic = 0x4846ULL;
        constexpr std::uint64_t compact_schema = 2ULL;
        const std::uint64_t compact = (compact_magic << 48) |
            (compact_schema << 44) |
            (static_cast<std::uint64_t>(reason) << 40) |
            (static_cast<std::uint64_t>(phase) << 36) |
            (static_cast<std::uint64_t>(status) << 28) |
            (config.epoch & 0x0fffffffULL);
        system_release(reinterpret_cast<std::uint64_t*>(
            config.compact_address), compact);
    }
    // This CAS targets a CUDA module global.  It never touches mapped host
    // memory and therefore remains valid when HostNativeAtomicSupported == 0.
    if (atomicCAS(&__hbfsim_first_fault_claimed, 0U, 1U) != 0U) return;
    auto* record = reinterpret_cast<hbfsim::device::FirstFaultRecord*>(
        config.record_address);
    record->schema_version = config.schema_version;
    record->epoch = config.epoch;
    for (unsigned i = 0; i != 4; ++i)
        record->module_identity[i] = config.module_identity[i];
    record->status = static_cast<std::uint32_t>(observation.status);
    record->reason = static_cast<std::uint32_t>(observation.reason);
    record->phase = static_cast<std::uint32_t>(observation.phase);
    record->reserved0 = 0;
    record->valid_bits = observation.valid_bits;
    record->gpu_now_ns = gpu_time_ns();
    record->arrival_ns = observation.arrival_ns;
    record->deadline_ns = observation.deadline_ns;
    record->target_ns = observation.target_ns;
    record->heartbeat_value = observation.heartbeat_value;
    record->heartbeat_observed_ns = observation.heartbeat_observed_ns;
    record->heartbeat_current =
        header != nullptr ? system_acquire(&header->heartbeat_ns) : 0;
    record->shutdown = header != nullptr ? system_acquire(&header->shutdown) : 0;
    record->fault = header != nullptr ? system_acquire(&header->fault) : 0;
    record->expected_generation = expected_generation;
    record->header_generation =
        header != nullptr ? system_acquire(&header->control_generation) : 0;
    if (state != nullptr) {
        record->sidecar_generation = device_acquire(&state->generation);
        record->sidecar_poisoned = device_acquire(&state->poisoned);
        record->valid_bits |= hbfsim::device::FirstFaultHasSidecar;
    } else {
        record->sidecar_generation = 0;
        record->sidecar_poisoned = 0;
    }
    record->ticket = observation.ticket;
    record->position = observation.position;
    record->slot_index = observation.slot_index;
    record->ring_capacity = header != nullptr ? header->ring_capacity : 0;
    record->request_slot_sequence = observation.request_slot_sequence;
    record->completion_slot_sequence = observation.completion_slot_sequence;
    record->device_request_producer =
        state != nullptr ? device_acquire(&state->request_producer) : 0;
    record->device_completion_consumer =
        state != nullptr ? device_acquire(&state->completion_consumer) : 0;
    record->host_request_consumer =
        header != nullptr ? system_acquire(&header->request_consumer) : 0;
    record->host_completion_producer =
        header != nullptr ? system_acquire(&header->completion_producer) : 0;
    record->admission_count = state != nullptr
        ? device_acquire(&state->admission_count)
        : (header != nullptr ? system_acquire(&header->admission_state) : 0);
    record->completion_request_id = observation.completion_request_id;
    record->completion_modeled_ns = observation.completion_modeled_ns;
    record->completion_status = observation.completion_status;
    record->block_x = blockIdx.x;
    record->block_y = blockIdx.y;
    record->block_z = blockIdx.z;
    record->thread_x = threadIdx.x;
    record->thread_y = threadIdx.y;
    record->thread_z = threadIdx.z;
    std::uint32_t diagnostic_lane;
    asm volatile("mov.u32 %0, %%laneid;" : "=r"(diagnostic_lane));
    record->lane = diagnostic_lane;
    record->reserved1 = 0;
    system_release(&record->ready, hbfsim::device::kFirstFaultReadyMagic);
}

struct EvalDelayClock {
    __device__ __forceinline__ std::uint64_t operator()() const
    {
        std::uint64_t now;
        asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(now) : : "memory");
        return now;
    }
};

// Keep a separately inspectable PTX boundary: no mapped host-control loads,
// memory operations or liveness polling may enter the measured clock interval.
__device__ __noinline__ hbfsim::device::EvalDelayInterval eval_delay_clock_wait(
    std::uint64_t delay_ns, std::uint64_t timeout_ns)
{
    return hbfsim::device::eval_delay_clock_interval(
        delay_ns, timeout_ns, EvalDelayClock{});
}

__device__ bool eval_delay_control_ready(
    const SharedControlHeader* header, std::uint64_t expected_generation)
{
    return system_acquire(&header->shutdown) == 0 &&
           system_acquire(&header->fault) == 0 &&
           system_acquire(&header->heartbeat_ns) != 0 &&
           system_acquire(&header->control_generation) == expected_generation;
}

#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
__device__ bool eval_chain_record(
    const hbfsim::device::EvalChainDiagnosticConfig& config,
    const hbfsim::device::EvalChainProducer& producer,
    hbfsim::device::EvalChainEventClass event_class, std::uint64_t address,
    std::uint32_t bytes, std::uint32_t operation, std::uint64_t begin_ns,
    std::uint64_t end_ns, RequestStatus status)
{
    const auto storage = hbfsim::device::eval_chain_storage(config,
                                                            producer.row);
    if (producer.valid == 0 || storage.valid == 0) return false;
    auto* row = reinterpret_cast<hbfsim::device::EvalChainRow*>(
        static_cast<std::uintptr_t>(storage.row_address));
    // One validated producer owns each row and the host reads only after the
    // launch completes, so these row-local updates need no shared atomics.
    const auto writer_observed = row->writer_observed;
    if (writer_observed == 0) {
        row->launch_epoch = config.launch_epoch;
        row->writer_thread_id = producer.thread_id;
        row->writer_observed = 1;
    } else if (writer_observed != 1 ||
               row->launch_epoch != config.launch_epoch ||
               row->writer_thread_id != producer.thread_id) {
        return false;
    }

    const auto index = row->event_count++;
    switch (event_class) {
        case hbfsim::device::EvalChainEventClass::CoveredLoad:
            ++row->counters.covered_accesses;
            row->counters.covered_bytes += bytes;
            break;
        case hbfsim::device::EvalChainEventClass::ChainOutputStore:
        case hbfsim::device::EvalChainEventClass::BlockOutputStore:
            ++row->counters.bypass_accesses;
            row->counters.bypass_bytes += bytes;
            break;
        case hbfsim::device::EvalChainEventClass::Rejected:
            ++row->counters.rejected_accesses;
            break;
        default:
            return false;
    }
    const auto slot = hbfsim::device::eval_chain_slot(config, producer.row,
                                                       index);
    if (slot.valid == 0) {
        ++row->counters.trace_overflow;
        return false;
    }
    auto* event = reinterpret_cast<hbfsim::device::EvalChainEvent*>(
        static_cast<std::uintptr_t>(slot.address));
    *event = {producer.thread_id, address, index, begin_ns, end_ns, bytes,
              operation, static_cast<std::uint32_t>(event_class),
              static_cast<std::uint32_t>(status)};
    return true;
}
#endif

// Caps for the clamped wait sleeps. The backoff cap matches the ceiling the
// old doubling schedule reached, and the PTX ISA caps one nanosleep at about
// 1 ms anyway. Below the spin floor a nanosleep costs more than it saves, and
// spinning the tail is what absorbs the ISA's [0, 2*t] approximation.
constexpr std::uint32_t kWaitBackoffCapNs = 1048576U;
constexpr std::uint32_t kWaitSpinFloorNs = 64U;

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
    std::uint32_t admitted{0};
};

__device__ bool access_accounting_enabled()
{
    const auto config = __hbfsim_access_accounting_config;
    return config.magic == hbfsim::device::kAccessAccountingMagic &&
           config.version == hbfsim::device::kAccessAccountingVersion &&
           config.struct_bytes == sizeof(config) && config.enabled == 1;
}

__device__ void account_add(std::uint64_t* field, std::uint64_t value)
{
    if (value == 0) return;
    auto observed = system_acquire(field);
    for (;;) {
        const bool overflow = value > UINT64_MAX - observed;
        const auto desired = overflow ? UINT64_MAX : observed + value;
        auto expected = observed;
        if (system_compare_exchange(field, expected, desired)) {
            if (overflow) {
                (void)system_fetch_add(
                    &__hbfsim_access_accounting_counters.counter_overflow, 1);
            }
            return;
        }
        observed = expected;
    }
}

__device__ std::uint64_t range_intersection_bytes(
    const SharedRangeRecord& range, std::uint64_t address,
    std::uint32_t bytes)
{
    if (bytes == 0 || range.length > UINT64_MAX - range.base ||
        bytes > UINT64_MAX - address) return 0;
    const auto access_end = address + bytes;
    const auto range_end = range.base + range.length;
    const auto begin = address > range.base ? address : range.base;
    const auto end = access_end < range_end ? access_end : range_end;
    return end > begin ? end - begin : 0;
}

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
    SharedControlHeader* header, hbfsim::device::DeviceTimingState* state,
    SharedRequestSlot* requests, SharedCompletionSlot* completions,
    const hbfsim::device::HbfRequest& request, WaitState& wait,
    std::uint64_t& ticket)
{
    auto admission = state != nullptr
                         ? device_acquire(&state->admission_count)
                         : system_acquire(&header->admission_state);
    for (;;) {
        if ((admission & hbfsim::device::kAdmissionClosedBit) != 0 ||
            (admission & hbfsim::device::kAdmissionCountMask) ==
                hbfsim::device::kAdmissionCountMask) {
            FirstFaultObservation observed{};
            observed.reason = hbfsim::device::FirstFaultReason::ReferenceReserve;
            observed.phase = hbfsim::device::FirstFaultPhase::ReserveSlot;
            observed.status = RequestStatus::IoError;
            observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                                  hbfsim::device::FirstFaultHasDeadline |
                                  hbfsim::device::FirstFaultHasHeartbeat;
            observed.arrival_ns = request.arrival_ns;
            observed.deadline_ns = wait.deadline_ns;
            observed.heartbeat_value = wait.heartbeat_value;
            observed.heartbeat_observed_ns = wait.heartbeat_observed_ns;
            first_fault_record(header, state, header->control_generation, observed);
            return RequestStatus::IoError;
        }
        auto expected = admission;
        if (state != nullptr
                ? device_compare_exchange(&state->admission_count, expected,
                                          admission + 1)
                : system_compare_exchange(&header->admission_state, expected,
                                          admission + 1)) {
            break;
        }
        admission = expected;
    }
    auto position = state != nullptr
                        ? device_acquire(&state->request_producer)
                        : system_acquire(&header->request_producer);
    for (;;) {
        if ((system_acquire(&header->admission_state) &
             hbfsim::device::kAdmissionClosedBit) != 0) {
            state != nullptr
                ? device_fetch_sub_release(&state->admission_count, 1)
                : system_fetch_sub_release(&header->admission_state, 1);
            FirstFaultObservation observed{};
            observed.reason = hbfsim::device::FirstFaultReason::ReferenceReserve;
            observed.phase = hbfsim::device::FirstFaultPhase::ReserveSlot;
            observed.status = RequestStatus::IoError;
            observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                                  hbfsim::device::FirstFaultHasDeadline |
                                  hbfsim::device::FirstFaultHasHeartbeat |
                                  hbfsim::device::FirstFaultHasRing;
            observed.arrival_ns = request.arrival_ns;
            observed.deadline_ns = wait.deadline_ns;
            observed.heartbeat_value = wait.heartbeat_value;
            observed.heartbeat_observed_ns = wait.heartbeat_observed_ns;
            observed.position = position;
            observed.slot_index = position & (header->ring_capacity - 1);
            first_fault_record(header, state, header->control_generation, observed);
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
            if (state != nullptr
                    ? device_compare_exchange(&state->request_producer,
                                              expected, position + 1)
                    : system_compare_exchange(&header->request_producer,
                                              expected, position + 1)) {
                request_slot.value = request;
                request_slot.value.request_id = position + 1;
                request_slot.value.sequence = position;
                system_release(&request_slot.sequence, position + 1);
                state != nullptr
                ? device_fetch_sub_release(&state->admission_count, 1)
                : system_fetch_sub_release(&header->admission_state, 1);
                ticket = position;
                return RequestStatus::Ready;
            }
            position = expected;
            continue;
        }
        if (request_difference > 0 && completion_difference > 0) {
            position = state != nullptr
                           ? device_acquire(&state->request_producer)
                           : system_acquire(&header->request_producer);
            continue;
        }
        const auto liveness = poll_liveness(header, wait);
        if (liveness != RequestStatus::Pending) {
            state != nullptr
                ? device_fetch_sub_release(&state->admission_count, 1)
                : system_fetch_sub_release(&header->admission_state, 1);
            FirstFaultObservation observed{};
            observed.reason = hbfsim::device::FirstFaultReason::Liveness;
            observed.phase = hbfsim::device::FirstFaultPhase::ReserveSlot;
            observed.status = liveness;
            observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                                  hbfsim::device::FirstFaultHasDeadline |
                                  hbfsim::device::FirstFaultHasHeartbeat |
                                  hbfsim::device::FirstFaultHasRing;
            observed.arrival_ns = request.arrival_ns;
            observed.deadline_ns = wait.deadline_ns;
            observed.heartbeat_value = wait.heartbeat_value;
            observed.heartbeat_observed_ns = wait.heartbeat_observed_ns;
            observed.position = position;
            observed.slot_index = position & (header->ring_capacity - 1);
            observed.request_slot_sequence = request_sequence;
            observed.completion_slot_sequence = completion_sequence;
            first_fault_record(header, state, header->control_generation, observed);
            return liveness;
        }
        position = state != nullptr
                           ? device_acquire(&state->request_producer)
                           : system_acquire(&header->request_producer);
    }
}

__device__ CompletionResult wait_for_completion(
    SharedControlHeader* header, hbfsim::device::DeviceTimingState* state,
    SharedCompletionSlot* completions, std::uint64_t ticket, WaitState& wait, std::uint64_t arrival_ns)
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
                FirstFaultObservation observed{};
                observed.reason = hbfsim::device::FirstFaultReason::Liveness;
                observed.phase = hbfsim::device::FirstFaultPhase::WaitCompletion;
                observed.status = liveness;
                observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                                      hbfsim::device::FirstFaultHasDeadline |
                                      hbfsim::device::FirstFaultHasHeartbeat |
                                      hbfsim::device::FirstFaultHasTicket |
                                      hbfsim::device::FirstFaultHasRing;
                observed.arrival_ns = arrival_ns;
                observed.deadline_ns = wait.deadline_ns;
                observed.heartbeat_value = wait.heartbeat_value;
                observed.heartbeat_observed_ns = wait.heartbeat_observed_ns;
                observed.ticket = ticket;
                observed.position = ticket;
                observed.slot_index = ticket & (header->ring_capacity - 1);
                observed.completion_slot_sequence = system_acquire(&slot.sequence);
                first_fault_record(header, state, header->control_generation, observed);
                return {.status = liveness, .admitted = 1};
            }
            break;
        }
    }
    const auto completion = slot.value;
    system_release(&slot.sequence, ticket + header->ring_capacity);
    if (state != nullptr)
        (void)device_fetch_add(&state->completion_consumer, 1);
    else
        (void)system_fetch_add(&header->completion_consumer, 1);
    if (completion.request_id != ticket + 1 ||
        completion.status == static_cast<std::uint32_t>(
                                 RequestStatus::Pending) ||
        completion.status > static_cast<std::uint32_t>(
                                RequestStatus::DaemonLost)) {
        FirstFaultObservation observed{};
        observed.reason = hbfsim::device::FirstFaultReason::ReferenceCompletion;
        observed.phase = hbfsim::device::FirstFaultPhase::WaitCompletion;
        observed.status = RequestStatus::IoError;
        observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                              hbfsim::device::FirstFaultHasDeadline |
                              hbfsim::device::FirstFaultHasTicket |
                              hbfsim::device::FirstFaultHasRing |
                              hbfsim::device::FirstFaultHasCompletion;
        observed.arrival_ns = arrival_ns;
        observed.deadline_ns = wait.deadline_ns;
        observed.ticket = ticket;
        observed.position = ticket;
        observed.slot_index = ticket & (header->ring_capacity - 1);
        observed.completion_request_id = completion.request_id;
        observed.completion_status = completion.status;
        observed.completion_modeled_ns = completion.modeled_ns;
        first_fault_record(header, state, header->control_generation, observed);
        return {.status = RequestStatus::IoError, .admitted = 1};
    }
    const auto status = static_cast<RequestStatus>(completion.status);
    if (status != RequestStatus::Ready) {
        FirstFaultObservation observed{};
        observed.reason = hbfsim::device::FirstFaultReason::ReferenceCompletion;
        observed.phase = hbfsim::device::FirstFaultPhase::WaitCompletion;
        observed.status = status;
        observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                              hbfsim::device::FirstFaultHasDeadline |
                              hbfsim::device::FirstFaultHasTicket |
                              hbfsim::device::FirstFaultHasRing |
                              hbfsim::device::FirstFaultHasCompletion;
        observed.arrival_ns = arrival_ns;
        observed.deadline_ns = wait.deadline_ns;
        observed.ticket = ticket;
        observed.position = ticket;
        observed.slot_index = ticket & (header->ring_capacity - 1);
        observed.completion_request_id = completion.request_id;
        observed.completion_status = completion.status;
        observed.completion_modeled_ns = completion.modeled_ns;
        first_fault_record(header, state, header->control_generation, observed);
        return {.status = status, .admitted = 1};
    }
    const auto scaled = hbfsim::device::saturating_multiply(
        completion.modeled_ns, header->time_scale);
    const auto target = hbfsim::device::saturating_add(arrival_ns, scaled);
    // The third of the three modeled-target waits, and the worst of them.
    // bounded_sleep doubles a backoff that knows nothing about `target`, and
    // wait.sleep_ns is a reference that the completion-ring loop above has
    // already escalated -- by the time control reaches here it can sit at the
    // 1,048,576 ns cap, so a 10,000 ns modeled delay could be served by a
    // single ~1 ms nap. wait_sleep_ns never naps past half the remaining time,
    // which the PTX ISA's [0, 2*t] guarantee on __nanosleep makes safe.
    //
    // The bounded_sleep call that remains, on the completion-ring wait, is
    // correct and deliberately left alone: that loop waits on the host, whose
    // finish time is not known, so exponential backoff is the right policy.
    // What makes backoff wrong here is that the target IS known.
    while (gpu_time_ns() < target) {
        const auto now = gpu_time_ns();
        if (now >= wait.deadline_ns) {
            FirstFaultObservation observed{};
            observed.reason = hbfsim::device::FirstFaultReason::ReferenceCompletion;
            observed.phase = hbfsim::device::FirstFaultPhase::ReferenceTarget;
            observed.status = RequestStatus::Timeout;
            observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                                  hbfsim::device::FirstFaultHasDeadline |
                                  hbfsim::device::FirstFaultHasTarget |
                                  hbfsim::device::FirstFaultHasTicket |
                                  hbfsim::device::FirstFaultHasRing |
                                  hbfsim::device::FirstFaultHasCompletion;
            observed.arrival_ns = arrival_ns;
            observed.deadline_ns = wait.deadline_ns;
            observed.target_ns = target;
            observed.ticket = ticket;
            observed.position = ticket;
            observed.slot_index = ticket & (header->ring_capacity - 1);
            observed.completion_request_id = completion.request_id;
            observed.completion_status = completion.status;
            observed.completion_modeled_ns = completion.modeled_ns;
            first_fault_record(header, state, header->control_generation, observed);
            return {.status = RequestStatus::Timeout, .admitted = 1};
        }
        const auto nap = hbfsim::device::wait_sleep_ns(
            now, target, kWaitBackoffCapNs, kWaitSpinFloorNs);
        if (nap != 0) {
            __nanosleep(nap);
        }
    }
    return {.status = RequestStatus::Ready,
            .frame_address = completion.cache_frame_address,
            .admitted = 1};
}

__device__ CompletionResult resolve_leader(
    SharedControlHeader* header, hbfsim::device::DeviceTimingState* state,
    const SharedRangeRecord& range,
    const hbfsim::device::MediaDescriptor& media,
    std::uint32_t operation)
{
    const auto capacity = header->ring_capacity;
    if (capacity < hbfsim::device::kMinimumRingCapacity ||
        capacity > hbfsim::device::kMaximumRingCapacity ||
        (capacity & (capacity - 1)) != 0 ||
        header->request_timeout_ns == 0 ||
        header->heartbeat_timeout_ns == 0 || header->time_scale == 0) {
        return {.status = RequestStatus::Unsupported, .admitted = 0};
    }
    const auto arrival = gpu_time_ns();
    WaitState wait{.deadline_ns = hbfsim::device::saturating_add(
                       arrival, header->request_timeout_ns),
                   .heartbeat_value = system_acquire(&header->heartbeat_ns),
                   .heartbeat_observed_ns = arrival};
    if (wait.heartbeat_value == 0) {
        return {.status = RequestStatus::DaemonLost, .admitted = 0};
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
    const auto reserved = reserve_request(header, state, requests, completions, request, wait, ticket);
    return reserved == RequestStatus::Ready
               ? wait_for_completion(header, state, completions, ticket, wait, arrival)
               : CompletionResult{.status = reserved, .admitted = 0};
}

__device__ CompletionResult resolve_fast_or_hybrid(
    SharedControlHeader* header, hbfsim::device::DeviceTimingState* state,
    const SharedRangeRecord& range,
    const hbfsim::device::MediaDescriptor& media,
    std::uint32_t operation)
{
    constexpr std::uint32_t kFast = 1;
    constexpr std::uint32_t kHybrid = 2;
    const auto empirical_enabled = header->empirical_flags != 0;
    if (empirical_enabled &&
        !hbfsim::device::empirical_control_valid(*header)) {
        return {.status = RequestStatus::Unsupported, .admitted = 0};
    }
    const auto sequence = (state != nullptr ? device_fetch_add(&state->fast_request_sequence, 1) : system_fetch_add(&header->fast_request_sequence, 1));
    const auto sample_key = media.logical_address ^
                            (static_cast<std::uint64_t>(range.range_id) << 32) ^
                            operation;
    if (header->timing_model == kHybrid &&
        hbfsim::device::hybrid_reference_sample(
            sequence, header->reference_warmup_requests,
            header->reference_sample_threshold, sample_key)) {
        (void)(state != nullptr ? device_fetch_add(&state->reference_requests, 1) : system_fetch_add(&header->reference_requests, 1));
        return resolve_leader(header, state, range, media, operation);
    }
    if (header->timing_model != kFast && header->timing_model != kHybrid) {
        return {.status = RequestStatus::Unsupported, .admitted = 0};
    }

    if (empirical_enabled) {
        if (media.bytes != 4096 || range.page_bytes != 4096 ||
            media.logical_address % media.bytes != 0 ||
            header->time_scale == 0 || header->request_timeout_ns == 0) {
            return {.status = RequestStatus::Unsupported, .admitted = 0};
        }
        const auto page = media.logical_address / media.bytes;
        auto previous_state =
            (state != nullptr ? device_acquire(&state->empirical_burst_state) : system_acquire(&header->empirical_burst_state));
        hbfsim::device::EmpiricalRequestService request{};
        for (;;) {
            request = hbfsim::device::empirical_request_service(
                *header, previous_state, page, operation);
            if (!request.valid) {
                return {.status = RequestStatus::Unsupported, .admitted = 0};
            }
            auto expected = previous_state;
            if ((state != nullptr ? device_compare_exchange(&state->empirical_burst_state, expected, request.packed_state) : system_compare_exchange(&header->empirical_burst_state, expected, request.packed_state))) {
                break;
            }
            previous_state = expected;
        }

        const auto arrival = gpu_time_ns();
        const auto scaled_service = hbfsim::device::saturating_multiply(
            request.service_ns, header->time_scale);
        auto tail = (state != nullptr ? device_acquire(&state->fast_channel_tail_ns) : system_acquire(&header->fast_channel_tail_ns));
        std::uint64_t target = 0;
        for (;;) {
            const auto start = tail > arrival ? tail : arrival;
            target = hbfsim::device::saturating_add(start, scaled_service);
            auto expected = tail;
            if ((state != nullptr ? device_compare_exchange(&state->fast_channel_tail_ns,
                                        expected, target) : system_compare_exchange(&header->fast_channel_tail_ns, expected, target))) {
                break;
            }
            tail = expected;
        }
        const auto deadline = hbfsim::device::saturating_add(
            arrival, header->request_timeout_ns);
        // Sleep only as far as the modeled target allows. The old schedule
        // doubled from 64 ns without consulting the target and overshot it by
        // 63 percent on a 10,000 ns target; wait_sleep_ns takes at most half
        // the remaining time, which the PTX ISA's [0, 2*t] bound makes safe.
        while (gpu_time_ns() < target) {
            const auto now = gpu_time_ns();
            if (now >= deadline) {
                FirstFaultObservation observed{};
                observed.reason = hbfsim::device::FirstFaultReason::FastWait;
                observed.phase = hbfsim::device::FirstFaultPhase::FastTarget;
                observed.status = RequestStatus::Timeout;
                observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                                      hbfsim::device::FirstFaultHasDeadline |
                                      hbfsim::device::FirstFaultHasTarget;
                observed.arrival_ns = arrival;
                observed.deadline_ns = deadline;
                observed.target_ns = target;
                first_fault_record(header, state, header->control_generation, observed);
                return {.status = RequestStatus::Timeout, .admitted = 1};
            }
            if (system_acquire(&header->shutdown) != 0 ||
                system_acquire(&header->fault) != 0) {
                FirstFaultObservation observed{};
                observed.reason = hbfsim::device::FirstFaultReason::Liveness;
                observed.phase = hbfsim::device::FirstFaultPhase::FastTarget;
                observed.status = RequestStatus::DaemonLost;
                observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                                      hbfsim::device::FirstFaultHasDeadline |
                                      hbfsim::device::FirstFaultHasTarget;
                observed.arrival_ns = arrival;
                observed.deadline_ns = deadline;
                observed.target_ns = target;
                first_fault_record(header, state, header->control_generation, observed);
                return {.status = RequestStatus::DaemonLost, .admitted = 1};
            }
            const auto nap = hbfsim::device::wait_sleep_ns(
                now, target, kWaitBackoffCapNs, kWaitSpinFloorNs);
            if (nap != 0) {
                __nanosleep(nap);
            }
        }
        (void)(state != nullptr ? device_fetch_add(&state->fast_requests, 1) : system_fetch_add(&header->fast_requests, 1));
        (void)(state != nullptr ? device_fetch_add(&state->fast_modeled_ns,
                               request.service_ns) : system_fetch_add(&header->fast_modeled_ns, request.service_ns));
        return {.status = RequestStatus::Ready, .admitted = 1};
    }

    const auto base_latency = operation == 0 ? header->read_latency_ns
                                             : header->program_latency_ns;
    const auto transfer_ns = hbfsim::device::fast_transfer_ns(
        media.bytes, header->aggregate_bandwidth_bytes_per_s);
    if (base_latency == 0 || transfer_ns == 0 || header->time_scale == 0) {
        return {.status = RequestStatus::Unsupported, .admitted = 0};
    }
    const auto arrival = gpu_time_ns();
    const auto base_scaled = hbfsim::device::saturating_multiply(
        base_latency, header->time_scale);
    const auto transfer_scaled = hbfsim::device::saturating_multiply(
        transfer_ns, header->time_scale);
    auto tail = (state != nullptr ? device_acquire(&state->fast_channel_tail_ns) : system_acquire(&header->fast_channel_tail_ns));
    std::uint64_t transfer_target = 0;
    for (;;) {
        const auto transfer_start = tail > arrival ? tail : arrival;
        transfer_target = hbfsim::device::saturating_add(
            transfer_start, transfer_scaled);
        auto expected = tail;
        if ((state != nullptr ? device_compare_exchange(&state->fast_channel_tail_ns, expected, transfer_target) : system_compare_exchange(&header->fast_channel_tail_ns, expected, transfer_target))) {
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
    // Same clamp as the empirical path above: never sleep past `target`.
    while (gpu_time_ns() < target) {
        const auto now = gpu_time_ns();
        if (now >= deadline) {
            FirstFaultObservation observed{};
            observed.reason = hbfsim::device::FirstFaultReason::FastWait;
            observed.phase = hbfsim::device::FirstFaultPhase::FastTarget;
            observed.status = RequestStatus::Timeout;
            observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                                  hbfsim::device::FirstFaultHasDeadline |
                                  hbfsim::device::FirstFaultHasTarget;
            observed.arrival_ns = arrival;
            observed.deadline_ns = deadline;
            observed.target_ns = target;
            first_fault_record(header, state, header->control_generation, observed);
            return {.status = RequestStatus::Timeout, .admitted = 1};
        }
        if (system_acquire(&header->shutdown) != 0 ||
            system_acquire(&header->fault) != 0) {
            FirstFaultObservation observed{};
            observed.reason = hbfsim::device::FirstFaultReason::Liveness;
            observed.phase = hbfsim::device::FirstFaultPhase::FastTarget;
            observed.status = RequestStatus::DaemonLost;
            observed.valid_bits = hbfsim::device::FirstFaultHasArrival |
                                  hbfsim::device::FirstFaultHasDeadline |
                                  hbfsim::device::FirstFaultHasTarget;
            observed.arrival_ns = arrival;
            observed.deadline_ns = deadline;
            observed.target_ns = target;
            first_fault_record(header, state, header->control_generation, observed);
            return {.status = RequestStatus::DaemonLost, .admitted = 1};
        }
        const auto nap = hbfsim::device::wait_sleep_ns(
            now, target, kWaitBackoffCapNs, kWaitSpinFloorNs);
        if (nap != 0) {
            __nanosleep(nap);
        }
    }
    (void)(state != nullptr ? device_fetch_add(&state->fast_requests, 1) : system_fetch_add(&header->fast_requests, 1));
    const auto modeled_ns = hbfsim::device::fast_service_ns(
        base_latency, media.bytes,
        header->aggregate_bandwidth_bytes_per_s);
    (void)(state != nullptr
               ? device_fetch_add(&state->fast_modeled_ns, modeled_ns)
               : system_fetch_add(&header->fast_modeled_ns, modeled_ns));
    return {.status = RequestStatus::Ready, .admitted = 1};
}

}  // namespace

extern "C" __device__ hbfsim::device::ResolveResult
__hbfsim_resolve(std::uint64_t address, std::uint32_t bytes,
                 std::uint32_t operation)
{
    const bool accounting = access_accounting_enabled();
    if (accounting) {
        account_add(&__hbfsim_access_accounting_counters.supported_accesses, 1);
        account_add(&__hbfsim_access_accounting_counters.supported_bytes, bytes);
    }
    const auto control_address =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control));
    const auto expected_generation =
        system_acquire(reinterpret_cast<const unsigned long long*>(
            &__hbfsim_control_generation));
    auto* mutable_control =
        reinterpret_cast<SharedControlHeader*>(control_address);
    auto* timing_state =
        mutable_control != nullptr &&
                mutable_control->producer_mode ==
                    hbfsim::device::kProducerModeGpuExclusive
            ? device_timing_state(mutable_control, expected_generation)
            : nullptr;
    // An unbound module must preserve ordinary HBM semantics. Accounting cannot
    // infer whether the address overlaps a registration without the control
    // range table, so keep this path explicitly unclassified.
    if (control_address == 0) {
        if (accounting) {
            account_add(&__hbfsim_access_accounting_counters.unclassified_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.unclassified_bytes, bytes);
        }
        return fail(address, bytes == 0 ? RequestStatus::Unsupported
                                        : RequestStatus::Ready);
    }
    if (expected_generation == 0 || bytes == 0) {
        if (accounting) {
            account_add(&__hbfsim_access_accounting_counters.unclassified_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.unclassified_bytes, bytes);
        }
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
        if (accounting) {
            account_add(&__hbfsim_access_accounting_counters.unclassified_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.unclassified_bytes, bytes);
        }
        return fail(address, RequestStatus::Unsupported);
    }
    const auto count = system_acquire(&header->range_count);
    if (count > hbfsim::device::kRangeCapacity) {
        if (accounting) {
            account_add(&__hbfsim_access_accounting_counters.unclassified_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.unclassified_bytes, bytes);
        }
        return fail(address, RequestStatus::Unsupported);
    }
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
    const auto chain_config = __hbfsim_eval_chain_diagnostic_config;
    const auto chain_experiment = hbfsim::device::eval_chain_diagnostic_action(
        chain_config, __hbfsim_eval_delay_config.magic);
    if (chain_experiment ==
            hbfsim::device::EvalChainDiagnosticAction::Reject ||
        (chain_experiment ==
             hbfsim::device::EvalChainDiagnosticAction::Apply &&
         !hbfsim::device::eval_chain_launch_matches(
             chain_config, gridDim.x, gridDim.y, gridDim.z, blockDim.x,
             blockDim.y, blockDim.z))) {
        if (accounting) {
            account_add(&__hbfsim_access_accounting_counters.unclassified_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.unclassified_bytes, bytes);
        }
        return fail(address, RequestStatus::Unsupported);
    }
    const auto chain_producer =
        chain_experiment == hbfsim::device::EvalChainDiagnosticAction::Apply
            ? hbfsim::device::eval_chain_producer(
                  chain_config, blockIdx.x, blockIdx.y, blockIdx.z,
                  threadIdx.x, threadIdx.y, threadIdx.z)
            : hbfsim::device::EvalChainProducer{};
    if (chain_experiment ==
            hbfsim::device::EvalChainDiagnosticAction::Apply &&
        chain_producer.valid == 0) {
        if (accounting) {
            account_add(&__hbfsim_access_accounting_counters.unclassified_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.unclassified_bytes, bytes);
        }
        return fail(address, RequestStatus::Unsupported);
    }
#endif
    const auto* ranges = reinterpret_cast<const SharedRangeRecord*>(
        reinterpret_cast<const std::byte*>(header) + header->range_offset);
    const auto* range = find_range(ranges, count, address);
    if (range == nullptr || address < range->base ||
        address - range->base >= range->length) {
        // find_range keys on the START address only. An access that begins
        // below every registered range but whose span reaches into one would
        // otherwise fall through to the bypass below and run at native speed
        // on HBF-backed bytes, counted as bypass_bytes and never modeled.
        // The mirror case -- starts inside, ends past the end -- is already
        // rejected by media_descriptor/access_supported, and the store side
        // already rejects any overlap in timing_future_native_store_span.
        // This makes the load side agree with both.
        const auto index = hbfsim::device::find_range_index(ranges, count, address);
        const auto successor = index == count ? 0U : index + 1U;
        const SharedRangeRecord* overlap =
            successor < count && hbfsim::device::range_overlaps(
                ranges[successor], address, bytes) ? &ranges[successor] : nullptr;
        if (overlap != nullptr) {
            if (accounting) {
                account_add(&__hbfsim_access_accounting_counters.in_range_accesses, 1);
                account_add(&__hbfsim_access_accounting_counters.in_range_intersection_bytes,
                            range_intersection_bytes(*overlap, address, bytes));
                account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_accesses, 1);
                account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_bytes,
                            range_intersection_bytes(*overlap, address, bytes));
            }
            return fail(address, RequestStatus::Unsupported);
        }
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
        if (chain_experiment ==
            hbfsim::device::EvalChainDiagnosticAction::Apply) {
            const auto event_class = hbfsim::device::eval_chain_event_class(
                chain_config, false, address, bytes, operation);
            const auto status =
                event_class == hbfsim::device::EvalChainEventClass::Rejected
                    ? RequestStatus::Unsupported
                    : RequestStatus::Ready;
            const auto observed_ns = gpu_time_ns();
            if (!eval_chain_record(chain_config, chain_producer, event_class,
                                   address, bytes, operation, observed_ns,
                                   observed_ns, status) ||
                status != RequestStatus::Ready) {
                if (accounting) {
                    account_add(&__hbfsim_access_accounting_counters.unclassified_accesses, 1);
                    account_add(&__hbfsim_access_accounting_counters.unclassified_bytes,
                                bytes);
                }
                return fail(address, RequestStatus::Unsupported);
            }
        }
#endif
        if (__hbfsim_eval_delay_config.magic != 0) {
            (void)system_fetch_add(&__hbfsim_eval_delay_counters.bypass_accesses, 1);
            (void)system_fetch_add(&__hbfsim_eval_delay_counters.bypass_bytes, bytes);
        }
        if (accounting) {
            account_add(&__hbfsim_access_accounting_counters.native_out_of_range_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.native_out_of_range_bytes, bytes);
        }
        return fail(address, RequestStatus::Ready);
    }
    const auto intersection = range_intersection_bytes(*range, address, bytes);
    if (accounting) {
        account_add(&__hbfsim_access_accounting_counters.in_range_accesses, 1);
        account_add(&__hbfsim_access_accounting_counters.in_range_intersection_bytes,
                    intersection);
    }
    if (header->producer_mode ==
            hbfsim::device::kProducerModeGpuExclusive &&
        timing_state == nullptr) {
        FirstFaultObservation observed{};
        observed.reason = hbfsim::device::FirstFaultReason::GenerationValidation;
        observed.phase = hbfsim::device::FirstFaultPhase::Validation;
        observed.status = RequestStatus::DaemonLost;
        first_fault_record(header, nullptr, expected_generation, observed);
        if (accounting) {
            account_add(&__hbfsim_access_accounting_counters.failed_preissue_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.failed_preissue_bytes,
                        intersection);
        }
        return fail(address, RequestStatus::DaemonLost);
    }
    const auto media = hbfsim::device::media_descriptor(
        *range, address, bytes, operation);
    if (!media.valid) {
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
        if (chain_experiment ==
            hbfsim::device::EvalChainDiagnosticAction::Apply) {
            const auto observed_ns = gpu_time_ns();
            (void)eval_chain_record(
                chain_config, chain_producer,
                hbfsim::device::EvalChainEventClass::Rejected, address, bytes,
                operation, observed_ns, observed_ns,
                RequestStatus::Unsupported);
        }
#endif
        if (accounting)
            account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_accesses, 1);
        if (accounting)
            account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_bytes,
                        intersection);
        return fail(address, RequestStatus::Unsupported);
    }

#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
    if (chain_experiment ==
        hbfsim::device::EvalChainDiagnosticAction::Apply) {
        const auto event_class = hbfsim::device::eval_chain_event_class(
            chain_config, true, address, bytes, operation);
        if (event_class != hbfsim::device::EvalChainEventClass::CoveredLoad ||
            range->mode != 1 || header->time_scale != 1) {
            const auto observed_ns = gpu_time_ns();
            (void)eval_chain_record(chain_config, chain_producer,
                                    hbfsim::device::EvalChainEventClass::Rejected,
                                    address, bytes, operation, observed_ns,
                                    observed_ns, RequestStatus::Unsupported);
            if (accounting) {
                account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_accesses, 1);
                account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_bytes,
                            intersection);
            }
            return fail(address, RequestStatus::Unsupported);
        }
    }
#endif

    const auto experiment = hbfsim::device::eval_delay_action(
        __hbfsim_eval_delay_config, *range, operation, header->time_scale);
    if (experiment == hbfsim::device::EvalDelayAction::Reject) {
        (void)system_fetch_add(&__hbfsim_eval_delay_counters.rejected_accesses, 1);
        if (accounting) {
            account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_bytes,
                        intersection);
        }
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
        if (experiment == hbfsim::device::EvalDelayAction::Apply) {
            // This synchronous experiment isolates only fixed added latency;
            // no scalar transfer/serialization/empirical service is also paid.
            // D=0 retains validation/grouping/translation but executes no wait.
            const auto config = __hbfsim_eval_delay_config;
            const auto index = system_fetch_add(
                &__hbfsim_eval_delay_counters.covered_accesses, 1);
            (void)system_fetch_add(&__hbfsim_eval_delay_counters.covered_bytes, bytes);
            if (index >= config.trace_capacity) {
                (void)system_fetch_add(&__hbfsim_eval_delay_counters.trace_overflow, 1);
                resolution.status = RequestStatus::Unsupported;
            } else {
                // D=0 and positive D share the same safety checks, all outside
                // the interval. Cache the finite timeout before the first clock
                // sample; a short clock-only wait never polls mapped host RAM.
                const auto timeout_ns = system_acquire(&header->request_timeout_ns);
                const bool live = eval_delay_control_ready(header, expected_generation) &&
                                  timeout_ns != 0;
                resolution.admitted = live ? 1U : 0U;
                const auto interval = eval_delay_clock_wait(
                    live ? config.delay_ns : 0, timeout_ns);
                resolution.status = live ? interval.status : RequestStatus::DaemonLost;
                if (!eval_delay_control_ready(header, expected_generation)) {
                    resolution.status = RequestStatus::DaemonLost;
                }
                auto* traces = reinterpret_cast<hbfsim::device::EvalDelayTrace*>(config.trace_address);
                traces[index] = {std::uint64_t{blockIdx.x} * blockDim.x + threadIdx.x,
                                 address, interval.begin_ns, interval.finish_ns, config.delay_ns};
            }
        }
#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
        else if (chain_experiment ==
                 hbfsim::device::EvalChainDiagnosticAction::Apply) {
            // Preserve the legacy experiment's clock-only interval and both
            // liveness checks; only the storage destination is per-chain.
            const auto timeout_ns =
                system_acquire(&header->request_timeout_ns);
            const bool live =
                eval_delay_control_ready(header, expected_generation) &&
                timeout_ns != 0;
            resolution.admitted = live ? 1U : 0U;
            const auto interval = eval_delay_clock_wait(
                live ? chain_config.delay_ns : 0, timeout_ns);
            resolution.status =
                live ? interval.status : RequestStatus::DaemonLost;
            if (!eval_delay_control_ready(header, expected_generation)) {
                resolution.status = RequestStatus::DaemonLost;
            }
            if (!eval_chain_record(
                    chain_config, chain_producer,
                    hbfsim::device::EvalChainEventClass::CoveredLoad, address,
                    bytes, operation, interval.begin_ns, interval.finish_ns,
                    resolution.status)) {
                resolution.status = RequestStatus::Unsupported;
            }
        }
#endif
        else {
            resolution = range->mode == 1 && header->timing_model != 0
                         ? resolve_fast_or_hybrid(mutable_header, timing_state, *range, media, operation)
                         : resolve_leader(mutable_header, timing_state, *range, media, operation);
        }
    }
    auto status = __shfl_sync(
        group, static_cast<std::uint32_t>(resolution.status), leader);
    const auto admitted = __shfl_sync(group, resolution.admitted, leader);
    if (accounting) {
        if (admitted != 0) {
            account_add(&__hbfsim_access_accounting_counters.modeled_admitted_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.modeled_admitted_bytes,
                        intersection);
            if (status == static_cast<std::uint32_t>(RequestStatus::Ready))
                account_add(&__hbfsim_access_accounting_counters.service_completed_accesses, 1);
            else
                account_add(&__hbfsim_access_accounting_counters.failed_after_issue_accesses, 1);
            if (status == static_cast<std::uint32_t>(RequestStatus::Ready))
                account_add(&__hbfsim_access_accounting_counters.service_completed_bytes,
                            intersection);
            else
                account_add(&__hbfsim_access_accounting_counters.failed_after_issue_bytes,
                            intersection);
        } else if (status == static_cast<std::uint32_t>(RequestStatus::Unsupported)) {
            account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.unsupported_preissue_bytes,
                        intersection);
        } else {
            account_add(&__hbfsim_access_accounting_counters.failed_preissue_accesses, 1);
            account_add(&__hbfsim_access_accounting_counters.failed_preissue_bytes,
                        intersection);
        }
        if (static_cast<int>(lane_id()) == leader && admitted != 0)
            account_add(&__hbfsim_access_accounting_counters.service_requests, 1);
    }
    const auto frame = __shfl_sync(group, resolution.frame_address, leader);
    if (status != static_cast<std::uint32_t>(RequestStatus::Ready)) {
        return {.address = address, .status = status, .reserved = 0};
    }
    const auto translated =
        hbfsim::device::resolved_address(*range, address, frame);
    if (translated == 0) {
        if (accounting)
            account_add(&__hbfsim_access_accounting_counters.translation_failed_accesses, 1);
        if (accounting)
            account_add(&__hbfsim_access_accounting_counters.translation_failed_bytes,
                        intersection);
        return fail(address, RequestStatus::CopyError);
    }
    return {.address = translated, .status = status, .reserved = 0};
}

extern "C" __device__ void __hbfsim_fault(std::uint32_t status)
{
    const auto diagnostic = __hbfsim_first_fault_config;
    if (diagnostic.magic != hbfsim::device::kFirstFaultConfigMagic ||
        diagnostic.schema_version != hbfsim::device::kFirstFaultSchemaVersion ||
        diagnostic.struct_bytes != sizeof(diagnostic) ||
        diagnostic.enabled != 1) {
        asm volatile("trap;");
        return;
    }
    FirstFaultObservation observed{};
    observed.reason = hbfsim::device::FirstFaultReason::Other;
    observed.phase = hbfsim::device::FirstFaultPhase::FaultTrap;
    observed.status = status <= static_cast<std::uint32_t>(RequestStatus::DaemonLost)
                          ? static_cast<RequestStatus>(status)
                          : RequestStatus::IoError;
    // The generic trap may follow an early invalid-header path.  Do not
    // dereference control/sidecar here; detailed sites have already claimed
    // the record when those pointers were validated.
    first_fault_record(nullptr, nullptr, 0, observed);
    asm volatile("trap;");
}

#if defined(HBFSIM_ENABLE_TIMING_FUTURES) && HBFSIM_ENABLE_TIMING_FUTURES
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
namespace hbfsim::device {
constexpr std::uint64_t kEvalFutureDelayMagic = 0x4836465554444c59ULL;
constexpr std::uint32_t kEvalFutureDelayAbi = 1;
constexpr std::uint32_t kEvalFutureDelayRecords = 32;
constexpr std::uint32_t kEvalFutureDelayRecordStride = 128;

struct alignas(8) EvalFutureDelayConfig {
    std::uint64_t magic;
    std::uint32_t abi_version;
    std::uint32_t struct_bytes;
    std::uint32_t enabled;
    std::uint32_t expected_instruction_id;
    std::uint64_t delay_ns;
    std::uint64_t launch_epoch;
    std::uint64_t input_base;
    std::uint64_t input_bytes;
    std::uint64_t records_address;
    std::uint64_t records_bytes;
    std::uint32_t record_count;
    std::uint32_t record_stride;
    std::uint32_t grid_x;
    std::uint32_t block_x;
    std::uint32_t work_count;
    std::uint32_t reserved;
};

struct alignas(8) EvalFutureDelayRecord {
    std::uint64_t launch_epoch;
    std::uint64_t configured_delay_ns;
    std::uint64_t helper_entry_ns;
    std::uint64_t arrival_ns;
    std::uint64_t helper_issue_exit_ns;
    std::uint64_t native_instruction_after_ns;
    std::uint64_t work_begin_ns;
    std::uint64_t work_end_ns;
    std::uint64_t wait_enter_ns;
    std::uint64_t wait_exit_ns;
    std::uint64_t consumer_after_ns;
    std::uint64_t ready_ns;
    std::uint64_t reservation_id;
    std::uint32_t lane;
    std::uint32_t status;
    std::uint32_t valid_bits;
    std::uint32_t work_count;
    std::uint64_t output_bits;
};
static_assert(sizeof(EvalFutureDelayConfig) == 96);
static_assert(offsetof(EvalFutureDelayConfig, delay_ns) == 24);
static_assert(offsetof(EvalFutureDelayConfig, records_address) == 56);
static_assert(offsetof(EvalFutureDelayConfig, work_count) == 88);
static_assert(sizeof(EvalFutureDelayRecord) == 128);
static_assert(offsetof(EvalFutureDelayRecord, helper_entry_ns) == 16);
static_assert(offsetof(EvalFutureDelayRecord, wait_enter_ns) == 64);
static_assert(offsetof(EvalFutureDelayRecord, reservation_id) == 96);
static_assert(offsetof(EvalFutureDelayRecord, output_bits) == 120);
} // namespace hbfsim::device

extern "C" __device__ hbfsim::device::EvalFutureDelayConfig
    __hbfsim_eval_future_delay_config_v1 = {};
#endif
extern "C" __device__ __constant__ hbfsim::timing_future::ModuleRequirements
    __hbfsim_timing_future_helper_abi_v1 = {};
extern "C" __device__ hbfsim::timing_future::ModuleConfig
    __hbfsim_timing_future_config_v1 = {};
extern "C" __device__ hbfsim::timing_future::Counters
    __hbfsim_timing_future_counters_v1 = {};
// CUDA module lifetime owns this finite allocation. The host binds its exact
// symbol address/size, and reserves a conservative cumulative launch budget.
extern "C" __device__ hbfsim::timing_future::Trace
    __hbfsim_timing_future_trace_v1[hbfsim::timing_future::kTraceCapacity] = {};

namespace {
namespace future = hbfsim::timing_future;

#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
constexpr std::uint32_t kFutureDelayHelperEntry = 1U << 0;
constexpr std::uint32_t kFutureDelayArrival = 1U << 1;
constexpr std::uint32_t kFutureDelayIssueExit = 1U << 2;
constexpr std::uint32_t kFutureDelayWaitEnter = 1U << 6;
constexpr std::uint32_t kFutureDelayWaitExit = 1U << 7;

struct FutureDelayBinding {
    hbfsim::device::EvalFutureDelayRecord* record{nullptr};
    bool requested{false};
    bool valid{false};
};

__device__ bool future_delay_record_is_zero(
    const hbfsim::device::EvalFutureDelayRecord& record)
{
    return record.launch_epoch == 0 && record.configured_delay_ns == 0 &&
        record.helper_entry_ns == 0 && record.arrival_ns == 0 &&
        record.helper_issue_exit_ns == 0 &&
        record.native_instruction_after_ns == 0 && record.work_begin_ns == 0 &&
        record.work_end_ns == 0 && record.wait_enter_ns == 0 &&
        record.wait_exit_ns == 0 && record.consumer_after_ns == 0 &&
        record.ready_ns == 0 && record.reservation_id == 0 &&
        record.lane == 0 && record.status == 0 && record.valid_bits == 0 &&
        record.work_count == 0 && record.output_bits == 0;
}

__device__ bool future_delay_config_is_zero(
    const hbfsim::device::EvalFutureDelayConfig& config)
{
    return config.magic == 0 && config.abi_version == 0 &&
        config.struct_bytes == 0 && config.enabled == 0 &&
        config.expected_instruction_id == 0 && config.delay_ns == 0 &&
        config.launch_epoch == 0 && config.input_base == 0 &&
        config.input_bytes == 0 && config.records_address == 0 &&
        config.records_bytes == 0 && config.record_count == 0 &&
        config.record_stride == 0 && config.grid_x == 0 &&
        config.block_x == 0 && config.work_count == 0 && config.reserved == 0;
}

__device__ FutureDelayBinding future_delay_prepare(
    std::uint64_t address, std::uint32_t bytes, std::uint32_t instruction)
{
    using Config = hbfsim::device::EvalFutureDelayConfig;
    using Record = hbfsim::device::EvalFutureDelayRecord;
    const auto config = __hbfsim_eval_future_delay_config_v1;
    if (future_delay_config_is_zero(config)) return {};
    FutureDelayBinding result{nullptr, true, false};
    const auto lane = lane_id();
    if (config.magic != hbfsim::device::kEvalFutureDelayMagic ||
        config.abi_version != hbfsim::device::kEvalFutureDelayAbi ||
        config.struct_bytes != sizeof(Config) || config.enabled != 1 ||
        config.expected_instruction_id != instruction || bytes != 4 ||
        (config.delay_ns != 0 && config.delay_ns != 20'000) ||
        !config.launch_epoch || !config.input_base || config.input_bytes != 128 ||
        config.input_base % alignof(std::uint32_t) ||
        !config.records_address || config.records_address % alignof(Record) ||
        config.records_bytes != 32ULL * sizeof(Record) ||
        config.record_count != hbfsim::device::kEvalFutureDelayRecords ||
        config.record_stride != sizeof(Record) || config.grid_x != 1 ||
        config.block_x != 32 ||
        (config.work_count != 0 && config.work_count != 4096) || config.reserved ||
        gridDim.x != 1 || gridDim.y != 1 || gridDim.z != 1 ||
        blockDim.x != 32 || blockDim.y != 1 || blockDim.z != 1 || lane >= 32 ||
        config.input_base > UINT64_MAX - config.input_bytes ||
        config.records_address > UINT64_MAX - config.records_bytes ||
        address > UINT64_MAX - bytes)
        return result;
    const auto input_end = config.input_base + config.input_bytes;
    const auto records_end = config.records_address + config.records_bytes;
    if (!(input_end <= config.records_address ||
          records_end <= config.input_base) ||
        address != config.input_base + std::uint64_t{lane} * sizeof(std::uint32_t) ||
        address + bytes > input_end)
        return result;
    auto* records = reinterpret_cast<Record*>(
        static_cast<std::uintptr_t>(config.records_address));
    auto* record = &records[lane];
    if (!future_delay_record_is_zero(*record)) return result;
    result.record = record;
    result.valid = true;
    return result;
}

// The launch configuration is frozen until the kernel returns. Preparation is
// read-only: failed issue/overflow paths must not partially initialize a record.
// This commit remains after the true issue clock and retains all observer stores.
__device__ void future_delay_commit(
    hbfsim::device::EvalFutureDelayRecord* record, std::uint64_t arrival)
{
    const auto config = __hbfsim_eval_future_delay_config_v1;
    const auto lane = lane_id();
    record->launch_epoch = config.launch_epoch;
    record->configured_delay_ns = config.delay_ns;
    record->helper_entry_ns = 0;
    record->arrival_ns = arrival;
    record->helper_issue_exit_ns = 0;
    record->ready_ns = 0;
    record->reservation_id = 0;
    record->lane = lane;
    record->status = future::kPending;
    record->work_count = config.work_count;
    record->valid_bits = kFutureDelayArrival;
}

__device__ hbfsim::device::EvalFutureDelayRecord* future_delay_existing(
    std::uint32_t instruction, std::uint32_t bytes)
{
    using Config = hbfsim::device::EvalFutureDelayConfig;
    using Record = hbfsim::device::EvalFutureDelayRecord;
    const auto config = __hbfsim_eval_future_delay_config_v1;
    const auto lane = lane_id();
    if (config.magic != hbfsim::device::kEvalFutureDelayMagic ||
        config.abi_version != hbfsim::device::kEvalFutureDelayAbi ||
        config.struct_bytes != sizeof(Config) || config.enabled != 1 ||
        config.expected_instruction_id != instruction || bytes != 4 ||
        (config.delay_ns != 0 && config.delay_ns != 20'000) ||
        !config.launch_epoch || !config.input_base || config.input_bytes != 128 ||
        config.input_base > UINT64_MAX-config.input_bytes ||
        lane >= 32 || !config.records_address ||
        config.records_address % alignof(Record) ||
        config.records_address > UINT64_MAX-config.records_bytes ||
        config.records_bytes != 32ULL * sizeof(Record) ||
        config.record_count != 32 || config.record_stride != sizeof(Record) ||
        config.grid_x != 1 || config.block_x != 32 ||
        (config.work_count != 0 && config.work_count != 4096) || config.reserved ||
        gridDim.x != 1 || gridDim.y != 1 || gridDim.z != 1 ||
        blockDim.x != 32 || blockDim.y != 1 || blockDim.z != 1)
        return nullptr;
    const auto input_end=config.input_base+config.input_bytes;
    const auto records_end=config.records_address+config.records_bytes;
    if (!(input_end<=config.records_address || records_end<=config.input_base))
        return nullptr;
    auto* record = reinterpret_cast<Record*>(
        static_cast<std::uintptr_t>(config.records_address)) + lane;
    if (record->launch_epoch != config.launch_epoch || record->lane != lane ||
        record->work_count != config.work_count ||
        record->configured_delay_ns != config.delay_ns ||
        !(record->valid_bits & kFutureDelayArrival))
        return nullptr;
    return record;
}
#endif

__device__ SharedControlHeader* future_header()
{
    const auto& config=__hbfsim_timing_future_config_v1;
    if (!future::valid_trace_span(config) ||
        blockDim.x>config.maximum_block_threads || blockDim.y>config.maximum_block_threads ||
        blockDim.z>config.maximum_block_threads ||
        std::uint64_t{blockDim.x}*blockDim.y*blockDim.z>config.maximum_block_threads) return nullptr;
    const auto alias=system_acquire(&__hbfsim_control);
    const auto generation=system_acquire(&__hbfsim_control_generation);
    if (alias!=config.control_alias || generation!=config.control_generation) return nullptr;
    auto* h=reinterpret_cast<SharedControlHeader*>(alias);
    if (h->magic!=hbfsim::device::kControlMagic ||
        h->abi_version!=hbfsim::device::kControlAbiVersion ||
        h->producer_mode==hbfsim::device::kProducerModeGpuExclusive ||
        h->header_bytes!=sizeof(*h) || h->range_capacity!=hbfsim::device::kRangeCapacity ||
        !hbfsim::device::valid_ring_capacity(h->ring_capacity) || h->page_capacity!=h->ring_capacity ||
        h->range_offset!=sizeof(*h) ||
        h->request_offset!=sizeof(*h)+sizeof(SharedRangeRecord)*hbfsim::device::kRangeCapacity ||
        h->completion_offset!=h->request_offset+sizeof(SharedRequestSlot)*h->ring_capacity ||
        h->page_offset!=h->completion_offset+sizeof(SharedCompletionSlot)*h->ring_capacity ||
        h->region_bytes!=h->page_offset+sizeof(hbfsim::device::PageEntry)*h->ring_capacity ||
        system_acquire(&h->control_generation)!=generation ||
        system_acquire(&h->range_count)>hbfsim::device::kRangeCapacity ||
        !future::derive_capabilities(h->timing_model,h->empirical_flags,h->time_scale,
            h->read_latency_ns,h->program_latency_ns,h->aggregate_bandwidth_bytes_per_s,
            h->request_timeout_ns).bits) return nullptr;
    return h;
}

__device__ std::uint32_t future_liveness(const SharedControlHeader* h,
    std::uint64_t generation, std::uint64_t now, WaitState* observation=nullptr)
{
    if (!h || system_acquire(&h->control_generation)!=generation) return future::kUnsupported;
    if (system_acquire(&h->shutdown) || system_acquire(&h->fault)) return future::kDaemonLost;
    const auto heartbeat=system_acquire(&h->heartbeat_ns);
    if (!heartbeat || !h->heartbeat_timeout_ns) return future::kDaemonLost;
    if (observation) {
        if (heartbeat!=observation->heartbeat_value) {
            observation->heartbeat_value=heartbeat;observation->heartbeat_observed_ns=now;
        } else if (now-observation->heartbeat_observed_ns>=h->heartbeat_timeout_ns)
            return future::kDaemonLost;
    }
    return 0;
}

__device__ bool future_trace(const future::DeviceTimingFutureV1& f,
    const future::TimingFutureLaneMetadataV1& m,std::uint32_t event,std::uint64_t now)
{
    const auto config=__hbfsim_timing_future_config_v1;
    auto& counters=__hbfsim_timing_future_counters_v1;
    if (!future::valid_trace_span(config) || config.control_alias!=f.control_alias ||
        config.control_generation!=f.control_generation) return false;
    auto index=system_acquire(&counters.trace_count);
    for (;;) {
        if (index>=config.trace_capacity) {
            (void)system_fetch_add(&counters.trace_overflow,1);return false;
        }
        auto expected=index;
        if (system_compare_exchange(&counters.trace_count,expected,index+1)) break;
        index=expected;
        if (EvalDelayClock{}()>=f.deadline_ns) {
            (void)system_fetch_add(&counters.trace_overflow,1);return false;
        }
    }
    auto* traces=reinterpret_cast<future::Trace*>(config.trace_address);
    traces[index]={f.reservation_id,f.original_address,f.issue_ns,f.ready_ns,now,
        m.instruction_id,m.bytes,lane_id(),m.group_mask,event,f.status};
    return true;
}

__device__ void future_account(SharedControlHeader* h,const future::DeviceTimingFutureV1& f,
    const future::TimingFutureLaneMetadataV1& m,const future::Transition& t)
{
    auto& c=__hbfsim_timing_future_counters_v1;
    const auto delta=future::accounting_delta(t.events,lane_id(),m.group_leader);
    if (delta.model_ready) {
        (void)system_fetch_add(&c.model_ready,1);
        if (delta.groups_completed) {
            (void)system_fetch_add(&c.groups_completed,1);
            // The issue-time leader accounts once; there is no wait-time
            // collective with a mask whose lanes may have diverged.
            if (h) {
                const auto* ranges=reinterpret_cast<const SharedRangeRecord*>(
                    reinterpret_cast<const std::byte*>(h)+h->range_offset);
                const auto* range=find_range(ranges,system_acquire(&h->range_count),f.original_address);
                if (range) {
                    (void)system_fetch_add(&h->fast_requests,1);
                    (void)system_fetch_add(&h->fast_modeled_ns,hbfsim::device::fast_service_ns(
                        h->read_latency_ns,static_cast<std::uint32_t>(range->page_bytes),h->aggregate_bandwidth_bytes_per_s));
                }
            }
        }
    }
    if (delta.consumed) (void)system_fetch_add(&c.consumed,1);
    if (delta.drained) (void)system_fetch_add(&c.drained,1);
    if (delta.terminal_error) (void)system_fetch_add(&c.terminal_error,1);
    if (delta.terminal)
        system_fetch_sub_release(&c.pending,1);
}

__device__ std::uint32_t future_transition(future::DeviceTimingFutureV1& f,
    const future::TimingFutureLaneMetadataV1& m,std::uint32_t instruction,std::uint32_t bytes,
    SharedControlHeader* h,std::uint64_t now,std::uint32_t liveness,
    bool consume=false,future::WaitKind kind=future::WaitKind::Dependency)
{
    const auto alias=h ? reinterpret_cast<std::uint64_t>(h) : 0;
    const auto generation=h ? system_acquire(&h->control_generation) : 0;
    auto candidate=f;
    const auto tentative=consume ? future::consume_state(candidate,m,lane_id(),instruction,bytes,alias,generation,now,liveness,kind)
        : future::poll_state(candidate,m,lane_id(),instruction,bytes,alias,generation,now,liveness);
    const bool traced=!tentative.events || future_trace(candidate,m,tentative.events,now);
    const auto t=future::commit_transition(f,candidate,tentative,traced);
    future_account(h,f,m,t);
    return t.status;
}

template<class T> __device__ bool future_local(const T* pointer)
{
    return pointer && reinterpret_cast<std::uintptr_t>(pointer)%alignof(T)==0 && __isLocal(pointer);
}
} // namespace

extern "C" __device__ __noinline__ std::uint32_t
__hbfsim_timing_future_native_store_guard_v1(std::uint64_t address,std::uint32_t bytes)
{
    namespace tf=hbfsim::timing_future;
    auto* h=future_header();
    if(!h)return tf::kUnsupported;
    const auto generation=system_acquire(&h->control_generation);
    if(const auto status=future_liveness(h,generation,EvalDelayClock{}()))return status;
    const auto count=system_acquire(&h->range_count);
    const auto* ranges=reinterpret_cast<const SharedRangeRecord*>(
        reinterpret_cast<const std::byte*>(h)+h->range_offset);
    if(!hbfsim::device::timing_future_native_store_span(ranges,count,address,bytes))
        return tf::kUnsupported;
    // Range retirement synchronizes kernel completion; recheck generation and
    // liveness after the bounded scan before the caller performs its store.
    if(const auto status=future_liveness(h,generation,EvalDelayClock{}()))return status;
    return tf::kReady;
}

extern "C" __device__ __noinline__ hbfsim::timing_future::DeviceTimingFutureV1
__hbfsim_timing_future_issue_v1(std::uint64_t address,std::uint32_t bytes,
    std::uint32_t instruction,std::uint32_t old_state,
    hbfsim::timing_future::TimingFutureLaneMetadataV1* metadata)
{
    namespace tf=hbfsim::timing_future;
    auto& counters=__hbfsim_timing_future_counters_v1;
    tf::DeviceTimingFutureV1 f{};f.original_address=address;
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
    const auto diagnostic_helper_entry=EvalDelayClock{}();
    hbfsim::device::EvalFutureDelayRecord* diagnostic_record=nullptr;
#endif
    const auto reject=[&](std::uint32_t status) {
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
        if (diagnostic_record) {
            diagnostic_record->helper_issue_exit_ns=EvalDelayClock{}();
            diagnostic_record->status=status;
            diagnostic_record->valid_bits|=kFutureDelayIssueExit;
        }
#endif
        (void)system_fetch_add(&counters.rejected,1);f.state=tf::State::TerminalError;f.status=status;return f;
    };
    if (!future_local(metadata) || !tf::can_issue(static_cast<tf::State>(old_state)) ||
        !tf::valid_bytes(bytes) || instruction==UINT32_MAX || !address || address>UINT64_MAX-bytes)
        return reject(tf::kUnsupported);
    *metadata={1,32,instruction,bytes,0,32,0};
    auto* h=future_header();if(!h)return reject(tf::kUnsupported);
    f.control_alias=reinterpret_cast<std::uint64_t>(h);f.control_generation=system_acquire(&h->control_generation);
    const auto* ranges=reinterpret_cast<const SharedRangeRecord*>(reinterpret_cast<const std::byte*>(h)+h->range_offset);
    const auto* range=find_range(ranges,system_acquire(&h->range_count),address);
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
    // Observer-only reads leave the model interval; all native mechanism work,
    // including header/range lookup above, stays on its original side of issue.
    // helper_entry_ns still includes preparation, so full issue cost is visible.
    const auto diagnostic=future_delay_prepare(address,bytes,instruction);
#endif
    const auto arrival=EvalDelayClock{}();f.issue_ns=arrival;f.ready_ns=arrival;
    if (arrival>UINT64_MAX-h->request_timeout_ns) return reject(tf::kUnsupported);
    f.deadline_ns=arrival+h->request_timeout_ns;
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
    const bool diagnostic_arrival_valid = !diagnostic.requested ||
        (diagnostic.valid && arrival <= UINT64_MAX -
            __hbfsim_eval_future_delay_config_v1.delay_ns);
    diagnostic_record=diagnostic_arrival_valid ? diagnostic.record : nullptr;
    if (diagnostic_record) future_delay_commit(diagnostic_record,arrival);
    if (diagnostic.requested &&
        (!diagnostic.valid || !diagnostic_arrival_valid ||
         arrival+diagnostic_record->configured_delay_ns>=f.deadline_ns)) {
        // The transformed caller's native load follows this helper. A hard
        // diagnostic rejection keeps malformed configuration from reaching it.
        asm volatile("trap;");
        return reject(tf::kUnsupported);
    }
    if (diagnostic_record) {
        diagnostic_record->helper_entry_ns=diagnostic_helper_entry;
        diagnostic_record->valid_bits|=kFutureDelayHelperEntry;
    }
#endif
    if (const auto live=future_liveness(h,f.control_generation,arrival)) return reject(live);
    if (!range || address<range->base || address-range->base>=range->length) {
        // Same start-address-only lookup as __hbfsim_resolve; same fix. A span
        // straddling into a range is not native, so it must not be counted as
        // a native load.
        if (hbfsim::device::span_touches_any_range(
                ranges, system_acquire(&h->range_count), address, bytes)) {
            return reject(tf::kUnsupported);
        }
        f.state=tf::State::Native;f.status=tf::kReady;
        (void)system_fetch_add(&counters.native_loads,1);
        (void)system_fetch_add(&counters.native_bytes,bytes);return f;
    }
    const auto media=hbfsim::device::media_descriptor(*range,address,bytes,0);
    if (range->mode!=1 || !media.valid) return reject(tf::kUnsupported);
    const auto page=media.logical_address/media.bytes;
    const auto active=__activemask();
    const auto same_range=__match_any_sync(active,range->range_id);
    const auto low=__match_any_sync(active,static_cast<std::uint32_t>(page));
    const auto high=__match_any_sync(active,static_cast<std::uint32_t>(page>>32));
    const auto group=same_range & low & high;
    const auto leader=__ffs(static_cast<int>(group))-1;
    std::uint64_t ready=0,deadline=0,reservation=0,group_arrival=0;
    std::uint32_t status=tf::kPending;
    if (static_cast<int>(lane_id())==leader) {
        group_arrival=arrival;deadline=f.deadline_ns;
        WaitState watch{deadline,system_acquire(&h->heartbeat_ns),arrival};
        auto id=system_acquire(&counters.next_reservation);
        for (;;) {
            if (!id || id==UINT64_MAX) { status=tf::kUnsupported;break; }
            const auto now=EvalDelayClock{}();
            if ((status=future_liveness(h,f.control_generation,now,&watch))!=0) break;
            if(now>=deadline) {status=tf::kTimeout;break;}
            auto expected=id;
            if(system_compare_exchange(&counters.next_reservation,expected,id+1)) {reservation=id;break;}
            id=expected;
        }
        auto tail=system_acquire(&h->fast_channel_tail_ns);
        const auto transfer=hbfsim::device::fast_transfer_ns(media.bytes,h->aggregate_bandwidth_bytes_per_s);
        while (status==tf::kPending) {
            const auto now=EvalDelayClock{}();
            if ((status=future_liveness(h,f.control_generation,now,&watch))!=0) break;
            if (now>=deadline) {status=tf::kTimeout;break;}
            const auto r=tf::scalar_reservation(arrival,tail,h->read_latency_ns,transfer,h->request_timeout_ns);
            if(!r.valid) {status=tf::kUnsupported;break;}
            auto expected=tail;
            if(system_compare_exchange(&h->fast_channel_tail_ns,expected,r.transfer_end_ns)) {
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
                ready=diagnostic_record
                    ? arrival+diagnostic_record->configured_delay_ns
                    : r.ready_ns;
#else
                ready=r.ready_ns;
#endif
                (void)system_fetch_add(&h->fast_request_sequence,1);
                (void)system_fetch_add(&counters.groups_issued,1);break;
            }
            tail=expected;
        }
    }
    status=__shfl_sync(group,status,leader);
    if(status!=tf::kPending)return reject(status);
    f.issue_ns=__shfl_sync(group,group_arrival,leader);
    f.ready_ns=__shfl_sync(group,ready,leader);f.deadline_ns=__shfl_sync(group,deadline,leader);
    f.reservation_id=__shfl_sync(group,reservation,leader);f.state=tf::State::Issued;f.status=tf::kPending;
    *metadata={1,32,instruction,bytes,group,static_cast<std::uint32_t>(leader),f.reservation_id};
    (void)system_fetch_add(&counters.issued,1);(void)system_fetch_add(&counters.pending,1);
    if(!future_trace(f,*metadata,0,EvalDelayClock{}())) {
        const auto error=tf::fail_state(f,tf::kUnsupported);future_account(h,f,*metadata,error);
    }
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
    if (diagnostic_record) {
        diagnostic_record->arrival_ns=f.issue_ns;
        diagnostic_record->ready_ns=f.ready_ns;
        diagnostic_record->reservation_id=f.reservation_id;
        diagnostic_record->status=f.status;
        diagnostic_record->helper_issue_exit_ns=EvalDelayClock{}();
        diagnostic_record->valid_bits|=kFutureDelayIssueExit;
    }
#endif
    return f;
}

extern "C" __device__ __noinline__ std::uint32_t __hbfsim_timing_future_poll_v1(
    hbfsim::timing_future::DeviceTimingFutureV1* f,
    const hbfsim::timing_future::TimingFutureLaneMetadataV1* metadata,
    std::uint32_t instruction,std::uint32_t bytes)
{
    if (!future_local(f) || !future_local(metadata)) return hbfsim::timing_future::kUnsupported;
    auto* h=future_header();const auto now=EvalDelayClock{}();
    return future_transition(*f,*metadata,instruction,bytes,h,now,future_liveness(h,f->control_generation,now));
}

extern "C" __device__ __noinline__ hbfsim::timing_future::ConsumeResult
__hbfsim_timing_future_wait_v1(hbfsim::timing_future::DeviceTimingFutureV1* f,
    const hbfsim::timing_future::TimingFutureLaneMetadataV1* metadata,std::uint64_t native_bits,
    std::uint32_t instruction,std::uint32_t bytes,std::uint32_t wait_kind)
{
    namespace tf=hbfsim::timing_future;
    if (!future_local(f) || !future_local(metadata)) return {};
    const auto enter=EvalDelayClock{}();
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
    auto* diagnostic_record=future_delay_existing(instruction,bytes);
    const bool diagnostic_requested=
        !future_delay_config_is_zero(__hbfsim_eval_future_delay_config_v1);
    if (diagnostic_requested && !diagnostic_record)
        return {0,tf::kUnsupported,tf::State::TerminalError};
    if (diagnostic_record) {
        diagnostic_record->wait_enter_ns=enter;
        diagnostic_record->valid_bits|=kFutureDelayWaitEnter;
    }
#endif
    auto* h=future_header();
    WaitState watch{f->deadline_ns,h ? system_acquire(&h->heartbeat_ns) : 0,enter};
    for (;;) {
        h=future_header();const auto now=EvalDelayClock{}();
        const auto status=future_transition(*f,*metadata,instruction,bytes,h,now,
            future_liveness(h,f->control_generation,now,&watch),true,static_cast<tf::WaitKind>(wait_kind));
        if (status==tf::kPending) {
            // Sleep out the bulk of the wait, spin only the tail.
            //
            // Why this is not a micro-optimisation. A real HBF scoreboard
            // would make the consuming warp ineligible the moment it reaches
            // the consumer. A software future cannot: the warp has to be
            // selected at least once to check whether the value is ready. With
            // a sleep that happens a handful of times; with the zero-backoff
            // spin this loop used to run, it happens thousands of times across
            // one modeled access, and each pass re-reads the shared control
            // header over host-mapped memory -- the same region the host
            // service daemon is writing completions into. The polling then
            // costs issue slots and PCIe bandwidth that other warps needed to
            // hide their own latency, so the measured end-to-end time carries
            // both the injected delay and the cost of waiting for it, with no
            // way to separate them.
            //
            // wait_sleep_ns never sleeps past the target and never sleeps
            // more than half of what is left, so the liveness, deadline and
            // control-generation checks below still run at a bounded rate
            // rather than being skipped for the whole wait. When the target is
            // unset or already past it returns 0 and this stays a spin, which
            // is the correct behaviour while the reservation is still being
            // published.
            //
            // The target is the earlier of ready_ns and deadline_ns, not
            // ready_ns alone. ready_ns is a modeled completion time and
            // nothing clamps it to the deadline, so sleeping toward it could
            // push the timeout check below past the deadline by as much as a
            // whole nap -- the ISA caps one sleep at 1 ms. Waiting on the
            // earlier of the two keeps the timeout as prompt as it was before
            // this loop slept at all, which the zero-backoff spin it replaced
            // got right by accident.
            const auto limit = f->ready_ns < f->deadline_ns ? f->ready_ns
                                                            : f->deadline_ns;
            const auto nap = hbfsim::device::wait_sleep_ns(
                now, limit, kWaitBackoffCapNs, kWaitSpinFloorNs);
            if (nap != 0) {
                __nanosleep(nap);
            }
            continue;
        }
        if (status!=tf::kReady) {
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
            if (diagnostic_record) {
                diagnostic_record->wait_exit_ns=EvalDelayClock{}();
                diagnostic_record->status=status;
                diagnostic_record->valid_bits|=kFutureDelayWaitExit;
            }
#endif
            return {0,status,f->state};
        }
        // The value is an actual call input and output. C6 must verify the
        // optimized native-load -> wait-result -> consumer SASS dependency.
        asm volatile("mov.b64 %0, %0;" : "+l"(native_bits) : : "memory");
#if defined(HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_FUTURE_DELAY_DIAGNOSTIC
        if (diagnostic_record) {
            diagnostic_record->wait_exit_ns=EvalDelayClock{}();
            diagnostic_record->status=status;
            diagnostic_record->valid_bits|=kFutureDelayWaitExit;
        }
#endif
        return {native_bits,status,f->state};
    }
}
#endif

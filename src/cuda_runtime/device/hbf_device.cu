#include "hbf_device.cuh"

#include <cuda/atomic>
#include <cuda_runtime.h>

extern "C" __device__ unsigned long long __hbfsim_control = 0;
extern "C" __device__ unsigned long long __hbfsim_control_generation = 0;
extern "C" __device__ hbfsim::device::EvalDelayConfig
    __hbfsim_eval_delay_config = {};
extern "C" __device__ hbfsim::device::EvalDelayCounters
    __hbfsim_eval_delay_counters = {};
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
        if (__hbfsim_eval_delay_config.magic != 0) {
            (void)system_fetch_add(&__hbfsim_eval_delay_counters.bypass_accesses, 1);
            (void)system_fetch_add(&__hbfsim_eval_delay_counters.bypass_bytes, bytes);
        }
        return fail(address, RequestStatus::Ready);
    }
    const auto media = hbfsim::device::media_descriptor(
        *range, address, bytes, operation);
    if (!media.valid) {
        return fail(address, RequestStatus::Unsupported);
    }

    const auto experiment = hbfsim::device::eval_delay_action(
        __hbfsim_eval_delay_config, *range, operation, header->time_scale);
    if (experiment == hbfsim::device::EvalDelayAction::Reject) {
        (void)system_fetch_add(&__hbfsim_eval_delay_counters.rejected_accesses, 1);
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
        } else {
            resolution = range->mode == 1 && header->timing_model != 0
                         ? resolve_fast_or_hybrid(mutable_header, *range,
                                                  media, operation)
                         : resolve_leader(mutable_header, *range, media,
                                          operation);
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

extern "C" __device__ void __hbfsim_fault(std::uint32_t)
{
    asm volatile("trap;");
}

#if defined(HBFSIM_ENABLE_TIMING_FUTURES) && HBFSIM_ENABLE_TIMING_FUTURES
extern "C" __device__ __constant__ hbfsim::timing_future::ModuleRequirements
    __hbfsim_timing_future_helper_abi_v1 = {};
extern "C" __device__ hbfsim::timing_future::ModuleConfig
    __hbfsim_timing_future_config_v1 = {};
extern "C" __device__ hbfsim::timing_future::Counters
    __hbfsim_timing_future_counters_v1 = {};

namespace {
namespace future = hbfsim::timing_future;

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
    if (h->magic!=hbfsim::device::kControlMagic || h->abi_version!=4 ||
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

extern "C" __device__ __noinline__ hbfsim::timing_future::DeviceTimingFutureV1
__hbfsim_timing_future_issue_v1(std::uint64_t address,std::uint32_t bytes,
    std::uint32_t instruction,std::uint32_t old_state,
    hbfsim::timing_future::TimingFutureLaneMetadataV1* metadata)
{
    namespace tf=hbfsim::timing_future;
    auto& counters=__hbfsim_timing_future_counters_v1;
    tf::DeviceTimingFutureV1 f{};f.original_address=address;
    const auto reject=[&](std::uint32_t status) {
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
    const auto arrival=EvalDelayClock{}();f.issue_ns=arrival;f.ready_ns=arrival;
    if (arrival>UINT64_MAX-h->request_timeout_ns) return reject(tf::kUnsupported);
    f.deadline_ns=arrival+h->request_timeout_ns;
    if (const auto live=future_liveness(h,f.control_generation,arrival)) return reject(live);
    if (!range || address<range->base || address-range->base>=range->length) {
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
                ready=r.ready_ns;
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
    auto* h=future_header();
    WaitState watch{f->deadline_ns,h ? system_acquire(&h->heartbeat_ns) : 0,enter};
    for (;;) {
        h=future_header();const auto now=EvalDelayClock{}();
        const auto status=future_transition(*f,*metadata,instruction,bytes,h,now,
            future_liveness(h,f->control_generation,now,&watch),true,static_cast<tf::WaitKind>(wait_kind));
        if (status==tf::kPending) continue;
        if (status!=tf::kReady) return {0,status,f->state};
        // The value is an actual call input and output. C6 must verify the
        // optimized native-load -> wait-result -> consumer SASS dependency.
        asm volatile("mov.b64 %0, %0;" : "+l"(native_bits) : : "memory");
        return {native_bits,status,f->state};
    }
}
#endif

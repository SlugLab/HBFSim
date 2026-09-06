#pragma once

#include <cstddef>
#include <cstdint>
#if defined(HBFSIM_ENABLE_TIMING_FUTURES) && HBFSIM_ENABLE_TIMING_FUTURES
#include <hbfsim/timing_future_abi.hpp>
#endif

namespace hbfsim::device {

#if defined(__CUDACC__)
#define HBFSIM_HOST_DEVICE __host__ __device__
#else
#define HBFSIM_HOST_DEVICE
#endif

inline constexpr std::uint64_t kControlMagic = 0x48424653494d3031ULL;
inline constexpr std::uint32_t kControlAbiVersion = 4;
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

// Explicit known-delay experiment, module-local only. These records are NOT
// part of the shared control ABI. A zero magic retains production behavior.
inline constexpr std::uint64_t kEvalDelayMagic = 0x4556414c444c5931ULL;
struct EvalDelayConfig {
    std::uint64_t magic;
    std::uint64_t delay_ns;
    std::uint64_t trace_address;
    std::uint64_t trace_capacity;
};
struct EvalDelayCounters {
    std::uint64_t covered_accesses;
    std::uint64_t covered_bytes;
    std::uint64_t bypass_accesses;
    std::uint64_t bypass_bytes;
    std::uint64_t rejected_accesses;
    std::uint64_t trace_overflow;
};
struct EvalDelayTrace {
    std::uint64_t thread_id;
    std::uint64_t address;
    std::uint64_t wait_enter_ns;
    std::uint64_t wait_exit_ns;
    std::uint64_t delay_ns;
};
enum class EvalDelayAction { Off, Apply, Reject };
HBFSIM_HOST_DEVICE constexpr EvalDelayAction eval_delay_action(
    const EvalDelayConfig& config, const SharedRangeRecord& range,
    std::uint32_t operation, std::uint32_t time_scale)
{
    if (config.magic == 0) return EvalDelayAction::Off;
    if (config.magic != kEvalDelayMagic || config.delay_ns > 20'000 ||
        config.trace_address == 0 || config.trace_capacity == 0 ||
        range.mode != 1 || operation != 0 || time_scale != 1)
        return EvalDelayAction::Reject;
    return EvalDelayAction::Apply;
}
HBFSIM_HOST_DEVICE constexpr std::uint64_t eval_delay_remaining(
    std::uint64_t now, std::uint64_t begin, std::uint64_t delay)
{
    return now - begin >= delay ? 0 : delay - (now - begin);
}
// Internal experiment timing only; neither this result nor the clock callable
// is part of the shared control ABI. Timeout and delay are cached scalar values.
struct EvalDelayInterval {
    std::uint64_t begin_ns;
    std::uint64_t finish_ns;
    RequestStatus status;
};
template <typename Clock>
HBFSIM_HOST_DEVICE inline EvalDelayInterval eval_delay_clock_interval(
    std::uint64_t delay_ns, std::uint64_t timeout_ns, Clock clock)
{
    const auto begin = clock();
    auto finish = begin;
    auto status = timeout_ns == 0 ? RequestStatus::DaemonLost
                                 : RequestStatus::Ready;
    while (status == RequestStatus::Ready &&
           eval_delay_remaining(finish, begin, delay_ns) != 0) {
        if (finish - begin >= timeout_ns) {
            status = RequestStatus::Timeout;
            break;
        }
        finish = clock();
    }
    return {begin, finish, status};
}
static_assert(sizeof(EvalDelayConfig) == 32);
static_assert(sizeof(EvalDelayCounters) == 48);
static_assert(sizeof(EvalDelayTrace) == 40);

#if defined(HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC) && \
    HBFSIM_ENABLE_EVAL_CHAIN_DIAGNOSTIC
// Separate, default-off storage contract for the dependent-chain diagnostic.
// It is module-local and does not change EvalDelayConfig or the shared ABI.
inline constexpr std::uint64_t kEvalChainDiagnosticMagic =
    0x4556434841494e31ULL;
inline constexpr std::uint32_t kEvalChainDiagnosticVersion = 2;
inline constexpr std::uint64_t kEvalChainNoWriter = ~std::uint64_t{0};
inline constexpr std::uint64_t kEvalChainOutputBytes = 32;
inline constexpr std::uint64_t kEvalBlockOutputBytes = 24;

struct EvalChainDiagnosticConfig {
    std::uint64_t magic;
    std::uint32_t version;
    std::uint32_t config_bytes;
    std::uint64_t delay_ns;
    std::uint64_t launch_epoch;
    std::uint32_t grid_x;
    std::uint32_t grid_y;
    std::uint32_t grid_z;
    std::uint32_t block_x;
    std::uint32_t block_y;
    std::uint32_t block_z;
    std::uint32_t warps_per_block;
    std::uint32_t hops;
    std::uint64_t row_count;
    std::uint64_t storage_address;
    std::uint64_t storage_bytes;
    std::uint64_t row_stride;
    std::uint64_t trace_capacity;
    std::uint64_t chain_output_address;
    std::uint64_t chain_output_bytes;
    std::uint64_t block_output_address;
    std::uint64_t block_output_bytes;
};

enum class EvalChainEventClass : std::uint32_t {
    CoveredLoad = 1,
    ChainOutputStore = 2,
    BlockOutputStore = 3,
    Rejected = 4,
};

struct EvalChainEvent {
    std::uint64_t thread_id;
    std::uint64_t address;
    std::uint64_t order;
    std::uint64_t begin_ns;
    std::uint64_t end_ns;
    std::uint32_t bytes;
    std::uint32_t operation;
    std::uint32_t event_class;
    std::uint32_t status;
};

struct alignas(64) EvalChainRow {
    EvalDelayCounters counters;
    std::uint64_t event_count;
    std::uint64_t launch_epoch;
    std::uint64_t writer_thread_id;
    std::uint32_t writer_observed;
    std::uint32_t reserved;
};

struct EvalChainProducer {
    std::uint64_t row;
    std::uint64_t thread_id;
    std::uint32_t warp;
    std::uint32_t valid;
};

struct EvalChainStorage {
    std::uint64_t row_address;
    std::uint64_t trace_address;
    std::uint32_t valid;
    std::uint32_t reserved;
};

struct EvalChainSlot {
    std::uint64_t address;
    std::uint32_t valid;
    std::uint32_t reserved;
};

enum class EvalChainDiagnosticAction { Off, Apply, Reject };

HBFSIM_HOST_DEVICE constexpr bool eval_chain_checked_add(
    std::uint64_t left, std::uint64_t right, std::uint64_t* result)
{
    if (result == nullptr || left > ~std::uint64_t{0} - right) return false;
    *result = left + right;
    return true;
}

HBFSIM_HOST_DEVICE constexpr bool eval_chain_checked_multiply(
    std::uint64_t left, std::uint64_t right, std::uint64_t* result)
{
    if (result == nullptr ||
        (right != 0 && left > ~std::uint64_t{0} / right)) return false;
    *result = left * right;
    return true;
}

HBFSIM_HOST_DEVICE constexpr bool eval_chain_warp_count_supported(
    std::uint32_t warps)
{
    return warps == 1 || warps == 2 || warps == 4 || warps == 8 ||
           warps == 16;
}

HBFSIM_HOST_DEVICE constexpr bool eval_chain_span_valid(
    std::uint64_t address, std::uint64_t bytes)
{
    std::uint64_t end = 0;
    return address != 0 && bytes != 0 &&
           eval_chain_checked_add(address, bytes, &end) && end > address;
}

HBFSIM_HOST_DEVICE constexpr bool eval_chain_spans_disjoint(
    std::uint64_t left_address, std::uint64_t left_bytes,
    std::uint64_t right_address, std::uint64_t right_bytes)
{
    std::uint64_t left_end = 0;
    std::uint64_t right_end = 0;
    return eval_chain_span_valid(left_address, left_bytes) &&
           eval_chain_span_valid(right_address, right_bytes) &&
           eval_chain_checked_add(left_address, left_bytes, &left_end) &&
           eval_chain_checked_add(right_address, right_bytes, &right_end) &&
           (left_end <= right_address || right_end <= left_address);
}

HBFSIM_HOST_DEVICE constexpr bool eval_chain_span_contains(
    std::uint64_t span_address, std::uint64_t span_bytes,
    std::uint64_t address, std::uint32_t bytes)
{
    std::uint64_t span_end = 0;
    std::uint64_t access_end = 0;
    return bytes != 0 && address >= span_address &&
           eval_chain_span_valid(span_address, span_bytes) &&
           eval_chain_checked_add(span_address, span_bytes, &span_end) &&
           eval_chain_checked_add(address, bytes, &access_end) &&
           access_end <= span_end;
}

HBFSIM_HOST_DEVICE constexpr bool eval_chain_config_valid(
    const EvalChainDiagnosticConfig& config,
    std::uint64_t legacy_eval_magic = 0)
{
    if (legacy_eval_magic != 0 || config.magic != kEvalChainDiagnosticMagic ||
        config.version != kEvalChainDiagnosticVersion ||
        config.config_bytes != sizeof(EvalChainDiagnosticConfig) ||
        config.delay_ns > 20'000 || config.launch_epoch == 0 ||
        config.grid_x == 0 || config.grid_y != 1 || config.grid_z != 1 ||
        config.block_y != 1 || config.block_z != 1 ||
        !eval_chain_warp_count_supported(config.warps_per_block) ||
        config.block_x != config.warps_per_block * 32 ||
        (config.hops != 1 && config.hops != 16 && config.hops != 64) ||
        config.trace_capacity != std::uint64_t{config.hops} + 7 ||
        config.storage_address == 0 ||
        config.storage_address % alignof(EvalChainRow) != 0 ||
        config.row_stride % alignof(EvalChainRow) != 0 ||
        config.chain_output_address % alignof(std::uint64_t) != 0 ||
        config.block_output_address % alignof(std::uint64_t) != 0) {
        return false;
    }
    std::uint64_t rows = 0;
    if (!eval_chain_checked_multiply(config.grid_x, config.warps_per_block,
                                     &rows) ||
        rows != config.row_count || rows > 0xffff'ffffULL) {
        return false;
    }
    std::uint64_t trace_bytes = 0;
    std::uint64_t row_bytes = 0;
    std::uint64_t storage_bytes = 0;
    std::uint64_t storage_end = 0;
    std::uint64_t chain_output_bytes = 0;
    std::uint64_t block_output_bytes = 0;
    return eval_chain_checked_multiply(config.trace_capacity,
                                       sizeof(EvalChainEvent), &trace_bytes) &&
           eval_chain_checked_add(sizeof(EvalChainRow), trace_bytes,
                                  &row_bytes) &&
           config.row_stride >= row_bytes &&
           eval_chain_checked_multiply(config.row_count, config.row_stride,
                                       &storage_bytes) &&
           storage_bytes == config.storage_bytes &&
           eval_chain_checked_add(config.storage_address,
                                  config.storage_bytes, &storage_end) &&
           storage_end > config.storage_address &&
           eval_chain_checked_multiply(config.row_count,
                                       kEvalChainOutputBytes,
                                       &chain_output_bytes) &&
           chain_output_bytes == config.chain_output_bytes &&
           eval_chain_checked_multiply(config.grid_x,
                                       kEvalBlockOutputBytes,
                                       &block_output_bytes) &&
           block_output_bytes == config.block_output_bytes &&
           eval_chain_spans_disjoint(config.storage_address,
                                     config.storage_bytes,
                                     config.chain_output_address,
                                     config.chain_output_bytes) &&
           eval_chain_spans_disjoint(config.storage_address,
                                     config.storage_bytes,
                                     config.block_output_address,
                                     config.block_output_bytes) &&
           eval_chain_spans_disjoint(config.chain_output_address,
                                     config.chain_output_bytes,
                                     config.block_output_address,
                                     config.block_output_bytes);
}

HBFSIM_HOST_DEVICE constexpr EvalChainDiagnosticAction
eval_chain_diagnostic_action(const EvalChainDiagnosticConfig& config,
                             std::uint64_t legacy_eval_magic)
{
    if (config.magic == 0) return EvalChainDiagnosticAction::Off;
    return eval_chain_config_valid(config, legacy_eval_magic)
               ? EvalChainDiagnosticAction::Apply
               : EvalChainDiagnosticAction::Reject;
}

HBFSIM_HOST_DEVICE constexpr bool eval_chain_launch_matches(
    const EvalChainDiagnosticConfig& config, std::uint32_t grid_x,
    std::uint32_t grid_y, std::uint32_t grid_z, std::uint32_t block_x,
    std::uint32_t block_y, std::uint32_t block_z)
{
    return eval_chain_config_valid(config) && grid_x == config.grid_x &&
           grid_y == config.grid_y && grid_z == config.grid_z &&
           block_x == config.block_x && block_y == config.block_y &&
           block_z == config.block_z;
}

HBFSIM_HOST_DEVICE constexpr EvalChainEventClass eval_chain_event_class(
    const EvalChainDiagnosticConfig& config, bool covered,
    std::uint64_t address, std::uint32_t bytes, std::uint32_t operation)
{
    if (!eval_chain_config_valid(config) || bytes == 0 || operation > 1)
        return EvalChainEventClass::Rejected;
    if (covered)
        return operation == 0 ? EvalChainEventClass::CoveredLoad
                              : EvalChainEventClass::Rejected;
    if (operation != 1) return EvalChainEventClass::Rejected;
    if (eval_chain_span_contains(config.chain_output_address,
                                 config.chain_output_bytes, address, bytes))
        return EvalChainEventClass::ChainOutputStore;
    if (eval_chain_span_contains(config.block_output_address,
                                 config.block_output_bytes, address, bytes))
        return EvalChainEventClass::BlockOutputStore;
    return EvalChainEventClass::Rejected;
}

// This maps supplied coordinates against an already validated declaration.
// A device caller must first compare all six actual gridDim/blockDim values
// with config and observe Apply from the actual legacy/new-mode state.
// This helper cannot establish that the declared geometry is the live launch.
HBFSIM_HOST_DEVICE constexpr EvalChainProducer eval_chain_producer(
    const EvalChainDiagnosticConfig& config, std::uint32_t block_index_x,
    std::uint32_t block_index_y, std::uint32_t block_index_z,
    std::uint32_t thread_index_x, std::uint32_t thread_index_y,
    std::uint32_t thread_index_z)
{
    if (!eval_chain_config_valid(config) || block_index_x >= config.grid_x ||
        block_index_y != 0 || block_index_z != 0 ||
        thread_index_x >= config.block_x || thread_index_y != 0 ||
        thread_index_z != 0 || (thread_index_x & 31U) != 0) {
        return {};
    }
    const auto warp = thread_index_x / 32;
    std::uint64_t row = 0;
    std::uint64_t thread_id = 0;
    if (!eval_chain_checked_multiply(block_index_x, config.warps_per_block,
                                     &row) ||
        !eval_chain_checked_add(row, warp, &row) || row >= config.row_count ||
        !eval_chain_checked_multiply(block_index_x, config.block_x,
                                     &thread_id) ||
        !eval_chain_checked_add(thread_id, thread_index_x, &thread_id)) {
        return {};
    }
    return {row, thread_id, warp, 1};
}

HBFSIM_HOST_DEVICE constexpr EvalChainStorage eval_chain_storage(
    const EvalChainDiagnosticConfig& config, std::uint64_t row)
{
    if (!eval_chain_config_valid(config) || row >= config.row_count) return {};
    std::uint64_t offset = 0;
    std::uint64_t row_address = 0;
    std::uint64_t trace_address = 0;
    if (!eval_chain_checked_multiply(row, config.row_stride, &offset) ||
        !eval_chain_checked_add(config.storage_address, offset, &row_address) ||
        !eval_chain_checked_add(row_address, sizeof(EvalChainRow),
                                &trace_address)) {
        return {};
    }
    return {row_address, trace_address, 1, 0};
}

HBFSIM_HOST_DEVICE constexpr EvalChainSlot eval_chain_slot(
    const EvalChainDiagnosticConfig& config, std::uint64_t row,
    std::uint64_t actual_event_index)
{
    const auto storage = eval_chain_storage(config, row);
    if (storage.valid == 0 || actual_event_index >= config.trace_capacity)
        return {};
    std::uint64_t offset = 0;
    std::uint64_t address = 0;
    if (!eval_chain_checked_multiply(actual_event_index,
                                     sizeof(EvalChainEvent), &offset) ||
        !eval_chain_checked_add(storage.trace_address, offset, &address)) {
        return {};
    }
    return {address, 1, 0};
}

static_assert(sizeof(EvalChainDiagnosticConfig) == 136);
static_assert(sizeof(EvalChainEvent) == 56);
static_assert(sizeof(EvalChainRow) == 128);
static_assert(sizeof(EvalChainProducer) == 24);
static_assert(sizeof(EvalChainStorage) == 24);
static_assert(sizeof(EvalChainSlot) == 16);
#endif

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

static_assert(sizeof(SharedControlHeader) == 384);
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
static_assert(offsetof(SharedControlHeader, read_latency_ns) == 176);
static_assert(offsetof(SharedControlHeader, fast_request_sequence) == 200);
static_assert(offsetof(SharedControlHeader, empirical_burst_state) == 256);
static_assert(offsetof(SharedControlHeader, empirical_cumulative_ns) == 264);
static_assert(offsetof(SharedControlHeader, empirical_breakpoint_pages) == 312);
static_assert(offsetof(SharedControlHeader, empirical_point_count) == 336);
static_assert(offsetof(SharedControlHeader, empirical_flags) == 340);

HBFSIM_HOST_DEVICE constexpr std::uint64_t fast_hash(
    std::uint64_t value) noexcept
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

HBFSIM_HOST_DEVICE constexpr bool hybrid_reference_sample(
    std::uint64_t sequence, std::uint32_t warmup,
    std::uint64_t threshold, std::uint64_t key) noexcept
{
    return sequence < warmup ||
           (threshold != 0 && fast_hash(sequence ^ key) <= threshold);
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t fast_transfer_ns(
    std::uint32_t bytes, std::uint64_t bandwidth_bytes_per_s) noexcept
{
    if (bytes == 0 || bandwidth_bytes_per_s == 0) {
        return 0;
    }
    const auto numerator = static_cast<unsigned long long>(bytes) *
                           1'000'000'000ULL;
    return (numerator + bandwidth_bytes_per_s - 1) /
           bandwidth_bytes_per_s;
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t fast_service_ns(
    std::uint64_t base_latency_ns, std::uint32_t bytes,
    std::uint64_t bandwidth_bytes_per_s) noexcept
{
    const auto transfer = fast_transfer_ns(bytes, bandwidth_bytes_per_s);
    return transfer > UINT64_MAX - base_latency_ns
               ? UINT64_MAX
               : base_latency_ns + transfer;
}

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

HBFSIM_HOST_DEVICE constexpr std::uint64_t ceiling_scaled_delta(
    std::uint64_t delta, std::uint32_t offset,
    std::uint32_t span) noexcept
{
    if (delta == 0 || offset == 0) {
        return 0;
    }
    if (span == 0) {
        return UINT64_MAX;
    }
    const auto quotient = delta / span;
    const auto remainder = delta % span;
    const auto integral = saturating_multiply(quotient, offset);
    const auto remainder_product = remainder * std::uint64_t{offset};
    const auto fractional =
        (remainder_product + span - 1) / span;
    return saturating_add(integral, fractional);
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t empirical_cumulative_ns(
    const std::uint32_t* pages, const std::uint64_t* cumulative,
    std::uint32_t count, std::uint32_t run_pages) noexcept
{
    if (run_pages == 0) {
        return 0;
    }
    if (pages == nullptr || cumulative == nullptr || count == 0) {
        return UINT64_MAX;
    }

    std::uint32_t right = 0;
    while (right < count && run_pages > pages[right]) {
        ++right;
    }
    if (right == count) {
        right = count - 1;
    }

    std::uint32_t left_pages = 0;
    std::uint64_t left_ns = 0;
    if (right != 0) {
        left_pages = pages[right - 1];
        left_ns = cumulative[right - 1];
    }
    if (run_pages > pages[right] && count > 1) {
        left_pages = pages[count - 2];
        left_ns = cumulative[count - 2];
        right = count - 1;
    }

    if (pages[right] <= left_pages || cumulative[right] < left_ns ||
        run_pages < left_pages) {
        return UINT64_MAX;
    }
    const auto increment = ceiling_scaled_delta(
        cumulative[right] - left_ns, run_pages - left_pages,
        pages[right] - left_pages);
    return saturating_add(left_ns, increment);
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t empirical_service_ns(
    const std::uint32_t* pages, const std::uint64_t* cumulative,
    std::uint32_t count, std::uint32_t run_pages) noexcept
{
    if (run_pages == 0) {
        return 0;
    }
    const auto current =
        empirical_cumulative_ns(pages, cumulative, count, run_pages);
    const auto previous =
        empirical_cumulative_ns(pages, cumulative, count, run_pages - 1);
    return current < previous ? UINT64_MAX : current - previous;
}

inline constexpr std::uint64_t kEmpiricalBurstRunMask = 1023;
inline constexpr std::uint64_t kMaximumEmpiricalPage =
    (std::uint64_t{1} << 53) - 2;

struct EmpiricalBurstUpdate {
    std::uint64_t packed;
    std::uint32_t run_pages;
    bool valid;
};

HBFSIM_HOST_DEVICE constexpr std::uint32_t empirical_burst_run_pages(
    std::uint64_t packed) noexcept
{
    return static_cast<std::uint32_t>(packed & kEmpiricalBurstRunMask);
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t empirical_burst_operation(
    std::uint64_t packed) noexcept
{
    return static_cast<std::uint32_t>((packed >> 10) & 1U);
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t empirical_burst_page(
    std::uint64_t packed) noexcept
{
    const auto page_plus_one = packed >> 11;
    return page_plus_one == 0 ? UINT64_MAX : page_plus_one - 1;
}

HBFSIM_HOST_DEVICE constexpr EmpiricalBurstUpdate update_empirical_burst(
    std::uint64_t previous, std::uint64_t page,
    std::uint32_t operation) noexcept
{
    if (page > kMaximumEmpiricalPage || operation > 1) {
        return {.packed = previous, .run_pages = 0, .valid = false};
    }

    std::uint32_t run_pages = 1;
    const auto previous_page = empirical_burst_page(previous);
    if (previous != 0 && empirical_burst_run_pages(previous) != 0 &&
        empirical_burst_operation(previous) == operation &&
        previous_page != UINT64_MAX && previous_page + 1 == page) {
        const auto previous_run = empirical_burst_run_pages(previous);
        run_pages = previous_run == kEmpiricalBurstRunMask
                        ? previous_run
                        : previous_run + 1;
    }
    const auto packed = ((page + 1) << 11) |
                        (std::uint64_t{operation} << 10) | run_pages;
    return {.packed = packed, .run_pages = run_pages, .valid = true};
}

struct EmpiricalRequestService {
    std::uint64_t service_ns;
    std::uint64_t packed_state;
    std::uint32_t run_pages;
    bool valid;
};

HBFSIM_HOST_DEVICE constexpr bool empirical_control_valid(
    const SharedControlHeader& header) noexcept
{
    if (header.empirical_flags != 1 || header.empirical_point_count != 6 ||
        header.program_latency_ns == 0) {
        return false;
    }
    for (std::uint32_t index = 0; index < 6; ++index) {
        if (header.empirical_breakpoint_pages[index] == 0 ||
            header.empirical_cumulative_ns[index] == 0 ||
            (index != 0 &&
             header.empirical_breakpoint_pages[index] <=
                 header.empirical_breakpoint_pages[index - 1]) ||
            (index != 0 &&
             header.empirical_cumulative_ns[index] <=
                 header.empirical_cumulative_ns[index - 1])) {
            return false;
        }
    }
    return header.empirical_breakpoint_pages[5] <=
           kEmpiricalBurstRunMask;
}

HBFSIM_HOST_DEVICE constexpr EmpiricalRequestService
empirical_request_service(const SharedControlHeader& header,
                          std::uint64_t previous_state,
                          std::uint64_t page,
                          std::uint32_t operation) noexcept
{
    if (!empirical_control_valid(header)) {
        return {};
    }
    const auto burst =
        update_empirical_burst(previous_state, page, operation);
    if (!burst.valid) {
        return {};
    }
    const auto service = operation == 0
                             ? empirical_service_ns(
                                   header.empirical_breakpoint_pages,
                                   header.empirical_cumulative_ns,
                                   header.empirical_point_count,
                                   burst.run_pages)
                             : header.program_latency_ns;
    return {.service_ns = service,
            .packed_state = burst.packed,
            .run_pages = burst.run_pages,
            .valid = true};
}

#if defined(HBFSIM_ENABLE_TIMING_FUTURES) && HBFSIM_ENABLE_TIMING_FUTURES
#define HBFSIM_FUTURE_STORE_SPAN_GUARD 1
// C6.1 output stores stay native only if their complete finite span is
// disjoint from every frozen HBF interval. Never classify by base alone.
HBFSIM_HOST_DEVICE inline bool timing_future_native_store_span(
    const SharedRangeRecord* ranges, std::uint32_t count,
    std::uint64_t address, std::uint32_t bytes)
{
    if (!timing_future::valid_bytes(bytes) || !address || address>UINT64_MAX-bytes ||
        count>kRangeCapacity || (count && !ranges)) return false;
    for (std::uint32_t i=0;i<count;++i) {
        const auto& r=ranges[i];
        if (!r.base || !r.length || r.base>UINT64_MAX-r.length ||
            (address<r.base+r.length && r.base<address+bytes)) return false;
    }
    return true;
}
#endif

#undef HBFSIM_HOST_DEVICE

}  // namespace hbfsim::device

#if defined(__CUDACC__) && defined(HBFSIM_ENABLE_TIMING_FUTURES) && HBFSIM_ENABLE_TIMING_FUTURES
extern "C" __device__ std::uint32_t
__hbfsim_timing_future_native_store_guard_v1(std::uint64_t, std::uint32_t);
extern "C" __device__ hbfsim::timing_future::DeviceTimingFutureV1
__hbfsim_timing_future_issue_v1(std::uint64_t, std::uint32_t, std::uint32_t,
    std::uint32_t, hbfsim::timing_future::TimingFutureLaneMetadataV1*);
// Poll returns modeled readiness/status only, not native-load completion.
extern "C" __device__ std::uint32_t __hbfsim_timing_future_poll_v1(
    hbfsim::timing_future::DeviceTimingFutureV1*,
    const hbfsim::timing_future::TimingFutureLaneMetadataV1*,std::uint32_t,std::uint32_t);
extern "C" __device__ hbfsim::timing_future::ConsumeResult __hbfsim_timing_future_wait_v1(
    hbfsim::timing_future::DeviceTimingFutureV1*,
    const hbfsim::timing_future::TimingFutureLaneMetadataV1*,std::uint64_t,
    std::uint32_t,std::uint32_t,std::uint32_t);
#endif

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
inline constexpr std::uint32_t kControlAbiVersion = 10;
inline constexpr std::uint32_t kRangeCapacity = 32'768;
inline constexpr std::uint32_t kTensorMapCapacity = 256;
inline constexpr std::uint32_t kSm120StateCapacity = 256;
inline constexpr std::uint32_t kSm120RoutingLutCapacity = 64;
inline constexpr std::uint64_t kTmaSoftwareTokenBit = 1ULL << 63;
inline constexpr std::uint64_t kTmaTimingTokenBit = 1ULL << 62;
inline constexpr std::uint64_t kSm120ChannelConfigMagic =
    0x534d313230434846ULL;
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
    ThermalShutdown = 8,
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

inline constexpr std::size_t kThermalGenerationOffset =
    offsetof(SharedControlHeader, thermal_generation);

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
    std::uint32_t instruction_id;
    std::uint32_t future_flags;
    std::uint64_t issue_timestamp_ns;
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

enum class DeviceFutureState : std::uint32_t {
    Native = 0,
    Issued = 1,
    Ready = 2,
    DeferredMaterialization = 3,
    TerminalError = 4,
    Consumed = 5,
};

enum DeviceFutureFlags : std::uint32_t {
    DeviceFutureNone = 0,
    DeviceFutureNative = 1U << 0,
    DeviceFutureTiming = 1U << 1,
    DeviceFutureCapacity = 1U << 2,
    DeviceFutureAtomic = 1U << 3,
    DeviceFutureReference = 1U << 4,
};

struct alignas(16) DeviceFuture {
    std::uint64_t ticket;
    std::uint64_t original_address;
    std::uint64_t resolved_address;
    std::uint64_t ready_ns;
    std::uint32_t bytes;
    std::uint32_t instruction_id;
    std::uint32_t channel;
    std::uint32_t flags;
    DeviceFutureState state;
    RequestStatus status;
};

struct MediaDescriptor {
    std::uint64_t logical_address;
    std::uint32_t bytes;
    bool valid;
};

struct TmaTileClassification {
    std::uint64_t hbm_bytes;
    std::uint64_t hbf_bytes;
    std::uint64_t oob_bytes;
    bool capacity;
    bool valid;
};

struct TmaElementAddress {
    std::uint64_t global_address;
    std::uint64_t destination_offset;
    std::uint32_t bytes;
    std::uint32_t shared_bytes;
    bool oob;
    bool valid;
};

HBFSIM_HOST_DEVICE constexpr bool tma_packed_element_type(
    std::uint32_t element_type) noexcept
{
    return element_type >= 13 && element_type <= 15;
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_global_unit_bytes(
    std::uint32_t element_type) noexcept
{
    // Internal element_type values follow the PTX TensorMap encoding order,
    // not CUtensorMapDataType. The host interposer performs the translation.
    constexpr std::uint32_t sizes[16]{
        1, 2, 4, 4, 8, 8, 2, 4, 4, 8, 2, 4, 4, 8, 8, 12};
    return element_type < 16 ? sizes[element_type] : 0;
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_shared_unit_bytes(
    std::uint32_t element_type) noexcept
{
    if (element_type == 14 || element_type == 15) return 16;
    return tma_global_unit_bytes(element_type);
}

HBFSIM_HOST_DEVICE constexpr bool tma_float_element_type(
    std::uint32_t element_type) noexcept
{
    return element_type >= 6 && element_type <= 12;
}

// SM120 TensorMap OOB NaN is the 16-bit marker 0x7ff7 repeated to
// the destination element width. This differs from the older cp.async
// OOB_NAN_F16 marker (0x7eff) and is covered by the native SM120 oracle.
HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_oob_nan_fill_byte(
    std::uint32_t element_type, std::uint32_t byte_offset) noexcept
{
    if (!tma_float_element_type(element_type) ||
        byte_offset >= tma_shared_unit_bytes(element_type)) {
        return UINT32_MAX;
    }
    return (byte_offset & 1U) == 0 ? 0xf7U : 0x7fU;
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_tf32_load_bits(
    std::uint32_t bits) noexcept
{
    constexpr std::uint32_t exponent_mask = 0x7f800000U;
    constexpr std::uint32_t mantissa_mask = 0x007fffffU;
    constexpr std::uint32_t retained_mask = 0xffffe000U;
    if ((bits & exponent_mask) == exponent_mask &&
        (bits & mantissa_mask) != 0) {
        return 0x7fffe000U;
    }
    const auto retained_lsb = (bits >> 13U) & 1U;
    return (bits + 0x00000fffU + retained_lsb) & retained_mask;
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t tma_swizzled_destination(
    std::uint64_t linear, std::uint64_t row_bytes,
    std::uint64_t destination_base, std::uint32_t swizzle,
    std::uint32_t atomicity) noexcept
{
    if (swizzle == 0) return linear;
    if (row_bytes == 0 || swizzle > 4 || atomicity > 3 ||
        (swizzle != 3 && atomicity != 0)) {
        return UINT64_MAX;
    }
    const std::uint64_t span = swizzle == 1 ? 32U
                                 : swizzle == 2 ? 64U
                                 : swizzle == 4 ? 96U
                                                : 128U;
    if (row_bytes > span) return UINT64_MAX;
    const std::uint64_t atom = atomicity == 1 || atomicity == 2
                                   ? 32U
                                   : atomicity == 3 ? 64U : 16U;
    if (span % atom != 0) return UINT64_MAX;
    const auto row = linear / row_bytes;
    const auto in_row = linear % row_bytes;
    const auto atoms = span / atom;
    const auto atom_index = in_row / atom;
    if (atom_index >= atoms) return UINT64_MAX;
    const auto base_offset = (destination_base / 128U) % atoms;
    const auto swizzled_atom = atom_index ^ ((row + base_offset) % atoms);
    auto within = in_row % atom;
    if (atomicity == 2 && ((row + base_offset) & 1U) != 0) within ^= 8U;
    if (row > UINT64_MAX / span || swizzled_atom > UINT64_MAX / atom ||
        row * span > UINT64_MAX - swizzled_atom * atom ||
        row * span + swizzled_atom * atom > UINT64_MAX - within) {
        return UINT64_MAX;
    }
    return row * span + swizzled_atom * atom + within;
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t
tma_im2col_wide_swizzled_destination(
    std::uint64_t linear, std::uint64_t destination_base,
    std::uint32_t swizzle, std::uint32_t atomicity) noexcept
{
    if (swizzle == 0 || swizzle > 4 || atomicity > 3 ||
        (swizzle != 3 && atomicity != 0)) {
        return UINT64_MAX;
    }
    const std::uint64_t atom = atomicity == 1 || atomicity == 2
                                   ? 32U
                                   : atomicity == 3 ? 64U : 16U;
    const std::uint64_t pattern = swizzle == 1 ? 2U
                                    : swizzle == 2 ? 4U
                                    : swizzle == 4 ? 2U
                                    : atomicity == 0 ? 8U
                                    : atomicity == 3 ? 2U : 4U;
    const auto row = linear / 128U;
    const auto in_row = linear % 128U;
    const auto atom_index = in_row / atom;
    const auto base_offset = (destination_base / 128U) % pattern;
    const auto swizzled_atom = atom_index ^ ((row + base_offset) % pattern);
    auto within = in_row % atom;
    if (atomicity == 2 && ((row + base_offset) & 1U) != 0) within ^= 8U;
    if (row > UINT64_MAX / 128U ||
        swizzled_atom > UINT64_MAX / atom ||
        row * 128U > UINT64_MAX - swizzled_atom * atom ||
        row * 128U + swizzled_atom * atom > UINT64_MAX - within) {
        return UINT64_MAX;
    }
    return row * 128U + swizzled_atom * atom + within;
}

HBFSIM_HOST_DEVICE constexpr bool tma_descriptor_layout_valid(
    const SharedTensorMapSlot& map, std::uint32_t access_mode) noexcept
{
    if (map.rank == 0 || map.rank > 5 || map.element_type > 15 ||
        map.base_address == 0 || map.base_address % 16U != 0 ||
        map.interleave > 2 || map.swizzle > 4 ||
        map.swizzle_atomicity > 3 || map.oob_fill > 1 ||
        access_mode > 4 ||
        (map.interleave != 0 && map.rank < 3) ||
        (map.interleave == 2 && map.base_address % 32U != 0) ||
        (map.element_type >= 14 && map.base_address % 32U != 0) ||
        (map.oob_fill != 0 && !tma_float_element_type(map.element_type)) ||
        (map.swizzle != 3 && map.swizzle_atomicity != 0)) {
        return false;
    }
    constexpr std::uint64_t max_dimension =
        static_cast<std::uint64_t>(UINT32_MAX) + 1U;
    for (std::uint32_t dimension = 0; dimension < map.rank; ++dimension) {
        if (map.global_dim[dimension] == 0 ||
            map.global_dim[dimension] > max_dimension ||
            map.element_stride[dimension] == 0 ||
            map.element_stride[dimension] > 8) {
            return false;
        }
        if (dimension != 0 &&
            (map.global_stride[dimension] == 0 ||
             map.global_stride[dimension] >= (1ULL << 40) ||
             map.global_stride[dimension] % 16U != 0 ||
             ((map.interleave == 2 || map.element_type >= 14) &&
              map.global_stride[dimension] % 32U != 0))) {
            return false;
        }
    }
    const auto packed = tma_packed_element_type(map.element_type);
    if (access_mode < 3) {
        for (std::uint32_t dimension = 0; dimension < map.rank; ++dimension) {
            if (map.box_dim[dimension] == 0 ||
                map.box_dim[dimension] > 256) return false;
        }
        const auto bits = map.element_type == 15 ? 6U
                          : packed                 ? 4U
                                                   : 8U * tma_global_unit_bytes(
                                                               map.element_type);
        if (bits == 0 || map.box_dim[0] > UINT64_MAX / bits ||
            (map.interleave == 0 &&
             (static_cast<std::uint64_t>(map.box_dim[0]) * bits) % 128U !=
                 0)) {
            return false;
        }
    } else {
        if (map.rank < 3 || map.channels_per_pixel == 0 ||
            map.channels_per_pixel > 256 || map.pixels_per_column == 0 ||
            map.pixels_per_column > 1024 ||
            (map.element_type >= 14 && map.channels_per_pixel != 128)) {
            return false;
        }
        const auto corners = map.rank - 2;
        const std::int32_t low_limit = map.rank == 3 ? -32768
                                       : map.rank == 4 ? -128
                                                       : -16;
        const std::int32_t high_limit = map.rank == 3 ? 32767
                                        : map.rank == 4 ? 127
                                                        : 15;
        for (std::uint32_t dimension = 0; dimension < corners; ++dimension) {
            if (map.lower_corner[dimension] < low_limit ||
                map.lower_corner[dimension] > high_limit ||
                map.upper_corner[dimension] < low_limit ||
                map.upper_corner[dimension] > high_limit ||
                -static_cast<std::int64_t>(map.upper_corner[dimension]) <
                    map.lower_corner[dimension]) {
                return false;
            }
        }
    }
    if (map.element_type == 13 && map.global_dim[0] % 2U != 0) return false;
    if (map.element_type >= 14 && map.global_dim[0] % 128U != 0) return false;
    if (map.swizzle == 4 &&
        (map.interleave != 0 || access_mode == 4 || map.element_type >= 14)) {
        return false;
    }
    if (map.swizzle != 0) {
        const std::uint64_t span = map.swizzle == 1 ? 32U
                                     : map.swizzle == 2 ? 64U
                                     : map.swizzle == 4 ? 96U
                                                        : 128U;
        std::uint64_t inner_elements = access_mode >= 3
                                           ? map.channels_per_pixel
                                           : map.box_dim[0];
        if (packed) {
            if (inner_elements % 16U != 0) return false;
            inner_elements /= 16U;
        }
        if (inner_elements > UINT64_MAX /
                                 tma_shared_unit_bytes(map.element_type) ||
            inner_elements * tma_shared_unit_bytes(map.element_type) > span) {
            return false;
        }
    }
    if (access_mode == 4 &&
        (map.interleave != 0 || map.swizzle == 0 ||
         map.swizzle_atomicity == 2)) {
        return false;
    }
    return true;
}

HBFSIM_HOST_DEVICE constexpr bool tma_software_copy_supported(
    const SharedTensorMapSlot& map, std::uint32_t direction,
    std::uint32_t access_mode, std::uint32_t shared_scope) noexcept
{
    if (!tma_descriptor_layout_valid(map, access_mode) ||
        direction > 1 || access_mode > 4 || shared_scope > 1 ||
        map.element_type > 15 || map.swizzle > 4 ||
        map.swizzle_atomicity > 3 ||
        (map.swizzle != 3 && map.swizzle_atomicity != 0) ||
        (map.oob_fill != 0 && !tma_float_element_type(map.element_type)) ||
        (direction == 1 && (access_mode == 1 || access_mode == 4)) ||
        (direction == 0 && access_mode == 2) ||
        (map.swizzle == 4 && (map.interleave != 0 || access_mode == 4 ||
                              map.swizzle_atomicity != 0)) ||
        (map.element_type >= 14 && map.swizzle == 4) ||
        (map.element_type == 14 && direction == 1)) {
        return false;
    }
    if (tma_packed_element_type(map.element_type) &&
        ((map.swizzle != 0 && map.swizzle != 3) ||
         map.swizzle_atomicity == 2)) {
        return false;
    }
    return true;
}

HBFSIM_HOST_DEVICE constexpr bool tma_copy_supported(
    const SharedTensorMapSlot& map, std::uint32_t direction,
    std::uint32_t access_mode, std::uint32_t shared_scope) noexcept
{
    if (!tma_software_copy_supported(map, direction, access_mode,
                                     shared_scope) ||
        (map.swizzle_atomicity == 2 &&
         (direction != 0 || access_mode == 4)) ||
        ((map.element_type == 14 || map.element_type == 15) &&
         direction == 0 && map.swizzle_atomicity == 3) ||
        (shared_scope == 1 && direction == 0 &&
         (tma_packed_element_type(map.element_type) ||
          map.swizzle_atomicity != 0))) {
        return false;
    }
    return true;
}

HBFSIM_HOST_DEVICE inline bool tma_pack_b6p2x16(
    const std::byte* source, std::byte* destination) noexcept
{
    if (source == nullptr || destination == nullptr) return false;
    auto* output = reinterpret_cast<unsigned char*>(destination);
    const auto* input = reinterpret_cast<const unsigned char*>(source);
    for (std::uint32_t byte = 0; byte < 12; ++byte) output[byte] = 0;
    for (std::uint32_t index = 0; index < 16; ++index) {
        const auto value = static_cast<std::uint32_t>(input[index] & 0x3fU);
        const auto bit = index * 6U;
        const auto byte = bit / 8U;
        const auto shift = bit % 8U;
        output[byte] |= static_cast<unsigned char>(value << shift);
        if (shift > 2U) {
            output[byte + 1] |=
                static_cast<unsigned char>(value >> (8U - shift));
        }
    }
    return true;
}

HBFSIM_HOST_DEVICE constexpr bool tma_reduction_supported(
    std::uint32_t element_type, std::uint32_t operation) noexcept
{
    if (operation == UINT32_MAX) return true;
    switch (operation) {
    case 0:  // add
        return element_type == 2 || element_type == 3 || element_type == 4 ||
               element_type == 6 || element_type == 7 || element_type == 10;
    case 1:  // and
    case 2:  // or
    case 3:  // xor
        return element_type == 2 || element_type == 3 || element_type == 4 ||
               element_type == 5;
    case 4:  // inc
    case 5:  // dec
        return element_type == 2;
    case 6:  // min
    case 7:  // max
        return element_type == 2 || element_type == 3 || element_type == 4 ||
               element_type == 5 || element_type == 6 || element_type == 10;
    default:
        return false;
    }
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_barrier_target_mask(
    std::uint32_t data_target_mask, std::uint32_t barrier_owner_rank,
    std::uint32_t cta_group) noexcept
{
    if ((data_target_mask & 0xffff0000U) != 0 ||
        barrier_owner_rank >= 16 || (cta_group != 1 && cta_group != 2)) {
        return 0;
    }
    if (cta_group == 1) return data_target_mask;
    std::uint32_t result = 0;
    const auto parity = barrier_owner_rank & 1U;
    for (std::uint32_t rank = 0; rank < 16; ++rank) {
        if ((data_target_mask & (1U << rank)) != 0) {
            result |= 1U << ((rank & ~1U) | parity);
        }
    }
    return result;
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_data_target_mask(
    std::uint32_t multicast_mask, std::uint32_t issuer_rank,
    std::uint32_t destination_rank, std::uint32_t shared_scope) noexcept
{
    if ((multicast_mask & 0xffff0000U) != 0 || issuer_rank >= 16 ||
        destination_rank >= 16 || shared_scope > 1) {
        return 0;
    }
    if (multicast_mask != 0) {
        return shared_scope == 1 ? multicast_mask : 0;
    }
    return 1U << (shared_scope == 1 ? destination_rank : issuer_rank);
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t encode_tma_software_token(
    std::uint32_t generation, std::uint32_t slot) noexcept
{
    return generation == 0 || slot > UINT16_MAX
               ? 0
               : kTmaSoftwareTokenBit |
                     (static_cast<std::uint64_t>(generation & 0x7fffffffU)
                      << 16) |
                     slot;
}

HBFSIM_HOST_DEVICE constexpr bool is_tma_software_token(
    std::uint64_t token) noexcept
{
    return (token & kTmaSoftwareTokenBit) != 0;
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t encode_tma_timing_token(
    std::uint32_t generation, std::uint32_t slot) noexcept
{
    return generation == 0 || slot > UINT16_MAX
               ? 0
               : kTmaTimingTokenBit |
                     (static_cast<std::uint64_t>(generation & 0x7fffffffU)
                      << 16) |
                     slot;
}

HBFSIM_HOST_DEVICE constexpr bool is_tma_timing_token(
    std::uint64_t token) noexcept
{
    return (token & kTmaTimingTokenBit) != 0 &&
           (token & kTmaSoftwareTokenBit) == 0;
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_tracked_token_slot(
    std::uint64_t token) noexcept
{
    return static_cast<std::uint32_t>(token & UINT16_MAX);
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_tracked_token_generation(
    std::uint64_t token) noexcept
{
    return static_cast<std::uint32_t>((token >> 16) & 0x7fffffffU);
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_software_token_slot(
    std::uint64_t token) noexcept
{
    return tma_tracked_token_slot(token);
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t tma_software_token_generation(
    std::uint64_t token) noexcept
{
    return tma_tracked_token_generation(token);
}

struct Sm120DeviceRoutingInput {
    std::uint32_t smid;
    std::uint32_t warpid;
    std::uint32_t cta_x;
    std::uint32_t cta_y;
    std::uint32_t cta_z;
    std::uint32_t resident_warps;
    std::uint32_t cluster_ctarank;
    std::uint32_t operation;
};

struct Sm120DeviceChannelSelection {
    std::uint32_t smsp_proxy;
    std::uint32_t gnic;
    std::uint32_t gpc;
    bool valid;
};

HBFSIM_HOST_DEVICE constexpr bool tensormap_sha_equal(
    const std::byte* left, const std::byte* right) noexcept
{
    if (left == nullptr || right == nullptr) return false;
    for (std::uint32_t index = 0; index < 32; ++index) {
        if (left[index] != right[index]) return false;
    }
    return true;
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t find_tensormap_slot(
    const SharedTensorMapSlot* slots, std::uint32_t count,
    const std::byte* sha, std::uint64_t descriptor_generation) noexcept
{
    if (slots == nullptr || sha == nullptr || descriptor_generation == 0 ||
        count > kTensorMapCapacity) {
        return count;
    }
    for (std::uint32_t index = count; index != 0; --index) {
        const auto& slot = slots[index - 1];
        if (slot.publication_generation != 0 &&
            slot.descriptor_generation == descriptor_generation &&
            tensormap_sha_equal(slot.descriptor_sha256, sha)) {
            return index - 1;
        }
    }
    return count;
}

enum class TensorMapReplaceField : std::uint32_t {
    GlobalAddress,
    Rank,
    BoxDim,
    GlobalDim,
    GlobalStride,
    ElementStride,
    ElementType,
    InterleaveLayout,
    SwizzleMode,
    SwizzleAtomicity,
    FillMode,
};

HBFSIM_HOST_DEVICE constexpr bool apply_tensormap_replace(
    SharedTensorMapSlot& slot, TensorMapReplaceField field,
    std::uint32_t ordinal, std::uint64_t value) noexcept
{
    switch (field) {
    case TensorMapReplaceField::GlobalAddress:
        if (value == 0) return false;
        slot.base_address = value;
        return true;
    case TensorMapReplaceField::Rank:
        if (value > 4) return false;
        slot.rank = static_cast<std::uint32_t>(value) + 1;
        return true;
    case TensorMapReplaceField::BoxDim:
        if (ordinal >= 5 || value == 0 || value > UINT32_MAX) return false;
        slot.box_dim[ordinal] = static_cast<std::uint32_t>(value);
        return true;
    case TensorMapReplaceField::GlobalDim:
        if (ordinal >= 5 || value == 0) return false;
        slot.global_dim[ordinal] = value;
        return true;
    case TensorMapReplaceField::GlobalStride:
        if (ordinal >= 5 || value == 0) return false;
        // The runtime table stores the byte stride at its tensor-dimension
        // index; dimension zero remains the implicit element byte size.
        if (ordinal == 4) return false;
        slot.global_stride[ordinal + 1] = value;
        return true;
    case TensorMapReplaceField::ElementStride:
        if (ordinal >= 5 || value == 0 || value > UINT32_MAX) return false;
        slot.element_stride[ordinal] = static_cast<std::uint32_t>(value);
        return true;
    case TensorMapReplaceField::ElementType:
        if (value > 15) return false;
        // Device tile expansion supports byte-addressable PTX element kinds;
        // packed sub-byte kinds are retained but fail closed at TMA issue.
        slot.element_type = static_cast<std::uint32_t>(value);
        return true;
    case TensorMapReplaceField::InterleaveLayout:
        if (value > 2) return false;
        slot.interleave = static_cast<std::uint32_t>(value);
        return true;
    case TensorMapReplaceField::SwizzleMode:
        if (value > 4) return false;
        slot.swizzle = static_cast<std::uint32_t>(value);
        return true;
    case TensorMapReplaceField::SwizzleAtomicity:
        if (value > 3) return false;
        slot.swizzle_atomicity = static_cast<std::uint32_t>(value);
        return true;
    case TensorMapReplaceField::FillMode:
        if (value > 1) return false;
        slot.oob_fill = static_cast<std::uint32_t>(value);
        return true;
    }
    return false;
}

HBFSIM_HOST_DEVICE inline std::uint32_t tensormap_rotr(
    std::uint32_t value, std::uint32_t shift) noexcept
{
    return (value >> shift) | (value << (32U - shift));
}

HBFSIM_HOST_DEVICE inline void tensormap_sha256_bytes(
    const std::byte* descriptor, std::byte* digest) noexcept
{
    constexpr std::uint32_t constants[64]{
        0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U,
        0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
        0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
        0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
        0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
        0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
        0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
        0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
        0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
        0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
        0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U,
        0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
        0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U,
        0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
        0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
        0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
    };
    std::uint32_t state[8]{
        0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
        0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U,
    };
    if (descriptor == nullptr || digest == nullptr) return;
    for (std::uint32_t block = 0; block < 3; ++block) {
        std::uint32_t words[64]{};
        for (std::uint32_t word = 0; word < 16; ++word) {
            std::uint32_t value = 0;
            for (std::uint32_t byte = 0; byte < 4; ++byte) {
                const auto position = block * 64 + word * 4 + byte;
                std::uint32_t octet = 0;
                if (position < 128) {
                    octet = static_cast<std::uint32_t>(
                        reinterpret_cast<const unsigned char*>(descriptor)
                            [position]);
                } else if (position == 128) {
                    octet = 0x80U;
                } else if (position == 190) {
                    octet = 0x04U;
                }
                value = (value << 8) | octet;
            }
            words[word] = value;
        }
        for (std::uint32_t word = 16; word < 64; ++word) {
            const auto a = words[word - 15];
            const auto b = words[word - 2];
            const auto sigma0 = tensormap_rotr(a, 7) ^
                                tensormap_rotr(a, 18) ^ (a >> 3);
            const auto sigma1 = tensormap_rotr(b, 17) ^
                                tensormap_rotr(b, 19) ^ (b >> 10);
            words[word] = words[word - 16] + sigma0 + words[word - 7] +
                          sigma1;
        }
        auto a = state[0];
        auto b = state[1];
        auto c = state[2];
        auto d = state[3];
        auto e = state[4];
        auto f = state[5];
        auto g = state[6];
        auto h = state[7];
        for (std::uint32_t round = 0; round < 64; ++round) {
            const auto upper = tensormap_rotr(e, 6) ^
                               tensormap_rotr(e, 11) ^
                               tensormap_rotr(e, 25);
            const auto choice = (e & f) ^ (~e & g);
            const auto first = h + upper + choice + constants[round] +
                               words[round];
            const auto lower = tensormap_rotr(a, 2) ^
                               tensormap_rotr(a, 13) ^
                               tensormap_rotr(a, 22);
            const auto majority = (a & b) ^ (a & c) ^ (b & c);
            const auto second = lower + majority;
            h = g;
            g = f;
            f = e;
            e = d + first;
            d = c;
            c = b;
            b = a;
            a = first + second;
        }
        state[0] += a;
        state[1] += b;
        state[2] += c;
        state[3] += d;
        state[4] += e;
        state[5] += f;
        state[6] += g;
        state[7] += h;
    }
    for (std::uint32_t word = 0; word < 8; ++word) {
        for (std::uint32_t byte = 0; byte < 4; ++byte) {
            digest[word * 4 + byte] = static_cast<std::byte>(
                (state[word] >> (24U - byte * 8U)) & 0xffU);
        }
    }
}

static_assert(sizeof(SharedControlHeader) == 768);
static_assert(sizeof(SharedRangeRecord) == 64);
static_assert(sizeof(SharedTensorMapSlot) == 448);
static_assert(sizeof(SharedSm120ChannelConfig) == 1024);
static_assert(sizeof(SharedSm120ChannelState) == 256);
static_assert(sizeof(HbfRequest) == 128);
static_assert(sizeof(HbfCompletion) == 64);
static_assert(sizeof(PageEntry) == 64);
static_assert(sizeof(SharedRequestSlot) == 192);
static_assert(sizeof(SharedCompletionSlot) == 128);
static_assert(sizeof(ResolveResult) == 16);
static_assert(sizeof(DeviceFuture) == 64);
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
static_assert(offsetof(SharedControlHeader, future_issued) == 344);
static_assert(offsetof(SharedControlHeader, future_faults) == 376);
static_assert(offsetof(SharedControlHeader, future_drained) == 384);

HBFSIM_HOST_DEVICE constexpr std::uint64_t fast_hash(
    std::uint64_t value) noexcept
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

HBFSIM_HOST_DEVICE constexpr bool sm120_channel_config_valid(
    const SharedSm120ChannelConfig& config) noexcept
{
    if (config.magic != kSm120ChannelConfigMagic || config.enabled != 1 ||
        config.routing_version == 0 || config.gnic_count != 4 ||
        config.gpc_count != 2 || config.gnic_depth == 0 ||
        config.gpc_depth == 0 || config.gnic_arbitration > 1 ||
        config.gpc_arbitration > 1 || config.smsp_proxy_lut_count == 0 ||
        config.smsp_proxy_lut_count > kSm120RoutingLutCapacity ||
        config.gnic_lut_count == 0 ||
        config.gnic_lut_count > kSm120RoutingLutCapacity ||
        config.gpc_lut_count == 0 ||
        config.gpc_lut_count > kSm120RoutingLutCapacity) return false;
    for (std::uint32_t index = 0; index < 7; ++index) {
        if (config.gnic_service_ns_by_class[index] == 0 ||
            config.gpc_service_ns_by_class[index] == 0) return false;
    }
    for (std::uint32_t index = 0; index < config.gnic_lut_count; ++index)
        if (config.gnic_lut[index] >= 4) return false;
    for (std::uint32_t index = 0; index < config.gpc_lut_count; ++index)
        if (config.gpc_lut[index] >= 2) return false;
    return true;
}

HBFSIM_HOST_DEVICE constexpr Sm120DeviceChannelSelection
route_sm120_channel(const SharedSm120ChannelConfig& config,
                    const Sm120DeviceRoutingInput& input) noexcept
{
    if (!sm120_channel_config_valid(config) || input.operation >= 7 ||
        input.cta_x == 0 || input.cta_y == 0 || input.cta_z == 0 ||
        input.resident_warps == 0) return {};
    auto key = fast_hash(input.smid);
    key = fast_hash(key ^ input.warpid);
    key = fast_hash(key ^ (std::uint64_t{input.cta_x} << 32) ^ input.cta_y);
    key = fast_hash(key ^ (std::uint64_t{input.cta_z} << 32) ^
                    input.resident_warps);
    key = fast_hash(key ^ (std::uint64_t{input.cluster_ctarank} << 32) ^
                    input.operation);
    const auto proxy = config.smsp_proxy_lut[
        key % config.smsp_proxy_lut_count];
    const auto gnic_index = fast_hash(
        key ^ proxy ^ (std::uint64_t{input.operation} << 32)) %
        config.gnic_lut_count;
    const auto gpc_index = fast_hash(
        key ^ (std::uint64_t{proxy} << 32) ^ input.operation) %
        config.gpc_lut_count;
    return {.smsp_proxy = proxy,
            .gnic = config.gnic_lut[gnic_index],
            .gpc = config.gpc_lut[gpc_index],
            .valid = true};
}

HBFSIM_HOST_DEVICE constexpr bool sm120_operation_uses_gnic(
    std::uint32_t operation) noexcept
{
    return operation == 0 || operation == 2 || operation == 4 ||
           operation == 5 || operation == 6;
}

HBFSIM_HOST_DEVICE constexpr bool sm120_operation_uses_gpc(
    std::uint32_t operation) noexcept
{
    return operation == 1 || operation == 3 || operation == 6;
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t sm120_ready_max(
    std::uint64_t base, std::uint64_t gnic, std::uint64_t gpc,
    std::uint64_t media, std::uint64_t capacity,
    std::uint64_t native) noexcept
{
    auto result = base > gnic ? base : gnic;
    result = result > gpc ? result : gpc;
    result = result > media ? result : media;
    result = result > capacity ? result : capacity;
    return result > native ? result : native;
}

HBFSIM_HOST_DEVICE constexpr bool hybrid_reference_sample(
    std::uint64_t sequence, std::uint32_t warmup,
    std::uint64_t threshold, std::uint64_t key) noexcept
{
    return sequence < warmup ||
           (threshold != 0 && fast_hash(sequence ^ key) <= threshold);
}

HBFSIM_HOST_DEVICE constexpr bool warp_hybrid_reference_sample(
    std::uint64_t warp_sequence_base, std::uint32_t warmup,
    std::uint64_t threshold, std::uint64_t leader_key) noexcept
{
    // Hybrid sampling is an instruction decision.  The base sequence counts
    // every active lane, while one leader key keeps all lanes on the same
    // reference/fast path so one warp instruction reserves the 4+2 queues
    // exactly once.
    return hybrid_reference_sample(warp_sequence_base, warmup, threshold,
                                   leader_key);
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

HBFSIM_HOST_DEVICE constexpr std::uint64_t tma_access_element_count(
    const SharedTensorMapSlot& map, std::uint32_t access_mode,
    const std::int32_t* runtime_offsets = nullptr) noexcept
{
    if (!tma_descriptor_layout_valid(map, access_mode)) return 0;
    const auto packed = tma_packed_element_type(map.element_type);
    if (access_mode >= 3) {
        if (map.rank < 3 || map.channels_per_pixel == 0 ||
            map.pixels_per_column == 0 ||
            (access_mode == 3 && map.mode != 1) ||
            (access_mode == 4 && (map.mode != 2 || map.wide_mode > 1))) {
            return 0;
        }
        if (runtime_offsets != nullptr) {
            if (access_mode == 3) {
                const auto limit = map.rank == 3 ? 0xffff :
                                   map.rank == 4 ? 0xff : 0x1f;
                for (std::uint32_t index = 0; index + 2 < map.rank; ++index) {
                    if (runtime_offsets[index] < 0 ||
                        runtime_offsets[index] > limit) {
                        return 0;
                    }
                }
            } else if (runtime_offsets[0] < 0 ||
                       runtime_offsets[0] >
                           (map.wide_mode == 1 ? 31 : 511) ||
                       runtime_offsets[1] < 0 ||
                       runtime_offsets[1] > 31) {
                return 0;
            }
        }
        std::uint64_t pixels = map.pixels_per_column;
        if (access_mode == 4) {
            const auto halo = runtime_offsets == nullptr
                                  ? 0U
                                  : static_cast<std::uint32_t>(
                                        runtime_offsets[0]);
            if (map.wide_mode == 1) {
                pixels = 128;
                if (halo > (UINT64_MAX - pixels) / 4U) return 0;
                pixels += static_cast<std::uint64_t>(halo) * 4U;
            } else {
                if (halo > UINT64_MAX - pixels) return 0;
                pixels += halo;
            }
        }
        auto channels = static_cast<std::uint64_t>(map.channels_per_pixel);
        if (packed) {
            if (channels % 16U != 0) return 0;
            channels /= 16U;
        }
        return pixels > UINT64_MAX / channels ? 0 : pixels * channels;
    }
    if (access_mode == 1 || access_mode == 2) {
        if (map.rank != 2 || map.box_dim[1] != 1) return 0;
    }
    std::uint64_t elements = 1;
    for (std::uint32_t dimension = 0; dimension < map.rank; ++dimension) {
        const auto extent = (access_mode == 1 || access_mode == 2) &&
                                    dimension == 1
                                ? 4U
                                : map.box_dim[dimension];
        if (extent == 0) return 0;
        const auto stride = dimension == 0 && map.interleave == 0
                                ? 1U
                                : map.element_stride[dimension] == 0
                                      ? 1U
                                      : map.element_stride[dimension];
        auto traversed = (static_cast<std::uint64_t>(extent) + stride - 1U) /
                         stride;
        if (packed && dimension == 0) {
            if (traversed % 16U != 0) return 0;
            traversed /= 16U;
        }
        if (traversed == 0 || elements > UINT64_MAX / traversed) return 0;
        elements *= traversed;
    }
    return elements;
}

HBFSIM_HOST_DEVICE constexpr TmaElementAddress tma_element_address(
    const SharedTensorMapSlot& map, const std::int32_t* coordinates,
    std::uint32_t access_mode, std::uint64_t linear,
    const std::int32_t* runtime_offsets = nullptr,
    std::uint64_t destination_base = 0) noexcept
{
    TmaElementAddress result{.global_address = 0,
                             .destination_offset = 0,
                             .bytes = 0,
                             .shared_bytes = 0,
                             .oob = false,
                             .valid = false};
    const auto packed = tma_packed_element_type(map.element_type);
    if (coordinates == nullptr ||
        !tma_descriptor_layout_valid(map, access_mode) ||
        map.rank == 0 || map.rank > 5 ||
        map.element_type > 15 || map.base_address == 0 || access_mode > 4 ||
        map.interleave > 2 || map.swizzle > 4 ||
        map.swizzle_atomicity > 3 || map.oob_fill > 1 ||
        (access_mode < 3 && map.mode != 0) ||
        (map.interleave != 0 &&
         (map.rank < 3 || access_mode == 1 || access_mode == 2 ||
          access_mode == 4)) ||
        ((access_mode == 1 || access_mode == 2) && map.rank != 2) ||
        (access_mode >= 3 && map.rank < 3) ||
        (packed && (map.interleave != 0 || map.oob_fill != 0)) ||
        (packed && map.base_address % (map.element_type == 13 ? 16U : 32U) !=
                       0) ||
        (map.element_type == 13 && map.global_dim[0] % 2U != 0) ||
        ((map.element_type == 14 || map.element_type == 15) &&
         (map.global_dim[0] % 128U != 0 ||
          (access_mode < 3 && map.box_dim[0] != 128))) ||
        (packed && map.swizzle != 0 && map.swizzle != 3) ||
        (packed && map.swizzle_atomicity == 2) ||
        (map.swizzle != 3 && map.swizzle_atomicity != 0) ||
        (map.swizzle == 4 &&
         (map.interleave != 0 || access_mode == 4 || packed))) {
        return result;
    }
    if ((map.element_type == 14 || map.element_type == 15)) {
        for (std::uint32_t dimension = 1; dimension < map.rank; ++dimension) {
            if (map.global_stride[dimension] == 0 ||
                map.global_stride[dimension] % 32U != 0) return result;
        }
    }
    if (packed) {
        const auto coordinate_alignment = map.element_type == 13 ? 32 : 128;
        if (coordinates[0] % coordinate_alignment != 0) return result;
        if (access_mode < 3 && map.box_dim[0] % 16U != 0) return result;
        if (access_mode >= 3 && map.channels_per_pixel % 16U != 0) {
            return result;
        }
    }
    std::uint32_t box[5]{};
    for (std::uint32_t dimension = 0; dimension < map.rank; ++dimension) {
        if (map.global_dim[dimension] == 0) return result;
        box[dimension] = map.box_dim[dimension];
    }
    if (access_mode == 1 || access_mode == 2) box[1] = 4;
    const auto elements =
        tma_access_element_count(map, access_mode, runtime_offsets);
    if (linear >= elements) return result;
    const auto element_bytes = tma_global_unit_bytes(map.element_type);
    const auto shared_bytes = tma_shared_unit_bytes(map.element_type);
    std::int64_t global_coordinates[5]{};
    if (access_mode < 3) {
        auto remainder = linear;
        for (std::uint32_t dimension = 0; dimension < map.rank; ++dimension) {
            const auto stride = dimension == 0 && map.interleave == 0
                                    ? 1U
                                    : map.element_stride[dimension] == 0
                                          ? 1U
                                          : map.element_stride[dimension];
            auto traversed =
                (static_cast<std::uint64_t>(box[dimension]) + stride - 1U) /
                stride;
            if (packed && dimension == 0) traversed /= 16U;
            if (traversed == 0) return result;
            auto local = remainder % traversed;
            remainder /= traversed;
            auto origin = static_cast<std::int64_t>(coordinates[dimension]);
            if ((access_mode == 1 || access_mode == 2) && dimension == 1) {
                origin = coordinates[local + 1];
                local = 0;
            }
            const auto coordinate_stride = packed && dimension == 0
                                               ? 16U
                                               : stride;
            if (local > static_cast<std::uint64_t>(INT64_MAX) /
                            coordinate_stride) {
                return result;
            }
            const auto delta = static_cast<std::int64_t>(
                local * coordinate_stride);
            if (delta > 0 && origin > INT64_MAX - delta) return result;
            global_coordinates[dimension] = origin + delta;
        }
    } else {
        const auto channels = packed
                                  ? static_cast<std::uint64_t>(
                                        map.channels_per_pixel / 16U)
                                  : static_cast<std::uint64_t>(
                                        map.channels_per_pixel);
        const auto channel = linear % channels;
        auto pixel = linear / channels;
        const auto channel_delta = packed ? channel * 16U : channel;
        if (channel_delta > static_cast<std::uint64_t>(INT64_MAX) ||
            coordinates[0] >
                INT64_MAX - static_cast<std::int64_t>(channel_delta)) {
            return result;
        }
        global_coordinates[0] = coordinates[0] +
                                static_cast<std::int64_t>(channel_delta);
        if (access_mode == 3) {
            for (std::uint32_t dimension = 1;
                 dimension + 1 < map.rank; ++dimension) {
                const auto lower = static_cast<std::int64_t>(
                    map.lower_corner[dimension - 1]);
                const auto upper = -static_cast<std::int64_t>(
                    map.upper_corner[dimension - 1]);
                if (upper < lower) return result;
                const auto span = static_cast<std::uint64_t>(upper - lower + 1);
                const auto start = runtime_offsets == nullptr
                                       ? 0U
                                       : static_cast<std::uint32_t>(
                                             runtime_offsets[dimension - 1]);
                const auto position = pixel % span;
                pixel /= span;
                const auto stride = map.element_stride[dimension] == 0
                                        ? 1U
                                        : map.element_stride[dimension];
                if (position > static_cast<std::uint64_t>(INT64_MAX) / stride ||
                    start > static_cast<std::uint64_t>(INT64_MAX)) {
                    return result;
                }
                const auto traversed =
                    static_cast<std::int64_t>(position * stride);
                if (lower > INT64_MAX - static_cast<std::int64_t>(start) ||
                    lower + static_cast<std::int64_t>(start) >
                        INT64_MAX - traversed) {
                    return result;
                }
                const auto delta = lower + static_cast<std::int64_t>(start) +
                                   traversed;
                const auto origin =
                    static_cast<std::int64_t>(coordinates[dimension]);
                if ((delta > 0 && origin > INT64_MAX - delta) ||
                    (delta < 0 && origin < INT64_MIN - delta)) {
                    return result;
                }
                global_coordinates[dimension] = origin + delta;
            }
            const auto batch_dimension = map.rank - 1;
            if (pixel > static_cast<std::uint64_t>(INT64_MAX) ||
                coordinates[batch_dimension] >
                    INT64_MAX - static_cast<std::int64_t>(pixel)) {
                return result;
            }
            global_coordinates[batch_dimension] =
                coordinates[batch_dimension] + static_cast<std::int64_t>(pixel);
        } else {
            const auto offset = runtime_offsets == nullptr
                                    ? 0U
                                    : static_cast<std::uint32_t>(
                                          runtime_offsets[1]);
            const auto lower = static_cast<std::int64_t>(map.lower_corner[0]);
            const auto stride = map.element_stride[1] == 0
                                    ? 1U
                                    : map.element_stride[1];
            if (pixel > static_cast<std::uint64_t>(INT64_MAX) / stride ||
                offset > static_cast<std::uint32_t>(INT64_MAX / 2)) {
                return result;
            }
            const auto traversed = static_cast<std::int64_t>(pixel * stride);
            const auto adjusted = static_cast<std::int64_t>(offset) * 2;
            if (lower > INT64_MAX - adjusted ||
                lower + adjusted > INT64_MAX - traversed) {
                return result;
            }
            const auto delta = lower + adjusted + traversed;
            const auto origin = static_cast<std::int64_t>(coordinates[1]);
            if ((delta > 0 && origin > INT64_MAX - delta) ||
                (delta < 0 && origin < INT64_MIN - delta)) {
                return result;
            }
            global_coordinates[1] = origin + delta;
            for (std::uint32_t dimension = 2; dimension < map.rank;
                 ++dimension) {
                global_coordinates[dimension] = coordinates[dimension];
            }
        }
    }
    std::uint64_t offset = 0;
    bool in_bounds = true;
    for (std::uint32_t dimension = 0; dimension < map.rank; ++dimension) {
        const auto coordinate = global_coordinates[dimension];
        const auto packed_dimension = packed && dimension == 0;
        if (coordinate < 0 ||
            static_cast<std::uint64_t>(coordinate) >=
                map.global_dim[dimension] ||
            (packed_dimension &&
             (map.global_dim[dimension] < 16U ||
              static_cast<std::uint64_t>(coordinate) >
                  map.global_dim[dimension] - 16U))) {
            in_bounds = false;
            continue;
        }
        if (packed_dimension) {
            const auto bits = map.element_type == 15 ? 6U : 4U;
            const auto unsigned_coordinate =
                static_cast<std::uint64_t>(coordinate);
            if (unsigned_coordinate > UINT64_MAX / bits ||
                (unsigned_coordinate * bits) % 8U != 0 ||
                offset > UINT64_MAX - unsigned_coordinate * bits / 8U) {
                return result;
            }
            offset += unsigned_coordinate * bits / 8U;
            continue;
        }
        auto byte_stride =
            dimension == 0 ? element_bytes : map.global_stride[dimension];
        const auto unsigned_coordinate =
            static_cast<std::uint64_t>(coordinate);
        if (map.interleave != 0 && dimension == 0) {
            const auto slice_bytes = map.interleave == 1 ? 16U : 32U;
            if (slice_bytes % element_bytes != 0) return result;
            const auto channels_per_slice = slice_bytes / element_bytes;
            const auto slices =
                (map.global_dim[0] + channels_per_slice - 1) /
                channels_per_slice;
            const auto batch_stride = map.global_stride[map.rank - 1];
            if (slices == 0 || batch_stride == 0 ||
                batch_stride % slices != 0) {
                return result;
            }
            const auto slice_stride = batch_stride / slices;
            const auto slice = unsigned_coordinate / channels_per_slice;
            const auto within = unsigned_coordinate % channels_per_slice;
            if (slice > UINT64_MAX / slice_stride ||
                within > UINT64_MAX / element_bytes ||
                slice * slice_stride >
                    UINT64_MAX - within * element_bytes) {
                return result;
            }
            offset = slice * slice_stride + within * element_bytes;
            continue;
        }
        if (byte_stride == 0 ||
            unsigned_coordinate > UINT64_MAX / byte_stride ||
            offset > UINT64_MAX - unsigned_coordinate * byte_stride) {
            return result;
        }
        offset += unsigned_coordinate * byte_stride;
    }
    if (linear > UINT64_MAX / shared_bytes) return result;
    auto destination = linear * shared_bytes;
    if (map.swizzle != 0 && map.mode == 2) {
        // im2col::w swizzles a continuous column in fixed 128-byte shared
        // memory rows.  This differs from tiled/im2col, whose row width comes
        // from the innermost box/channel extent.
        destination = tma_im2col_wide_swizzled_destination(
            destination, destination_base, map.swizzle,
            map.swizzle_atomicity);
        if (destination == UINT64_MAX) return result;
    } else if (map.swizzle != 0) {
        const auto inner_stride = map.interleave == 0
                                      ? 1U
                                      : map.element_stride[0] == 0
                                            ? 1U
                                            : map.element_stride[0];
        auto row_elements = access_mode >= 3
                                ? static_cast<std::uint64_t>(
                                      map.channels_per_pixel)
                                : (static_cast<std::uint64_t>(box[0]) +
                                   inner_stride - 1U) /
                                      inner_stride;
        if (packed) row_elements /= 16U;
        const auto row_bytes =
            static_cast<std::uint64_t>(row_elements) * shared_bytes;
        destination = tma_swizzled_destination(
            destination, row_bytes, destination_base, map.swizzle,
            map.swizzle_atomicity);
        if (destination == UINT64_MAX) return result;
    }
    result.destination_offset = destination;
    result.bytes = element_bytes;
    result.shared_bytes = shared_bytes;
    result.oob = !in_bounds;
    if (!in_bounds) {
        result.valid = true;
        return result;
    }
    if (offset > UINT64_MAX - map.base_address ||
        map.base_address + offset > UINT64_MAX - element_bytes) {
        return result;
    }
    result.global_address = map.base_address + offset;
    result.valid = true;
    return result;
}

HBFSIM_HOST_DEVICE constexpr TmaTileClassification classify_tma_access(
    const SharedTensorMapSlot& map, const std::int32_t* coordinates,
    const SharedRangeRecord* ranges, std::uint32_t range_count,
    std::uint32_t direction, std::uint32_t access_mode,
    const std::int32_t* runtime_offsets = nullptr) noexcept
{
    TmaTileClassification result{.hbm_bytes = 0,
                                 .hbf_bytes = 0,
                                 .oob_bytes = 0,
                                 .capacity = false,
                                 .valid = false};
    if (coordinates == nullptr || (range_count != 0 && ranges == nullptr) ||
        map.rank == 0 || map.rank > 5 || map.element_type > 15 ||
        map.base_address == 0 || direction > 1 || access_mode > 4 ||
        range_count > kRangeCapacity ||
        (map.element_type == 14 && direction == 1) ||
        (access_mode == 1 && (direction != 0 || map.rank != 2)) ||
        (access_mode == 2 && (direction != 1 || map.rank != 2)) ||
        (access_mode == 3 && map.rank < 3) ||
        (access_mode == 4 && (direction != 0 || map.rank < 3))) {
        return result;
    }
    const auto elements =
        tma_access_element_count(map, access_mode, runtime_offsets);
    if (elements == 0) return result;
    for (std::uint64_t linear = 0; linear < elements; ++linear) {
        const auto element = tma_element_address(
            map, coordinates, access_mode, linear, runtime_offsets);
        if (!element.valid) return result;
        if (element.oob) {
            if (direction == 1) return {};
            if (result.oob_bytes > UINT64_MAX - element.bytes) return result;
            result.oob_bytes += element.bytes;
            continue;
        }
        const auto address = element.global_address;
        for (std::uint32_t byte = 0; byte < element.bytes; ++byte) {
            const auto current = address + byte;
            const auto index = find_range_index(ranges, range_count, current);
            const SharedRangeRecord* range = nullptr;
            if (index != range_count) {
                const auto& candidate = ranges[index];
                if (candidate.length != 0 &&
                    candidate.base <= UINT64_MAX - candidate.length &&
                    current >= candidate.base &&
                    current < candidate.base + candidate.length) {
                    range = &candidate;
                }
            }
            if (range == nullptr) {
                if (result.hbm_bytes == UINT64_MAX) return result;
                ++result.hbm_bytes;
                continue;
            }
            if ((range->mode != 1 && range->mode != 2) ||
                (range->permissions & (1U << direction)) == 0 ||
                result.hbf_bytes == UINT64_MAX) {
                return result;
            }
            ++result.hbf_bytes;
            result.capacity = result.capacity || range->mode == 2;
        }
    }
    result.valid = true;
    return result;
}

HBFSIM_HOST_DEVICE constexpr TmaTileClassification classify_tma_tiled(
    const SharedTensorMapSlot& map, const std::int32_t* coordinates,
    const SharedRangeRecord* ranges, std::uint32_t range_count,
    std::uint32_t direction) noexcept
{
    return classify_tma_access(map, coordinates, ranges, range_count,
                               direction, 0);
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

HBFSIM_HOST_DEVICE constexpr std::uint64_t fast_future_ready_ns(
    std::uint64_t arrival_ns, std::uint64_t previous_tail_ns,
    std::uint64_t latency_ns, std::uint64_t transfer_ns) noexcept
{
    const auto transfer_start = previous_tail_ns > arrival_ns
                                    ? previous_tail_ns
                                    : arrival_ns;
    const auto transfer_target = saturating_add(transfer_start, transfer_ns);
    const auto latency_target = saturating_add(arrival_ns, latency_ns);
    return transfer_target > latency_target ? transfer_target
                                             : latency_target;
}

HBFSIM_HOST_DEVICE constexpr bool sm120_reference_channel_delay_valid(
    std::uint64_t arrival_ns, std::uint64_t channel_ready_ns) noexcept
{
    return channel_ready_ns >= arrival_ns &&
           channel_ready_ns - arrival_ns <= UINT32_MAX;
}

HBFSIM_HOST_DEVICE constexpr std::uint32_t sm120_reference_channel_delay(
    std::uint64_t arrival_ns, std::uint64_t channel_ready_ns) noexcept
{
    return static_cast<std::uint32_t>(channel_ready_ns - arrival_ns);
}

HBFSIM_HOST_DEVICE constexpr std::uint64_t sm120_reference_ready_ns(
    std::uint64_t arrival_ns, std::uint32_t channel_delay_ns,
    std::uint64_t modeled_ready_ns) noexcept
{
    const auto channel_ready_ns =
        saturating_add(arrival_ns, channel_delay_ns);
    return channel_ready_ns > modeled_ready_ns ? channel_ready_ns
                                                : modeled_ready_ns;
}

HBFSIM_HOST_DEVICE constexpr bool future_ring_slot_available(
    std::uint64_t position, std::uint64_t request_sequence,
    std::uint64_t completion_sequence) noexcept
{
    return request_sequence == position && completion_sequence == position;
}

HBFSIM_HOST_DEVICE constexpr bool future_deadline_expired(
    std::uint64_t now_ns, std::uint64_t deadline_ns) noexcept
{
    return deadline_ns != 0 && now_ns >= deadline_ns;
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

#undef HBFSIM_HOST_DEVICE

}  // namespace hbfsim::device

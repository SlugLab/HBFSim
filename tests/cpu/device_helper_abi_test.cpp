#include "../../src/cuda_runtime/device/hbf_device.cuh"
#include "../../src/host_service/control_layout.hpp"

#include <cstddef>
#include <cstdint>
#include <limits>

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            return __LINE__;                                                   \
        }                                                                      \
    } while (false)

int main()
{
    using namespace hbfsim;
    using namespace hbfsim::device;
    static_assert(device::kControlAbiVersion == 9);
    static_assert(host_service::kControlAbiVersion == 9);
    static_assert(sizeof(SharedControlHeader) == 576);
    static_assert(sizeof(SharedControlHeader) ==
                  sizeof(host_service::SharedControlHeader));
    static_assert(sizeof(SharedRangeRecord) ==
                  sizeof(host_service::SharedRangeRecord));
    static_assert(sizeof(SharedTensorMapSlot) ==
                  sizeof(host_service::SharedTensorMapSlot));
    static_assert(sizeof(SharedSm120ChannelConfig) ==
                  sizeof(host_service::SharedSm120ChannelConfig));
    static_assert(sizeof(SharedSm120ChannelState) ==
                  sizeof(host_service::SharedSm120ChannelState));
    static_assert(sizeof(hbfsim::device::HbfRequest) ==
                  sizeof(hbfsim::HbfRequest));
    static_assert(sizeof(hbfsim::device::HbfCompletion) ==
                  sizeof(hbfsim::HbfCompletion));
    static_assert(sizeof(SharedRequestSlot) ==
                  sizeof(host_service::SharedRequestSlot));
    static_assert(sizeof(SharedCompletionSlot) ==
                  sizeof(host_service::SharedCompletionSlot));
    static_assert(sizeof(device::PageEntry) == sizeof(hbfsim::PageEntry));
    static_assert(offsetof(SharedControlHeader, request_producer) ==
                  offsetof(host_service::SharedControlHeader,
                           request_producer));
    static_assert(offsetof(SharedControlHeader, range_count) ==
                  offsetof(host_service::SharedControlHeader, range_count));
    static_assert(offsetof(SharedControlHeader, range_offset) ==
                  offsetof(host_service::SharedControlHeader, range_offset));
    static_assert(offsetof(SharedControlHeader, heartbeat_ns) ==
                  offsetof(host_service::SharedControlHeader, heartbeat_ns));
    static_assert(offsetof(SharedControlHeader, request_timeout_ns) ==
                  offsetof(host_service::SharedControlHeader,
                           request_timeout_ns));
    static_assert(offsetof(SharedControlHeader, control_generation) ==
                  offsetof(host_service::SharedControlHeader,
                           control_generation));
    static_assert(offsetof(SharedControlHeader, read_latency_ns) ==
                  offsetof(host_service::SharedControlHeader,
                           read_latency_ns));
    static_assert(offsetof(SharedControlHeader, fast_request_sequence) ==
                  offsetof(host_service::SharedControlHeader,
                           fast_request_sequence));
    static_assert(offsetof(SharedControlHeader, empirical_burst_state) ==
                  offsetof(host_service::SharedControlHeader,
                           empirical_burst_state));
    static_assert(offsetof(SharedControlHeader, empirical_cumulative_ns) ==
                  offsetof(host_service::SharedControlHeader,
                           empirical_cumulative_ns));
    static_assert(offsetof(SharedControlHeader, empirical_breakpoint_pages) ==
                  offsetof(host_service::SharedControlHeader,
                           empirical_breakpoint_pages));
    static_assert(offsetof(SharedControlHeader, empirical_flags) ==
                  offsetof(host_service::SharedControlHeader,
                           empirical_flags));
    static_assert(offsetof(SharedControlHeader, future_issued) ==
                  offsetof(host_service::SharedControlHeader,
                           future_issued));
    static_assert(offsetof(SharedControlHeader, future_faults) ==
                  offsetof(host_service::SharedControlHeader,
                           future_faults));
    static_assert(offsetof(SharedControlHeader, future_drained) ==
                  offsetof(host_service::SharedControlHeader,
                           future_drained));
    static_assert(offsetof(SharedControlHeader, tensormap_offset) ==
                  offsetof(host_service::SharedControlHeader,
                           tensormap_offset));
    static_assert(offsetof(SharedControlHeader, sm120_channel_state_offset) ==
                  offsetof(host_service::SharedControlHeader,
                           sm120_channel_state_offset));
    static_assert(hbfsim::device::hybrid_reference_sample(0, 4, 0, 7));
    static_assert(!hbfsim::device::hybrid_reference_sample(
        100, 4, 0, 7));
    constexpr auto warmup_future_count = [] {
        std::uint64_t count = 0;
        for (std::uint64_t sequence = 0; sequence < 2048; sequence += 32) {
            if (hbfsim::device::warp_hybrid_reference_sample(
                    sequence, 1024, 0, sequence ^ 0x5a5aU)) {
                count += 32;
            }
        }
        return count;
    }();
    static_assert(warmup_future_count == 1024);
    constexpr auto sampled_future_count = [] {
        std::uint64_t count = 0;
        for (std::uint64_t sequence = 0; sequence < 33'792;
             sequence += 32) {
            if (hbfsim::device::warp_hybrid_reference_sample(
                    sequence, 1024,
                    std::numeric_limits<std::uint64_t>::max() / 2,
                    sequence * 0x9e3779b97f4a7c15ULL + 0xa5a5U)) {
                count += 32;
            }
        }
        return count;
    }();
    static_assert(sampled_future_count > 1024);
    static_assert(sampled_future_count < 33'792);
    static_assert(hbfsim::device::fast_service_ns(10'000, 16'384,
                                                  512'000'000'000ULL) ==
                  10'032);
    static_assert(tma_global_unit_bytes(0) == 1);
    static_assert(tma_global_unit_bytes(1) == 2);
    static_assert(tma_global_unit_bytes(2) == 4);
    static_assert(tma_global_unit_bytes(3) == 4);
    static_assert(tma_global_unit_bytes(4) == 8);
    static_assert(tma_global_unit_bytes(5) == 8);
    static_assert(tma_global_unit_bytes(6) == 2);
    static_assert(tma_global_unit_bytes(7) == 4);
    // Internal IDs use PTX order: f32_ftz=8, f64=9, bf16=10.
    static_assert(tma_global_unit_bytes(8) == 4);
    static_assert(tma_global_unit_bytes(9) == 8);
    static_assert(tma_global_unit_bytes(10) == 2);
    static_assert(tma_global_unit_bytes(11) == 4);
    static_assert(tma_global_unit_bytes(12) == 4);
    static_assert(tma_global_unit_bytes(13) == 8);
    static_assert(tma_global_unit_bytes(14) == 8);
    static_assert(tma_global_unit_bytes(15) == 12);
    static_assert(tma_global_unit_bytes(16) == 0);
    static_assert(tma_shared_unit_bytes(13) == 8);
    static_assert(tma_shared_unit_bytes(14) == 16);
    static_assert(tma_shared_unit_bytes(15) == 16);
    static_assert(tma_oob_nan_fill_byte(6, 0) == 0xf7);
    static_assert(tma_oob_nan_fill_byte(6, 1) == 0x7f);
    static_assert(tma_oob_nan_fill_byte(7, 2) == 0xf7);
    static_assert(tma_oob_nan_fill_byte(8, 3) == 0x7f);
    static_assert(tma_oob_nan_fill_byte(9, 7) == 0x7f);
    static_assert(tma_oob_nan_fill_byte(10, 1) == 0x7f);
    static_assert(tma_oob_nan_fill_byte(11, 0) == 0xf7);
    static_assert(tma_oob_nan_fill_byte(12, 1) == 0x7f);
    static_assert(tma_oob_nan_fill_byte(5, 0) == UINT32_MAX);
    static_assert(tma_tf32_load_bits(0x00000000U) == 0x00000000U);
    static_assert(tma_tf32_load_bits(0x80000000U) == 0x80000000U);
    static_assert(tma_tf32_load_bits(0x00000001U) == 0x00000000U);
    static_assert(tma_tf32_load_bits(0x00002000U) == 0x00002000U);
    static_assert(tma_tf32_load_bits(0x80002000U) == 0x80002000U);
    static_assert(tma_tf32_load_bits(0x007fffffU) == 0x00800000U);
    static_assert(tma_tf32_load_bits(0x3f801000U) == 0x3f800000U);
    static_assert(tma_tf32_load_bits(0x3f803000U) == 0x3f804000U);
    static_assert(tma_tf32_load_bits(0x7f7fffffU) == 0x7f800000U);
    static_assert(tma_tf32_load_bits(0x7f800000U) == 0x7f800000U);
    static_assert(tma_tf32_load_bits(0xff800000U) == 0xff800000U);
    static_assert(tma_tf32_load_bits(0x7fc12345U) == 0x7fffe000U);
    static_assert(tma_tf32_load_bits(0x7fa12345U) == 0x7fffe000U);
    static_assert(tma_tf32_load_bits(0xffc12345U) == 0x7fffe000U);
    static_assert(tma_tf32_load_bits(0xffa12345U) == 0x7fffe000U);
    static_assert(offsetof(hbfsim::device::HbfRequest, logical_address) ==
                  offsetof(hbfsim::HbfRequest, logical_address));
    static_assert(offsetof(hbfsim::device::HbfRequest, instruction_id) ==
                  offsetof(hbfsim::HbfRequest, instruction_id));
    static_assert(offsetof(hbfsim::device::HbfRequest, issue_timestamp_ns) ==
                  offsetof(hbfsim::HbfRequest, issue_timestamp_ns));
    static_assert(offsetof(hbfsim::device::HbfCompletion, status) ==
                  offsetof(hbfsim::HbfCompletion, status));

    const SharedRangeRecord ranges[]{
        {.base = 0x1000, .length = 0x1000, .file_offset = 0x8000,
         .range_id = 1,
         .mode = 1, .permissions = 3, .page_bytes = 0x1000},
        {.base = 0x4000, .length = 0x2000, .file_offset = 0x10'000,
         .range_id = 2,
         .mode = 1, .permissions = 1, .page_bytes = 0x1000},
        {.base = 0x8000, .length = 0x2000, .file_offset = 0x20'000,
         .range_id = 3,
         .mode = 2, .permissions = 3, .page_bytes = 0x1000},
    };
    CHECK(find_range_index(ranges, 2, 0x0fff) == 2);
    CHECK(find_range_index(ranges, 2, 0x1000) == 0);
    CHECK(find_range_index(ranges, 2, 0x3fff) == 0);
    CHECK(find_range_index(ranges, 2, 0x4000) == 1);
    CHECK(access_supported(ranges[0], 0x1000, 16, 0));
    CHECK(access_supported(ranges[0], 0x1ff8, 8, 1));
    CHECK(!access_supported(ranges[0], 0x1ff8, 16, 0));
    CHECK(!access_supported(ranges[1], 0x4000, 4, 1));
    CHECK(access_supported(ranges[2], 0x8008, 8, 0));
    CHECK(access_supported(ranges[2], 0x9008, 8, 1));
    const auto first_page = media_descriptor(ranges[0], 0x1008, 8, 0);
    CHECK(first_page.valid);
    CHECK(first_page.logical_address == 0x8000);
    CHECK(first_page.bytes == 0x1000);
    const auto second_page = media_descriptor(ranges[1], 0x5001, 8, 0);
    CHECK(second_page.valid);
    CHECK(second_page.logical_address == 0x11'000);
    CHECK(second_page.bytes == 0x1000);
    CHECK(media_descriptor(ranges[0], 0x1fff, 1, 0).valid);
    CHECK(!media_descriptor(ranges[0], 0x1fff, 2, 0).valid);
    const auto capacity_page = media_descriptor(ranges[2], 0x9123, 8, 0);
    CHECK(capacity_page.valid);
    CHECK(capacity_page.logical_address == 0x21'000);
    CHECK(resolved_address(ranges[0], 0x1018, 0) == 0x1018);
    CHECK(resolved_address(ranges[2], 0x8123, 0x40'000) == 0x40'123);
    CHECK(resolved_address(ranges[2], 0x8123, 0) == 0);
    CHECK(resolved_address(ranges[2], 0x8123,
                           std::numeric_limits<std::uint64_t>::max()) == 0);
    auto malformed = ranges[0];
    malformed.page_bytes = 0;
    CHECK(!media_descriptor(malformed, 0x1000, 1, 0).valid);
    CHECK(valid_ring_capacity(2));
    CHECK(valid_ring_capacity(4096));
    CHECK(!valid_ring_capacity(3));
    CHECK(saturating_add(std::numeric_limits<std::uint64_t>::max() - 1, 2) ==
          std::numeric_limits<std::uint64_t>::max());
    CHECK(saturating_multiply(std::numeric_limits<std::uint64_t>::max(), 2) ==
          std::numeric_limits<std::uint64_t>::max());

    constexpr std::uint32_t pages[]{1, 4, 16, 64, 256, 512};
    constexpr std::uint64_t cumulative[]{11'133, 41'495, 168'606,
                                         2'824'351, 10'767'793, 20'254'374};
    CHECK(empirical_cumulative_ns(pages, cumulative, 6, 0) == 0);
    CHECK(empirical_cumulative_ns(pages, cumulative, 6, 1) == 11'133);
    CHECK(empirical_cumulative_ns(pages, cumulative, 6, 3) == 31'375);
    CHECK(empirical_cumulative_ns(pages, cumulative, 6, 4) == 41'495);
    CHECK(empirical_cumulative_ns(pages, cumulative, 6, 512) == 20'254'374);
    CHECK(empirical_cumulative_ns(pages, cumulative, 6, 513) == 20'291'431);
    CHECK(empirical_service_ns(pages, cumulative, 6, 4) ==
          41'495 - empirical_cumulative_ns(pages, cumulative, 6, 3));
    CHECK(sm120_reference_channel_delay_valid(100, 125));
    CHECK(sm120_reference_channel_delay(100, 125) == 25);
    CHECK(!sm120_reference_channel_delay_valid(125, 100));
    CHECK(!sm120_reference_channel_delay_valid(
        100, 100 + std::uint64_t{UINT32_MAX} + 1));
    CHECK(sm120_reference_ready_ns(100, 25, 120) == 125);
    CHECK(sm120_reference_ready_ns(100, 25, 150) == 150);

    constexpr std::uint32_t overflow_pages[]{1, 2};
    constexpr std::uint64_t overflow_cumulative[]{
        std::numeric_limits<std::uint64_t>::max() - 1,
        std::numeric_limits<std::uint64_t>::max()};
    CHECK(empirical_cumulative_ns(overflow_pages, overflow_cumulative, 2,
                                  std::numeric_limits<std::uint32_t>::max()) ==
          std::numeric_limits<std::uint64_t>::max());

    auto state = update_empirical_burst(0, 100, 0);
    CHECK(state.valid);
    CHECK(state.run_pages == 1);
    CHECK(empirical_burst_page(state.packed) == 100);
    CHECK(empirical_burst_operation(state.packed) == 0);
    state = update_empirical_burst(state.packed, 101, 0);
    CHECK(state.valid);
    CHECK(state.run_pages == 2);
    CHECK(update_empirical_burst(state.packed, 103, 0).run_pages == 1);
    CHECK(update_empirical_burst(state.packed, 101, 0).run_pages == 1);
    CHECK(update_empirical_burst(state.packed, 102, 1).run_pages == 1);

    auto saturated = update_empirical_burst(0, 0, 0);
    for (std::uint64_t page = 1; page < 1'024; ++page) {
        saturated = update_empirical_burst(saturated.packed, page, 0);
    }
    CHECK(saturated.run_pages == 1023);
    CHECK(update_empirical_burst(saturated.packed, 1'024, 0).run_pages ==
          1023);
    constexpr auto maximum_empirical_page = (std::uint64_t{1} << 53) - 2;
    CHECK(update_empirical_burst(0, maximum_empirical_page, 1).valid);
    CHECK(!update_empirical_burst(0, maximum_empirical_page + 1, 0).valid);
    CHECK(!update_empirical_burst(0, 0, 2).valid);

    hbfsim::device::SharedControlHeader empirical{};
    empirical.empirical_flags = 1;
    empirical.empirical_point_count = 6;
    empirical.program_latency_ns = 408'305;
    for (std::size_t index = 0; index < 6; ++index) {
        empirical.empirical_breakpoint_pages[index] = pages[index];
        empirical.empirical_cumulative_ns[index] = cumulative[index];
    }
    const auto first = empirical_request_service(empirical, 0, 0, 0);
    CHECK(first.valid);
    CHECK(first.run_pages == 1);
    CHECK(first.service_ns == 11'133);

    std::uint64_t packed = 0;
    std::uint64_t cumulative_ns = 0;
    for (std::uint64_t page = 0; page < 4; ++page) {
        const auto request =
            empirical_request_service(empirical, packed, page, 0);
        CHECK(request.valid);
        packed = request.packed_state;
        cumulative_ns += request.service_ns;
    }
    CHECK(cumulative_ns == 41'495);
    const auto random = empirical_request_service(empirical, packed, 99, 0);
    CHECK(random.valid);
    CHECK(random.run_pages == 1);
    CHECK(random.service_ns == 11'133);
    const auto write =
        empirical_request_service(empirical, random.packed_state, 100, 1);
    CHECK(write.valid);
    CHECK(write.run_pages == 1);
    CHECK(write.service_ns == 408'305);

    auto malformed_empirical = empirical;
    malformed_empirical.empirical_point_count = 5;
    CHECK(!empirical_request_service(malformed_empirical, 0, 0, 0).valid);
    malformed_empirical = empirical;
    malformed_empirical.empirical_breakpoint_pages[2] = 4;
    CHECK(!empirical_request_service(malformed_empirical, 0, 0, 0).valid);
    malformed_empirical = empirical;
    malformed_empirical.empirical_cumulative_ns[2] = 41'495;
    CHECK(!empirical_request_service(malformed_empirical, 0, 0, 0).valid);
    malformed_empirical = empirical;
    malformed_empirical.empirical_flags = 2;
    CHECK(!empirical_request_service(malformed_empirical, 0, 0, 0).valid);
    return 0;
}

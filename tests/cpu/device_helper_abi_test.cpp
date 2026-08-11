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
    static_assert(device::kControlAbiVersion == 4);
    static_assert(host_service::kControlAbiVersion == 4);
    static_assert(sizeof(SharedControlHeader) == 384);
    static_assert(sizeof(SharedControlHeader) ==
                  sizeof(host_service::SharedControlHeader));
    static_assert(sizeof(SharedRangeRecord) ==
                  sizeof(host_service::SharedRangeRecord));
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
    static_assert(hbfsim::device::hybrid_reference_sample(0, 4, 0, 7));
    static_assert(!hbfsim::device::hybrid_reference_sample(
        100, 4, 0, 7));
    static_assert(hbfsim::device::fast_service_ns(10'000, 16'384,
                                                  512'000'000'000ULL) ==
                  10'032);
    static_assert(offsetof(hbfsim::device::HbfRequest, logical_address) ==
                  offsetof(hbfsim::HbfRequest, logical_address));
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
    return 0;
}

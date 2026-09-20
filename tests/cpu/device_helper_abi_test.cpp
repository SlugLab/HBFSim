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
    // Was 10'032, the sum of a 10,000 ns latency and a 32 ns transfer. That
    // encoded the model the wait loop never used; see the fast_service_ns
    // block near the end of this file.
    static_assert(hbfsim::device::fast_service_ns(10'000, 16'384,
                                                  512'000'000'000ULL) ==
                  10'000);
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

    // The wait loop must never sleep past the modeled completion target.
    //
    // Before this test existed, the backoff doubled blindly from 64 ns and
    // ignored how much time was left: cumulative wake times are 64*(2^k - 1),
    // so a 10,000 ns target was not reached at 8,128 ns, the loop slept 8,192
    // ns more, and the thread woke at 16,320 ns. Every injected read came out
    // 63 percent slow, and the shorter the target the larger the error: a
    // 1,000 ns target woke at 1,984 ns, 98 percent over.
    //
    // The bound on a single sleep comes from the PTX ISA, which specifies
    // nanosleep's duration as "approximated, but guaranteed to be in the
    // interval [0, 2*t]". Asking for at most half the remaining time therefore
    // cannot overshoot the target even when the hardware sleeps the full 2x.
    {
        using hbfsim::device::wait_sleep_ns;
        constexpr std::uint32_t backoff = 1048576U;
        constexpr std::uint32_t floor_ns = 64U;

        // Walk a 10,000 ns target the way the device loop would, charging the
        // worst case the ISA allows for every sleep.
        std::uint64_t now = 0;
        const std::uint64_t target = 10000;
        unsigned iterations = 0;
        while (now < target && iterations < 1000) {
            const auto nap = wait_sleep_ns(now, target, backoff, floor_ns);
            if (nap == 0) { ++now; }           // spin one nanosecond
            else { now += std::uint64_t{nap} * 2; }  // worst case the ISA allows
            ++iterations;
        }
        CHECK(now >= target);
        CHECK(now <= target + floor_ns);       // lands on target, never 16,320

        // Same for the short target the old schedule missed by 98 percent.
        now = 0; iterations = 0;
        const std::uint64_t short_target = 1000;
        while (now < short_target && iterations < 1000) {
            const auto nap = wait_sleep_ns(now, short_target, backoff, floor_ns);
            if (nap == 0) { ++now; } else { now += std::uint64_t{nap} * 2; }
            ++iterations;
        }
        CHECK(now >= short_target);
        CHECK(now <= short_target + floor_ns);

        // Already past the target: nothing to sleep.
        CHECK(wait_sleep_ns(10000, 10000, backoff, floor_ns) == 0);
        CHECK(wait_sleep_ns(10001, 10000, backoff, floor_ns) == 0);
        // A single sleep never exceeds half of what is left.
        CHECK(wait_sleep_ns(0, 10000, backoff, floor_ns) <= 5000);
    }


    // What the fast path records must be what the fast path waited.
    //
    // The wait target is max(arrival + latency, channel_tail + transfer): the
    // latency overlaps the transfer, so a request is done when both are
    // satisfied. The accounting used to add them instead, so it reported a
    // service time the loop never enforced. On the shipped cd8p-vmem-p50
    // profile -- 4,096-byte pages at 103,540,697 bytes/s, a 39,560 ns transfer
    // against an 11,133 ns latency -- sum and max differ by 28 percent.
    //
    // read_latency_ns is a first-byte latency that overlaps the transfer. The
    // calibration anchor settles it: that profile's read_latency_ns of 11,133
    // ns is exactly the measured single-page P50, an end-to-end total, so
    // adding a transfer on top would count the same time twice.
    {
        using hbfsim::device::fast_service_ns;
        // Latency dominates: the synthetic profiles all look like this.
        static_assert(fast_service_ns(10'000, 16'384, 512'000'000'000ULL) == 10'000,
                      "latency dominates a 32 ns transfer");
        // Transfer dominates: the empirical profile looks like this.
        static_assert(fast_service_ns(11'133, 4'096, 103'540'697ULL) == 39'560,
                      "transfer dominates an 11,133 ns latency");
        // Never the sum of the two.
        static_assert(fast_service_ns(11'133, 4'096, 103'540'697ULL) < 11'133 + 39'560,
                      "service time is the max, not the sum");
    }


    // Span classification, in BOTH directions. An earlier version of this test
    // only checked one of them and recorded the other as "not a defect"; an
    // independent review caught that, and the two directions are kept together
    // here so the mistake cannot repeat.
    //
    // Direction one, already handled: the access starts inside a range and ends
    // past it. media_descriptor rejects it, through the chain
    //   __hbfsim_resolve                -> media_descriptor (hbf_device.cu:659)
    //   __hbfsim_timing_future_issue_v1 -> media_descriptor (hbf_device.cu:1226)
    //   media_descriptor                -> access_supported (hbf_device.cuh:722)
    //   access_supported                -> address + bytes > range.base + range.length
    //                                                       (hbf_device.cuh:691)
    {
        using hbfsim::device::media_descriptor;
        const SharedRangeRecord range{
            .base = 0x1000, .length = 0x1000, .file_offset = 0,
            .range_id = 1, .mode = 1, .permissions = 3, .page_bytes = 0x1000};
        CHECK(media_descriptor(range, 0x1000, 16, 0).valid);
        CHECK(media_descriptor(range, 0x1ff8, 8, 0).valid);
        CHECK(!media_descriptor(range, 0x1ff8, 16, 0).valid);
        CHECK(!media_descriptor(range, 0x1fff, 2, 0).valid);
    }

    // Direction two, the one that was missed: the access STARTS OUTSIDE every
    // range and its span reaches into one. The resolver looks up a range by
    // start address only (hbf_device.cu:632), finds none, and returns
    // RequestStatus::Ready at hbf_device.cu:657 -- which means "bypass, use the
    // native address". The bytes that fall inside the registered range are then
    // neither modeled nor rejected, and are counted as bypass_bytes.
    //
    // The store guard already treats any overlap as disqualifying:
    //   timing_future_native_store_span (hbfsim/hbf_device.cuh:944)
    //   (address < r.base + r.length && r.base < address + bytes) -> not native
    // so the load path is the asymmetric one. range_overlaps below gives the
    // load path the same predicate.
    {
        using hbfsim::device::range_overlaps;
        const SharedRangeRecord range{
            .base = 0x1000, .length = 0x1000, .file_offset = 0,
            .range_id = 1, .mode = 1, .permissions = 3, .page_bytes = 0x1000};
        // Ends one byte before the range: genuinely outside, bypass is correct.
        CHECK(!range_overlaps(range, 0x0ff0, 16));
        // Last byte of the access is the first byte of the range: overlaps.
        CHECK(range_overlaps(range, 0x0ff8, 9));
        // Straddles the lower boundary.
        CHECK(range_overlaps(range, 0x0ff8, 16));
        // Wholly inside.
        CHECK(range_overlaps(range, 0x1800, 8));
        // Straddles the upper boundary.
        CHECK(range_overlaps(range, 0x1ff8, 16));
        // Starts exactly at the end: outside.
        CHECK(!range_overlaps(range, 0x2000, 8));
        // Encloses the whole range.
        CHECK(range_overlaps(range, 0x0800, 0x2000));
        // Degenerate inputs must not report an overlap.
        CHECK(!range_overlaps(range, 0x1800, 0));
        CHECK(!range_overlaps(SharedRangeRecord{}, 0x1800, 8));
    }

    // The resolver calls span_touches_any_range, which must reach the same
    // verdict in O(1) over a sorted range table rather than by scanning all
    // kRangeCapacity == 32'768 entries. Sortedness is not an assumption added
    // here: find_range_index already binary searches this table.
    {
        using hbfsim::device::span_touches_any_range;
        const SharedRangeRecord table[3] = {
            {.base = 0x1000, .length = 0x1000, .file_offset = 0,
             .range_id = 1, .mode = 1, .permissions = 3, .page_bytes = 0x1000},
            {.base = 0x4000, .length = 0x1000, .file_offset = 0,
             .range_id = 2, .mode = 1, .permissions = 3, .page_bytes = 0x1000},
            {.base = 0x9000, .length = 0x1000, .file_offset = 0,
             .range_id = 3, .mode = 1, .permissions = 3, .page_bytes = 0x1000},
        };
        // Below every base, reaching into the first range.
        CHECK(span_touches_any_range(table, 3, 0x0ff8, 16));
        CHECK(!span_touches_any_range(table, 3, 0x0ff0, 16));
        // In the hole between range 0 and range 1, reaching into range 1.
        CHECK(span_touches_any_range(table, 3, 0x3ff8, 16));
        CHECK(!span_touches_any_range(table, 3, 0x3000, 16));
        // In the hole after the last range: nothing above it to reach.
        CHECK(!span_touches_any_range(table, 3, 0xa000, 16));
        // Reaching into the last range from the hole below it.
        CHECK(span_touches_any_range(table, 3, 0x8ff8, 16));
        // Degenerate inputs.
        CHECK(!span_touches_any_range(table, 0, 0x0ff8, 16));
        CHECK(!span_touches_any_range(nullptr, 3, 0x0ff8, 16));
        CHECK(!span_touches_any_range(table, 3, 0x0ff8, 0));
    }

    return 0;
}

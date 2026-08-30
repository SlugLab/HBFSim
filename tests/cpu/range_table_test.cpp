#include <hbfsim/api.h>

#include "../../src/cuda_runtime/range_table.hpp"
#include "../../src/cuda_runtime/device/hbf_device.cuh"
#include "../../src/host_service/control_layout.hpp"

#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <future>
#include <semaphore>
#include <cstdio>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "range table CHECK failed at line %d: %s\n", line,
                 expression);
    std::exit(1);
}

#define CHECK(expression)                                                       \
    do {                                                                        \
        if (!(expression)) {                                                    \
            fail(#expression, __LINE__);                                        \
        }                                                                       \
    } while (false)

struct PublishState {
    bool accept{true};
    bool invoke_publish{true};
    bool publish_twice{false};
    bool error_after_publish{false};
    std::size_t calls{0};
    hbfsim::host_service::SharedRangeRecord last_record{};
};

int publish(const hbfsim::host_service::SharedRangeRecord& record,
            hbfsim::runtime::PublishRange commit, void* commit_state,
            void* opaque) noexcept
{
    auto& state = *static_cast<PublishState*>(opaque);
    ++state.calls;
    state.last_record = record;
    if (!state.accept) {
        return HBFSIM_IO_ERROR;
    }
    if (state.invoke_publish) {
        commit(commit_state);
        if (state.publish_twice) {
            commit(commit_state);
        }
    }
    return state.error_after_publish ? HBFSIM_IO_ERROR : HBFSIM_OK;
}

struct BlockingPublishState {
    std::binary_semaphore entered{0};
    std::binary_semaphore proceed{0};
    std::size_t calls{0};
};

int blocking_publish(const hbfsim::host_service::SharedRangeRecord&,
                     hbfsim::runtime::PublishRange commit,
                     void* commit_state, void* opaque) noexcept
{
    auto& state = *static_cast<BlockingPublishState*>(opaque);
    ++state.calls;
    state.entered.release();
    state.proceed.acquire();
    commit(commit_state);
    return HBFSIM_OK;
}

hbfsim_range_options timing(std::uint32_t permissions =
                                HBFSIM_RANGE_READ_WRITE)
{
    return {
        .mode = HBFSIM_RANGE_MODE_TIMING,
        .permissions = permissions,
        .cache_policy = HBFSIM_CACHE_POLICY_NONE,
        .stream_id = 7,
    };
}

hbfsim_range_options capacity(std::uint32_t permissions =
                                  HBFSIM_RANGE_READ_WRITE)
{
    auto result = timing(permissions);
    result.mode = HBFSIM_RANGE_MODE_CAPACITY;
    return result;
}

}  // namespace

int main()
{
    CHECK(hbfsim::host_service::kRangeCapacity >= 32'768);
    CHECK(hbfsim::host_service::kRangeCapacity ==
          hbfsim::device::kRangeCapacity);
    constexpr std::uint32_t ring_capacity = 8;
    const auto bytes =
        hbfsim::host_service::control_region_bytes(ring_capacity);
    void* storage = nullptr;
    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    hbfsim::host_service::ControlView control(storage, bytes);
    CHECK(control.initialize(ring_capacity));
    hbfsim::runtime::RangeTable ranges(control, 16'384, 1ULL << 30);
    PublishState publisher;

    const auto second = timing(HBFSIM_RANGE_READ);
    CHECK(ranges.add(0x3000, 0x1000, second, publish, &publisher) ==
           HBFSIM_OK);
    const auto first = timing();
    CHECK(ranges.add(0x1000, 0x1000, first, publish, &publisher) ==
           HBFSIM_OK);
    CHECK(publisher.calls == 2);
    CHECK(ranges.size() == 2);
    CHECK(control.header()->range_count == 2);
    CHECK(control.ranges()[0].base == 0x1000);
    CHECK(control.ranges()[1].base == 0x3000);
    CHECK(control.ranges()[0].page_bytes == 16'384);
    CHECK(control.ranges()[0].file_offset == 16'384);
    CHECK(control.ranges()[1].file_offset == 0);
    CHECK(control.ranges()[0].range_id != control.ranges()[1].range_id);

    const auto at_begin = ranges.lookup(0x1000, 4);
    CHECK(at_begin.kind == hbfsim::runtime::RangeLookupKind::Matched);
    CHECK(at_begin.record->permissions == HBFSIM_RANGE_READ_WRITE);
    const auto at_end = ranges.lookup(0x1fff, 1);
    CHECK(at_end.kind == hbfsim::runtime::RangeLookupKind::Matched);
    CHECK(ranges.lookup(0x2000, 1).kind ==
           hbfsim::runtime::RangeLookupKind::Outside);
    CHECK(ranges.lookup(0x1fff, 2).kind ==
           hbfsim::runtime::RangeLookupKind::CrossesBoundary);
    CHECK(ranges.lookup(UINTPTR_MAX, 2).kind ==
           hbfsim::runtime::RangeLookupKind::Outside);

    CHECK(ranges.add(0x1800, 0x1000, first, publish, &publisher) ==
           HBFSIM_INVALID_ARGUMENT);
    CHECK(ranges.add(UINTPTR_MAX - 7, 8, first, publish, &publisher) ==
           HBFSIM_INVALID_ARGUMENT);
    CHECK(ranges.add(UINTPTR_MAX - 7, 9, first, publish, &publisher) ==
           HBFSIM_INVALID_ARGUMENT);

    auto invalid = first;
    invalid.mode = 99;
    CHECK(ranges.add(0x5000, 0x1000, invalid, publish, &publisher) ==
           HBFSIM_INVALID_ARGUMENT);
    const auto capacity_range = capacity(HBFSIM_RANGE_READ);
    CHECK(ranges.add(0x5000, 0x1000, capacity_range, publish, &publisher) ==
          HBFSIM_OK);
    CHECK(publisher.last_record.mode == HBFSIM_RANGE_MODE_CAPACITY);
    CHECK(ranges.lookup(0x5000, 1).record->mode ==
          HBFSIM_RANGE_MODE_CAPACITY);
    invalid = first;
    invalid.permissions = 0;
    CHECK(ranges.add(0x5000, 0x1000, invalid, publish, &publisher) ==
           HBFSIM_INVALID_ARGUMENT);
    invalid.permissions = HBFSIM_RANGE_READ_WRITE | 4U;
    CHECK(ranges.add(0x5000, 0x1000, invalid, publish, &publisher) ==
           HBFSIM_INVALID_ARGUMENT);
    invalid = first;
    invalid.cache_policy = 1;
    CHECK(ranges.add(0x5000, 0x1000, invalid, publish, &publisher) ==
           HBFSIM_INVALID_ARGUMENT);

    publisher.accept = false;
    CHECK(ranges.add(0x6000, 0x1000, first, publish, &publisher) ==
           HBFSIM_IO_ERROR);
    CHECK(ranges.size() == 3);
    CHECK(control.header()->range_count == 3);
    publisher.accept = true;
    publisher.invoke_publish = false;
    CHECK(ranges.add(0x6000, 0x1000, first, publish, &publisher) ==
           HBFSIM_IO_ERROR);
    CHECK(ranges.size() == 3);
    publisher.invoke_publish = true;
    publisher.publish_twice = true;
    CHECK(ranges.add(0x6000, 0x1000, first, publish, &publisher) ==
           HBFSIM_OK);
    CHECK(ranges.size() == 4);
    publisher.publish_twice = false;
    publisher.error_after_publish = true;
    CHECK(ranges.add(0x7000, 0x1000, first, publish, &publisher) ==
           HBFSIM_OK);
    CHECK(ranges.size() == 5);
    publisher.error_after_publish = false;

    for (std::uintptr_t index = 5;
         index < hbfsim::host_service::kRangeCapacity; ++index) {
        const auto base = 0x10'0000 + index * 0x2000;
        CHECK(ranges.add(base, 0x1000, first, publish, &publisher) ==
               HBFSIM_OK);
    }
    CHECK(ranges.size() == hbfsim::host_service::kRangeCapacity);
    CHECK(ranges.add(0x90'0000, 0x1000, first, publish, &publisher) ==
           HBFSIM_UNSUPPORTED);

    std::free(storage);

    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    control = hbfsim::host_service::ControlView(storage, bytes);
    CHECK(control.initialize(ring_capacity));
    hbfsim::runtime::RangeTable concurrent(control, 16'384, 1ULL << 30);
    BlockingPublishState blocked;
    auto first_registration = std::async(std::launch::async, [&] {
        return concurrent.add(0x1000, 0x1000, first, blocking_publish,
                              &blocked);
    });
    blocked.entered.acquire();
    auto overlapping_registration = std::async(std::launch::async, [&] {
        return concurrent.add(0x1800, 0x1000, first, blocking_publish,
                              &blocked);
    });
    CHECK(overlapping_registration.wait_for(std::chrono::milliseconds(20)) ==
           std::future_status::timeout);
    CHECK(blocked.calls == 1);
    blocked.proceed.release();
    CHECK(first_registration.get() == HBFSIM_OK);
    CHECK(overlapping_registration.get() == HBFSIM_INVALID_ARGUMENT);
    CHECK(blocked.calls == 1);
    CHECK(concurrent.size() == 1);
    std::free(storage);

    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    control = hbfsim::host_service::ControlView(storage, bytes);
    CHECK(control.initialize(ring_capacity));
    hbfsim::runtime::RangeTable removable(control, 16'384, 1ULL << 30);
    PublishState remove_publisher;
    CHECK(removable.add(0x1000, 0x1000, first, publish,
                        &remove_publisher) == HBFSIM_OK);
    CHECK(removable.add(0x5000, 0x1000, capacity(), publish,
                        &remove_publisher) == HBFSIM_OK);
    CHECK(removable.remove(0x3000, publish, &remove_publisher) ==
          HBFSIM_INVALID_ARGUMENT);
    remove_publisher.accept = false;
    CHECK(removable.remove(0x1000, publish, &remove_publisher) ==
          HBFSIM_IO_ERROR);
    CHECK(removable.size() == 2);
    remove_publisher.accept = true;
    CHECK(removable.remove(0x1000, publish, &remove_publisher) == HBFSIM_OK);
    CHECK(removable.size() == 1);
    CHECK(control.header()->range_count == 1);
    CHECK(control.ranges()[0].base == 0x5000);
    CHECK(control.ranges()[1].base == 0);
    CHECK(removable.lookup(0x1000, 1).kind ==
          hbfsim::runtime::RangeLookupKind::Outside);
    CHECK(removable.lookup(0x5000, 1).kind ==
          hbfsim::runtime::RangeLookupKind::Matched);
    remove_publisher.error_after_publish = true;
    CHECK(removable.remove(0x5000, publish, &remove_publisher) == HBFSIM_OK);
    CHECK(removable.size() == 0);
    CHECK(control.header()->range_count == 0);
    std::free(storage);

    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    control = hbfsim::host_service::ControlView(storage, bytes);
    CHECK(control.initialize(ring_capacity));
    hbfsim::runtime::RangeTable bounded(control, 16'384, 32'768);
    PublishState bounded_publisher;
    CHECK(bounded.add(0x1000, 1, first, publish, &bounded_publisher) ==
          HBFSIM_OK);
    CHECK(bounded_publisher.last_record.file_offset == 0);
    bounded_publisher.accept = false;
    CHECK(bounded.add(0x3000, 1, first, publish, &bounded_publisher) ==
          HBFSIM_IO_ERROR);
    CHECK(bounded_publisher.last_record.file_offset == 16'384);
    bounded_publisher.accept = true;
    CHECK(bounded.add(0x5000, 16'384, first, publish,
                      &bounded_publisher) == HBFSIM_OK);
    CHECK(bounded_publisher.last_record.file_offset == 16'384);
    CHECK(bounded.add(0x9000, 1, first, publish, &bounded_publisher) ==
          HBFSIM_UNSUPPORTED);
    CHECK(bounded.size() == 2);
    std::free(storage);
    return 0;
}

#include "../../src/cuda_runtime/device/hbf_device.cuh"

#include <hbfsim/shadow_future.hpp>

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
    using hbfsim::device::DeviceFuture;
    static_assert(sizeof(DeviceFuture) == 64);
    static_assert(alignof(DeviceFuture) == 16);
    static_assert(sizeof(DeviceFuture) == sizeof(hbfsim::ShadowFuture));
    static_assert(offsetof(DeviceFuture, ticket) ==
                  offsetof(hbfsim::ShadowFuture, ticket));
    static_assert(offsetof(DeviceFuture, resolved_address) ==
                  offsetof(hbfsim::ShadowFuture, resolved_address));
    static_assert(offsetof(DeviceFuture, instruction_id) ==
                  offsetof(hbfsim::ShadowFuture, instruction_id));
    static_assert(offsetof(DeviceFuture, state) ==
                  offsetof(hbfsim::ShadowFuture, state));
    static_assert(static_cast<std::uint32_t>(
                      hbfsim::device::DeviceFutureNative) ==
                  static_cast<std::uint32_t>(hbfsim::FutureNative));
    static_assert(static_cast<std::uint32_t>(
                      hbfsim::device::DeviceFutureTiming) ==
                  static_cast<std::uint32_t>(hbfsim::FutureTiming));
    static_assert(static_cast<std::uint32_t>(
                      hbfsim::device::DeviceFutureCapacity) ==
                  static_cast<std::uint32_t>(hbfsim::FutureCapacity));
    static_assert(static_cast<std::uint32_t>(
                      hbfsim::device::DeviceFutureAtomic) ==
                  static_cast<std::uint32_t>(hbfsim::FutureAtomic));

    CHECK(hbfsim::device::fast_future_ready_ns(100, 80, 20, 40) == 140);
    CHECK(hbfsim::device::fast_future_ready_ns(100, 200, 20, 40) == 240);
    CHECK(hbfsim::device::fast_future_ready_ns(
              std::numeric_limits<std::uint64_t>::max() - 2,
              std::numeric_limits<std::uint64_t>::max() - 1, 10, 20) ==
          std::numeric_limits<std::uint64_t>::max());
    CHECK(hbfsim::device::future_ring_slot_available(8, 8, 8));
    CHECK(!hbfsim::device::future_ring_slot_available(8, 7, 8));
    CHECK(!hbfsim::device::future_ring_slot_available(8, 8, 9));
    CHECK(!hbfsim::device::future_deadline_expired(99, 100));
    CHECK(hbfsim::device::future_deadline_expired(100, 100));

    hbfsim::ShadowFutureMachine capacity(1);
    auto future = capacity.issue({
        .original_address = 0x1234,
        .issue_ns = 0,
        .deadline_ns = 100,
        .bytes = 4,
        .instruction_id = 77,
        .flags = hbfsim::FutureCapacity,
    });
    CHECK(capacity.complete(
        future.ticket,
        {.resolved_address = 0x9234,
         .ready_ns = 25,
         .status = hbfsim::RequestStatus::Ready,
         .deferred_materialization = true}));
    CHECK(capacity.wait(future, hbfsim::FutureWaitKind::Dependency).state ==
          hbfsim::FutureState::DeferredMaterialization);
    CHECK(future.resolved_address == 0x9234);

    hbfsim::ShadowFutureMachine pressure(1);
    (void)pressure.issue({.original_address = 0x1000,
                          .ready_ns = 10,
                          .deadline_ns = 100,
                          .bytes = 4,
                          .instruction_id = 1,
                          .flags = hbfsim::FutureTiming});
    (void)pressure.issue({.original_address = 0x2000,
                          .ready_ns = 20,
                          .deadline_ns = 100,
                          .bytes = 4,
                          .instruction_id = 2,
                          .flags = hbfsim::FutureTiming});
    CHECK(pressure.now_ns() == 10);
    CHECK(pressure.counters().issue_throttle_ns == 10);
    return 0;
}

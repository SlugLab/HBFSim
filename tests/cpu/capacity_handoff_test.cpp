#include "../../src/host_service/control_layout.hpp"
#include "../../src/host_service/request_dispatcher.hpp"

#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <atomic>
#include <barrier>
#include <optional>
#include <semaphore>
#include <thread>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "capacity handoff CHECK failed at line %d: %s\n",
                 line, expression);
    std::exit(1);
}

#define CHECK(expression)                                                       \
    do {                                                                        \
        if (!(expression)) {                                                    \
            fail(#expression, __LINE__);                                        \
        }                                                                       \
    } while (false)

}  // namespace

int main()
{
    constexpr std::uint32_t capacity = 4;
    const auto bytes =
        hbfsim::host_service::control_region_bytes(capacity);
    void* storage = nullptr;
    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    hbfsim::host_service::ControlView control(storage, bytes);
    CHECK(control.initialize(capacity));

    hbfsim::HbfRequest request{
        .request_id = 17,
        .sequence = 5,
        .logical_address = 0x12'000,
        .bytes = 0x1000,
        .range_id = 3,
        .operation = static_cast<std::uint32_t>(
            hbfsim::RequestOperation::Write),
    };
    CHECK(control.begin_capacity_handoff(request));
    CHECK(!control.begin_capacity_handoff(request));

    hbfsim::host_service::CapacityHandoff handoff{};
    CHECK(control.try_capacity_handoff(1, handoff));
    CHECK(handoff.ticket == 5);
    CHECK(handoff.request_id == 17);
    CHECK(handoff.logical_page == 0x12);
    CHECK(handoff.operation == static_cast<std::uint32_t>(
                                      hbfsim::RequestOperation::Write));

    CHECK(!control.complete_capacity_handoff(
        handoff.ticket + capacity, handoff.request_id, 0x80'000,
        hbfsim::RequestStatus::Ready,
        {.flags = hbfsim::host_service::CapacityMediaRead}));
    CHECK(control.complete_capacity_handoff(
        handoff.ticket, handoff.request_id, 0x80'000,
        hbfsim::RequestStatus::Ready,
        {.flags = hbfsim::host_service::CapacityMediaRead}));
    hbfsim::host_service::CapacityHandoffResult result{};
    CHECK(control.capacity_handoff_result(request, result));
    CHECK(result.status == hbfsim::RequestStatus::Ready);
    CHECK(result.frame_address == 0x80'000);
    CHECK(result.media.flags == hbfsim::host_service::CapacityMediaRead);
    CHECK(result.media.program_page == 0);
    CHECK(result.media.program_range_id == 0);
    CHECK(control.release_capacity_handoff(request));

    request.sequence += capacity;
    ++request.request_id;
    CHECK(control.begin_capacity_handoff(request));
    CHECK(control.try_capacity_handoff(
        static_cast<std::uint32_t>(request.sequence & (capacity - 1)),
        handoff));
    std::barrier completion_start(3);
    std::atomic<bool> ready_won{false};
    std::atomic<bool> timeout_won{false};
    std::thread ready([&] {
        completion_start.arrive_and_wait();
        ready_won.store(control.complete_capacity_handoff(
                            request.sequence, request.request_id, 0xa0'000,
                            hbfsim::RequestStatus::Ready),
                        std::memory_order_release);
    });
    std::thread timeout([&] {
        completion_start.arrive_and_wait();
        timeout_won.store(control.complete_capacity_handoff(
                              request.sequence, request.request_id, 0,
                              hbfsim::RequestStatus::Timeout),
                          std::memory_order_release);
    });
    completion_start.arrive_and_wait();
    ready.join();
    timeout.join();
    CHECK(ready_won.load(std::memory_order_acquire) !=
          timeout_won.load(std::memory_order_acquire));
    CHECK(control.capacity_handoff_result(request, result));
    CHECK((result.status == hbfsim::RequestStatus::Ready &&
           result.frame_address == 0xa0'000) ||
          (result.status == hbfsim::RequestStatus::Timeout &&
           result.frame_address == 0));
    CHECK(control.release_capacity_handoff(request));

    request.sequence += capacity;
    ++request.request_id;
    CHECK(control.begin_capacity_handoff(request));
    std::binary_semaphore scanner_claimed{0};
    std::binary_semaphore scanner_continue{0};
    const auto block_after_claim = +[](void* opaque) noexcept {
        auto* semaphores = static_cast<std::pair<std::binary_semaphore*,
                                                 std::binary_semaphore*>*>(
            opaque);
        semaphores->first->release();
        semaphores->second->acquire();
    };
    std::pair scanner_semaphores{&scanner_claimed, &scanner_continue};
    bool scanner_succeeded = false;
    std::thread scanner([&] {
        scanner_succeeded = control.try_capacity_handoff_with_hook_for_test(
            static_cast<std::uint32_t>(request.sequence & (capacity - 1)),
            handoff, block_after_claim, &scanner_semaphores);
    });
    scanner_claimed.acquire();
    CHECK(control.complete_capacity_handoff(
        request.sequence, request.request_id, 0,
        hbfsim::RequestStatus::Timeout));
    CHECK(control.release_capacity_handoff(request));
    scanner_continue.release();
    scanner.join();
    CHECK(!scanner_succeeded);
    CHECK(!control.complete_capacity_handoff(
        request.sequence, request.request_id, 0xb0'000,
        hbfsim::RequestStatus::Ready));
    CHECK(!control.release_capacity_handoff(request));
    CHECK(!control.try_capacity_handoff(1, handoff));

    request.sequence += capacity;
    ++request.request_id;
    CHECK(control.begin_capacity_handoff(request));
    CHECK(control.try_capacity_handoff(
        static_cast<std::uint32_t>(request.sequence & (capacity - 1)),
        handoff));
    const auto old_request = request;
    std::binary_semaphore stale_checked{0};
    std::binary_semaphore stale_continue{0};
    std::pair stale_semaphores{&stale_checked, &stale_continue};
    bool stale_succeeded = true;
    std::thread stale([&] {
        stale_succeeded =
            control.complete_capacity_handoff_with_hook_for_test(
                old_request.sequence, old_request.request_id, 0xc0'000,
                hbfsim::RequestStatus::Ready, block_after_claim,
                &stale_semaphores,
                {.flags = hbfsim::host_service::CapacityMediaProgram |
                          hbfsim::host_service::CapacityMediaRead,
                 .program_page = 0xfeed,
                 .program_range_id = 99});
    });
    stale_checked.acquire();
    CHECK(control.complete_capacity_handoff(
        old_request.sequence, old_request.request_id, 0,
        hbfsim::RequestStatus::Timeout));
    CHECK(control.release_capacity_handoff(old_request));

    request.sequence += capacity;
    ++request.request_id;
    CHECK(control.begin_capacity_handoff(request));
    CHECK(control.try_capacity_handoff(
        static_cast<std::uint32_t>(request.sequence & (capacity - 1)),
        handoff));
    stale_continue.release();
    CHECK(control.complete_capacity_handoff(
        request.sequence, request.request_id, 0xd0'000,
        hbfsim::RequestStatus::Ready));
    stale.join();
    CHECK(!stale_succeeded);
    CHECK(control.capacity_handoff_result(request, result));
    CHECK(result.status == hbfsim::RequestStatus::Ready);
    CHECK(result.frame_address == 0xd0'000);
    CHECK(result.media.flags == hbfsim::host_service::CapacityMediaNone);
    CHECK(control.release_capacity_handoff(request));

    request.sequence += capacity;
    ++request.request_id;
    CHECK(control.begin_capacity_handoff(request));
    CHECK(control.complete_capacity_handoff(
        request.sequence, request.request_id, 0xe0'000,
        hbfsim::RequestStatus::Ready,
        {.flags = hbfsim::host_service::CapacityMediaProgram |
                  hbfsim::host_service::CapacityMediaRead,
         .program_page = 0x1234,
         .program_range_id = 7}));
    CHECK(control.capacity_handoff_result(request, result));
    CHECK(result.media.flags ==
          (hbfsim::host_service::CapacityMediaProgram |
           hbfsim::host_service::CapacityMediaRead));
    CHECK(result.media.program_page == 0x1234);
    CHECK(result.media.program_range_id == 7);
    CHECK(control.release_capacity_handoff(request));

    request.sequence += capacity;
    ++request.request_id;
    request.operation = 99;
    CHECK(!control.begin_capacity_handoff(request));
    request.operation = static_cast<std::uint32_t>(
        hbfsim::RequestOperation::Read);
    CHECK(control.begin_capacity_handoff(request));
    CHECK(control.complete_capacity_handoff(
        request.sequence, request.request_id, 0,
        hbfsim::RequestStatus::CopyError));
    CHECK(control.capacity_handoff_result(request, result));
    CHECK(result.status == hbfsim::RequestStatus::CopyError);
    CHECK(result.frame_address == 0);
    CHECK(result.media.flags == hbfsim::host_service::CapacityMediaNone);
    CHECK(control.release_capacity_handoff(request));

    request.sequence = 0;
    request.request_id = 23;
    request.operation = static_cast<std::uint32_t>(
        hbfsim::RequestOperation::Read);
    std::uint64_t ticket = 0;
    CHECK(control.try_push_request(request, ticket));
    hbfsim::HbfRequest submitted{};
    bool finalized = false;
    hbfsim::host_service::RequestDispatcher dispatcher(
        control,
        hbfsim::host_service::RequestDispatcher::Engine{
            .submit = [&](const hbfsim::HbfRequest& value) {
                submitted = value;
            },
            .run_next_completion = [&]()
                -> std::optional<hbfsim::HbfCompletion> {
                hbfsim::HbfCompletion completion{};
                completion.request_id = submitted.request_id;
                completion.page_generation = submitted.page_generation;
                completion.status = static_cast<std::uint32_t>(
                    hbfsim::RequestStatus::Ready);
                return completion;
            },
            .finalize = [&](const hbfsim::HbfRequest& value,
                            hbfsim::HbfCompletion& completion) {
                CHECK(value.sequence == ticket);
                completion.cache_frame_address = 0x90'000;
                finalized = true;
            },
        });
    CHECK(dispatcher.poll_once());
    CHECK(finalized);
    hbfsim::HbfCompletion completion{};
    CHECK(control.try_consume_completion(ticket, completion));
    CHECK(completion.cache_frame_address == 0x90'000);

    std::free(storage);
    return 0;
}

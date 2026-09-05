#include <hbfsim/shadow_future.hpp>

#include <cstdint>
#include <stdexcept>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

hbfsim::ShadowFutureRequest timing_request(std::uint32_t instruction_id,
                                           std::uint64_t ready_ns)
{
    return {
        .original_address = 0x1000,
        .resolved_address = 0x1000,
        .issue_ns = 0,
        .ready_ns = ready_ns,
        .deadline_ns = 1'000,
        .bytes = 8,
        .instruction_id = instruction_id,
        .channel = 1,
        .flags = hbfsim::FutureTiming,
        .scope = hbfsim::FutureScope::Thread,
    };
}

}  // namespace

int main()
{
    static_assert(sizeof(hbfsim::ShadowFuture) == 64);

    hbfsim::ShadowFutureMachine machine(2);
    auto native_request = timing_request(1, 0);
    native_request.flags = hbfsim::FutureNative;
    auto native = machine.issue(native_request);
    require(native.state == hbfsim::FutureState::Native,
            "native request did not bypass shadow state");
    require(machine.poll(native) == hbfsim::FuturePoll::Native,
            "native request did not poll as native");

    auto timing = machine.issue(timing_request(2, 50));
    require(timing.state == hbfsim::FutureState::Issued,
            "timing future was not issued");
    require(machine.poll(timing) == hbfsim::FuturePoll::Pending,
            "timing future became ready too early");
    machine.advance_to(50);
    require(machine.poll(timing) == hbfsim::FuturePoll::Ready,
            "timing future did not become ready");
    require(machine.wait(timing, hbfsim::FutureWaitKind::Dependency).state ==
                hbfsim::FutureState::Ready,
            "ready timing future could not be waited");
    require(machine.consume(timing), "ready future could not be consumed");
    require(!machine.consume(timing), "future was consumed twice");

    hbfsim::ShadowFutureRequest capacity_request{
        .original_address = 0x20'123,
        .issue_ns = 60,
        .deadline_ns = 1'000,
        .bytes = 4,
        .instruction_id = 3,
        .flags = hbfsim::FutureCapacity,
        .scope = hbfsim::FutureScope::Cta,
    };
    auto capacity = machine.issue(capacity_request);
    require(machine.poll(capacity) == hbfsim::FuturePoll::Pending,
            "capacity future did not remain pending");
    const hbfsim::ShadowFutureCompletion capacity_completion{
        .resolved_address = 0x80'123,
        .ready_ns = 90,
        .status = hbfsim::RequestStatus::Ready,
        .deferred_materialization = true,
    };
    require(machine.complete(capacity.ticket, capacity_completion),
            "capacity future completion failed");
    require(!machine.complete(capacity.ticket, capacity_completion),
            "duplicate terminal completion was accepted");
    require(machine.poll(capacity) == hbfsim::FuturePoll::Pending,
            "modeled capacity completion ignored ready time");
    const auto materialized =
        machine.wait(capacity, hbfsim::FutureWaitKind::Dependency);
    require(materialized.state ==
                hbfsim::FutureState::DeferredMaterialization &&
                materialized.resolved_address == 0x80'123,
            "capacity future lost deferred resident address");

    hbfsim::ShadowFutureMachine throttled(1);
    auto first = throttled.issue(timing_request(10, 25));
    auto second_request = timing_request(11, 35);
    auto second = throttled.issue(second_request);
    require(first.ticket != second.ticket &&
                throttled.now_ns() == 25 &&
                throttled.counters().issue_throttle_ns == 25,
            "slot pressure did not throttle until a terminal slot");

    hbfsim::ShadowFutureMachine failures(4);
    auto timeout_request = timing_request(20, 0);
    timeout_request.flags = hbfsim::FutureCapacity;
    timeout_request.deadline_ns = 10;
    auto timed_out = failures.issue(timeout_request);
    failures.advance_to(10);
    require(failures.poll(timed_out) == hbfsim::FuturePoll::TerminalError &&
                timed_out.status == hbfsim::RequestStatus::Timeout,
            "deadline did not terminally fail the future");

    auto daemon_request = timeout_request;
    daemon_request.instruction_id = 21;
    daemon_request.issue_ns = 10;
    daemon_request.deadline_ns = 100;
    auto daemon_lost = failures.issue(daemon_request);
    require(failures.complete(
                daemon_lost.ticket,
                {.ready_ns = 11,
                 .status = hbfsim::RequestStatus::DaemonLost}),
            "daemon failure completion was rejected");
    failures.advance_to(11);
    require(failures.poll(daemon_lost) ==
                hbfsim::FuturePoll::TerminalError,
            "daemon failure was not terminal");

    hbfsim::ShadowFutureMachine drains(8);
    auto thread_request = timing_request(30, 20);
    thread_request.scope = hbfsim::FutureScope::Thread;
    auto cta_request = timing_request(31, 30);
    cta_request.scope = hbfsim::FutureScope::Cta;
    auto thread_future = drains.issue(thread_request);
    auto cta_future = drains.issue(cta_request);
    require(drains.drain(hbfsim::FutureScope::Warp,
                         hbfsim::FutureWaitKind::Ordering) == 1,
            "warp drain did not select only narrower futures");
    require(drains.poll(thread_future) == hbfsim::FuturePoll::Ready &&
                drains.poll(cta_future) == hbfsim::FuturePoll::Pending,
            "scoped drain affected the wrong futures");
    require(drains.drain(hbfsim::FutureScope::System,
                         hbfsim::FutureWaitKind::Ordering) == 1,
            "system drain did not finish the CTA future");

    hbfsim::ShadowFutureMachine scheduled_completion(2);
    auto scheduled_request = capacity_request;
    scheduled_request.issue_ns = 0;
    scheduled_request.deadline_ns = 100;
    scheduled_request.scope = hbfsim::FutureScope::Thread;
    auto scheduled = scheduled_completion.issue(scheduled_request);
    require(scheduled_completion.complete(
                scheduled.ticket,
                {.resolved_address = 0x90'123,
                 .ready_ns = 40,
                 .status = hbfsim::RequestStatus::Ready,
                 .deferred_materialization = true}),
            "scheduled completion could not be installed");
    require(scheduled_completion.drain(
                hbfsim::FutureScope::System,
                hbfsim::FutureWaitKind::Ordering) == 1 &&
                scheduled_completion.now_ns() == 40,
            "drain skipped a completion whose modeled ready time is future");

    const auto counters = failures.counters();
    require(counters.issued == 2 && counters.faults == 2 &&
                counters.terminal_completions == 2,
            "failure counter conservation is wrong");
    require(machine.counters().dependency_wait_ns == 30,
            "dependency wait time is wrong");
    require(drains.counters().ordering_wait_ns == 30,
            "ordering wait time is wrong");
    return 0;
}

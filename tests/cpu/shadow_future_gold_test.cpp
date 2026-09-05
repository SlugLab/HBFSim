#include <hbfsim/shadow_future.hpp>

#include <functional>
#include <iostream>
#include <stdexcept>
#include <utility>
#include <vector>

namespace {
void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

hbfsim::ShadowFutureRequest request()
{
    return {.original_address = 4096, .resolved_address = 4096,
            .issue_ns = 0, .ready_ns = 40, .deadline_ns = 100,
            .bytes = 4, .instruction_id = 1,
            .flags = hbfsim::FutureCapacity};
}
}  // namespace

int main()
{
    using namespace hbfsim;
    const std::vector<std::pair<const char*, std::function<void()>>> cases{
        {"completion_is_not_arrival", [] {
            ShadowFutureMachine machine(1);
            auto future = machine.issue(request());
            require(machine.complete(future.ticket, {.resolved_address = 4096,
                        .ready_ns = 40}), "completion rejected");
            require(!machine.consume(future), "consumed before modeled arrival");
            machine.advance_to(40);
            require(machine.consume(future), "ready value not consumed");
            require(!machine.consume(future), "double consume accepted");
        }},
        {"deadline_precedes_late_ready", [] {
            ShadowFutureMachine machine(1);
            auto input = request();
            input.flags = FutureTiming;
            input.ready_ns = 200;
            auto future = machine.issue(input);
            machine.advance_to(300);
            require(machine.poll(future) == FuturePoll::TerminalError &&
                    future.status == RequestStatus::Timeout,
                    "time jump skipped earlier deadline");
        }},
        {"wait_uses_first_terminal_event", [] {
            ShadowFutureMachine machine(1);
            auto input = request();
            input.flags = FutureTiming;
            input.ready_ns = 200;
            auto future = machine.issue(input);
            (void)machine.wait(future, FutureWaitKind::Dependency);
            require(future.status == RequestStatus::Timeout && machine.now_ns() == 100,
                    "wait ignored earlier deadline");
        }},
        {"external_completion_after_deadline", [] {
            ShadowFutureMachine machine(1);
            auto future = machine.issue(request());
            require(machine.complete(future.ticket, {.resolved_address = 4096,
                        .ready_ns = 200}), "completion rejected");
            (void)machine.wait(future, FutureWaitKind::Dependency);
            require(future.status == RequestStatus::Timeout && machine.now_ns() == 100,
                    "future arrival defeated deadline");
        }},
        {"missing_deadline_rejected", [] {
            ShadowFutureMachine machine(1);
            auto input = request(); input.deadline_ns = 0;
            bool rejected = false;
            try { (void)machine.issue(input); }
            catch (const std::invalid_argument&) { rejected = true; }
            require(rejected, "unbounded wait admitted");
        }},
        {"residual_oracle", [] {
            for (const auto work : {0ULL, 20ULL, 40ULL, 80ULL}) {
                ShadowFutureMachine machine(1);
                auto input = request(); input.flags = FutureTiming;
                auto future = machine.issue(input);
                machine.advance_to(work);
                (void)machine.wait(future, FutureWaitKind::Dependency);
                require(future.status == RequestStatus::Ready &&
                        machine.counters().dependency_wait_ns == (work < 40 ? 40 - work : 0),
                        "residual is not max(0,D-W)");
            }
        }},
        {"native_work_advances_pending_deadlines", [] {
            ShadowFutureMachine machine(1);
            auto input = request(); input.deadline_ns = 10;
            (void)machine.issue(input);
            input.flags = FutureNative; input.issue_ns = 20;
            (void)machine.issue(input);
            const auto counters = machine.counters();
            require(counters.terminal_completions == 1 && counters.faults == 1,
                    "native work skipped pending deadline");
            require(machine.drain(FutureScope::System, FutureWaitKind::Ordering) == 0,
                    "expired future remained in drain set");
        }},
    };
    unsigned failures = 0;
    for (const auto& [name, run] : cases) {
        try { run(); std::cout << "PASS " << name << '\n'; }
        catch (const std::exception& error) {
            ++failures; std::cerr << "FAIL " << name << ": " << error.what() << '\n';
        }
    }
    return failures ? 1 : 0;
}

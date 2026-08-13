#include <hbfsim/shadow_future.hpp>

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <vector>

namespace hbfsim {
namespace {

bool scope_covers(FutureScope drain, FutureScope future) noexcept
{
    return static_cast<std::uint32_t>(drain) >=
           static_cast<std::uint32_t>(future);
}

}  // namespace

ShadowFutureMachine::ShadowFutureMachine(std::size_t maximum_outstanding)
    : maximum_outstanding_(maximum_outstanding)
{
    if (maximum_outstanding_ == 0) {
        throw std::invalid_argument("shadow future capacity must be nonzero");
    }
}

void ShadowFutureMachine::make_terminal(Record& record, FutureState state,
                                        RequestStatus status)
{
    if (record.future.state != FutureState::Issued) {
        return;
    }
    record.future.state = state;
    record.future.status = status;
    if (!record.terminal_counted) {
        record.terminal_counted = true;
        ++counters_.terminal_completions;
        if (state == FutureState::TerminalError) {
            ++counters_.faults;
        }
    }
}

void ShadowFutureMachine::refresh_record(Record& record)
{
    if (record.future.state != FutureState::Issued) {
        return;
    }
    if ((record.future.flags & FutureTiming) != 0 &&
        record.future.ready_ns <= now_ns_) {
        make_terminal(record, FutureState::Ready, RequestStatus::Ready);
        return;
    }
    if (record.deadline_ns != 0 && record.deadline_ns <= now_ns_) {
        record.future.ready_ns = now_ns_;
        make_terminal(record, FutureState::TerminalError,
                      RequestStatus::Timeout);
    }
}

void ShadowFutureMachine::refresh_all()
{
    for (auto& [ticket, record] : records_) {
        (void)ticket;
        refresh_record(record);
    }
}

std::size_t ShadowFutureMachine::active_issued() const
{
    return static_cast<std::size_t>(std::count_if(
        records_.begin(), records_.end(), [](const auto& entry) {
            return entry.second.future.state == FutureState::Issued;
        }));
}

std::uint64_t ShadowFutureMachine::next_progress_time() const
{
    auto next = std::numeric_limits<std::uint64_t>::max();
    for (const auto& [ticket, record] : records_) {
        (void)ticket;
        if (record.future.state != FutureState::Issued) {
            continue;
        }
        if ((record.future.flags & FutureTiming) != 0 &&
            record.future.ready_ns > now_ns_) {
            next = std::min(next, record.future.ready_ns);
        }
        if (record.deadline_ns > now_ns_) {
            next = std::min(next, record.deadline_ns);
        }
    }
    return next;
}

ShadowFuture ShadowFutureMachine::issue(const ShadowFutureRequest& request)
{
    std::scoped_lock lock(mutex_);
    now_ns_ = std::max(now_ns_, request.issue_ns);
    if ((request.flags & FutureNative) != 0) {
        return {
            .original_address = request.original_address,
            .resolved_address = request.resolved_address == 0
                                    ? request.original_address
                                    : request.resolved_address,
            .ready_ns = now_ns_,
            .bytes = request.bytes,
            .instruction_id = request.instruction_id,
            .channel = request.channel,
            .flags = request.flags,
            .state = FutureState::Native,
            .status = RequestStatus::Ready,
        };
    }

    refresh_all();
    const auto throttle_begin = now_ns_;
    while (active_issued() >= maximum_outstanding_) {
        const auto next = next_progress_time();
        if (next == std::numeric_limits<std::uint64_t>::max()) {
            throw std::runtime_error(
                "shadow future slots cannot make terminal progress");
        }
        now_ns_ = next;
        refresh_all();
    }
    counters_.issue_throttle_ns += now_ns_ - throttle_begin;

    ShadowFuture future{
        .ticket = next_ticket_++,
        .original_address = request.original_address,
        .resolved_address = request.resolved_address,
        .ready_ns = request.ready_ns,
        .bytes = request.bytes,
        .instruction_id = request.instruction_id,
        .channel = request.channel,
        .flags = request.flags,
        .state = FutureState::Issued,
        .status = RequestStatus::Pending,
    };
    records_.emplace(future.ticket,
                     Record{.future = future,
                            .deadline_ns = request.deadline_ns,
                            .scope = request.scope});
    ++counters_.issued;
    refresh_record(records_.at(future.ticket));
    return records_.at(future.ticket).future;
}

FuturePoll ShadowFutureMachine::poll(ShadowFuture& future)
{
    std::scoped_lock lock(mutex_);
    if (future.state == FutureState::Native) {
        return FuturePoll::Native;
    }
    const auto found = records_.find(future.ticket);
    if (found == records_.end()) {
        future.state = FutureState::TerminalError;
        future.status = RequestStatus::Unsupported;
        return FuturePoll::TerminalError;
    }
    refresh_record(found->second);
    future = found->second.future;
    if ((future.state == FutureState::Ready ||
         future.state == FutureState::DeferredMaterialization ||
         future.state == FutureState::TerminalError) &&
        future.ready_ns > now_ns_) {
        return FuturePoll::Pending;
    }
    switch (future.state) {
    case FutureState::Native: return FuturePoll::Native;
    case FutureState::Issued: return FuturePoll::Pending;
    case FutureState::Ready: return FuturePoll::Ready;
    case FutureState::DeferredMaterialization:
        return FuturePoll::DeferredMaterialization;
    case FutureState::TerminalError: return FuturePoll::TerminalError;
    case FutureState::Consumed: return FuturePoll::Consumed;
    }
    return FuturePoll::TerminalError;
}

bool ShadowFutureMachine::complete(
    std::uint64_t ticket, const ShadowFutureCompletion& completion)
{
    std::scoped_lock lock(mutex_);
    const auto found = records_.find(ticket);
    if (found == records_.end() ||
        found->second.future.state != FutureState::Issued) {
        return false;
    }
    auto& record = found->second;
    record.future.resolved_address = completion.resolved_address;
    record.future.ready_ns = completion.ready_ns == 0 ? now_ns_
                                                       : completion.ready_ns;
    if (completion.status == RequestStatus::Ready) {
        make_terminal(record,
                      completion.deferred_materialization
                          ? FutureState::DeferredMaterialization
                          : FutureState::Ready,
                      RequestStatus::Ready);
    } else {
        make_terminal(record, FutureState::TerminalError,
                      completion.status);
    }
    return true;
}

ShadowFuture ShadowFutureMachine::wait(ShadowFuture& future,
                                       FutureWaitKind kind)
{
    const auto begin = now_ns();
    for (;;) {
        const auto state = poll(future);
        if (state != FuturePoll::Pending) {
            break;
        }
        std::uint64_t next = 0;
        {
            std::scoped_lock lock(mutex_);
            const auto found = records_.find(future.ticket);
            if (found == records_.end()) {
                break;
            }
            const auto& record = found->second;
            if (record.future.state != FutureState::Issued &&
                record.future.ready_ns > now_ns_) {
                next = record.future.ready_ns;
            } else if ((record.future.flags & FutureTiming) != 0 &&
                       record.future.ready_ns > now_ns_) {
                next = record.future.ready_ns;
            } else if (record.deadline_ns > now_ns_) {
                next = record.deadline_ns;
            }
        }
        if (next == 0) {
            break;
        }
        advance_to(next);
    }
    const auto elapsed = now_ns() - begin;
    {
        std::scoped_lock lock(mutex_);
        if (kind == FutureWaitKind::Dependency) {
            counters_.dependency_wait_ns += elapsed;
        } else {
            counters_.ordering_wait_ns += elapsed;
        }
    }
    return future;
}

bool ShadowFutureMachine::consume(ShadowFuture& future)
{
    std::scoped_lock lock(mutex_);
    const auto found = records_.find(future.ticket);
    if (found == records_.end() ||
        (found->second.future.state != FutureState::Ready &&
         found->second.future.state !=
             FutureState::DeferredMaterialization)) {
        return false;
    }
    found->second.future.state = FutureState::Consumed;
    future = found->second.future;
    return true;
}

std::size_t ShadowFutureMachine::drain(FutureScope scope, FutureWaitKind kind)
{
    std::vector<ShadowFuture> selected;
    {
        std::scoped_lock lock(mutex_);
        for (const auto& [ticket, record] : records_) {
            (void)ticket;
            const bool modeled_arrival_pending =
                (record.future.state == FutureState::Ready ||
                 record.future.state ==
                     FutureState::DeferredMaterialization ||
                 record.future.state == FutureState::TerminalError) &&
                record.future.ready_ns > now_ns_;
            if (scope_covers(scope, record.scope) &&
                (record.future.state == FutureState::Issued ||
                 modeled_arrival_pending)) {
                selected.push_back(record.future);
            }
        }
    }
    for (auto& future : selected) {
        (void)wait(future, kind);
    }
    return selected.size();
}

void ShadowFutureMachine::advance_to(std::uint64_t time_ns)
{
    std::scoped_lock lock(mutex_);
    now_ns_ = std::max(now_ns_, time_ns);
    refresh_all();
}

std::uint64_t ShadowFutureMachine::now_ns() const
{
    std::scoped_lock lock(mutex_);
    return now_ns_;
}

FutureCounters ShadowFutureMachine::counters() const
{
    std::scoped_lock lock(mutex_);
    return counters_;
}

}  // namespace hbfsim

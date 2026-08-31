#include "request_dispatcher.hpp"

#include <hbfsim/api.h>

#include <algorithm>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <thread>
#include <utility>
#include <vector>

namespace hbfsim::host_service {
namespace {

HbfCompletion failed_completion(const HbfRequest& request,
                                RequestStatus status) noexcept
{
    return HbfCompletion{
        .request_id = request.request_id,
        .modeled_completion_ns = 0,
        .modeled_ns = 0,
        .service_ns = 0,
        .cache_frame_address = 0,
        .page_generation = request.page_generation,
        .status = static_cast<std::uint32_t>(status),
        .checksum = 0,
        .reserved = 0,
    };
}

bool terminal(std::uint32_t status) noexcept
{
    return status != static_cast<std::uint32_t>(RequestStatus::Pending) &&
           status <= static_cast<std::uint32_t>(RequestStatus::DaemonLost);
}

bool valid_prepared_action(const HbfRequest& action,
                           const HbfRequest& original) noexcept
{
    return action.bytes != 0 &&
           action.operation <=
               static_cast<std::uint32_t>(RequestOperation::Write) &&
           action.page_generation == original.page_generation;
}

bool valid_explicit_capacity_program(
    const HbfRequest& request,
    const SharedRangeRecord& range) noexcept
{
    if (request.flags != kRequestFlagExplicitCapacityProgram ||
        request.operation !=
            static_cast<std::uint32_t>(RequestOperation::Write) ||
        request.bytes == 0 || request.bytes != range.page_bytes ||
        range.length == 0 || range.file_offset % request.bytes != 0 ||
        request.logical_address < range.file_offset ||
        request.logical_address % request.bytes != 0) {
        return false;
    }
    const auto page_count =
        range.length / request.bytes +
        static_cast<std::uint64_t>(range.length % request.bytes != 0);
    const auto page =
        (request.logical_address - range.file_offset) / request.bytes;
    return page < page_count;
}

}  // namespace

PreparedDispatch prepare_capacity_media_dispatch(
    const HbfRequest& request, const CapacityHandoffResult& result,
    std::uint64_t service_ns) noexcept
{
    PreparedDispatch prepared{
        .completion = {
            .request_id = request.request_id,
            .modeled_completion_ns = 0,
            .modeled_ns = 0,
            .service_ns = service_ns,
            .cache_frame_address = result.frame_address,
            .page_generation = request.page_generation,
            .status = static_cast<std::uint32_t>(result.status),
            .checksum = 0,
            .reserved = 0,
        },
    };
    const auto invalidate = [&] {
        prepared.completion.status =
            static_cast<std::uint32_t>(RequestStatus::IoError);
        prepared.completion.cache_frame_address = 0;
        prepared.media_action_count = 0;
    };
    if (request.request_id == 0 || request.bytes == 0 ||
        result.status == RequestStatus::Pending ||
        result.status > RequestStatus::DaemonLost ||
        !valid_capacity_media_plan(result.media) ||
        (result.status == RequestStatus::Ready) !=
            (result.frame_address != 0) ||
        (result.status != RequestStatus::Ready &&
         result.media.flags != CapacityMediaNone)) {
        invalidate();
        return prepared;
    }
    if (result.status != RequestStatus::Ready) {
        return prepared;
    }
    if ((result.media.flags & CapacityMediaProgram) != 0) {
        if (result.media.program_page >
            std::numeric_limits<std::uint64_t>::max() / request.bytes) {
            invalidate();
            return prepared;
        }
        auto program = request;
        program.logical_address = result.media.program_page * request.bytes;
        program.range_id = result.media.program_range_id;
        program.operation =
            static_cast<std::uint32_t>(RequestOperation::Write);
        program.flags |= kRequestFlagExplicitCapacityProgram;
        prepared.media_actions[prepared.media_action_count++] = program;
    }
    if ((result.media.flags & CapacityMediaRead) != 0) {
        auto read = request;
        read.operation = static_cast<std::uint32_t>(RequestOperation::Read);
        read.flags &= ~kRequestFlagExplicitCapacityProgram;
        prepared.media_actions[prepared.media_action_count++] = read;
    }
    return prepared;
}

PreparedDispatch prepare_host_dispatch(ControlView control,
                                       const HbfRequest& request,
                                       const DispatchNow& now,
                                       const DispatchWait& wait,
                                       bool capacity_media_enabled) noexcept
{
    const auto failure = [&] {
        return PreparedDispatch{.completion = failed_completion(
                                    request, RequestStatus::IoError)};
    };
    if (!control.valid() || !now || !wait) {
        return failure();
    }
    const SharedRangeRecord* range = nullptr;
    const auto count = atomic_load(control.header()->range_count,
                                   std::memory_order_acquire);
    if (count > kRangeCapacity) {
        return failure();
    }
    for (std::uint32_t index = 0; index < count; ++index) {
        if (control.ranges()[index].range_id == request.range_id) {
            range = &control.ranges()[index];
            break;
        }
    }

    if (range != nullptr && range->mode == HBFSIM_RANGE_MODE_CAPACITY &&
        !capacity_media_enabled) {
        return PreparedDispatch{.completion = failed_completion(
                                    request, RequestStatus::Unsupported)};
    }

    const auto explicit_program =
        (request.flags & kRequestFlagExplicitCapacityProgram) != 0;
    if (explicit_program) {
        if (range == nullptr || range->mode != HBFSIM_RANGE_MODE_CAPACITY ||
            !valid_explicit_capacity_program(request, *range)) {
            return failure();
        }
        return PreparedDispatch{
            .completion = failed_completion(request, RequestStatus::Ready),
            .media_actions = {request},
            .media_action_count = 1,
        };
    }
    if (range == nullptr || range->mode != HBFSIM_RANGE_MODE_CAPACITY) {
        return PreparedDispatch{
            .completion = failed_completion(request, RequestStatus::Ready),
            .media_actions = {request},
            .media_action_count = 1,
        };
    }
    if (request.bytes != range->page_bytes ||
        !control.begin_capacity_handoff(request)) {
        return failure();
    }

    std::uint64_t started = 0;
    bool fail_all = false;
    try {
        started = now();
    } catch (...) {
        fail_all = true;
        (void)control.complete_capacity_handoff(
            request.sequence, request.request_id, 0,
            RequestStatus::IoError);
    }
    CapacityHandoffResult result{};
    while (!control.capacity_handoff_result(request, result)) {
        bool timed_out = false;
        RequestStatus failure_status = RequestStatus::IoError;
        try {
            const auto fault = atomic_load(control.header()->fault,
                                           std::memory_order_acquire);
            const auto shutdown = atomic_load(control.header()->shutdown,
                                              std::memory_order_acquire);
            const auto current = now();
            timed_out = fault != 0 || shutdown != 0 || current < started ||
                        current - started >=
                            control.header()->request_timeout_ns;
            failure_status = fault != 0 || shutdown != 0
                                 ? RequestStatus::DaemonLost
                                 : RequestStatus::Timeout;
        } catch (...) {
            timed_out = true;
        }
        if (timed_out) {
            fail_all = true;
            if (control.complete_capacity_handoff(
                    request.sequence, request.request_id, 0,
                    failure_status)) {
                result = {.status = failure_status};
            } else if (!control.capacity_handoff_result(request, result)) {
                result = {.status = RequestStatus::IoError};
            }
            break;
        }
        try {
            wait();
        } catch (...) {
            fail_all = true;
            if (control.complete_capacity_handoff(
                    request.sequence, request.request_id, 0,
                    RequestStatus::IoError)) {
                result = {.status = RequestStatus::IoError};
            } else if (!control.capacity_handoff_result(request, result)) {
                result = {.status = RequestStatus::IoError};
            }
            break;
        }
    }
    std::uint64_t service_ns = 0;
    try {
        const auto finished = now();
        service_ns = finished >= started ? finished - started : 0;
    } catch (...) {
    }
    if (!control.release_capacity_handoff(request)) {
        fail_all = true;
        result = {.status = RequestStatus::IoError};
    }
    auto prepared =
        prepare_capacity_media_dispatch(request, result, service_ns);
    prepared.fail_all = fail_all;
    return prepared;
}

RequestDispatcher::RequestDispatcher(ControlView control, Engine engine)
    : control_(control), engine_(std::move(engine))
{
    if (!control_.valid() || !engine_.submit ||
        !engine_.run_next_completion) {
        throw std::invalid_argument("invalid request dispatcher configuration");
    }
}

std::optional<std::uint64_t> RequestDispatcher::next_engine_id() noexcept
{
    if (engine_ids_exhausted_) {
        return std::nullopt;
    }
    const auto id = next_engine_id_;
    if (id == 0) {
        return std::nullopt;
    }
    if (id == std::numeric_limits<std::uint64_t>::max()) {
        engine_ids_exhausted_ = true;
    } else {
        ++next_engine_id_;
    }
    return id;
}

bool RequestDispatcher::submit_next(std::uint64_t ticket,
                                    DispatchGroup& group)
{
    if (group.next_action >= group.prepared.media_action_count) {
        return false;
    }
    const auto engine_id = next_engine_id();
    if (!engine_id.has_value()) {
        return false;
    }
    auto action = group.prepared.media_actions[group.next_action];
    if (group.next_action != 0) {
        action.arrival_ns = std::max(
            action.arrival_ns,
            group.prepared.completion.modeled_completion_ns);
    }
    action.request_id = *engine_id;
    const auto [mapping, inserted] =
        ticket_by_engine_id_.emplace(*engine_id, ticket);
    (void)mapping;
    if (!inserted) {
        return false;
    }
    try {
        engine_.submit(action);
    } catch (...) {
        ticket_by_engine_id_.erase(*engine_id);
        return false;
    }
    ++group.next_action;
    return true;
}

bool RequestDispatcher::submit_speculative(const HbfRequest& request)
{
    // 64 is arbitrary but bounded. The readahead is advisory, so losing the
    // oldest speculative read is better than letting this grow.
    constexpr std::size_t kSpeculativeQueueLimit = 64;
    if (speculative_.size() >= kSpeculativeQueueLimit) {
        ++speculative_dropped_;
        return false;
    }
    try {
        speculative_.push_back(request);
    } catch (...) {
        ++speculative_dropped_;
        return false;
    }
    return true;
}

bool RequestDispatcher::drain_one_speculative()
{
    if (speculative_.empty()) {
        return false;
    }
    auto request = speculative_.front();
    speculative_.pop_front();

    const auto ticket = kSpeculativeTicketBit | next_speculative_ticket_++;
    // UNVERIFIED, PLEASE CHECK: next_speculative_ticket_ wraps after 2^63
    // speculative reads. That is not reachable in any run this project makes,
    // but nothing here enforces it.
    request.sequence = ticket;
    PreparedDispatch prepared{};
    prepared.media_actions[0] = request;
    prepared.media_action_count = 1;

    const auto [group_it, inserted] = groups_by_ticket_.emplace(
        ticket, DispatchGroup{.original = request, .prepared = prepared});
    if (!inserted) {
        // A ticket collision here would mean the reserved space is not
        // reserved. Drop the speculative read rather than disturb a real
        // group: a missing speculative read costs accuracy, touching someone
        // else's group costs correctness.
        ++speculative_dropped_;
        return false;
    }
    if (!submit_next(ticket, group_it->second)) {
        groups_by_ticket_.erase(group_it);
        ++speculative_dropped_;
        return false;
    }
    ++speculative_submitted_;
    return true;
}

bool RequestDispatcher::publish(std::uint64_t ticket,
                                DispatchGroup& group)
{
    if ((ticket & kSpeculativeTicketBit) != 0) {
        // Nothing is waiting on a speculative read, so there is no slot to
        // write and no completion to hand back. The modeled time it spent in
        // the engine has already done its work: it occupied the engine while
        // demand traffic was queued behind it.
        //
        // Deliberately does NOT erase the group here. Both callers erase after
        // publish returns, one of them through the very iterator that was used
        // to reach this call, so erasing here invalidated that iterator and
        // the caller then erased through it.
        return true;
    }
    auto& completion = group.prepared.completion;
    if (group.legacy && engine_.finalize) {
        try {
            engine_.finalize(group.original, completion);
        } catch (...) {
            return false;
        }
    }
    if (!terminal(completion.status) ||
        completion.request_id != group.original.request_id ||
        completion.page_generation != group.original.page_generation) {
        return false;
    }
    return control_.try_publish_completion(ticket, completion);
}

bool RequestDispatcher::poll_once()
{
    if (atomic_load(control_.header()->fault, std::memory_order_acquire) != 0) {
        return false;
    }

    bool progressed = false;
    HbfRequest request{};
    while (control_.try_pop_request(request)) {
        progressed = true;
        const auto [group_it, inserted] = groups_by_ticket_.emplace(
            request.sequence, DispatchGroup{.original = request});
        if (!inserted) {
            fail_all();
            return true;
        }
        auto& group = group_it->second;
        if (engine_.prepare) {
            try {
                group.prepared = engine_.prepare(request);
            } catch (...) {
                fail_all();
                return true;
            }
            if (group.prepared.fail_all) {
                fail_all();
                return true;
            }
            bool actions_valid = true;
            for (std::uint32_t index = 0;
                 index < group.prepared.media_action_count &&
                 index < group.prepared.media_actions.size();
                 ++index) {
                actions_valid &= valid_prepared_action(
                    group.prepared.media_actions[index], request);
            }
            if (group.prepared.media_action_count >
                    group.prepared.media_actions.size() ||
                !actions_valid ||
                group.prepared.completion.request_id != request.request_id ||
                group.prepared.completion.page_generation !=
                    request.page_generation ||
                !terminal(group.prepared.completion.status) ||
                (group.prepared.media_action_count != 0 &&
                 group.prepared.completion.status !=
                     static_cast<std::uint32_t>(RequestStatus::Ready)) ||
                (group.prepared.media_action_count == 0 &&
                 group.prepared.completion.modeled_ns != 0)) {
                fail_all();
                return true;
            }
        } else {
            group.legacy = true;
            group.prepared.completion = failed_completion(
                request, RequestStatus::Ready);
            group.prepared.media_actions[0] = request;
            group.prepared.media_action_count = 1;
        }
        if (group.prepared.media_action_count == 0) {
            if (!publish(group_it->first, group)) {
                fail_all();
                return true;
            }
            groups_by_ticket_.erase(group_it);
        } else if (!submit_next(group_it->first, group)) {
            fail_all();
            return true;
        }
    }

    // A speculative read goes in only when this poll found no demand to pop
    // and nothing is already in flight, so it can never delay a request a warp
    // is blocked on. The completion loop below then drains it, and publish()
    // discards its completion because no slot is behind it.
    //
    // One per poll on purpose: the demand ring is re-read on the next poll
    // before another speculative read is considered.
    if (!progressed && groups_by_ticket_.empty()) {
        (void)drain_one_speculative();
    }

    while (!groups_by_ticket_.empty()) {
        std::optional<HbfCompletion> completion;
        try {
            completion = engine_.run_next_completion();
        } catch (...) {
            fail_all();
            return true;
        }
        if (!completion.has_value()) {
            fail_all();
            return true;
        }
        progressed = true;
        const auto request_ticket =
            ticket_by_engine_id_.find(completion->request_id);
        if (request_ticket == ticket_by_engine_id_.end()) {
            fail_all();
            return true;
        }
        const auto descriptor =
            groups_by_ticket_.find(request_ticket->second);
        if (descriptor == groups_by_ticket_.end()) {
            fail_all();
            return true;
        }
        auto& group = descriptor->second;
        const auto action_index = group.next_action - 1;
        if (action_index >= group.prepared.media_action_count ||
            completion->page_generation !=
                group.prepared.media_actions[action_index].page_generation ||
            !terminal(completion->status) ||
            completion->status !=
                static_cast<std::uint32_t>(RequestStatus::Ready)) {
            fail_all();
            return true;
        }
        ticket_by_engine_id_.erase(request_ticket);
        if (group.legacy) {
            group.prepared.completion = *completion;
            group.prepared.completion.request_id = group.original.request_id;
        } else {
            auto& aggregate = group.prepared.completion;
            if (aggregate.modeled_ns >
                    std::numeric_limits<std::uint64_t>::max() -
                        completion->modeled_ns ||
                aggregate.service_ns >
                    std::numeric_limits<std::uint64_t>::max() -
                        completion->service_ns) {
                fail_all();
                return true;
            }
            aggregate.modeled_ns += completion->modeled_ns;
            aggregate.service_ns += completion->service_ns;
            aggregate.modeled_completion_ns =
                completion->modeled_completion_ns;
        }
        if (group.next_action < group.prepared.media_action_count) {
            if (!submit_next(descriptor->first, group)) {
                fail_all();
                return true;
            }
            continue;
        }
        if (!publish(descriptor->first, group)) {
            fail_all();
            return true;
        }
        groups_by_ticket_.erase(descriptor);
    }
    return progressed;
}

void RequestDispatcher::fail_all() noexcept
{
    (void)atomic_fetch_or(control_.header()->admission_state,
                          kAdmissionClosedBit, std::memory_order_acq_rel);
    while ((atomic_load(control_.header()->admission_state,
                        std::memory_order_acquire) &
            kAdmissionCountMask) != 0) {
        std::this_thread::yield();
    }
    HbfRequest queued{};
    while (control_.try_pop_request(queued)) {
        groups_by_ticket_.emplace(
            queued.sequence, DispatchGroup{.original = queued});
    }
    for (const auto& [ticket, group] : groups_by_ticket_) {
        (void)control_.try_publish_completion(
            ticket,
            failed_completion(group.original, RequestStatus::IoError));
    }
    groups_by_ticket_.clear();
    ticket_by_engine_id_.clear();
    atomic_store(control_.header()->fault, HBFSIM_IO_ERROR,
                 std::memory_order_release);
}

}  // namespace hbfsim::host_service

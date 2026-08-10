#include "request_dispatcher.hpp"

#include <hbfsim/api.h>

#include <cstdint>
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

}  // namespace

RequestDispatcher::RequestDispatcher(ControlView control, Engine engine)
    : control_(control), engine_(std::move(engine))
{
    if (!control_.valid() || !engine_.submit ||
        !engine_.run_next_completion) {
        throw std::invalid_argument("invalid request dispatcher configuration");
    }
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
        const auto [ticket_it, ticket_inserted] =
            outstanding_by_ticket_.emplace(request.sequence, request);
        const auto [request_it, request_inserted] =
            ticket_by_request_id_.emplace(request.request_id,
                                          request.sequence);
        (void)ticket_it;
        (void)request_it;
        if (!ticket_inserted || !request_inserted) {
            fail_all();
            return true;
        }
        try {
            engine_.submit(request);
        } catch (...) {
            fail_all();
            return true;
        }
    }

    while (!outstanding_by_ticket_.empty()) {
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
            ticket_by_request_id_.find(completion->request_id);
        if (request_ticket == ticket_by_request_id_.end()) {
            fail_all();
            return true;
        }
        const auto descriptor =
            outstanding_by_ticket_.find(request_ticket->second);
        if (descriptor == outstanding_by_ticket_.end() ||
            completion->page_generation !=
                descriptor->second.page_generation ||
            !terminal(completion->status) ||
            !control_.try_publish_completion(descriptor->first,
                                             *completion)) {
            fail_all();
            return true;
        }
        outstanding_by_ticket_.erase(descriptor);
        ticket_by_request_id_.erase(request_ticket);
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
        outstanding_by_ticket_.emplace(queued.sequence, queued);
    }
    for (const auto& [ticket, request] : outstanding_by_ticket_) {
        (void)control_.try_publish_completion(
            ticket, failed_completion(request, RequestStatus::IoError));
    }
    outstanding_by_ticket_.clear();
    ticket_by_request_id_.clear();
    atomic_store(control_.header()->fault, HBFSIM_IO_ERROR,
                 std::memory_order_release);
}

}  // namespace hbfsim::host_service

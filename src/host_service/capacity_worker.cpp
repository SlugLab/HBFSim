#include "capacity_worker.hpp"

#include <stdexcept>

namespace hbfsim::host_service {

CapacityWorker::CapacityWorker(ControlView control,
                               CapacityPageService& service,
                               std::chrono::microseconds idle_poll)
    : control_(control), service_(service), idle_poll_(idle_poll)
{
    if (!control_.valid() || idle_poll_ <= std::chrono::microseconds::zero()) {
        throw std::invalid_argument("invalid capacity worker configuration");
    }
    slot_count_ = control_.header()->page_capacity;
    thread_ = std::jthread([this](std::stop_token stop) { run(stop); });
}

CapacityWorker::~CapacityWorker()
{
    stop();
}

void CapacityWorker::stop()
{
    stop_with_hook_for_test(nullptr, nullptr);
}

void CapacityWorker::stop_with_hook_for_test(
    CapacityWorkerStopHook after_lock, void* hook_state)
{
    std::lock_guard lifecycle(lifecycle_mutex_);
    if (after_lock != nullptr) {
        after_lock(hook_state);
    }
    if (!thread_.joinable()) {
        return;
    }
    thread_.request_stop();
    idle_condition_.notify_all();
    thread_.join();
}

RequestStatus CapacityWorker::flush()
{
    return service_.flush();
}

RequestStatus CapacityWorker::flush(
    const CapacityPageService::ModelProgram& model_program,
    std::optional<std::uint32_t> range_id)
{
    return service_.flush(model_program, range_id);
}

void CapacityWorker::run(std::stop_token stop)
{
    while (!stop.stop_requested()) {
        bool claimed = false;
        for (std::uint32_t slot = 0;
             slot < slot_count_ && !stop.stop_requested(); ++slot) {
            CapacityHandoff handoff{};
            if (!control_.try_capacity_handoff(slot, handoff)) {
                continue;
            }
            claimed = true;
            CapacityResolveResult resolved{};
            try {
                resolved = service_.resolve(handoff.logical_page,
                                            handoff.operation);
            } catch (...) {
                resolved = {.status = RequestStatus::IoError,
                            .frame_address = 0};
            }
            if (resolved.status == RequestStatus::Pending ||
                resolved.status > RequestStatus::DaemonLost ||
                (resolved.status == RequestStatus::Ready) !=
                    (resolved.frame_address != 0) ||
                !valid_capacity_media_plan(resolved.media) ||
                (resolved.status != RequestStatus::Ready &&
                 resolved.media.flags != CapacityMediaNone)) {
                resolved = {.status = RequestStatus::IoError,
                            .frame_address = 0};
            }
            // A timeout/reclaim may have already completed or reused this
            // generation. Exact ticket/request validation makes that a benign
            // lost race; never write the slot by any other path.
            (void)control_.complete_capacity_handoff(
                handoff.ticket, handoff.request_id, resolved.frame_address,
                resolved.status, resolved.media);
        }
        if (!claimed && !stop.stop_requested()) {
            std::unique_lock lock(idle_mutex_);
            (void)idle_condition_.wait_for(lock, stop, idle_poll_, [] {
                return false;
            });
        }
    }
}

}  // namespace hbfsim::host_service

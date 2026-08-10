#pragma once

#include "capacity_page_service.hpp"
#include "control_layout.hpp"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <thread>

namespace hbfsim::host_service {

using CapacityWorkerStopHook = void (*)(void*) noexcept;

// CapacityWorker does not own its ControlView mapping or CapacityPageService.
// Both must outlive the worker. Destruction and stop() synchronously join the
// worker thread, including an in-flight service callback, but do not flush
// dirty pages. An owner requiring persistence must complete a successful
// flush() before teardown. As with ordinary C++ object lifetime, destruction
// must not race calls to stop(), flush(), or other member functions.
class CapacityWorker {
  public:
    CapacityWorker(
        ControlView control, CapacityPageService& service,
        std::chrono::microseconds idle_poll = std::chrono::microseconds(50));
    ~CapacityWorker();

    CapacityWorker(const CapacityWorker&) = delete;
    CapacityWorker& operator=(const CapacityWorker&) = delete;

    void stop();
    void stop_with_hook_for_test(CapacityWorkerStopHook after_lock,
                                 void* hook_state);
    RequestStatus flush();

  private:
    void run(std::stop_token stop);

    ControlView control_;
    CapacityPageService& service_;
    std::uint32_t slot_count_;
    std::chrono::microseconds idle_poll_;
    std::mutex lifecycle_mutex_;
    std::mutex idle_mutex_;
    std::condition_variable_any idle_condition_;
    std::jthread thread_;
};

}  // namespace hbfsim::host_service

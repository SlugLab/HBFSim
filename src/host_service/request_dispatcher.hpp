#pragma once

#include "control_layout.hpp"

#include <functional>
#include <optional>
#include <unordered_map>

namespace hbfsim::host_service {

class RequestDispatcher {
public:
    struct Engine {
        std::function<void(const HbfRequest&)> submit;
        // nullopt means the engine has no event capable of completing an
        // outstanding request; it is terminal, not a transient not-ready state.
        std::function<std::optional<HbfCompletion>()> run_next_completion;
        // Runs after the timing engine returns an exact completion and before
        // the shared completion slot is published. Capacity mode uses this
        // boundary to wait for the parent-context page service.
        std::function<void(const HbfRequest&, HbfCompletion&)> finalize;
    };

    RequestDispatcher(ControlView control, Engine engine);

    // Returns true when a request was consumed or a completion was published.
    bool poll_once();

private:
    void fail_all() noexcept;

    ControlView control_;
    Engine engine_;
    std::unordered_map<std::uint64_t, HbfRequest> outstanding_by_ticket_;
    std::unordered_map<std::uint64_t, std::uint64_t> ticket_by_request_id_;
};

}  // namespace hbfsim::host_service

#pragma once

#include "control_layout.hpp"
#include "refresh_scheduler.hpp"

#include <array>
#include <deque>
#include <functional>
#include <optional>
#include <unordered_map>

namespace hbfsim::host_service {

struct PreparedDispatch {
    HbfCompletion completion{};
    std::array<HbfRequest, 2> media_actions{};
    std::uint32_t media_action_count{0};
    bool fail_all{false};
};

inline constexpr std::uint32_t kRequestFlagExplicitCapacityProgram = 1U << 0;
inline constexpr std::uint32_t kRequestFlagBackgroundRefresh = 1U << 1;
inline constexpr std::uint64_t kBackgroundRequestIdBit = 1ULL << 63;

PreparedDispatch prepare_capacity_media_dispatch(
    const HbfRequest& request, const CapacityHandoffResult& result,
    std::uint64_t service_ns) noexcept;

using DispatchNow = std::function<std::uint64_t()>;
using DispatchWait = std::function<void()>;

PreparedDispatch prepare_host_dispatch(ControlView control,
                                       const HbfRequest& request,
                                       const DispatchNow& now,
                                       const DispatchWait& wait,
                                       bool capacity_media_enabled = true) noexcept;

class RequestDispatcher {
public:
    struct Engine {
        std::function<PreparedDispatch(const HbfRequest&)> prepare;
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
    void attach_refresh_scheduler(RefreshScheduler* scheduler) noexcept;
    bool enqueue_background(const RefreshAction& action);

private:
    friend class RequestDispatcherTestAccess;

    struct DispatchGroup {
        HbfRequest original{};
        PreparedDispatch prepared{};
        std::uint32_t next_action{0};
        bool legacy{false};
    };

    [[nodiscard]] std::optional<std::uint64_t> next_engine_id() noexcept;
    bool submit_next(std::uint64_t ticket, DispatchGroup& group);
    bool publish(std::uint64_t ticket, DispatchGroup& group);
    bool account_application_media(const HbfRequest& action) noexcept;
    void publish_reliability_accounting() noexcept;
    bool submit_next_background();
    void fail_all() noexcept;

    ControlView control_;
    Engine engine_;
    std::unordered_map<std::uint64_t, DispatchGroup> groups_by_ticket_;
    std::unordered_map<std::uint64_t, std::uint64_t> ticket_by_engine_id_;
    std::deque<RefreshAction> background_pending_;
    std::unordered_map<std::uint64_t, RefreshAction> background_inflight_;
    RefreshScheduler* refresh_scheduler_{nullptr};
    std::uint64_t next_engine_id_{1};
    std::uint64_t next_background_id_{kBackgroundRequestIdBit};
    bool engine_ids_exhausted_{false};
};

}  // namespace hbfsim::host_service

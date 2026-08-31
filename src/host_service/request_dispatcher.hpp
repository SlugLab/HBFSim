#pragma once

#include "control_layout.hpp"

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

    // Puts a speculative media read on the timing timeline.
    //
    // A readahead performs a real backing read that no GPU is waiting on, so
    // before this it never reached the timing engine at all: it contributed no
    // modeled latency, no queueing and no contention, and the demand that
    // later hit the prefetched page reported no media time. In simulation the
    // speculative read had been deleted from the timeline.
    //
    // A speculative request is submitted to the same engine as a demand, so it
    // occupies the same channels and contends with demand traffic, but its
    // completion is discarded rather than published, because there is no
    // shared-memory slot behind it.
    //
    // Returns false when the queue is full. A dropped speculative read is not
    // an error: the readahead is advisory, and dropping it only means this
    // read is missing from the timeline.
    bool submit_speculative(const HbfRequest& request);

    [[nodiscard]] std::uint64_t speculative_submitted() const noexcept
    {
        return speculative_submitted_;
    }
    [[nodiscard]] std::uint64_t speculative_dropped() const noexcept
    {
        return speculative_dropped_;
    }

private:
    friend class RequestDispatcherTestAccess;

    struct DispatchGroup {
        HbfRequest original{};
        PreparedDispatch prepared{};
        std::uint32_t next_action{0};
        bool legacy{false};
    };

    // Tickets for real requests are the request's own `sequence` field, taken
    // from the shared ring. Speculative groups need a ticket space that cannot
    // collide with those, so they set the top bit.
    //
    // UNVERIFIED, PLEASE CHECK: I could not establish from the producer side
    // that `sequence` never reaches 2^63. If it can, this reservation is
    // wrong and the two spaces must be separated another way, for example by
    // a flag on DispatchGroup instead of a bit in the ticket. Everything else
    // here holds either way; only the choice of namespace depends on it.
    static constexpr std::uint64_t kSpeculativeTicketBit = 1ULL << 63;

    [[nodiscard]] bool drain_one_speculative();
    [[nodiscard]] std::optional<std::uint64_t> next_engine_id() noexcept;
    bool submit_next(std::uint64_t ticket, DispatchGroup& group);
    bool publish(std::uint64_t ticket, DispatchGroup& group);
    void fail_all() noexcept;

    ControlView control_;
    Engine engine_;
    std::unordered_map<std::uint64_t, DispatchGroup> groups_by_ticket_;
    std::unordered_map<std::uint64_t, std::uint64_t> ticket_by_engine_id_;
    std::uint64_t next_engine_id_{1};
    bool engine_ids_exhausted_{false};
    // Bounded on purpose: a readahead that outruns the engine should lose its
    // oldest speculative reads rather than grow this without limit.
    std::deque<HbfRequest> speculative_;
    std::uint64_t next_speculative_ticket_{1};
    std::uint64_t speculative_submitted_{0};
    std::uint64_t speculative_dropped_{0};
};

}  // namespace hbfsim::host_service

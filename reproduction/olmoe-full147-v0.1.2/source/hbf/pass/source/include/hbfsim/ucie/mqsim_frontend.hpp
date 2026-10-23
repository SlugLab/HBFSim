#pragma once

#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/ucie/link.hpp>

#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <optional>
#include <vector>

namespace hbfsim::ucie {

struct AxiRead {
    std::uint64_t request_id;
    std::uint64_t arrival_ns;
    std::uint64_t local_address;
    std::uint32_t axi_id;
    std::uint32_t bytes{64};
    std::uint32_t operation{0}; // 0=regular read; all other operations unsupported
};

struct FrontendCompletion {
    AxiRead request;
    HbfCompletion media;
    std::uint64_t request_delivered_ns;
    std::uint64_t media_ready_ns;
    std::uint64_t response_delivered_ns;
    std::uint32_t interface_bytes{64};
    std::uint32_t media_submit_bytes{4096};
};

struct FrontendCounters {
    std::uint64_t accepted{};
    std::uint64_t application_bytes{};
    std::uint64_t media_submit_bytes{};
    std::uint64_t media_completions{};
    std::uint64_t delivered{};
    std::uint64_t consumed{};
    std::uint64_t trace_dropped{};
    std::uint64_t observations_dropped{};
};

struct TraceEvent {
    std::uint64_t request_id;
    std::uint64_t time_ns;
    const char* phase;
};

// The production constructor always creates the existing patched MQSim engine.
// The MediaPort constructor exists for deterministic CPU fault/order fixtures.
class MediaPort {
public:
    virtual ~MediaPort() = default;
    virtual void submit(const HbfRequest& request) = 0;
    virtual std::optional<HbfCompletion> run_until(std::uint64_t horizon_ns) = 0;
    virtual std::uint64_t current_time_ns() const = 0;
    virtual std::vector<MqsimObservation> take_observations() = 0;
};

class UcieMqsimFrontend {
public:
    UcieMqsimFrontend(LinkProfile link_profile, const Profile& mqsim_profile);
    UcieMqsimFrontend(LinkProfile link_profile, const Profile& mqsim_profile,
                       std::unique_ptr<MediaPort> test_media);
    ~UcieMqsimFrontend();
    UcieMqsimFrontend(const UcieMqsimFrontend&) = delete;
    UcieMqsimFrontend& operator=(const UcieMqsimFrontend&) = delete;

    // false means finite frontend backpressure; no request was accepted.
    bool try_submit(const AxiRead& request);
    std::optional<FrontendCompletion> run_next_completion_until(std::uint64_t horizon_ns);
    [[nodiscard]] std::uint64_t current_time_ns() const;
    [[nodiscard]] std::size_t outstanding() const noexcept;
    [[nodiscard]] const FrontendCounters& counters() const noexcept { return counters_; }
    [[nodiscard]] const StreamingLink& link() const noexcept { return link_; }
    std::vector<MqsimObservation> take_media_observations();
    [[nodiscard]] const std::vector<TraceEvent>& trace() const noexcept { return trace_; }

private:
    struct RequestState {
        AxiRead axi;
        std::uint64_t request_delivered_ns{};
        std::optional<HbfCompletion> media;
        bool response_enqueued{};
    };
    struct EngineGuard;
    void handle_media(HbfCompletion completion);
    void enqueue_ready_response(std::uint32_t axi_id);
    void record(std::uint64_t id, std::uint64_t time, const char* phase);
    void drain_observations();

    std::unique_ptr<EngineGuard> guard_;
    std::unique_ptr<MediaPort> media_;
    StreamingLink link_;
    LinkProfile profile_;
    std::map<std::uint64_t, RequestState> active_;
    std::map<std::uint32_t, std::deque<std::uint64_t>> same_id_;
    std::map<std::uint32_t, std::deque<std::uint64_t>> same_id_return_order_;
    std::deque<FrontendCompletion> delivered_;
    std::vector<MqsimObservation> observations_;
    std::vector<TraceEvent> trace_;
    FrontendCounters counters_;
    std::uint64_t sequence_{};
    std::uint64_t last_accepted_arrival_ns_{};
};

} // namespace hbfsim::ucie

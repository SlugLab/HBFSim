#pragma once

#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/ucie/backing_registry.hpp>
#include <hbfsim/ucie/device_coalescer.hpp>
#include <hbfsim/ucie/engine_guard.hpp>
#include <hbfsim/ucie/link.hpp>

#include <cstdint>
#include <cstddef>
#include <deque>
#include <map>
#include <memory>
#include <optional>
#include <vector>

namespace hbfsim::ucie {

struct DeviceRead {
    std::uint64_t request_id{};
    std::uint64_t arrival_ns{};
    std::uint64_t deadline_ns{}; // zero disables caller deadline
    std::uint64_t local_address{};
    std::uint32_t axi_id{};
    std::uint32_t stack_id{};
    std::uint32_t module_id{};
    std::uint64_t endpoint_id{};
    std::uint32_t bytes{64};
    std::uint32_t operation{};
    std::optional<std::uint64_t> expected_media_page;
    std::optional<std::uint64_t> expected_generation;
};

enum class DeviceResult { Ready, BackendError, Cancelled, TimedOut };

struct DeviceCompletion {
    DeviceRead request;
    DeviceResult result{};
    std::uint64_t ar_delivered_ns{};
    std::uint64_t media_ready_ns{};
    std::uint64_t response_delivered_ns{};
    std::uint64_t consumed_ns{};
    std::uint32_t axi_payload_bytes{};
};

// Bounded diagnostic mapping; validation never depends on retained records.
struct DeviceRequestRecord {
    std::uint64_t request_id{};
    std::uint64_t transport_id{}; // Stage 2 link uses the active user ID
    std::uint64_t group_token{};
    std::uint64_t canonical_id{};
    std::uint64_t generation{};
    std::uint32_t stack_id{};
    std::uint32_t module_id{};
    std::uint64_t endpoint_id{};
    std::uint64_t canonical_page{};
    std::uint64_t media_lpa{};
    HbfReadBank bank;
    std::uint64_t accepted_ns{};
    std::uint64_t ar_delivered_ns{};
    std::uint64_t media_ready_ns{};
    std::uint64_t r_delivered_ns{};
    std::uint64_t consumed_ns{};
    DeviceResult result{};
};

struct NativeGroupRecord {
    std::uint32_t stack_id{};
    std::uint32_t module_id{};
    std::uint64_t group_token{};
    HbfReadBank bank;
    NativeReadProof proof;
};

struct DeviceFrontendCounters {
    std::uint64_t accepted{};
    std::uint64_t succeeded{};
    std::uint64_t failed{};
    std::uint64_t cancelled{};
    std::uint64_t caller_outstanding{};
    std::uint64_t media_submits{};
    std::uint64_t native_commands{};
    std::uint64_t application_bytes{};
    std::uint64_t axi_payload_bytes{};
    std::uint64_t media_submit_bytes{};
    std::uint64_t trace_dropped{};
    std::uint64_t native_proofs_dropped{};
    std::uint64_t request_records_dropped{};
    std::uint64_t rejected_backpressure{};
    std::uint64_t peak_transport_outstanding{};
    std::uint64_t peak_media_groups{};
};

struct DeviceTraceEvent {
    std::uint64_t id{};
    std::uint64_t time_ns{};
    const char* phase{};
};

class DeviceMediaPort {
public:
    struct EventPeek {
        bool supported{};
        std::optional<std::uint64_t> next_ns;
    };
    virtual ~DeviceMediaPort()=default;
    virtual void submit(const HbfRequest& request)=0;
    virtual std::optional<HbfCompletion> run_until(std::uint64_t horizon)=0;
    virtual std::uint64_t current_time_ns() const=0;
    // Unsupported ports force the conservative 1 ns parent reference path.
    // A supported port with no event can safely advance to the caller horizon.
    virtual EventPeek next_event() { return {}; }
    virtual HbfReadBank inspect(std::uint64_t media_page)=0;
    virtual void expect(std::uint64_t group_token,std::uint64_t media_page,
                        const HbfReadBank& bank)=0;
    virtual NativeReadProof finish(std::uint64_t group_token)=0;
};

class UcieDeviceFrontend {
public:
    UcieDeviceFrontend(LinkProfile link, const Profile& stack_profile,
                       HbfBankLayout layout, std::uint32_t stack_id,
                       std::uint32_t module_id, bool coalescing=true,
                       bool buffer_cache=false);
    // Deterministic CPU fixtures only; production always creates real MQSim.
    UcieDeviceFrontend(LinkProfile link, const Profile& stack_profile,
                       HbfBankLayout layout, std::uint32_t stack_id,
                       std::uint32_t module_id,
                       std::unique_ptr<DeviceMediaPort> test_media,
                       bool coalescing=true,bool buffer_cache=false);
    ~UcieDeviceFrontend();
    UcieDeviceFrontend(const UcieDeviceFrontend&)=delete;
    UcieDeviceFrontend& operator=(const UcieDeviceFrontend&)=delete;

    void add_backing(const BackingRange& range);
    void retire_backing_generation(std::uint64_t canonical_id,
                                   std::uint64_t generation,
                                   std::uint64_t endpoint_id);
    // false means bounded backpressure, with no accepted side effects.
    bool try_submit(const DeviceRead& read);
    void cancel(std::uint64_t request_id);
    // Advance the shared causal clock without consuming any caller result.
    void advance_until(std::uint64_t horizon);
    std::optional<DeviceCompletion> poll_completion();
    std::optional<DeviceCompletion> run_next_completion_until(std::uint64_t horizon);
    void drain_until_idle(std::uint64_t max_horizon);
    [[nodiscard]] std::uint64_t current_time_ns() const;
    [[nodiscard]] std::size_t outstanding() const noexcept { return active_.size(); }
    [[nodiscard]] const DeviceFrontendCounters& counters() const noexcept
    { return counters_; }
    [[nodiscard]] const DeviceReadCoalescer& coalescer() const noexcept
    { return coalescer_; }
    [[nodiscard]] const StreamingLink& link() const noexcept { return link_; }
    [[nodiscard]] const std::vector<DeviceTraceEvent>& trace() const noexcept
    { return trace_; }
    [[nodiscard]] const std::vector<NativeGroupRecord>& native_proofs() const noexcept
    { return native_proofs_; }
    [[nodiscard]] const std::vector<DeviceRequestRecord>& request_records() const noexcept
    { return request_records_; }
private:
    struct State {
        DeviceRead read;
        ResolvedBacking backing;
        std::uint64_t media_page{};
        std::uint64_t ar_delivered_ns{};
        std::uint64_t media_ready_ns{};
        std::uint64_t r_delivered_ns{};
        std::optional<std::uint64_t> group;
        DeviceResult result{DeviceResult::Ready};
        bool ar_delivered{};
        bool media_done{};
        bool r_enqueued{};
        bool r_delivered{};
        bool r_consumed{};
        bool caller_delivered{};
        bool caller_consumed{};
        bool detached{};
        std::optional<std::size_t> record_index;
    };
    void validate_configuration(const Profile& stack_profile) const;
    void start_media();
    void handle_media(HbfCompletion completion);
    void handle_delivery(const Delivery& delivery);
    void process_deadline(std::uint64_t request_id);
    void mark_terminal(std::uint64_t request_id,DeviceResult reason);
    void release_same_id(std::uint32_t axi_id);
    void present(std::uint64_t request_id);
    void maybe_erase(std::uint64_t request_id);
    void record(std::uint64_t id,std::uint64_t time,const char* phase);
    std::optional<DeviceCompletion> pop_delivered();
    void sweep_retired();
    void advance_impl(std::uint64_t horizon,bool stop_on_delivery);

    std::unique_ptr<UcieEngineGuard> guard_;
    std::unique_ptr<DeviceMediaPort> media_;
    LinkProfile link_profile_;
    Profile stack_profile_;
    HbfBankLayout layout_;
    std::uint32_t stack_id_;
    std::uint32_t module_id_;
    BackingRegistry registry_;
    DeviceReadCoalescer coalescer_;
    StreamingLink link_;
    std::map<std::uint64_t,State> active_;
    std::map<std::uint32_t,std::deque<std::uint64_t>> same_id_;
    std::map<std::uint32_t,std::deque<std::uint64_t>> r_order_;
    std::multimap<std::uint64_t,std::uint64_t> deadlines_;
    std::deque<std::uint64_t> delivered_;
    std::vector<DeviceTraceEvent> trace_;
    std::vector<NativeGroupRecord> native_proofs_;
    std::vector<DeviceRequestRecord> request_records_;
    std::map<std::uint64_t,HbfReadBank> expected_banks_;
    DeviceFrontendCounters counters_;
    std::uint64_t last_arrival_ns_{};
};

} // namespace hbfsim::ucie

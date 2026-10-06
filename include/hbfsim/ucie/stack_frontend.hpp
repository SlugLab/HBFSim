#pragma once

#include <hbfsim/ucie/device_frontend.hpp>

#include <deque>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <vector>
#include <utility>

namespace hbfsim::ucie {

// Internal parent-to-worker proof for an AXI packet containing fewer than
// 64 original caller bytes. Padding never broadens BackingRegistry access.
struct PacketBackingProof {
    std::uint64_t original_local_address{};
    std::uint32_t original_bytes{};
    std::uint64_t canonical_id{};
    std::uint64_t generation{};
};

// One stack-wide media engine and coalescer. Each host module owns an
// independent AoU/UCIe link and AXI ID order, but all clocks are advanced by
// this single central event loop.
class StackFrontend {
public:
    StackFrontend(std::vector<LinkProfile> modules, const Profile& media,
                  HbfBankLayout layout, std::uint32_t stack_id,
                  bool coalescing=true, bool buffer_cache=false);
    // A deterministic port is only for CPU fixtures; production owns MQSim.
    StackFrontend(std::vector<LinkProfile> modules, const Profile& media,
                  HbfBankLayout layout, std::uint32_t stack_id,
                  std::unique_ptr<DeviceMediaPort> test_media,
                  bool coalescing=true, bool buffer_cache=false);
    ~StackFrontend();
    StackFrontend(const StackFrontend&)=delete;
    StackFrontend& operator=(const StackFrontend&)=delete;

    void add_backing(const BackingRange& range);
    void retire_backing_generation(std::uint64_t canonical_id,
                                   std::uint64_t generation,
                                   std::uint32_t module_id,
                                   std::uint64_t endpoint_id);
    bool try_submit(const DeviceRead& read); // false: bounded, no acceptance
    bool try_submit_packet(const DeviceRead& read,
                           const PacketBackingProof& proof);
    [[nodiscard]] bool can_reserve(
        const std::vector<std::uint32_t>& per_module) const;
    // Exact admission state; no inference from ready/R/Consume counts.
    [[nodiscard]] std::pair<std::uint32_t,std::uint32_t> module_capacity(
        std::uint32_t module) const;
    void cancel(std::uint64_t request_id);
    void advance_until(std::uint64_t horizon);
    [[nodiscard]] DeviceMediaPort::EventPeek next_event();
    // These calls never free media/link resources. Only consume_completion does.
    [[nodiscard]] std::vector<std::uint64_t> ready_ids() const;
    [[nodiscard]] std::optional<DeviceCompletion> peek_completion(
        std::uint64_t request_id) const;
    DeviceCompletion consume_completion(std::uint64_t request_id);
    void drain_until_idle(std::uint64_t max_horizon);
    [[nodiscard]] std::uint64_t current_time_ns() const;
    [[nodiscard]] std::size_t outstanding() const noexcept { return active_.size(); }
    [[nodiscard]] bool is_active(std::uint64_t id) const noexcept
    { return active_.contains(id); }
    [[nodiscard]] const DeviceFrontendCounters& counters() const noexcept
    { return counters_; }
    [[nodiscard]] const DeviceReadCoalescer& coalescer() const noexcept
    { return coalescer_; }
    [[nodiscard]] const StreamingLink& link(std::uint32_t module) const;
    [[nodiscard]] const std::vector<NativeGroupRecord>& native_proofs() const noexcept
    { return native_proofs_; }
    [[nodiscard]] const std::vector<DeviceRequestRecord>& request_records() const noexcept
    { return request_records_; }

private:
    struct Module {
        Module(LinkProfile profile);
        LinkProfile profile;
        BackingRegistry registry;
        StreamingLink link;
        std::map<std::uint32_t,std::deque<std::uint64_t>> same_id;
        std::map<std::uint32_t,std::deque<std::uint64_t>> r_order;
        std::size_t outstanding{};
        std::uint64_t last_arrival_ns{};
    };
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
    void start_media();
    void handle_media(HbfCompletion completion);
    void handle_delivery(std::uint32_t module,const Delivery& delivery);
    void release_same_id(std::uint32_t module,std::uint32_t axi_id);
    void mark_terminal(std::uint64_t id,DeviceResult reason);
    void present(std::uint64_t id);
    void maybe_erase(std::uint64_t id);
    void sweep_retired();
    void process_deadline(std::uint64_t id);
    [[nodiscard]] DeviceCompletion completion_of(const State& state) const;
    bool try_submit_impl(const DeviceRead& read,
                         const std::optional<PacketBackingProof>& proof);

    std::unique_ptr<UcieEngineGuard> guard_;
    std::unique_ptr<DeviceMediaPort> media_;
    Profile stack_profile_;
    HbfBankLayout layout_;
    std::uint32_t stack_id_;
    std::vector<Module> modules_;
    DeviceReadCoalescer coalescer_;
    std::map<std::uint64_t,State> active_;
    // Active caller-consumed requests awaiting the original physical-tail predicate.
    std::set<std::uint64_t> consumed_pending_;
    std::multimap<std::uint64_t,std::uint64_t> deadlines_;
    std::deque<std::uint64_t> ready_;
    std::map<std::uint64_t,HbfReadBank> expected_banks_;
    std::map<std::uint64_t,std::uint32_t> group_module_;
    std::vector<NativeGroupRecord> native_proofs_;
    std::vector<DeviceRequestRecord> request_records_;
    DeviceFrontendCounters counters_;
};

} // namespace hbfsim::ucie

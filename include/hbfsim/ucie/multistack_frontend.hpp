#pragma once

#include <hbfsim/ucie/stack_router.hpp>
#include <hbfsim/ucie/worker_protocol.hpp>
#include <hbfsim/ucie/device_profile.hpp>
#include <hbfsim/ucie/multistack_profile.hpp>

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <tuple>
#include <vector>

namespace hbfsim::ucie {

struct GlobalRead {
    std::uint64_t request_id{};
    std::uint64_t arrival_ns{};
    std::uint64_t deadline_ns{};
    std::uint64_t address{};
    std::uint32_t bytes{};
    std::uint64_t endpoint_id{};
    std::uint32_t axi_id{};
    std::uint32_t operation{}; // only regular read 0
};

struct GlobalCompletion {
    GlobalRead request;
    DeviceResult result{};
    std::uint64_t ready_ns{};
    std::uint64_t consumed_ns{};
    std::uint32_t original_bytes{};
    std::uint32_t axi_payload_bytes{};
    std::uint32_t child_count{};
};

struct MultistackCounters {
    std::uint64_t accepted{};
    std::uint64_t succeeded{};
    std::uint64_t failed{};
    std::uint64_t cancelled{};
    std::uint64_t caller_outstanding{};
    std::uint64_t original_bytes{};
    std::uint64_t accepted_axi_payload_bytes{};
    std::uint64_t axi_payload_bytes{};
    std::uint64_t unique_canonical_bytes{};
    bool unique_canonical_exact{true};
    std::uint64_t unique_tracking_dropped_spans{};
    std::uint64_t rejected_backpressure{};
    std::uint64_t rejected_reassembly{};
    std::uint64_t reassembly_reserved_bytes{};
    std::uint64_t peak_reassembly_reserved_bytes{};
};

struct MultistackAccounting {
    MultistackCounters host;
    std::uint64_t worker_ar_wire_bytes{};
    std::uint64_t worker_r_wire_bytes{};
    std::uint64_t upstream_ar_wire_bytes{};
    std::uint64_t upstream_r_wire_bytes{};
    std::uint64_t media_submit_bytes{};
    std::uint64_t native_commands{};
    std::vector<WorkerStats> stacks;
};

// NanosecondReference is retained for deterministic CPU equivalence tests.
enum class MultistackAdvanceMode {
    EventDriven,
    EventDrivenCachedSeparate, // CPU baseline for CLOSE plus ADVANCE
    EventDrivenUncached, // CPU comparison only
    NanosecondReference,
    EventDrivenIdleDeferred // isolated CPU candidate; original enum values unchanged
};

// Parent process: continuous global address/registration, exactly-once parent
// results and conservative common-horizon coordination. The constructor
// starts one real MQSim worker process per stack; this process owns no MQSim.
class MultistackFrontend {
public:
    MultistackFrontend(ContiguousStackMap map,
                       const std::filesystem::path& worker_executable,
                       const std::filesystem::path& stack_profile,
                       bool shared_upstream=false,
                       std::uint64_t max_reassembly_bytes=4ULL<<20,
                       MultistackAdvanceMode advance_mode=
                           MultistackAdvanceMode::EventDriven);
    MultistackFrontend(const std::filesystem::path& top_profile,
                       const std::filesystem::path& worker_executable,
                       MultistackAdvanceMode advance_mode=
                           MultistackAdvanceMode::EventDriven);
    ~MultistackFrontend();
#ifdef HBFSIM_RELEASABLE_CHILD_INDEX_TEST_ORACLE
    struct ChildIndexActivationEvidence {
        bool enabled;
        std::uint64_t index_release_entries,legacy_release_entries,oracle_calls;
    };
    [[nodiscard]] ChildIndexActivationEvidence child_index_activation_evidence() const noexcept
    { return {releasable_child_index_,index_release_entries_,legacy_release_entries_,index_oracle_calls_}; }
#endif
    MultistackFrontend(const MultistackFrontend&)=delete;
    MultistackFrontend& operator=(const MultistackFrontend&)=delete;
    void add_backing(const GlobalBackingRange& range);
    void add_page_striped_backing(const PageStripeBacking& range);
    void retire_backing_generation(std::uint64_t canonical_id,
                                   std::uint64_t generation,
                                   std::uint64_t endpoint_id);
    bool try_submit(const GlobalRead& read);
    void cancel(std::uint64_t request_id);
    void advance_until(std::uint64_t horizon);
    // Earliest pending modeled event across workers, the shared link and
    // parent deadlines. Null means no internal work is pending.
    [[nodiscard]] std::optional<std::uint64_t> next_event_ns();
    [[nodiscard]] std::vector<std::uint64_t> ready_ids() const;
    [[nodiscard]] std::optional<GlobalCompletion> peek_completion(
        std::uint64_t request_id) const;
    GlobalCompletion consume_completion(std::uint64_t request_id);
    [[nodiscard]] std::uint64_t current_time_ns() const noexcept { return now_; }
    [[nodiscard]] const MultistackCounters& counters() const noexcept
    { return counters_; }
    [[nodiscard]] std::uint64_t actual_parent_inflight_peak() const noexcept
    { return actual_parent_inflight_peak_; }
    [[nodiscard]] MultistackAccounting accounting();
    [[nodiscard]] const StackWorkerClient& worker(std::uint32_t stack) const
    { return *workers_.at(stack); }
    [[nodiscard]] StackWorkerClient& worker(std::uint32_t stack);
    [[nodiscard]] const StreamingLink& shared_upstream_link() const;
private:
    MultistackFrontend(MultistackProfile top,
                       const std::filesystem::path& worker_executable,
                       const std::filesystem::path& top_profile,
                       MultistackAdvanceMode advance_mode);
    struct Child {
        RoutedChild route;
        WorkerReady latest;
        bool reported{};
        bool dispatched{};
        bool upstream_ar_queued{};
        bool upstream_ar_delivered{};
        bool upstream_ar_consumed{};
        bool upstream_r_queued{};
        bool upstream_r_delivered{};
        bool upstream_r_consumed{};
        bool r_received{};
        bool assembled{};
        bool worker_consumed{};
        bool cancel_sent{};
    };
    struct Parent {
        GlobalRead read;
        std::uint64_t canonical_id{};
        std::uint64_t generation{};
        std::vector<Child> children;
        // Admission-only immutable query-domain certificate; no release-time child scan.
        bool compact_wave_single_domain{};
        std::uint32_t compact_wave_stack{},compact_wave_module{};
        // Fixed original child indices; allocated once after children are complete.
        std::vector<std::uint64_t> releasable_child_words;
        std::size_t eligible_children{};
        std::size_t unassembled_children{};
        std::size_t undispatched_children{};
        bool nonready_report_seen{};
        DeviceResult result{DeviceResult::Ready};
        std::uint64_t ready_ns{};
        bool admitted{};
        bool presented{};
        bool caller_consumed{};
    };
    void set_assembled(Parent& parent,Child& child,bool assembled);
    bool dispatch_pending(std::uint64_t horizon);
    bool dispatch_child(std::uint64_t child_id,std::uint64_t arrival);
    bool observe_workers(std::uint64_t horizon,
        const std::vector<std::vector<WorkerReady>>* first_snapshots=nullptr);
    bool process_upstream(std::uint64_t horizon);
    bool update_parent(std::uint64_t id,std::uint64_t horizon);
    void fixed_point(std::uint64_t horizon,
        const std::vector<std::vector<WorkerReady>>* first_snapshots=nullptr,
        bool allow_compact_wave=true);
    void present(std::uint64_t id,std::uint64_t horizon);
    bool release_children(std::uint64_t id);
    struct CompactWaveHead {
        std::uint64_t parent_id{};
        std::uint32_t stack{};
        compact_consume_v1::Request request;
        std::array<std::size_t,compact_consume_v1::max_count> selected{};
    };
    [[nodiscard]] std::optional<CompactWaveHead> prepare_compact_wave_head(std::uint64_t id);
    bool commit_compact_wave_head(const CompactWaveHead& head,const compact_consume_v1::Reply& reply);
    void release_fixed_point_parents(const std::vector<std::uint64_t>& ids,
        bool& changed,bool allow_compact_wave);
    [[nodiscard]] bool child_release_eligible(const Child& child) const noexcept;
    void refresh_releasable_child(Parent& parent,std::size_t index);
#ifdef HBFSIM_RELEASABLE_CHILD_INDEX_TEST_ORACLE
    void verify_releasable_child_index(const Parent& parent) const;
#endif
    void maybe_erase(std::uint64_t id);
    void sweep_retired();
    void record_unique(const Parent& parent);
    [[nodiscard]] bool all_physical_done(const Parent& state);
    struct IdleWorker {
        std::uint64_t physical_now{};
        bool certified{};
        std::chrono::steady_clock::time_point heartbeat{};
    };
    [[nodiscard]] bool idle_mode() const noexcept;
    [[nodiscard]] bool no_parent_child_on(std::uint32_t stack) const;
    void certify_idle_workers();
    void check_idle_liveness(std::uint32_t stack);
    void catch_up_worker(std::uint32_t stack);
    void touch_worker(std::uint32_t stack);
    void flush_idle_workers();
    void invalidate_event_cache() noexcept
    { cached_next_event_.reset(); quiescent_at_now_=false; }

    ContiguousStackMap map_;
    GlobalSpanRegistry registry_;
    DeviceProfile profile_;
    std::vector<std::unique_ptr<StackWorkerClient>> workers_;
    std::vector<IdleWorker> idle_workers_;
    bool shared_;
    bool releasable_child_index_{};
    bool compact_wave_requested_{};
    struct CompactWaveCounters {
        std::uint64_t attempts{},published{},validated{},confirmed{},unknown_pending{};
        std::array<std::uint64_t,4> published_width{};
    } compact_wave_counters_;
#ifdef HBFSIM_RELEASABLE_CHILD_INDEX_TEST_ORACLE
    std::uint64_t index_release_entries_{},legacy_release_entries_{};
    mutable std::uint64_t index_oracle_calls_{};
#endif
    MultistackAdvanceMode advance_mode_;
    std::unique_ptr<StreamingLink> upstream_;
    std::map<std::uint64_t,Parent> parents_;
    using Generation=std::tuple<std::uint64_t,std::uint64_t,std::uint64_t>;
    std::map<Generation,std::set<std::pair<std::uint32_t,std::uint32_t>>>
        installed_;
    std::map<Generation,std::map<std::uint64_t,std::uint64_t>> unique_intervals_;
    std::size_t unique_interval_count_{};
    std::set<Generation> retiring_;
    std::deque<std::uint64_t> parent_order_;
    std::map<std::uint64_t,std::uint64_t> child_to_parent_;
    std::deque<std::uint64_t> ready_;
    MultistackCounters counters_;
    std::uint64_t actual_parent_inflight_peak_{};
    std::uint64_t next_child_id_{1};
    std::uint64_t now_{};
    std::uint64_t last_closed_global_{};
    std::uint64_t last_arrival_ns_{};
    std::uint64_t reassembly_reserved_bytes_{};
    std::uint64_t max_reassembly_bytes_{};
    std::uint32_t max_parent_requests_{1024};
    std::uint32_t max_child_records_{4096};
    bool opened_{};
    bool poisoned_{};
    bool external_worker_access_{};
    bool quiescent_at_now_{};
    // Outer optional: no cached result. Inner nullopt: known idle.
    std::optional<std::optional<std::uint64_t>> cached_next_event_;
    void abort_workers() noexcept;
};

} // namespace hbfsim::ucie

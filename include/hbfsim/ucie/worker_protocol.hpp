#pragma once

#include <hbfsim/ucie/stack_frontend.hpp>
#include <hbfsim/ucie/ordered_admission.hpp>
#include <hbfsim/ucie/compact_consume.hpp>

#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace hbfsim::ucie {

struct WorkerReady {
    std::uint64_t request_id{};
    std::uint32_t module_id{};
    DeviceResult result{};
    std::uint64_t ar_delivered_ns{};
    std::uint64_t media_ready_ns{};
    std::uint64_t response_delivered_ns{};
};

struct WorkerStats {
    std::uint64_t process_id{};
    std::uint64_t accepted{};
    std::uint64_t succeeded{};
    std::uint64_t cancelled{};
    std::uint64_t failed{};
    std::uint64_t native_commands{};
    std::uint64_t media_submit_bytes{};
    std::uint64_t max_rss_kib{};
    std::uint64_t physical_outstanding{};
    std::uint64_t current_time_ns{};
    std::uint64_t ipc_commands{};
    std::uint64_t advance_commands{};
    std::uint64_t peek_commands{};
    std::uint64_t close_commands{};
    std::uint64_t close_advance_commands{};
    std::uint64_t reserve_commands{};
    std::uint64_t submit_commands{};
    std::uint64_t consume_commands{};
    std::uint64_t is_active_commands{};
    std::string transport{"process"};
    std::uint64_t physical_ipc_send_count{};
    std::uint64_t physical_ipc_receive_count{};
    struct ModuleWire {
        std::uint32_t module_id{};
        std::uint64_t ar_wire_bytes{};
        std::uint64_t r_wire_bytes{};
        std::uint32_t ar_credit_granules{};
        std::uint32_t r_credit_granules{};
    };
    std::vector<ModuleWire> module_links;
};

// The default RAII client owns one child process/stack. Explicit local mode
// runs the same sequenced command handler in the parent, which then owns the
// sole MQSim instance in that process.
class StackWorkerClient {
public:
    StackWorkerClient(const std::filesystem::path& executable,
                      const std::filesystem::path& profile,
                      std::uint32_t stack_id,
                      bool top_profile=false,
                      bool local=false);
    ~StackWorkerClient();
    StackWorkerClient(const StackWorkerClient&)=delete;
    StackWorkerClient& operator=(const StackWorkerClient&)=delete;
    void add_backing(const BackingRange& range);
    void retire_backing_generation(std::uint64_t canonical_id,
                                   std::uint64_t generation,
                                   std::uint32_t module_id,
                                   std::uint64_t endpoint_id);
    bool reserve(std::uint64_t token,std::uint64_t horizon,
                 const std::vector<std::uint32_t>& per_module);
    // Separate scheduler eligibility: true only for a proved full requested
    // module. False means unknown/possibly available, never positive admission.
    // Single synchronous owner; external calls through this client invalidate
    // observations on every potential mutation. No shared/out-of-band handler.
    [[nodiscard]] bool capacity_proves_full(std::uint64_t token,
        std::uint64_t horizon,const std::vector<std::uint32_t>& per_module);
    // True only when the existing validated same-horizon module snapshot
    // proves no active child. False means unknown; the caller must query.
    [[nodiscard]] bool capacity_proves_inactive(std::uint32_t module_id,
        std::uint64_t horizon);
    void release_reservation(std::uint64_t token);
    bool try_submit(const DeviceRead& read,
                    std::optional<PacketBackingProof> proof=std::nullopt);
    void cancel(std::uint64_t request_id,std::uint64_t horizon);
    [[nodiscard]] std::vector<WorkerReady> advance_until(std::uint64_t horizon);
    [[nodiscard]] std::vector<WorkerReady> close_and_advance(
        std::uint64_t from_horizon,std::uint64_t to_horizon);
    [[nodiscard]] DeviceMediaPort::EventPeek next_event(std::uint64_t horizon);
    [[nodiscard]] DeviceCompletion consume_completion(std::uint64_t id,
                                                      std::uint64_t horizon);
    [[nodiscard]] bool is_active(std::uint64_t id);
    void close_horizon(std::uint64_t horizon);
    [[nodiscard]] WorkerStats stats();
    // Checks the owned socket for peer failure without an event-advance RPC.
    [[nodiscard]] bool transport_alive() const;
    [[nodiscard]] std::vector<NativeGroupRecord> native_evidence(
        std::size_t offset,std::size_t count=16);
    [[nodiscard]] std::vector<DeviceRequestRecord> request_evidence(
        std::size_t offset,std::size_t count=16);
    [[nodiscard]] std::uint32_t stack_id() const noexcept { return stack_id_; }
private:
    friend class MultistackFrontend;
    friend struct StartupHelloTestAccess;
    // Private same-owner initial socket HELLO; default public ctor stays synchronous.
    StackWorkerClient(const std::filesystem::path& executable,
        const std::filesystem::path& profile,std::uint32_t stack_id,
        bool top_profile,bool local,bool defer_initial_hello);
    [[nodiscard]] bool initial_hello_pending() const;
    void finish_initial_hello();
    void abandon_initial_hello() noexcept;
    friend struct ReserveSubmitSingleTestAccess;
    friend struct OrderedAdmissionTestAccess;
    friend struct CompactConsumeTestAccess;
    [[nodiscard]] bool compact_consume_remote() const;
    compact_consume_v1::Reply consume_compact_ordered(const compact_consume_v1::Request& request);
    // Same opcode12 and copied request; one owned pending slot, no public async API.
    [[nodiscard]] bool compact_consume_begin_available() const;
    [[nodiscard]] bool compact_consume_pending() const;
    void begin_compact_consume(const compact_consume_v1::Request& request);
    compact_consume_v1::Reply finish_compact_consume();
    void abandon_compact_consume() noexcept;
    [[nodiscard]] bool ordered_admission_remote() const;
    ordered_admission_v1::Reply reserve_submit_ordered(
        const ordered_admission_v1::Request& request);
    [[nodiscard]] bool reserve_submit_single_remote() const;
    // Narrow owned remote path: one actual Reserve then one actual Submit.
    bool reserve_submit_single(std::uint64_t token,std::uint64_t horizon,
        const std::vector<std::uint32_t>& counts,const DeviceRead& read,
        std::optional<PacketBackingProof> proof=std::nullopt);
    // Narrow single-owner host split phase; no wire/opcode or object-layout change.
    friend struct CloseAdvanceGatherTestAccess;
    [[nodiscard]] bool close_gather_local() const;
    [[nodiscard]] bool close_gather_remote() const;
    void begin_close_gather(std::uint64_t from_horizon,std::uint64_t to_horizon);
    [[nodiscard]] std::vector<WorkerReady> finish_close_gather();
    void abandon_close_gather() noexcept;
    [[nodiscard]] bool advance_gather_local() const;
    [[nodiscard]] bool advance_gather_remote() const;
    void begin_advance_gather(std::uint64_t horizon);
    [[nodiscard]] std::vector<WorkerReady> finish_advance_gather();
    void abandon_advance_gather() noexcept;
    struct Impl;
    std::unique_ptr<Impl> impl_;
    std::uint32_t stack_id_;
};

} // namespace hbfsim::ucie

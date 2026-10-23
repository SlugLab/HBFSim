#pragma once

#include <hbfsim/ucie/stack_frontend.hpp>

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
    struct Impl;
    std::unique_ptr<Impl> impl_;
    std::uint32_t stack_id_;
};

} // namespace hbfsim::ucie

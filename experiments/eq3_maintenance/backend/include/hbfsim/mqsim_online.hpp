#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace hbfsim {

enum class MqsimEventKind { Arrival, Admission, Completion };

// Optional adapter-boundary observations. Admission is the handoff to MQSim,
// not the start of an individual NAND command. Completion time is the raw
// media callback; modeled_completion_ns also includes the existing bandwidth
// lower bound. The two timestamps must not be conflated.
struct MqsimObservation {
    MqsimEventKind kind;
    std::uint64_t request_id;
    std::uint64_t arrival_ns;
    std::uint64_t time_ns;
    std::uint64_t modeled_completion_ns;
    std::uint32_t bytes;
    std::size_t device_outstanding;
};

enum class MqsimMaintenanceStatus : std::uint32_t {
    Committed, CommittedReclaimDeferred, RejectedUnsupported,
    RejectedInvalidTarget, RejectedUnmapped, RejectedNoSpare,
    FailedRead, FailedProgram, FailedStaleVersion,
    FailedAfterCommitNeedsReconcile, RejectedSourceBusy,
    FailedVersionOverflow
};
enum class MqsimMaintenanceFailurePoint : std::uint32_t {
    None, Read, Program, StaleCommit, Erase
};
enum class MqsimMaintenanceState : std::uint32_t {
    Due, Queued, Read, ProgramDest, Commit, RetireOld,
    ReclaimErase, Done, Failed
};
struct MqsimMaintenanceRequest {
    std::uint64_t request_id{};
    std::uint64_t parent_id{};
    std::uint64_t due_ns{};
    std::uint64_t deadline_ns{};
    std::uint64_t logical_page{};
    std::uint32_t channel{}, chip{}, die{}, plane{};
    bool plane_is_exact{};
    bool reclaim_invalid_source_block{};
    MqsimMaintenanceFailurePoint failure_point{MqsimMaintenanceFailurePoint::None};
};
struct MqsimMaintenanceEvent {
    std::uint64_t request_id{}, parent_id{};
    MqsimMaintenanceState state{};
    std::uint64_t time_ns{}, transaction_id{};
    std::uint32_t source_channel{}, source_chip{}, source_die{}, source_plane{},
        source_block{}, source_page{};
    std::optional<std::uint32_t> destination_channel, destination_chip,
        destination_die, destination_plane, destination_block, destination_page;
};
struct MqsimMaintenanceCompletion {
    std::uint64_t request_id{}, parent_id{};
    MqsimMaintenanceStatus status{};
    std::uint64_t enqueue_ns{}, start_ns{}, end_ns{}, logical_page{},
        source_version{}, committed_version{};
    bool mapping_committed{}, source_retired{}, erase_completed{};
    std::vector<std::uint64_t> transaction_ids;
};

class MqsimOnlineEngine {
public:
    explicit MqsimOnlineEngine(const Profile& profile);
    ~MqsimOnlineEngine();

    MqsimOnlineEngine(const MqsimOnlineEngine&) = delete;
    MqsimOnlineEngine& operator=(const MqsimOnlineEngine&) = delete;
    MqsimOnlineEngine(MqsimOnlineEngine&&) noexcept;
    MqsimOnlineEngine& operator=(MqsimOnlineEngine&&) noexcept;

    void submit(const HbfRequest& request);
    void submit_maintenance(const MqsimMaintenanceRequest& request);
    std::optional<HbfCompletion> run_next_completion();
    // Opt-in coordination with external compute/replay events. Return one
    // completion when its reported deadline is reached, or advance exactly to
    // deadline_ns and return nullopt. Never advance beyond the horizon.
    // Unlike run_next_completion(), nullopt here does not mean lost work.
    std::optional<HbfCompletion> run_next_completion_until(std::uint64_t deadline_ns);
    [[nodiscard]] std::size_t pending() const noexcept;
    [[nodiscard]] std::size_t pending_maintenance() const noexcept;
    [[nodiscard]] std::uint64_t current_time_ns() const noexcept;

    // Opt in before the first submission; disabled by default. Callers should
    // drain these CPU diagnostics after each returned completion.
    void enable_observations();
    [[nodiscard]] std::vector<MqsimObservation> take_observations();
    [[nodiscard]] std::vector<MqsimMaintenanceEvent> take_maintenance_events();
    [[nodiscard]] std::vector<MqsimMaintenanceCompletion> take_maintenance_completions();

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

std::vector<HbfCompletion> run_mqsim_trace(
    const Profile& profile, const std::filesystem::path& trace_path);

}  // namespace hbfsim

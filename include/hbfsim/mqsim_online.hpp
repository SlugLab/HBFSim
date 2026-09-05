#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <filesystem>
#include <memory>
#include <optional>
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

class MqsimOnlineEngine {
public:
    explicit MqsimOnlineEngine(const Profile& profile);
    ~MqsimOnlineEngine();

    MqsimOnlineEngine(const MqsimOnlineEngine&) = delete;
    MqsimOnlineEngine& operator=(const MqsimOnlineEngine&) = delete;
    MqsimOnlineEngine(MqsimOnlineEngine&&) noexcept;
    MqsimOnlineEngine& operator=(MqsimOnlineEngine&&) noexcept;

    void submit(const HbfRequest& request);
    std::optional<HbfCompletion> run_next_completion();
    [[nodiscard]] std::size_t pending() const noexcept;
    [[nodiscard]] std::uint64_t current_time_ns() const noexcept;

    // Opt in before the first submission; disabled by default. Callers should
    // drain these CPU diagnostics after each returned completion.
    void enable_observations();
    [[nodiscard]] std::vector<MqsimObservation> take_observations();

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

std::vector<HbfCompletion> run_mqsim_trace(
    const Profile& profile, const std::filesystem::path& trace_path);

}  // namespace hbfsim

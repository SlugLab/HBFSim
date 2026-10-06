#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>
#include <hbfsim/ucie/bank_layout.hpp>

#include <cstddef>
#include <array>
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

// Per-active-read validation is independent of any bounded diagnostic trace.
struct NativeReadProof {
    std::uint64_t request_id{};
    std::uint64_t issued_commands{};
    std::uint64_t first_issued_ns{};
    std::uint64_t last_issued_ns{};
    // COMMAND_ISSUED, MEDIA_BEGIN, MEDIA_END, DATA_OUT_BEGIN, DATA_OUT_END.
    std::array<std::uint64_t,5> phase_events{};
    std::uint64_t first_media_begin_ns{};
    std::uint64_t last_media_end_ns{};
    std::uint64_t expected_logical_page{};
    std::uint64_t observed_logical_page{};
    std::uint64_t observed_media_bytes{};
    bool logical_page_match{};
    bool bank_match{};
    bool observer_failed{};
};

class MqsimOnlineEngine {
public:
    explicit MqsimOnlineEngine(const Profile& profile);
    MqsimOnlineEngine(const Profile& profile,
                      const ucie::HbfBankLayout& hbf_layout);
    ~MqsimOnlineEngine();

    MqsimOnlineEngine(const MqsimOnlineEngine&) = delete;
    MqsimOnlineEngine& operator=(const MqsimOnlineEngine&) = delete;
    MqsimOnlineEngine(MqsimOnlineEngine&&) noexcept;
    MqsimOnlineEngine& operator=(MqsimOnlineEngine&&) noexcept;

    void submit(const HbfRequest& request);
    std::optional<HbfCompletion> run_next_completion();
    // Opt-in coordination with external compute/replay events. Return one
    // completion when its reported deadline is reached, or advance exactly to
    // deadline_ns and return nullopt. Never advance beyond the horizon.
    // Unlike run_next_completion(), nullopt here does not mean lost work.
    std::optional<HbfCompletion> run_next_completion_until(std::uint64_t deadline_ns);
    [[nodiscard]] std::size_t pending() const noexcept;
    [[nodiscard]] std::uint64_t current_time_ns() const noexcept;
    // Prepares staged arrivals and MQSim objects, then reports the earliest
    // queued event or modeled completion without executing an event.
    [[nodiscard]] std::optional<std::uint64_t> next_event_ns();
    // Opt-in HBF-only read query. Does not submit, allocate, or advance time.
    [[nodiscard]] ucie::HbfReadBank inspect_read_bank(
        std::uint64_t aligned_media_page_address) const;
    void expect_native_read(std::uint64_t request_id,
                            std::uint64_t media_page_address,
                            const ucie::HbfReadBank& expected);
    [[nodiscard]] NativeReadProof finish_native_read(std::uint64_t request_id);

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

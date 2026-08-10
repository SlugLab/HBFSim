#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <filesystem>
#include <memory>
#include <optional>
#include <vector>

namespace hbfsim {

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

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

std::vector<HbfCompletion> run_mqsim_trace(
    const Profile& profile, const std::filesystem::path& trace_path);

}  // namespace hbfsim

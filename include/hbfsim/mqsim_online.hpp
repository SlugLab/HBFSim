#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <memory>
#include <optional>

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

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace hbfsim

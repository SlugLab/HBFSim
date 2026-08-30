#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <vector>

namespace hbfsim {

enum class MediaActivityKind : std::uint8_t {
    Read,
    Program,
    Erase,
};

enum class MediaTransactionSource : std::uint8_t {
    UserIo,
    Cache,
    GcWl,
    Mapping,
};

struct MediaActivity {
    MediaActivityKind kind;
    std::uint64_t start_time_ns;
    std::uint64_t end_time_ns;
    std::uint32_t channel;
    std::uint32_t chip;
    std::uint32_t die;
    std::uint32_t plane;
    std::uint64_t block;
    std::uint64_t page;
    std::uint64_t bytes;
    MediaTransactionSource source;

    bool operator==(const MediaActivity&) const = default;
};

using MediaActivitySink = std::function<void(const MediaActivity&)>;

class MqsimOnlineEngine {
public:
    explicit MqsimOnlineEngine(const Profile& profile);
    MqsimOnlineEngine(const Profile& profile, MediaActivitySink media_activity_sink);
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

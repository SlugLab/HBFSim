#pragma once

#include <hbfsim/exact_profile.hpp>

#include <array>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <optional>
#include <string>
#include <vector>

namespace hbfsim {

inline constexpr std::uint32_t kSm120GnicChannels = 4;
inline constexpr std::uint32_t kSm120GpcChannels = 2;
inline constexpr std::uint32_t kSm120OperationClasses = 7;

enum class Sm120Operation : std::uint32_t {
    OrdinaryLoad = 0,
    OrdinaryStore = 1,
    TmaLoad = 2,
    TmaStore = 3,
    TmaUnicast = 4,
    TmaMulticast = 5,
    MixedHbmHbf = 6,
};

enum class QueueArbitration : std::uint32_t {
    Fifo = 0,
    RoundRobin = 1,
};

struct Sm120QueueConfig {
    std::uint32_t count{0};
    std::uint32_t depth{0};
    QueueArbitration arbitration{QueueArbitration::Fifo};
    std::array<std::uint64_t, kSm120OperationClasses> service_ns_by_class{};
};

struct Sm120RoutingProgram {
    std::uint32_t version{0};
    std::vector<std::uint32_t> smsp_proxy_lut;
    std::vector<std::uint32_t> gnic_lut;
    std::vector<std::uint32_t> gpc_lut;
};

struct Sm120ChannelConfig {
    Sm120QueueConfig gnic;
    Sm120QueueConfig gpc;
    Sm120RoutingProgram routing;
};

struct RoutingInput {
    std::uint32_t smid{0};
    std::uint32_t warpid{0};
    std::uint32_t cta_x{0};
    std::uint32_t cta_y{0};
    std::uint32_t cta_z{0};
    std::uint32_t resident_warps{0};
    std::uint32_t cluster_ctarank{0};
    Sm120Operation operation{Sm120Operation::OrdinaryLoad};
};

struct ChannelSelection {
    std::uint32_t smsp_proxy{0};
    std::uint32_t gnic{0};
    std::uint32_t gpc{0};
    bool valid{false};
};

struct ReservationConditions {
    std::uint64_t arrival_ns{0};
    std::uint64_t base_ready_ns{0};
    std::uint64_t media_ready_ns{0};
    std::uint64_t capacity_ready_ns{0};
    std::uint64_t native_ready_ns{0};
    std::uint32_t bytes{0};
};

struct ChannelReservation {
    std::uint64_t ready_ns{0};
    std::uint32_t gnic{0};
    std::uint32_t gpc{0};
    std::uint64_t gnic_service_ns{0};
    std::uint64_t gpc_service_ns{0};
    bool uses_gnic{false};
    bool uses_gpc{false};
    bool accepted{false};
    bool saturated{false};
};

struct Sm120ChannelCounters {
    std::array<std::uint64_t, kSm120GnicChannels> gnic_bytes{};
    std::array<std::uint64_t, kSm120GpcChannels> gpc_bytes{};
    std::array<std::uint64_t, kSm120GnicChannels> gnic_service_ns{};
    std::array<std::uint64_t, kSm120GpcChannels> gpc_service_ns{};
    std::array<std::uint64_t, kSm120GnicChannels> gnic_requests{};
    std::array<std::uint64_t, kSm120GpcChannels> gpc_requests{};
    std::uint64_t saturated_requests{0};
};

[[nodiscard]] std::string
validate_sm120_channel_config(const Sm120ChannelConfig& config);
[[nodiscard]] Sm120ChannelConfig
make_sm120_channel_config(const ExactCalibration& calibration);
[[nodiscard]] ChannelSelection
route_sm120(const Sm120ChannelConfig& config,
            const RoutingInput& input);
[[nodiscard]] bool publish_sm120_channel_config(
    void* control_address, std::size_t control_bytes,
    const ExactCalibration& calibration,
    std::uint32_t sm_count = 256) noexcept;

class Sm120ChannelModel {
  public:
    Sm120ChannelModel(Sm120ChannelConfig config,
                      std::uint32_t sm_count);

    [[nodiscard]] ChannelReservation
    reserve(const RoutingInput& input,
            const ReservationConditions& conditions) noexcept;
    [[nodiscard]] std::optional<Sm120ChannelCounters>
    counters(std::uint32_t smid) const noexcept;
    void reset() noexcept;

  private:
    struct QueueState {
        std::uint64_t tail_ns{0};
        std::deque<std::uint64_t> completions;
    };
    struct SmState {
        std::array<QueueState, kSm120GnicChannels> gnic;
        std::array<QueueState, kSm120GpcChannels> gpc;
        std::uint64_t gnic_round_robin{0};
        std::uint64_t gpc_round_robin{0};
        Sm120ChannelCounters counters;
    };

    Sm120ChannelConfig config_;
    std::vector<SmState> states_;
};

}  // namespace hbfsim

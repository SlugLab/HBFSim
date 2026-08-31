#pragma once

#include <cstdint>
#include <stdexcept>

namespace hbfsim::reliability {

inline constexpr double kBoltzmannEvPerK = 8.62e-5;

class RetentionError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct RetentionRefreshConfig {
    double reference_temperature_c{85.0};
    std::uint64_t reference_interval_ns{86'400'000'000'000ULL};
    double activation_energy_ev{1.1};
    std::uint64_t protected_bytes{0};
    std::uint64_t page_bytes{4096};
    std::uint32_t independent_channels{16};
    std::uint64_t read_latency_ns{4'000};
    std::uint64_t program_latency_ns{75'000};
    double read_command_j{0.0};
    double read_joules_per_byte{0.0};
    double program_command_j{0.0};
    double program_joules_per_byte{0.0};
};

struct RefreshDemand {
    std::uint64_t start_time_ns{0};
    std::uint64_t end_time_ns{0};
    double temperature_c{0.0};
    double acceleration_factor{0.0};
    double equivalent_age_increment_s{0.0};
    double equivalent_age_remainder_s{0.0};
    std::uint64_t refresh_cycles{0};
    std::uint64_t refresh_pages{0};
    std::uint64_t extra_read_bytes{0};
    std::uint64_t extra_program_bytes{0};
    double extra_media_energy_j{0.0};
    std::uint64_t serialized_media_busy_ns{0};
    std::uint64_t channel_critical_path_ns{0};
    std::uint64_t same_die_collision_upper_bound_ns{0};
};

struct RetentionRefreshStats {
    std::uint64_t observed_time_ns{0};
    double equivalent_age_s{0.0};
    double equivalent_age_remainder_s{0.0};
    std::uint64_t refresh_cycles{0};
    std::uint64_t refresh_pages{0};
    std::uint64_t extra_read_bytes{0};
    std::uint64_t extra_program_bytes{0};
    double extra_media_energy_j{0.0};
    std::uint64_t serialized_media_busy_ns{0};
    std::uint64_t channel_critical_path_ns{0};
};

class RetentionRefreshModel {
public:
    explicit RetentionRefreshModel(RetentionRefreshConfig config);

    [[nodiscard]] static double acceleration_factor(
        double reference_temperature_c, double temperature_c,
        double activation_energy_ev);
    [[nodiscard]] RefreshDemand advance(std::uint64_t start_time_ns,
                                        std::uint64_t end_time_ns,
                                        double temperature_c);
    [[nodiscard]] const RetentionRefreshConfig& config() const noexcept;
    [[nodiscard]] const RetentionRefreshStats& stats() const noexcept;

private:
    RetentionRefreshConfig config_;
    RetentionRefreshStats stats_{};
    long double equivalent_age_remainder_ns_{0.0L};
    std::uint64_t last_end_time_ns_{0};
    bool started_{false};
};

}  // namespace hbfsim::reliability

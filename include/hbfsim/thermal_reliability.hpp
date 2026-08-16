#pragma once

#include <hbfsim/profile.hpp>

#include <chrono>
#include <cstdint>
#include <optional>
#include <span>
#include <vector>

namespace hbfsim {

enum class ThermalMode : std::uint32_t {
    Normal = 0,
    Light = 1,
    Severe = 2,
    Shutdown = 3,
};

struct ThermalInput {
    std::uint64_t elapsed_ns;
    std::int64_t gpu_millic;
    std::uint64_t read_bytes;
    std::uint64_t write_bytes;
};

struct ThermalSnapshot {
    std::uint64_t generation;
    ThermalMode mode;
    std::int64_t junction_millic;
    std::uint32_t service_ppm;
};

struct ZoneReliability {
    bool valid = false;
    long double retention_damage = 0.0L;
    std::uint64_t read_disturb_count = 0;
    std::uint64_t current_pec = 0;
    std::uint64_t maximum_pec = 0;
};

struct TemperatureInterval {
    std::int64_t temperature_millic;
    std::chrono::nanoseconds elapsed;
};

struct MtbfSensitivityPoint {
    long double activation_energy_ev;
    long double hazard;
    long double failure_probability;
    std::optional<long double> equivalent_mtbf_hours;
};

class ThermalReliabilityModel {
public:
    ThermalReliabilityModel(ThermalReliabilityProfile profile,
                            std::int64_t initial_junction_millic);

    ThermalSnapshot advance(const ThermalInput& input);
    [[nodiscard]] ThermalSnapshot snapshot() const noexcept;
    [[nodiscard]] std::uint64_t transition_count() const noexcept;
    [[nodiscard]] const std::vector<ThermalMode>&
    last_transitions() const noexcept;

private:
    ThermalReliabilityProfile profile_;
    long double junction_millic_;
    ThermalMode mode_ = ThermalMode::Normal;
    std::uint64_t generation_ = 0;
    std::uint64_t transition_count_ = 0;
    std::vector<ThermalMode> last_transitions_;
};

long double retention_hours(const ThermalReliabilityProfile& profile,
                            std::int64_t temperature_millic);

void integrate_zone_damage(ZoneReliability& zone,
                           const ThermalReliabilityProfile& profile,
                           std::int64_t temperature_millic,
                           std::chrono::nanoseconds elapsed);

[[nodiscard]] bool refresh_eligible(
    const ZoneReliability& zone,
    const ThermalReliabilityProfile& profile) noexcept;

void commit_refresh(ZoneReliability& zone);

std::vector<MtbfSensitivityPoint> integrate_mtbf_sensitivity(
    const ThermalReliabilityProfile& profile,
    std::span<const TemperatureInterval> intervals);

}  // namespace hbfsim

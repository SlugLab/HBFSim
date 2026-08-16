#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/thermal_reliability.hpp>

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace hbfsim {

struct ThermalSampleRecord {
    std::uint64_t host_ns{0};
    std::int64_t gpu_millic{0};
    std::uint64_t gpu_power_mw{0};
    std::int64_t hbf_junction_millic{0};
};

struct ThermalTransition {
    std::uint64_t host_ns{0};
    ThermalMode from{ThermalMode::Normal};
    ThermalMode to{ThermalMode::Normal};
    std::int64_t junction_millic{0};
};

struct ThermalAccounting {
    std::uint64_t application_read_bytes{0};
    std::uint64_t application_write_bytes{0};
    std::uint64_t refresh_read_bytes{0};
    std::uint64_t refresh_write_bytes{0};
    std::uint64_t refresh_debt_bytes{0};
    std::uint64_t refresh_claimed_bytes{0};
    std::uint64_t refresh_background_drained_bytes{0};
    std::uint64_t completed_refresh_blocks{0};
    std::uint64_t maximum_pec{0};
    std::uint64_t average_pec_millionths{0};
    std::uint64_t normal_residency_ns{0};
    std::uint64_t light_residency_ns{0};
    std::uint64_t severe_residency_ns{0};
    std::uint64_t shutdown_residency_ns{0};
    std::uint64_t maximum_retention_damage_millionths{0};
    std::uint64_t average_retention_damage_millionths{0};
};

struct ThermalRunSummary {
    std::string profile_sha256;
    ThermalReliabilityProfile profile{};
    std::string temperature_source;
    std::vector<ThermalSampleRecord> samples;
    std::vector<ThermalTransition> transitions;
    ThermalAccounting accounting;
    std::vector<MtbfSensitivityPoint> mtbf;
    std::string terminal_status;
};

void write_thermal_summary(const std::filesystem::path& path,
                           const ThermalRunSummary& summary);

}  // namespace hbfsim

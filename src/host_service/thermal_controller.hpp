#pragma once

#include "control_layout.hpp"
#include "../cuda_runtime/thermal_telemetry.hpp"

#include <hbfsim/thermal_reliability.hpp>
#include <hbfsim/thermal_report.hpp>

#include <chrono>
#include <cstdint>
#include <vector>

namespace hbfsim::host_service {

enum class ThermalControllerStatus : std::uint32_t {
    Ready = 0,
    StaleTelemetry = 1,
    SourceFailed = 2,
    InvalidTelemetry = 3,
    CounterRegression = 4,
    CounterOverflow = 5,
    ModelError = 6,
    Shutdown = 7,
};

class ThermalController {
public:
    ThermalController(ThermalReliabilityProfile profile, ControlView control,
                      std::chrono::nanoseconds start_time);

    ThermalControllerStatus tick_at(std::chrono::nanoseconds now) noexcept;
    [[nodiscard]] ThermalSnapshot snapshot() const noexcept;
    [[nodiscard]] const std::vector<ThermalSampleRecord>& samples() const noexcept;
    [[nodiscard]] const std::vector<ThermalTransition>& transitions() const noexcept;
    [[nodiscard]] const std::vector<TemperatureInterval>&
    temperature_intervals() const noexcept;
    [[nodiscard]] ThermalAccounting accounting() const noexcept;

private:
    struct TelemetrySnapshot {
        std::uint64_t generation;
        std::uint64_t host_ns;
        std::int64_t gpu_millic;
        std::uint64_t gpu_power_mw;
        runtime::TelemetryStatus status;
    };

    struct ByteCounters {
        std::uint64_t read_bytes;
        std::uint64_t write_bytes;
        std::uint64_t refresh_read_bytes;
        std::uint64_t refresh_write_bytes;
    };

    [[nodiscard]] std::optional<TelemetrySnapshot>
    read_telemetry() const noexcept;
    [[nodiscard]] ByteCounters read_counters() const noexcept;
    void publish(const ThermalSnapshot& snapshot) noexcept;

    ControlView control_;
    ThermalReliabilityModel model_;
    std::chrono::nanoseconds last_time_;
    std::uint64_t last_telemetry_generation_ = 0;
    std::uint64_t last_telemetry_host_ns_ = 0;
    ByteCounters last_counters_{};
    std::vector<ThermalSampleRecord> samples_;
    std::vector<ThermalTransition> transitions_;
    std::vector<TemperatureInterval> temperature_intervals_;
    ThermalAccounting accounting_{};
};

}  // namespace hbfsim::host_service

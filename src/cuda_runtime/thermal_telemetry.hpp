#pragma once

#include "../host_service/control_layout.hpp"

#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <optional>
#include <filesystem>
#include <stop_token>
#include <thread>
#include <string>
#include <vector>

namespace hbfsim::runtime {

enum class TelemetryStatus : std::uint32_t {
    Disabled = 0,
    Ready = 1,
    SourceFailed = 2,
    Stopped = 3,
};

struct ThermalTelemetrySample {
    std::uint64_t host_ns;
    std::int64_t gpu_millic;
    std::uint64_t gpu_power_mw;
};

class ThermalTelemetrySource {
public:
    virtual ~ThermalTelemetrySource() = default;
    virtual std::optional<ThermalTelemetrySample> sample() noexcept = 0;
};

class ConstantThermalSource final : public ThermalTelemetrySource {
public:
    explicit ConstantThermalSource(ThermalTelemetrySample sample) noexcept;
    std::optional<ThermalTelemetrySample> sample() noexcept override;

private:
    ThermalTelemetrySample sample_;
};

class TraceThermalSource final : public ThermalTelemetrySource {
public:
    explicit TraceThermalSource(std::vector<ThermalTelemetrySample> samples);
    std::optional<ThermalTelemetrySample> sample() noexcept override;

private:
    std::vector<ThermalTelemetrySample> samples_;
    std::size_t next_ = 0;
};

class NvmlThermalSource final : public ThermalTelemetrySource {
public:
    explicit NvmlThermalSource(std::string pci_bus_id);
    std::optional<ThermalTelemetrySample> sample() noexcept override;

private:
    std::string pci_bus_id_;
};

std::vector<ThermalTelemetrySample> load_thermal_trace(
    const std::filesystem::path& path, std::string_view expected_sha256);

class ThermalTelemetryPublisher {
public:
    ThermalTelemetryPublisher(host_service::ControlView control,
                              ThermalTelemetrySource& source) noexcept;
    ~ThermalTelemetryPublisher();

    ThermalTelemetryPublisher(const ThermalTelemetryPublisher&) = delete;
    ThermalTelemetryPublisher& operator=(const ThermalTelemetryPublisher&) =
        delete;

    TelemetryStatus publish_once() noexcept;
    bool start(std::chrono::milliseconds period) noexcept;
    void stop() noexcept;

private:
    void publish(TelemetryStatus status,
                 const ThermalTelemetrySample* sample) noexcept;
    void run(std::stop_token stop_token,
             std::chrono::milliseconds period) noexcept;

    host_service::ControlView control_;
    ThermalTelemetrySource& source_;
    std::jthread worker_;
    std::mutex wait_mutex_;
    std::condition_variable_any wait_condition_;
    bool terminal_failure_ = false;
};

}  // namespace hbfsim::runtime

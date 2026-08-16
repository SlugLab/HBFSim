#include "../../src/cuda_runtime/thermal_telemetry.hpp"

#include "../../src/host_service/control_layout.hpp"

#include <hbfsim/exact_artifact.hpp>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

void check(bool condition, std::string_view message)
{
    if (!condition) {
        std::cerr << "check failed: " << message << '\n';
        std::abort();
    }
}

class FakeThermalSource final
    : public hbfsim::runtime::ThermalTelemetrySource {
public:
    explicit FakeThermalSource(
        std::vector<hbfsim::runtime::ThermalTelemetrySample> samples)
        : samples_(std::move(samples))
    {
    }

    std::optional<hbfsim::runtime::ThermalTelemetrySample>
    sample() noexcept override
    {
        if (next_ == samples_.size()) {
            return std::nullopt;
        }
        return samples_[next_++];
    }

private:
    std::vector<hbfsim::runtime::ThermalTelemetrySample> samples_;
    std::size_t next_ = 0;
};

}  // namespace

int main()
{
    using hbfsim::host_service::ControlView;
    using hbfsim::runtime::TelemetryStatus;
    using hbfsim::runtime::ThermalTelemetryPublisher;

    constexpr std::uint32_t ring_capacity = 8;
    std::vector<std::byte> storage(
        hbfsim::host_service::control_region_bytes(ring_capacity));
    ControlView control(storage.data(), storage.size());
    check(control.initialize(ring_capacity), "control initializes");

    FakeThermalSource source({
        {.host_ns = 10, .gpu_millic = 45'000, .gpu_power_mw = 200'000},
        {.host_ns = 20, .gpu_millic = 46'000, .gpu_power_mw = 210'000},
    });
    ThermalTelemetryPublisher publisher(control, source);
    check(publisher.publish_once() == TelemetryStatus::Ready,
          "first telemetry sample is ready");
    check(control.header()->telemetry_generation == 2,
          "first sample publishes stable generation two");
    check(control.header()->telemetry_host_ns == 10,
          "first host timestamp is published");
    check(control.header()->telemetry_gpu_millic == 45'000,
          "first GPU temperature is published");
    check(control.header()->telemetry_gpu_power_mw == 200'000,
          "first GPU power is published");

    check(publisher.publish_once() == TelemetryStatus::Ready,
          "second telemetry sample is ready");
    check(control.header()->telemetry_generation == 4,
          "second sample publishes stable generation four");
    check(control.header()->telemetry_gpu_millic == 46'000,
          "second GPU temperature is published");

    check(publisher.publish_once() == TelemetryStatus::SourceFailed,
          "source exhaustion fails closed");
    check(control.header()->telemetry_generation == 6,
          "terminal source failure is generation stamped");
    check(control.header()->telemetry_status ==
              static_cast<std::uint32_t>(TelemetryStatus::SourceFailed),
          "terminal source failure is published");

    hbfsim::runtime::ConstantThermalSource constant(
        {.host_ns = 30, .gpu_millic = 79'000, .gpu_power_mw = 0});
    const auto first_constant = constant.sample();
    const auto second_constant = constant.sample();
    check(first_constant.has_value() && second_constant.has_value(),
          "constant source never exhausts");
    check(second_constant->host_ns > first_constant->host_ns,
          "constant source timestamps are monotonic");
    check(second_constant->gpu_millic == 79'000,
          "constant source preserves temperature");

    hbfsim::runtime::TraceThermalSource trace({
        {.host_ns = 1, .gpu_millic = 50'000, .gpu_power_mw = 1},
        {.host_ns = 2, .gpu_millic = 51'000, .gpu_power_mw = 2},
    });
    check(trace.sample()->gpu_millic == 50'000,
          "trace returns the first sample");
    check(trace.sample()->gpu_millic == 51'000,
          "trace returns the second sample");
    check(!trace.sample().has_value(), "trace exhaustion is terminal");

    const std::string trace_document = R"JSON({
  "schema_version": 1,
  "samples": [
    {"host_ns": 100, "gpu_millic": 60000, "gpu_power_mw": 100000},
    {"host_ns": 200, "gpu_millic": 61000, "gpu_power_mw": 110000}
  ]
}
)JSON";
    const auto trace_path = std::filesystem::temp_directory_path() /
                            "hbfsim-thermal-trace-test.json";
    {
        std::ofstream output(trace_path, std::ios::binary);
        output << trace_document;
        check(static_cast<bool>(output), "trace fixture is written");
    }
    const auto trace_bytes = std::as_bytes(std::span(trace_document));
    const auto trace_sha256 = hbfsim::sha256_hex(trace_bytes);
    const auto loaded =
        hbfsim::runtime::load_thermal_trace(trace_path, trace_sha256);
    check(loaded.size() == 2, "trace loader returns every sample");
    check(loaded.back().gpu_power_mw == 110'000,
          "trace loader preserves power");
    bool rejected_digest = false;
    try {
        (void)hbfsim::runtime::load_thermal_trace(
            trace_path, std::string(64, '0'));
    } catch (const std::runtime_error&) {
        rejected_digest = true;
    }
    check(rejected_digest, "trace loader rejects a digest mismatch");
    std::filesystem::remove(trace_path);

    return 0;
}

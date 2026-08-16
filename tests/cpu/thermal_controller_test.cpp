#include "../../src/host_service/thermal_controller.hpp"

#include <hbfsim/profile.hpp>

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string_view>
#include <vector>

namespace {

void check(bool condition, std::string_view message)
{
    if (!condition) {
        std::cerr << "check failed: " << message << '\n';
        std::abort();
    }
}

void publish_telemetry(hbfsim::host_service::ControlView control,
                       std::uint64_t host_ns, std::int64_t gpu_millic,
                       hbfsim::runtime::TelemetryStatus status =
                           hbfsim::runtime::TelemetryStatus::Ready)
{
    auto* header = control.header();
    const auto generation = hbfsim::host_service::atomic_load(
        header->telemetry_generation, std::memory_order_acquire);
    hbfsim::host_service::atomic_store(
        header->telemetry_generation, generation + 1,
        std::memory_order_release);
    hbfsim::host_service::atomic_store(
        header->telemetry_host_ns, host_ns, std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->telemetry_gpu_millic, gpu_millic,
        std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->telemetry_gpu_power_mw, std::uint64_t{0},
        std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->telemetry_status, static_cast<std::uint32_t>(status),
        std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->telemetry_generation, generation + 2,
        std::memory_order_release);
}

void set_counters(hbfsim::host_service::ControlView control,
                  std::uint64_t read_bytes, std::uint64_t write_bytes,
                  std::uint64_t refresh_read_bytes = 0,
                  std::uint64_t refresh_write_bytes = 0)
{
    auto* header = control.header();
    hbfsim::host_service::atomic_store(
        header->thermal_read_bytes, read_bytes, std::memory_order_release);
    hbfsim::host_service::atomic_store(
        header->thermal_write_bytes, write_bytes, std::memory_order_release);
    hbfsim::host_service::atomic_store(
        header->thermal_refresh_read_bytes, refresh_read_bytes,
        std::memory_order_release);
    hbfsim::host_service::atomic_store(
        header->thermal_refresh_write_bytes, refresh_write_bytes,
        std::memory_order_release);
}

}  // namespace

int main()
{
    using namespace std::chrono_literals;
    using hbfsim::ThermalMode;
    using hbfsim::host_service::ControlView;
    using hbfsim::host_service::ThermalController;
    using hbfsim::host_service::ThermalControllerStatus;

    const auto profile =
        hbfsim::load_profile("configs/profiles/thermal-validation.json");
    check(profile.thermal_reliability.has_value(),
          "validation profile has thermal model");

    constexpr std::uint32_t ring_capacity = 8;
    std::vector<std::byte> storage(
        hbfsim::host_service::control_region_bytes(ring_capacity));
    ControlView control(storage.data(), storage.size());
    check(control.initialize(ring_capacity), "control initializes");
    ThermalController controller(*profile.thermal_reliability, control, 0ns);

    publish_telemetry(control, 100'000'000, 79'000);
    set_counters(control, 0, 0);
    check(controller.tick_at(100ms) == ThermalControllerStatus::Ready,
          "first controller tick is ready");
    check(controller.snapshot().mode == ThermalMode::Normal,
          "controller starts Normal");

    publish_telemetry(control, 200'000'000, 85'000);
    set_counters(control, 1ULL << 20, 0);
    check(controller.tick_at(200ms) == ThermalControllerStatus::Ready,
          "Light tick is ready");
    check(controller.snapshot().mode == ThermalMode::Light,
          "LTT enters Light");
    check(controller.snapshot().service_ppm == 900'000,
          "Light publishes reduced service");

    publish_telemetry(control, 300'000'000, 95'000);
    set_counters(control, 2ULL << 20, 0);
    check(controller.tick_at(300ms) == ThermalControllerStatus::Ready,
          "Severe tick is ready");
    check(controller.snapshot().mode == ThermalMode::Severe,
          "STT enters Severe");
    check(control.header()->thermal_admission_open == 0,
          "Severe closes admission");

    publish_telemetry(control, 400'000'000, 79'000);
    check(controller.tick_at(400ms) == ThermalControllerStatus::Ready,
          "Severe cooldown tick is ready");
    check(controller.snapshot().mode == ThermalMode::Light,
          "LTT cooldown enters Light");

    publish_telemetry(control, 500'000'000, 77'000);
    check(controller.tick_at(500ms) == ThermalControllerStatus::Ready,
          "Light cooldown tick is ready");
    check(controller.snapshot().mode == ThermalMode::Normal,
          "RTT cooldown enters Normal");
    check(control.header()->thermal_transitions == 4,
          "four hysteresis edges are published");
    check(controller.samples().size() == 5,
          "every successful tick records a thermal sample");
    check(controller.transitions().size() == 4,
          "every hysteresis edge is recorded for reporting");
    check(controller.temperature_intervals().size() == 5,
          "every successful tick records an MTBF interval");
    check(controller.accounting().normal_residency_ns == 200'000'000,
          "residency is charged to the mode active during each interval");
    check(controller.accounting().light_residency_ns == 200'000'000,
          "Light residency is accumulated exactly");
    check(controller.accounting().severe_residency_ns == 100'000'000,
          "Severe residency is accumulated exactly");

    const auto generation = control.header()->thermal_generation;
    check(controller.tick_at(600ms) ==
              ThermalControllerStatus::StaleTelemetry,
          "unchanged telemetry generation is stale");
    check(control.header()->thermal_generation == generation,
          "stale telemetry does not publish a thermal state");

    publish_telemetry(control, 600'000'000, 77'000,
                      hbfsim::runtime::TelemetryStatus::SourceFailed);
    check(controller.tick_at(600ms) ==
              ThermalControllerStatus::SourceFailed,
          "terminal telemetry failure propagates");
    check(hbfsim::host_service::thermal_report_terminal_status(
              ThermalControllerStatus::SourceFailed) == "source_failed",
          "source failure has a schema-valid terminal status");
    check(hbfsim::host_service::thermal_report_terminal_status(
              ThermalControllerStatus::Shutdown) == "thermal_shutdown",
          "shutdown has a schema-valid terminal status");
    check(hbfsim::host_service::thermal_report_terminal_status(
              ThermalControllerStatus::ModelError) == "model_error" &&
              hbfsim::host_service::thermal_report_terminal_status(
                  ThermalControllerStatus::StaleTelemetry) == "model_error",
          "controller validation failures map to model_error");

    std::vector<std::byte> regression_storage(
        hbfsim::host_service::control_region_bytes(ring_capacity));
    ControlView regression_control(regression_storage.data(),
                                   regression_storage.size());
    check(regression_control.initialize(ring_capacity),
          "regression control initializes");
    ThermalController regression(*profile.thermal_reliability,
                                 regression_control, 0ns);
    publish_telemetry(regression_control, 100'000'000, 79'000);
    set_counters(regression_control, 10, 0);
    check(regression.tick_at(100ms) == ThermalControllerStatus::Ready,
          "regression baseline is ready");
    publish_telemetry(regression_control, 200'000'000, 79'000);
    set_counters(regression_control, 9, 0);
    check(regression.tick_at(200ms) ==
              ThermalControllerStatus::CounterRegression,
          "counter regression fails closed");

    return 0;
}

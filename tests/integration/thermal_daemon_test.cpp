#include "../../src/host_service/thermal_controller.hpp"

#include <hbfsim/profile.hpp>
#include <hbfsim/api.h>

#include <chrono>
#include <vector>

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (false)

namespace {
void publish(hbfsim::host_service::ControlView control,
             std::uint64_t host_ns, std::int64_t temperature)
{
    auto* header = control.header();
    auto generation = hbfsim::host_service::atomic_load(
        header->telemetry_generation, std::memory_order_acquire);
    hbfsim::host_service::atomic_store(header->telemetry_generation,
                                       generation + 1,
                                       std::memory_order_release);
    hbfsim::host_service::atomic_store(header->telemetry_host_ns, host_ns,
                                       std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(header->telemetry_gpu_millic,
                                       temperature,
                                       std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->telemetry_status,
        static_cast<std::uint32_t>(hbfsim::runtime::TelemetryStatus::Ready),
        std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(header->telemetry_generation,
                                       generation + 2,
                                       std::memory_order_release);
}
}

int main()
{
    using namespace std::chrono_literals;
    constexpr std::uint32_t capacity = 8;
    std::vector<std::byte> storage(
        hbfsim::host_service::control_region_bytes(capacity));
    hbfsim::host_service::ControlView control(storage.data(), storage.size());
    CHECK(control.initialize(capacity));
    const auto profile = hbfsim::load_profile(
        "configs/profiles/thermal-validation.json");
    hbfsim::host_service::ThermalController controller(
        *profile.thermal_reliability, control, 0ns);

    publish(control, 100'000'000, 79'000);
    CHECK(controller.tick_at(100ms) ==
          hbfsim::host_service::ThermalControllerStatus::Ready);
    publish(control, 200'000'000, 95'000);
    CHECK(controller.tick_at(200ms) ==
          hbfsim::host_service::ThermalControllerStatus::Ready);
    CHECK(control.header()->thermal_admission_open == 0);
    const auto producer = control.header()->request_producer;
    CHECK(control.header()->request_producer == producer);

    publish(control, 300'000'000, 79'000);
    CHECK(controller.tick_at(300ms) ==
          hbfsim::host_service::ThermalControllerStatus::Ready);
    CHECK(controller.snapshot().mode == hbfsim::ThermalMode::Light);
    publish(control, 400'000'000, 77'000);
    CHECK(controller.tick_at(400ms) ==
          hbfsim::host_service::ThermalControllerStatus::Ready);
    CHECK(control.header()->thermal_admission_open == 1);

    publish(control, 500'000'000, 101'000);
    CHECK(controller.tick_at(500ms) ==
          hbfsim::host_service::ThermalControllerStatus::Shutdown);
    CHECK(controller.snapshot().mode == hbfsim::ThermalMode::Shutdown);
    CHECK(static_cast<int>(HBFSIM_THERMAL_SHUTDOWN) !=
          static_cast<int>(HBFSIM_DAEMON_LOST));
    return 0;
}

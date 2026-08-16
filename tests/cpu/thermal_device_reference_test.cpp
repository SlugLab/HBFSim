#include "../../src/cuda_runtime/device/hbf_device.cuh"

#include <cstdint>
#include <limits>

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (false)

int main()
{
    using hbfsim::device::ThermalAdmission;
    using hbfsim::device::ThermalMode;

    CHECK(hbfsim::device::scale_thermal_service_ns(10'000, 1'000'000) ==
          10'000);
    CHECK(hbfsim::device::scale_thermal_service_ns(10'000, 900'000) ==
          11'112);
    CHECK(hbfsim::device::scale_thermal_service_ns(
              std::numeric_limits<std::uint64_t>::max(), 900'000) ==
          std::numeric_limits<std::uint64_t>::max());
    CHECK(hbfsim::device::scale_thermal_service_ns(1, 0) ==
          std::numeric_limits<std::uint64_t>::max());
    CHECK(hbfsim::device::thermal_admission(ThermalMode::Normal, false) ==
          ThermalAdmission::Admit);
    CHECK(hbfsim::device::thermal_admission(ThermalMode::Light, false) ==
          ThermalAdmission::Admit);
    CHECK(hbfsim::device::thermal_admission(ThermalMode::Severe, false) ==
          ThermalAdmission::Wait);
    CHECK(hbfsim::device::thermal_admission(ThermalMode::Severe, true) ==
          ThermalAdmission::Drain);
    CHECK(hbfsim::device::thermal_admission(ThermalMode::Shutdown, false) ==
          ThermalAdmission::Shutdown);
    return 0;
}

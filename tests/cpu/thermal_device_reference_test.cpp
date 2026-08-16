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
    const auto full = hbfsim::device::claim_refresh_debt(12ULL << 10,
                                                         4ULL << 10);
    CHECK(full.claimed == (4ULL << 10) && full.remaining == (8ULL << 10));
    const auto short_claim = hbfsim::device::claim_refresh_debt(2ULL << 10,
                                                                4ULL << 10);
    CHECK(short_claim.claimed == (2ULL << 10) && short_claim.remaining == 0);
    const auto application_service = hbfsim::device::fast_service_ns(
        10'000, 4ULL << 10, 512ULL << 30);
    const auto refresh_service = hbfsim::device::scale_thermal_service_ns(
        hbfsim::device::fast_service_ns(
            100'000, static_cast<std::uint32_t>(full.claimed), 512ULL << 30),
        900'000);
    CHECK(refresh_service > 0 &&
          application_service + refresh_service > application_service);
    return 0;
}

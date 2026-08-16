#include <hbfsim/thermal_reliability.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>
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

bool close_ld(long double actual, long double expected,
              long double relative_epsilon)
{
    const auto scale = std::max(std::fabs(actual), std::fabs(expected));
    return std::fabs(actual - expected) <=
           relative_epsilon * std::max(1.0L, scale);
}

hbfsim::ThermalReliabilityProfile make_profile()
{
    return {
        .temperature_source = hbfsim::ThermalTemperatureSource::Constant,
        .ambient_millic = 25'000,
        .initial_hbf_junction_millic = 79'000,
        .tau_seconds = 0.001L,
        .gpu_coupling_ratio = 1.0L,
        .thermal_resistance_c_per_w = 0.0L,
        .idle_power_w = 0.0L,
        .read_energy_j_per_byte = 0.0L,
        .write_energy_j_per_byte = 0.0L,
        .telemetry_period_ms = 100,
        .controller_period_ms = 100,
        .rtt_millic = 78'000,
        .ltt_millic = 80'000,
        .stt_millic = 90'000,
        .shutdown_millic = 100'000,
        .light_service_ppm = 900'000,
        .zone_bytes = 1ULL << 20,
        .reference_retention_hours = 24.0L,
        .reference_retention_millic = 85'000,
        .retention_ea_ev = 1.10L,
        .refresh_damage_threshold = 0.95L,
        .read_disturb_limit = 1'000'000,
        .refresh_quantum_bytes = 4ULL << 10,
        .registered_ranges_contain_valid_data = true,
        .reliability_time_acceleration = 1.0L,
        .max_pec = 3'000,
        .reference_mtbf_hours = 20'000'000.0L,
        .mtbf_reference_millic = 85'000,
        .mtbf_ea_min_ev = 1.05L,
        .mtbf_ea_max_ev = 1.20L,
        .mtbf_ea_step_ev = 0.05L,
        .source_sha256 =
            "4fb6d2847c3ce4a09b7f2ce07dcb4cf8254145243c1985bce2848261b8d0724f",
    };
}

}  // namespace

int main()
{
    using namespace std::chrono_literals;
    using hbfsim::ThermalMode;

    const auto profile = make_profile();
    hbfsim::ThermalReliabilityModel model(profile, 79'000);

    const auto light = model.advance({
        .elapsed_ns = 1'000'000'000,
        .gpu_millic = 85'000,
        .read_bytes = 1ULL << 20,
        .write_bytes = 0,
    });
    check(light.mode == ThermalMode::Light, "LTT enters Light");
    check(light.service_ppm == 900'000, "Light service is 90 percent");

    const auto severe = model.advance({
        .elapsed_ns = 1'000'000'000,
        .gpu_millic = 95'000,
        .read_bytes = 0,
        .write_bytes = 0,
    });
    check(severe.mode == ThermalMode::Severe, "STT enters Severe");
    check(severe.service_ppm == 0, "Severe closes service admission");

    const auto cooled_light = model.advance({
        .elapsed_ns = 1'000'000'000,
        .gpu_millic = 79'000,
        .read_bytes = 0,
        .write_bytes = 0,
    });
    check(cooled_light.mode == ThermalMode::Light,
          "cooling through LTT returns to Light");
    const auto cooled_normal = model.advance({
        .elapsed_ns = 1'000'000'000,
        .gpu_millic = 77'000,
        .read_bytes = 0,
        .write_bytes = 0,
    });
    check(cooled_normal.mode == ThermalMode::Normal,
          "cooling through RTT returns to Normal");

    const auto shutdown = model.advance({
        .elapsed_ns = 1'000'000'000,
        .gpu_millic = 101'000,
        .read_bytes = 0,
        .write_bytes = 0,
    });
    check(shutdown.mode == ThermalMode::Shutdown,
          "shutdown threshold is terminal");
    const auto still_shutdown = model.advance({
        .elapsed_ns = 1'000'000'000,
        .gpu_millic = 25'000,
        .read_bytes = 0,
        .write_bytes = 0,
    });
    check(still_shutdown.mode == ThermalMode::Shutdown,
          "Shutdown never recovers");

    check(close_ld(hbfsim::retention_hours(profile, 85'000),
                   24.0L, 1e-12L),
          "reference retention identity");
    check(hbfsim::retention_hours(profile, 70'000) > 24.0L,
          "cooler temperature extends retention");
    check(hbfsim::retention_hours(profile, 95'000) < 24.0L,
          "hotter temperature shortens retention");

    hbfsim::ZoneReliability zone{.valid = true};
    hbfsim::integrate_zone_damage(zone, profile, 85'000, 12h);
    check(close_ld(zone.retention_damage, 0.5L, 1e-12L),
          "twelve reference hours accumulate half damage");
    const auto hot_lifetime = hbfsim::retention_hours(profile, 95'000);
    hbfsim::integrate_zone_damage(zone, profile, 95'000, 6h);
    check(close_ld(zone.retention_damage,
                   0.5L + 6.0L / hot_lifetime, 1e-12L),
          "variable-temperature damage is integrated");
    check(hbfsim::refresh_eligible(zone, profile),
          "damage threshold makes a valid zone eligible");

    hbfsim::commit_refresh(zone);
    check(close_ld(zone.retention_damage, 0.0L, 0.0L),
          "complete refresh resets damage");
    check(zone.read_disturb_count == 0, "complete refresh resets disturb");
    check(zone.current_pec == 1 && zone.maximum_pec == 1,
          "complete refresh commits exactly one PEC");

    const std::vector intervals{
        hbfsim::TemperatureInterval{.temperature_millic = 85'000,
                                    .elapsed = 10h},
    };
    const auto at_reference =
        hbfsim::integrate_mtbf_sensitivity(profile, intervals);
    check(at_reference.size() == 4, "activation-energy sweep is inclusive");
    for (const auto& point : at_reference) {
        check(close_ld(point.hazard, 10.0L / 20'000'000.0L, 1e-12L),
              "reference-temperature hazard identity");
        check(point.equivalent_mtbf_hours.has_value(),
              "positive hazard defines equivalent MTBF");
        check(close_ld(*point.equivalent_mtbf_hours,
                       20'000'000.0L, 1e-12L),
              "reference equivalent MTBF identity");
    }

    const std::vector hotter{
        hbfsim::TemperatureInterval{.temperature_millic = 95'000,
                                    .elapsed = 10h},
    };
    const auto hot_sweep = hbfsim::integrate_mtbf_sensitivity(profile, hotter);
    check(hot_sweep.front().hazard > at_reference.front().hazard,
          "hot temperature accelerates failure hazard");
    check(hot_sweep.back().hazard > hot_sweep.front().hazard,
          "higher activation energy increases hot hazard");
    check(hot_sweep.front().failure_probability > 0.0L,
          "positive hazard produces failure probability");

    return 0;
}

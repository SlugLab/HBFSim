#include <hbfsim/profile.hpp>
#include <hbfsim/thermal_reliability.hpp>
#include <hbfsim/thermal_report.hpp>

#include <chrono>
#include <filesystem>
#include <string>
#include <vector>

int main(int argc, char** argv)
{
    using namespace std::chrono_literals;
    if (argc != 3) {
        return 2;
    }
    const auto profile =
        hbfsim::load_profile("configs/profiles/thermal-validation.json");
    if (!profile.thermal_reliability) {
        return 3;
    }

    hbfsim::ThermalRunSummary summary;
    summary.profile_sha256 = std::string(64, 'a');
    summary.profile = *profile.thermal_reliability;
    summary.temperature_source = "constant";
    summary.samples = {
        {.host_ns = 100,
         .gpu_millic = 79'000,
         .gpu_power_mw = 0,
         .hbf_junction_millic = 79'000},
    };
    summary.transitions = {
        {.host_ns = 200,
         .from = hbfsim::ThermalMode::Normal,
         .to = hbfsim::ThermalMode::Light,
         .junction_millic = 85'000},
    };
    summary.accounting = {
        .application_read_bytes = 4096,
        .application_write_bytes = 4096,
        .refresh_read_bytes = 4096,
        .refresh_write_bytes = 4096,
        .refresh_debt_bytes = 0,
        .refresh_claimed_bytes = 4096,
        .refresh_background_drained_bytes = 0,
        .completed_refresh_blocks = 1,
        .maximum_pec = 1,
        .average_pec_millionths = 1'000'000,
    };
    summary.mtbf = hbfsim::integrate_mtbf_sensitivity(
        *profile.thermal_reliability,
        std::vector{hbfsim::TemperatureInterval{
            .temperature_millic = 85'000, .elapsed = 1h}});
    summary.terminal_status = argv[2];
    hbfsim::write_thermal_summary(argv[1], summary);
    return 0;
}

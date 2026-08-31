#include <hbfsim/retention_refresh.hpp>

#include <cstdint>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char** argv)
try {
    if (argc != 13) {
        throw std::invalid_argument(
            "usage: retention_refresh_cli Ea_eV temperature_C horizon_ns "
            "protected_bytes page_bytes channels read_ns program_ns "
            "read_command_J read_J_per_byte program_command_J "
            "program_J_per_byte");
    }
    const auto number = [](const char* text) { return std::stod(text); };
    const auto integer = [](const char* text) {
        return static_cast<std::uint64_t>(std::stoull(text));
    };
    const auto horizon = integer(argv[3]);
    hbfsim::reliability::RetentionRefreshModel model({
        .reference_temperature_c = 85.0,
        .reference_interval_ns = 86'400'000'000'000ULL,
        .activation_energy_ev = number(argv[1]),
        .protected_bytes = integer(argv[4]),
        .page_bytes = integer(argv[5]),
        .independent_channels =
            static_cast<std::uint32_t>(integer(argv[6])),
        .read_latency_ns = integer(argv[7]),
        .program_latency_ns = integer(argv[8]),
        .read_command_j = number(argv[9]),
        .read_joules_per_byte = number(argv[10]),
        .program_command_j = number(argv[11]),
        .program_joules_per_byte = number(argv[12]),
    });
    const auto row = model.advance(0, horizon, number(argv[2]));
    const auto& stats = model.stats();
    const auto duty = static_cast<long double>(
                          stats.channel_critical_path_ns) /
                      horizon;
    const auto power = stats.extra_media_energy_j /
                       (static_cast<double>(horizon) / 1.0e9);
    std::cout << std::setprecision(17)
              << "{\"schema_version\":1"
              << ",\"activation_energy_ev\":" << argv[1]
              << ",\"temperature_c\":" << argv[2]
              << ",\"horizon_ns\":" << horizon
              << ",\"acceleration_factor\":"
              << row.acceleration_factor
              << ",\"equivalent_age_s\":" << stats.equivalent_age_s
              << ",\"equivalent_age_remainder_s\":"
              << stats.equivalent_age_remainder_s
              << ",\"refresh_cycles\":" << stats.refresh_cycles
              << ",\"refresh_pages\":" << stats.refresh_pages
              << ",\"extra_read_bytes\":" << stats.extra_read_bytes
              << ",\"extra_program_bytes\":"
              << stats.extra_program_bytes
              << ",\"extra_media_energy_j\":"
              << stats.extra_media_energy_j
              << ",\"extra_average_media_power_w\":" << power
              << ",\"serialized_media_busy_ns\":"
              << stats.serialized_media_busy_ns
              << ",\"channel_critical_path_ns\":"
              << stats.channel_critical_path_ns
              << ",\"channel_refresh_duty_cycle\":"
              << static_cast<double>(duty)
              << ",\"same_die_collision_upper_bound_ns\":"
              << row.same_die_collision_upper_bound_ns << "}\n";
    return 0;
} catch (const std::exception& error) {
    std::cerr << "retention-refresh-cli: " << error.what() << '\n';
    return 1;
}

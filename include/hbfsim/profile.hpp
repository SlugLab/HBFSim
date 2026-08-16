#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>

namespace hbfsim {

struct EmpiricalVmemPoint {
    std::uint32_t pages;
    std::uint64_t cumulative_ns;
    std::uint64_t p95_ns;
};

struct EmpiricalVmemProfile {
    std::string source_kind;
    std::string source_sha256;
    std::uint64_t source_capacity_bytes;
    std::string quantile;
    std::uint32_t sample_count;
    std::array<EmpiricalVmemPoint, 6> read_curve;
    std::uint64_t program_p50_ns;
    std::uint64_t program_p95_ns;
};

enum class ThermalTemperatureSource : std::uint32_t {
    LiveGpu = 1,
    Trace = 2,
    Constant = 3,
};

struct ThermalReliabilityProfile {
    ThermalTemperatureSource temperature_source;
    std::int64_t ambient_millic;
    std::int64_t initial_hbf_junction_millic;
    long double tau_seconds;
    long double gpu_coupling_ratio;
    long double thermal_resistance_c_per_w;
    long double idle_power_w;
    long double read_energy_j_per_byte;
    long double write_energy_j_per_byte;
    std::uint32_t telemetry_period_ms;
    std::uint32_t controller_period_ms;
    std::int64_t rtt_millic;
    std::int64_t ltt_millic;
    std::int64_t stt_millic;
    std::int64_t shutdown_millic;
    std::uint32_t light_service_ppm;
    std::uint64_t zone_bytes;
    long double reference_retention_hours;
    std::int64_t reference_retention_millic;
    long double retention_ea_ev;
    long double refresh_damage_threshold;
    std::uint64_t read_disturb_limit;
    std::uint64_t refresh_quantum_bytes;
    bool registered_ranges_contain_valid_data;
    long double reliability_time_acceleration;
    std::uint64_t max_pec;
    long double reference_mtbf_hours;
    std::int64_t mtbf_reference_millic;
    long double mtbf_ea_min_ev;
    long double mtbf_ea_max_ev;
    long double mtbf_ea_step_ev;
    std::string source_sha256;
};

struct Profile {
    std::string name;
    std::uint64_t capacity_bytes;
    std::uint32_t page_bytes;
    std::uint64_t read_latency_ns;
    std::uint64_t program_latency_ns;
    std::uint32_t channels;
    std::uint32_t dies_per_channel;
    std::uint32_t planes_per_die;
    std::uint32_t pages_per_block;
    std::uint32_t channel_width_bits;
    std::uint32_t channel_transfer_rate_mtps;
    std::uint32_t queue_depth;
    std::uint64_t aggregate_bandwidth_bytes_per_s;
    std::uint64_t hbm_cache_bytes;
    double reference_sample_rate;
    std::uint32_t reference_warmup_requests;
    std::uint32_t time_scale;
    std::uint64_t timing_tolerance_ns;
    std::optional<EmpiricalVmemProfile> empirical_vmem;
    std::optional<ThermalReliabilityProfile> thermal_reliability;
};

class ProfileError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

Profile load_profile(const std::filesystem::path& path);
void validate_profile(const Profile& profile);
std::uint64_t blocks_per_plane(const Profile& profile);

}  // namespace hbfsim

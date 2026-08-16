#include <hbfsim/profile.hpp>

#include <json.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <fstream>
#include <limits>
#include <string>
#include <string_view>

namespace hbfsim {
namespace {

bool is_power_of_two(std::uint32_t value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

void require_nonzero(std::uint32_t value, const char* field)
{
    if (value == 0) {
        throw ProfileError(std::string(field) + " must be greater than zero");
    }
}

std::uint64_t calculate_blocks_per_plane(const Profile& profile)
{
    if (profile.page_bytes == 0 || profile.channels == 0 ||
        profile.dies_per_channel == 0 || profile.planes_per_die == 0 ||
        profile.pages_per_block == 0) {
        throw ProfileError(
            "capacity geometry must contain an integral number of blocks per plane");
    }

    const auto denominator =
        static_cast<unsigned __int128>(profile.page_bytes) * profile.channels *
        profile.dies_per_channel * profile.planes_per_die *
        profile.pages_per_block;
    if (denominator > std::numeric_limits<std::uint64_t>::max() ||
        profile.capacity_bytes % static_cast<std::uint64_t>(denominator) != 0) {
        throw ProfileError(
            "capacity geometry must contain an integral number of blocks per plane");
    }

    const auto blocks =
        profile.capacity_bytes / static_cast<std::uint64_t>(denominator);
    if (blocks == 0) {
        throw ProfileError(
            "capacity geometry must contain an integral number of blocks per plane");
    }
    return blocks;
}

std::optional<EmpiricalVmemProfile> parse_empirical_vmem(
    const nlohmann::json& document)
{
    const auto iterator = document.find("empirical_vmem");
    if (iterator == document.end()) {
        return std::nullopt;
    }

    const auto& empirical = *iterator;
    const auto& curve = empirical.at("read_curve");
    if (!curve.is_array() || curve.size() != 6) {
        throw ProfileError(
            "empirical_vmem read_curve must contain exactly 6 points");
    }

    EmpiricalVmemProfile parsed{
        .source_kind = empirical.at("source_kind").get<std::string>(),
        .source_sha256 = empirical.at("source_sha256").get<std::string>(),
        .source_capacity_bytes =
            empirical.at("source_capacity_bytes").get<std::uint64_t>(),
        .quantile = empirical.at("quantile").get<std::string>(),
        .sample_count = empirical.at("sample_count").get<std::uint32_t>(),
        .read_curve = {},
        .program_p50_ns =
            empirical.at("program_p50_ns").get<std::uint64_t>(),
        .program_p95_ns =
            empirical.at("program_p95_ns").get<std::uint64_t>(),
    };
    for (std::size_t index = 0; index < parsed.read_curve.size(); ++index) {
        parsed.read_curve[index] = EmpiricalVmemPoint{
            .pages = curve.at(index).at("pages").get<std::uint32_t>(),
            .cumulative_ns =
                curve.at(index).at("cumulative_ns").get<std::uint64_t>(),
            .p95_ns = curve.at(index).at("p95_ns").get<std::uint64_t>(),
        };
    }
    return parsed;
}

std::int64_t parse_millic(const nlohmann::json& object, const char* field)
{
    const auto celsius = object.at(field).get<long double>();
    if (!std::isfinite(celsius) ||
        celsius < static_cast<long double>(std::numeric_limits<std::int64_t>::min()) /
                      1000.0L ||
        celsius > static_cast<long double>(std::numeric_limits<std::int64_t>::max()) /
                      1000.0L) {
        throw ProfileError(std::string("thermal_reliability ") + field +
                           " must be a finite temperature");
    }
    return static_cast<std::int64_t>(std::llround(celsius * 1000.0L));
}

ThermalTemperatureSource parse_temperature_source(std::string_view source)
{
    if (source == "live_gpu") {
        return ThermalTemperatureSource::LiveGpu;
    }
    if (source == "trace") {
        return ThermalTemperatureSource::Trace;
    }
    if (source == "constant") {
        return ThermalTemperatureSource::Constant;
    }
    throw ProfileError(
        "thermal_reliability temperature_source must be live_gpu, trace, or constant");
}

bool is_lower_sha256(std::string_view digest)
{
    return digest.size() == 64 &&
           std::all_of(digest.begin(), digest.end(), [](char character) {
               return (character >= '0' && character <= '9') ||
                      (character >= 'a' && character <= 'f');
           });
}

std::optional<ThermalReliabilityProfile> parse_thermal_reliability(
    const nlohmann::json& document)
{
    const auto iterator = document.find("thermal_reliability");
    if (iterator == document.end()) {
        return std::nullopt;
    }
    const auto& thermal = *iterator;
    if (!thermal.is_object()) {
        throw ProfileError("thermal_reliability must be an object");
    }

    static constexpr std::array<std::string_view, 35> fields{
        "temperature_source",
        "source_identity",
        "constant_gpu_c",
        "trace_path",
        "ambient_c",
        "initial_hbf_junction_c",
        "tau_seconds",
        "gpu_coupling_ratio",
        "thermal_resistance_c_per_w",
        "idle_power_w",
        "read_energy_j_per_byte",
        "write_energy_j_per_byte",
        "telemetry_period_ms",
        "controller_period_ms",
        "rtt_c",
        "ltt_c",
        "stt_c",
        "shutdown_c",
        "light_service_ppm",
        "zone_bytes",
        "reference_retention_hours",
        "reference_retention_temperature_c",
        "retention_activation_energy_ev",
        "refresh_damage_threshold",
        "read_disturb_limit",
        "refresh_quantum_bytes",
        "registered_ranges_contain_valid_data",
        "reliability_time_acceleration",
        "max_pec",
        "reference_mtbf_hours",
        "mtbf_reference_temperature_c",
        "mtbf_activation_energy_ev_min",
        "mtbf_activation_energy_ev_max",
        "mtbf_activation_energy_ev_step",
        "source_sha256",
    };
    for (const auto& [key, value] : thermal.items()) {
        (void)value;
        if (std::find(fields.begin(), fields.end(), key) == fields.end()) {
            throw ProfileError("thermal_reliability contains unknown field: " +
                               key);
        }
    }

    return ThermalReliabilityProfile{
        .temperature_source = parse_temperature_source(
            thermal.at("temperature_source").get<std::string>()),
        .source_identity = thermal.at("source_identity").get<std::string>(),
        .constant_gpu_millic =
            thermal.contains("constant_gpu_c")
                ? std::optional<std::int64_t>(
                      parse_millic(thermal, "constant_gpu_c"))
                : std::nullopt,
        .trace_path =
            thermal.contains("trace_path")
                ? std::optional<std::filesystem::path>(
                      thermal.at("trace_path").get<std::string>())
                : std::nullopt,
        .ambient_millic = parse_millic(thermal, "ambient_c"),
        .initial_hbf_junction_millic =
            parse_millic(thermal, "initial_hbf_junction_c"),
        .tau_seconds = thermal.at("tau_seconds").get<long double>(),
        .gpu_coupling_ratio =
            thermal.at("gpu_coupling_ratio").get<long double>(),
        .thermal_resistance_c_per_w =
            thermal.at("thermal_resistance_c_per_w").get<long double>(),
        .idle_power_w = thermal.at("idle_power_w").get<long double>(),
        .read_energy_j_per_byte =
            thermal.at("read_energy_j_per_byte").get<long double>(),
        .write_energy_j_per_byte =
            thermal.at("write_energy_j_per_byte").get<long double>(),
        .telemetry_period_ms =
            thermal.at("telemetry_period_ms").get<std::uint32_t>(),
        .controller_period_ms =
            thermal.at("controller_period_ms").get<std::uint32_t>(),
        .rtt_millic = parse_millic(thermal, "rtt_c"),
        .ltt_millic = parse_millic(thermal, "ltt_c"),
        .stt_millic = parse_millic(thermal, "stt_c"),
        .shutdown_millic = parse_millic(thermal, "shutdown_c"),
        .light_service_ppm =
            thermal.at("light_service_ppm").get<std::uint32_t>(),
        .zone_bytes = thermal.at("zone_bytes").get<std::uint64_t>(),
        .reference_retention_hours =
            thermal.at("reference_retention_hours").get<long double>(),
        .reference_retention_millic =
            parse_millic(thermal, "reference_retention_temperature_c"),
        .retention_ea_ev =
            thermal.at("retention_activation_energy_ev").get<long double>(),
        .refresh_damage_threshold =
            thermal.at("refresh_damage_threshold").get<long double>(),
        .read_disturb_limit =
            thermal.at("read_disturb_limit").get<std::uint64_t>(),
        .refresh_quantum_bytes =
            thermal.at("refresh_quantum_bytes").get<std::uint64_t>(),
        .registered_ranges_contain_valid_data =
            thermal.at("registered_ranges_contain_valid_data").get<bool>(),
        .reliability_time_acceleration =
            thermal.at("reliability_time_acceleration").get<long double>(),
        .max_pec = thermal.at("max_pec").get<std::uint64_t>(),
        .reference_mtbf_hours =
            thermal.at("reference_mtbf_hours").get<long double>(),
        .mtbf_reference_millic =
            parse_millic(thermal, "mtbf_reference_temperature_c"),
        .mtbf_ea_min_ev =
            thermal.at("mtbf_activation_energy_ev_min").get<long double>(),
        .mtbf_ea_max_ev =
            thermal.at("mtbf_activation_energy_ev_max").get<long double>(),
        .mtbf_ea_step_ev =
            thermal.at("mtbf_activation_energy_ev_step").get<long double>(),
        .source_sha256 = thermal.at("source_sha256").get<std::string>(),
    };
}

}  // namespace

Profile load_profile(const std::filesystem::path& path)
{
    std::ifstream input(path);
    if (!input) {
        throw ProfileError("could not open profile: " + path.string());
    }

    try {
        const auto document = nlohmann::json::parse(input);
        Profile profile{
            .name = document.at("name").get<std::string>(),
            .capacity_bytes = document.at("capacity_bytes").get<std::uint64_t>(),
            .page_bytes = document.at("page_bytes").get<std::uint32_t>(),
            .read_latency_ns =
                document.at("read_latency_ns").get<std::uint64_t>(),
            .program_latency_ns =
                document.at("program_latency_ns").get<std::uint64_t>(),
            .channels = document.at("channels").get<std::uint32_t>(),
            .dies_per_channel =
                document.at("dies_per_channel").get<std::uint32_t>(),
            .planes_per_die =
                document.at("planes_per_die").get<std::uint32_t>(),
            .pages_per_block =
                document.at("pages_per_block").get<std::uint32_t>(),
            .channel_width_bits =
                document.at("channel_width_bits").get<std::uint32_t>(),
            .channel_transfer_rate_mtps =
                document.at("channel_transfer_rate_mtps").get<std::uint32_t>(),
            .queue_depth = document.at("queue_depth").get<std::uint32_t>(),
            .aggregate_bandwidth_bytes_per_s =
                document.at("aggregate_bandwidth_bytes_per_s")
                    .get<std::uint64_t>(),
            .hbm_cache_bytes =
                document.at("hbm_cache_bytes").get<std::uint64_t>(),
            .reference_sample_rate =
                document.at("reference_sample_rate").get<double>(),
            .reference_warmup_requests =
                document.at("reference_warmup_requests").get<std::uint32_t>(),
            .time_scale = document.at("time_scale").get<std::uint32_t>(),
            .timing_tolerance_ns =
                document.at("timing_tolerance_ns").get<std::uint64_t>(),
            .empirical_vmem = parse_empirical_vmem(document),
            .thermal_reliability = parse_thermal_reliability(document),
        };
        validate_profile(profile);
        return profile;
    } catch (const ProfileError&) {
        throw;
    } catch (const nlohmann::json::exception& error) {
        throw ProfileError("invalid profile JSON: " + std::string(error.what()));
    }
}

void validate_profile(const Profile& profile)
{
    if (profile.capacity_bytes == 0) {
        throw ProfileError("capacity_bytes must be greater than zero");
    }
    if (!is_power_of_two(profile.page_bytes)) {
        throw ProfileError("page_bytes must be a power of two");
    }
    if (profile.page_bytes < 512) {
        throw ProfileError(
            "page_bytes must be at least 512 for MQSim sector alignment");
    }
    if (profile.read_latency_ns == 0) {
        throw ProfileError("read_latency_ns must be greater than zero");
    }
    if (profile.program_latency_ns == 0) {
        throw ProfileError("program_latency_ns must be greater than zero");
    }
    require_nonzero(profile.channels, "channels");
    require_nonzero(profile.dies_per_channel, "dies_per_channel");
    require_nonzero(profile.planes_per_die, "planes_per_die");
    require_nonzero(profile.pages_per_block, "pages_per_block");
    require_nonzero(profile.channel_width_bits, "channel_width_bits");
    require_nonzero(profile.channel_transfer_rate_mtps,
                    "channel_transfer_rate_mtps");
    require_nonzero(profile.queue_depth, "queue_depth");
    if (profile.aggregate_bandwidth_bytes_per_s == 0) {
        throw ProfileError(
            "aggregate_bandwidth_bytes_per_s must be greater than zero");
    }
    if (profile.hbm_cache_bytes > profile.capacity_bytes) {
        throw ProfileError("hbm_cache_bytes must not exceed capacity_bytes");
    }
    if (!std::isfinite(profile.reference_sample_rate) ||
        profile.reference_sample_rate < 0.0 ||
        profile.reference_sample_rate > 1.0) {
        throw ProfileError("reference_sample_rate must be in [0, 1]");
    }
    require_nonzero(profile.time_scale, "time_scale");
    calculate_blocks_per_plane(profile);

    if (profile.thermal_reliability) {
        const auto& thermal = *profile.thermal_reliability;
        const auto finite_positive = [](long double value) {
            return std::isfinite(value) && value > 0.0L;
        };
        const auto finite_nonnegative = [](long double value) {
            return std::isfinite(value) && value >= 0.0L;
        };
        if (thermal.source_identity.empty()) {
            throw ProfileError(
                "thermal source_identity must not be empty");
        }
        if (thermal.temperature_source == ThermalTemperatureSource::Constant &&
            (!thermal.constant_gpu_millic || thermal.trace_path ||
             *thermal.constant_gpu_millic < 0 ||
             *thermal.constant_gpu_millic > 105'000)) {
            throw ProfileError(
                "constant thermal source requires only constant_gpu_c in [0, 105]");
        }
        if (thermal.temperature_source == ThermalTemperatureSource::Trace &&
            (!thermal.trace_path || thermal.trace_path->empty() ||
             thermal.constant_gpu_millic)) {
            throw ProfileError(
                "trace thermal source requires only a nonempty trace_path");
        }
        if (thermal.temperature_source == ThermalTemperatureSource::LiveGpu &&
            (thermal.constant_gpu_millic || thermal.trace_path)) {
            throw ProfileError(
                "live_gpu thermal source does not accept constant_gpu_c or trace_path");
        }
        if (thermal.ambient_millic < 0 ||
            thermal.initial_hbf_junction_millic < thermal.ambient_millic ||
            thermal.initial_hbf_junction_millic > 105'000) {
            throw ProfileError(
                "thermal temperatures must satisfy 0C <= ambient <= initial <= 105C");
        }
        if (!finite_positive(thermal.tau_seconds)) {
            throw ProfileError("thermal tau_seconds must be finite and positive");
        }
        if (!std::isfinite(thermal.gpu_coupling_ratio) ||
            thermal.gpu_coupling_ratio < 0.0L ||
            thermal.gpu_coupling_ratio > 1.0L) {
            throw ProfileError("thermal gpu_coupling_ratio must be in [0, 1]");
        }
        if (!finite_nonnegative(thermal.thermal_resistance_c_per_w) ||
            !finite_nonnegative(thermal.idle_power_w) ||
            !finite_nonnegative(thermal.read_energy_j_per_byte) ||
            !finite_nonnegative(thermal.write_energy_j_per_byte)) {
            throw ProfileError(
                "thermal power and energy parameters must be finite and nonnegative");
        }
        if (thermal.telemetry_period_ms == 0 ||
            thermal.controller_period_ms == 0 ||
            thermal.controller_period_ms > thermal.telemetry_period_ms) {
            throw ProfileError(
                "thermal periods must satisfy 0 < controller_period_ms <= telemetry_period_ms");
        }
        if (thermal.rtt_millic < 0 ||
            !(thermal.rtt_millic < thermal.ltt_millic &&
              thermal.ltt_millic < thermal.stt_millic &&
              thermal.stt_millic < thermal.shutdown_millic) ||
            thermal.shutdown_millic > 105'000) {
            throw ProfileError(
                "thermal thresholds must satisfy RTT < LTT < STT < shutdown <= 105C");
        }
        if (thermal.light_service_ppm == 0 ||
            thermal.light_service_ppm >= 1'000'000) {
            throw ProfileError(
                "thermal light_service_ppm must be in (0, 1000000)");
        }
        const auto block_bytes = static_cast<unsigned __int128>(profile.page_bytes) *
                                 profile.pages_per_block;
        if (block_bytes > std::numeric_limits<std::uint64_t>::max() ||
            thermal.zone_bytes == 0 ||
            thermal.zone_bytes % static_cast<std::uint64_t>(block_bytes) != 0) {
            throw ProfileError(
                "thermal zone_bytes must be a nonzero multiple of block bytes");
        }
        if (thermal.refresh_quantum_bytes == 0 ||
            thermal.refresh_quantum_bytes % profile.page_bytes != 0 ||
            static_cast<std::uint64_t>(block_bytes) %
                    thermal.refresh_quantum_bytes !=
                0) {
            throw ProfileError(
                "thermal refresh_quantum_bytes must be page aligned and divide block bytes");
        }
        if (!finite_positive(thermal.reference_retention_hours) ||
            thermal.reference_retention_millic < 0 ||
            thermal.reference_retention_millic > 105'000 ||
            !finite_positive(thermal.retention_ea_ev) ||
            !std::isfinite(thermal.refresh_damage_threshold) ||
            thermal.refresh_damage_threshold <= 0.0L ||
            thermal.refresh_damage_threshold > 1.0L ||
            thermal.read_disturb_limit == 0 ||
            !finite_positive(thermal.reliability_time_acceleration)) {
            throw ProfileError("thermal retention parameters are invalid");
        }
        if (thermal.max_pec == 0 ||
            !finite_positive(thermal.reference_mtbf_hours) ||
            thermal.mtbf_reference_millic < 0 ||
            thermal.mtbf_reference_millic > 105'000 ||
            !finite_positive(thermal.mtbf_ea_min_ev) ||
            !finite_positive(thermal.mtbf_ea_max_ev) ||
            !finite_positive(thermal.mtbf_ea_step_ev) ||
            thermal.mtbf_ea_max_ev < thermal.mtbf_ea_min_ev) {
            throw ProfileError("thermal endurance and MTBF parameters are invalid");
        }
        if (!is_lower_sha256(thermal.source_sha256)) {
            throw ProfileError(
                "thermal source_sha256 must be lowercase hexadecimal SHA256");
        }
    }

    if (!profile.empirical_vmem) {
        return;
    }
    const auto& empirical = *profile.empirical_vmem;
    if (profile.page_bytes != 4096) {
        throw ProfileError("empirical_vmem requires page_bytes == 4096");
    }
    if (!is_lower_sha256(empirical.source_sha256)) {
        throw ProfileError(
            "empirical_vmem source_sha256 must be lowercase hexadecimal SHA256");
    }
    if (empirical.source_kind.empty()) {
        throw ProfileError("empirical_vmem source_kind must not be empty");
    }
    if (empirical.source_capacity_bytes == 0) {
        throw ProfileError(
            "empirical_vmem source_capacity_bytes must be greater than zero");
    }
    if (empirical.quantile != "p50") {
        throw ProfileError("empirical_vmem quantile must be p50");
    }
    if (empirical.sample_count == 0) {
        throw ProfileError(
            "empirical_vmem sample_count must be greater than zero");
    }

    for (std::size_t index = 0; index < empirical.read_curve.size(); ++index) {
        const auto& point = empirical.read_curve[index];
        if (index != 0 &&
            point.pages <= empirical.read_curve[index - 1].pages) {
            throw ProfileError(
                "empirical_vmem pages must be strictly increasing");
        }
        if (point.cumulative_ns == 0 ||
            (index != 0 && point.cumulative_ns <=
                               empirical.read_curve[index - 1].cumulative_ns)) {
            throw ProfileError(
                "empirical_vmem P50 latency must be strictly increasing");
        }
        if (point.p95_ns < point.cumulative_ns) {
            throw ProfileError(
                "empirical_vmem P95 latency must not be below P50");
        }
    }
    if (empirical.read_curve.back().pages > 1023) {
        throw ProfileError("empirical_vmem final page must not exceed 1023");
    }
    if (empirical.program_p50_ns == 0) {
        throw ProfileError(
            "empirical_vmem program P50 must be greater than zero");
    }
    if (empirical.program_p95_ns < empirical.program_p50_ns) {
        throw ProfileError(
            "empirical_vmem program P95 must not be below P50");
    }
}

std::uint64_t blocks_per_plane(const Profile& profile)
{
    return calculate_blocks_per_plane(profile);
}

}  // namespace hbfsim

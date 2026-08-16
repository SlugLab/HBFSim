#include <hbfsim/thermal_report.hpp>

#include <json.hpp>

#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unistd.h>

namespace hbfsim {
namespace {

std::atomic<std::uint64_t> temporary_sequence{0};

[[noreturn]] void fail(std::string_view operation,
                       const std::filesystem::path& path)
{
    throw std::runtime_error(std::string(operation) + " " + path.string() +
                             ": " + std::strerror(errno));
}

double checked_number(long double value, std::string_view field)
{
    if (!std::isfinite(value)) {
        throw std::invalid_argument(std::string(field) + " must be finite");
    }
    const auto converted = static_cast<double>(value);
    if (!std::isfinite(converted)) {
        throw std::invalid_argument(std::string(field) + " exceeds JSON range");
    }
    return converted;
}

std::string_view source_name(ThermalTemperatureSource source)
{
    switch (source) {
    case ThermalTemperatureSource::LiveGpu:
        return "live_gpu";
    case ThermalTemperatureSource::Trace:
        return "trace";
    case ThermalTemperatureSource::Constant:
        return "constant";
    }
    throw std::invalid_argument("invalid thermal temperature source");
}

std::string_view mode_name(ThermalMode mode)
{
    switch (mode) {
    case ThermalMode::Normal:
        return "normal";
    case ThermalMode::Light:
        return "light";
    case ThermalMode::Severe:
        return "severe";
    case ThermalMode::Shutdown:
        return "shutdown";
    }
    throw std::invalid_argument("invalid thermal mode");
}

nlohmann::json profile_json(const ThermalReliabilityProfile& profile)
{
    return {
        {"temperature_source", source_name(profile.temperature_source)},
        {"source_identity", profile.source_identity},
        {"constant_gpu_millic",
         profile.constant_gpu_millic
             ? nlohmann::json(*profile.constant_gpu_millic)
             : nlohmann::json(nullptr)},
        {"trace_path",
         profile.trace_path ? nlohmann::json(profile.trace_path->string())
                            : nlohmann::json(nullptr)},
        {"ambient_millic", profile.ambient_millic},
        {"initial_hbf_junction_millic",
         profile.initial_hbf_junction_millic},
        {"tau_seconds", checked_number(profile.tau_seconds, "tau_seconds")},
        {"gpu_coupling_ratio",
         checked_number(profile.gpu_coupling_ratio, "gpu_coupling_ratio")},
        {"thermal_resistance_c_per_w",
         checked_number(profile.thermal_resistance_c_per_w,
                        "thermal_resistance_c_per_w")},
        {"idle_power_w", checked_number(profile.idle_power_w, "idle_power_w")},
        {"read_energy_j_per_byte",
         checked_number(profile.read_energy_j_per_byte,
                        "read_energy_j_per_byte")},
        {"write_energy_j_per_byte",
         checked_number(profile.write_energy_j_per_byte,
                        "write_energy_j_per_byte")},
        {"telemetry_period_ms", profile.telemetry_period_ms},
        {"controller_period_ms", profile.controller_period_ms},
        {"rtt_millic", profile.rtt_millic},
        {"ltt_millic", profile.ltt_millic},
        {"stt_millic", profile.stt_millic},
        {"shutdown_millic", profile.shutdown_millic},
        {"light_service_ppm", profile.light_service_ppm},
        {"zone_bytes", profile.zone_bytes},
        {"reference_retention_hours",
         checked_number(profile.reference_retention_hours,
                        "reference_retention_hours")},
        {"reference_retention_millic",
         profile.reference_retention_millic},
        {"retention_activation_energy_ev",
         checked_number(profile.retention_ea_ev,
                        "retention_activation_energy_ev")},
        {"refresh_damage_threshold",
         checked_number(profile.refresh_damage_threshold,
                        "refresh_damage_threshold")},
        {"read_disturb_limit", profile.read_disturb_limit},
        {"refresh_quantum_bytes", profile.refresh_quantum_bytes},
        {"registered_ranges_contain_valid_data",
         profile.registered_ranges_contain_valid_data},
        {"reliability_time_acceleration",
         checked_number(profile.reliability_time_acceleration,
                        "reliability_time_acceleration")},
        {"max_pec", profile.max_pec},
        {"reference_mtbf_hours",
         checked_number(profile.reference_mtbf_hours,
                        "reference_mtbf_hours")},
        {"mtbf_reference_millic", profile.mtbf_reference_millic},
        {"mtbf_activation_energy_ev_min",
         checked_number(profile.mtbf_ea_min_ev,
                        "mtbf_activation_energy_ev_min")},
        {"mtbf_activation_energy_ev_max",
         checked_number(profile.mtbf_ea_max_ev,
                        "mtbf_activation_energy_ev_max")},
        {"mtbf_activation_energy_ev_step",
         checked_number(profile.mtbf_ea_step_ev,
                        "mtbf_activation_energy_ev_step")},
        {"source_sha256", profile.source_sha256},
    };
}

nlohmann::json to_json(const ThermalRunSummary& summary)
{
    if (summary.profile_sha256.size() != 64 ||
        summary.temperature_source !=
            source_name(summary.profile.temperature_source) ||
        summary.terminal_status.empty()) {
        throw std::invalid_argument("invalid thermal summary provenance");
    }
    nlohmann::json samples = nlohmann::json::array();
    for (const auto& sample : summary.samples) {
        samples.push_back({
            {"host_ns", sample.host_ns},
            {"gpu_millic", sample.gpu_millic},
            {"gpu_power_mw", sample.gpu_power_mw},
            {"hbf_junction_millic", sample.hbf_junction_millic},
        });
    }
    nlohmann::json transitions = nlohmann::json::array();
    for (const auto& transition : summary.transitions) {
        transitions.push_back({
            {"host_ns", transition.host_ns},
            {"from", mode_name(transition.from)},
            {"to", mode_name(transition.to)},
            {"junction_millic", transition.junction_millic},
        });
    }
    nlohmann::json mtbf = nlohmann::json::array();
    for (const auto& point : summary.mtbf) {
        mtbf.push_back({
            {"activation_energy_ev",
             checked_number(point.activation_energy_ev,
                            "activation_energy_ev")},
            {"hazard", checked_number(point.hazard, "hazard")},
            {"failure_probability",
             checked_number(point.failure_probability,
                            "failure_probability")},
            {"equivalent_mtbf_hours",
             point.equivalent_mtbf_hours
                 ? nlohmann::json(checked_number(
                       *point.equivalent_mtbf_hours,
                       "equivalent_mtbf_hours"))
                 : nlohmann::json(nullptr)},
        });
    }
    const auto& accounting = summary.accounting;
    return {
        {"schema_version", 1},
        {"profile_sha256", summary.profile_sha256},
        {"profile", profile_json(summary.profile)},
        {"source",
         {{"kind", summary.temperature_source},
          {"identity", summary.profile.source_identity},
          {"sha256", summary.profile.source_sha256}}},
        {"reliability_time_acceleration",
         checked_number(summary.profile.reliability_time_acceleration,
                        "reliability_time_acceleration")},
        {"accelerated_reliability_time",
         summary.profile.reliability_time_acceleration != 1.0L},
        {"samples", std::move(samples)},
        {"transitions", std::move(transitions)},
        {"accounting",
         {{"application_read_bytes", accounting.application_read_bytes},
          {"application_write_bytes", accounting.application_write_bytes},
          {"refresh_read_bytes", accounting.refresh_read_bytes},
          {"refresh_write_bytes", accounting.refresh_write_bytes},
          {"refresh_debt_bytes", accounting.refresh_debt_bytes},
          {"refresh_claimed_bytes", accounting.refresh_claimed_bytes},
          {"refresh_background_drained_bytes",
           accounting.refresh_background_drained_bytes},
          {"completed_refresh_blocks", accounting.completed_refresh_blocks},
          {"maximum_pec", accounting.maximum_pec},
          {"average_pec_millionths", accounting.average_pec_millionths},
          {"normal_residency_ns", accounting.normal_residency_ns},
          {"light_residency_ns", accounting.light_residency_ns},
          {"severe_residency_ns", accounting.severe_residency_ns},
          {"shutdown_residency_ns", accounting.shutdown_residency_ns},
          {"maximum_retention_damage_millionths",
           accounting.maximum_retention_damage_millionths},
          {"average_retention_damage_millionths",
           accounting.average_retention_damage_millionths}}},
        {"mtbf_sensitivity", std::move(mtbf)},
        {"terminal_status", summary.terminal_status},
    };
}

void write_all(int fd, std::string_view bytes,
               const std::filesystem::path& path)
{
    std::size_t offset = 0;
    while (offset < bytes.size()) {
        const auto written =
            ::write(fd, bytes.data() + offset, bytes.size() - offset);
        if (written < 0 && errno == EINTR) {
            continue;
        }
        if (written <= 0) {
            fail("unable to write", path);
        }
        offset += static_cast<std::size_t>(written);
    }
}

}  // namespace

void write_thermal_summary(const std::filesystem::path& path,
                           const ThermalRunSummary& summary)
{
    const auto parent = path.parent_path().empty()
                            ? std::filesystem::path{"."}
                            : path.parent_path();
    if (!std::filesystem::is_directory(parent)) {
        throw std::invalid_argument("thermal summary parent must exist");
    }
    const auto sequence = temporary_sequence.fetch_add(1);
    const auto temporary = parent /
                           (path.filename().string() + ".tmp." +
                            std::to_string(::getpid()) + "." +
                            std::to_string(sequence));
    const auto payload = to_json(summary).dump(2) + '\n';
    int fd = ::open(temporary.c_str(),
                    O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
    if (fd < 0) {
        fail("unable to create", temporary);
    }
    try {
        write_all(fd, payload, temporary);
        while (::fdatasync(fd) != 0) {
            if (errno != EINTR) {
                fail("unable to sync", temporary);
            }
        }
        if (::close(fd) != 0) {
            fd = -1;
            fail("unable to close", temporary);
        }
        fd = -1;
        if (::rename(temporary.c_str(), path.c_str()) != 0) {
            fail("unable to rename", path);
        }
        const int directory =
            ::open(parent.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        if (directory < 0) {
            fail("unable to open parent for", path);
        }
        int sync_status;
        do {
            sync_status = ::fsync(directory);
        } while (sync_status != 0 && errno == EINTR);
        const auto sync_error = errno;
        const auto close_status = ::close(directory);
        if (sync_status != 0) {
            errno = sync_error;
            fail("unable to sync parent for", path);
        }
        if (close_status != 0) {
            fail("unable to close parent for", path);
        }
    } catch (...) {
        if (fd >= 0) {
            ::close(fd);
        }
        (void)::unlink(temporary.c_str());
        throw;
    }
}

}  // namespace hbfsim

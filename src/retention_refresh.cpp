#include <hbfsim/retention_refresh.hpp>

#include <algorithm>
#include <cmath>
#include <limits>
#include <string>

namespace hbfsim::reliability {
namespace {

void finite_nonnegative(double value, const char* field)
{
    if (!std::isfinite(value) || value < 0.0) {
        throw RetentionError(std::string(field) +
                             " must be finite and non-negative");
    }
}

std::uint64_t checked_multiply(std::uint64_t left, std::uint64_t right,
                               const char* field)
{
    if (right != 0 && left > std::numeric_limits<std::uint64_t>::max() / right) {
        throw RetentionError(std::string(field) + " overflows");
    }
    return left * right;
}

std::uint64_t checked_add(std::uint64_t left, std::uint64_t right,
                          const char* field)
{
    if (left > std::numeric_limits<std::uint64_t>::max() - right) {
        throw RetentionError(std::string(field) + " overflows");
    }
    return left + right;
}

std::uint64_t ceil_div(std::uint64_t value, std::uint64_t divisor)
{
    return value / divisor + (value % divisor != 0);
}

}  // namespace

RetentionRefreshModel::RetentionRefreshModel(RetentionRefreshConfig config)
    : config_(config)
{
    if (!std::isfinite(config_.reference_temperature_c) ||
        config_.reference_temperature_c <= -273.15 ||
        config_.reference_temperature_c > 1000.0) {
        throw RetentionError("reference temperature is outside sanity bounds");
    }
    if (!std::isfinite(config_.activation_energy_ev) ||
        config_.activation_energy_ev <= 0.0 ||
        config_.activation_energy_ev > 10.0) {
        throw RetentionError("activation energy must be in (0, 10] eV");
    }
    if (config_.reference_interval_ns == 0 || config_.protected_bytes == 0 ||
        config_.page_bytes == 0 || config_.independent_channels == 0 ||
        config_.read_latency_ns == 0 || config_.program_latency_ns == 0) {
        throw RetentionError("retention geometry and timing must be non-zero");
    }
    finite_nonnegative(config_.read_command_j, "read command energy");
    finite_nonnegative(config_.read_joules_per_byte, "read byte energy");
    finite_nonnegative(config_.program_command_j, "program command energy");
    finite_nonnegative(config_.program_joules_per_byte,
                       "program byte energy");
}

double RetentionRefreshModel::acceleration_factor(
    double reference_temperature_c, double temperature_c,
    double activation_energy_ev)
{
    for (const auto value : {reference_temperature_c, temperature_c}) {
        if (!std::isfinite(value) || value <= -273.15 || value > 1000.0) {
            throw RetentionError("temperature is outside sanity bounds");
        }
    }
    if (!std::isfinite(activation_energy_ev) || activation_energy_ev <= 0.0 ||
        activation_energy_ev > 10.0) {
        throw RetentionError("activation energy must be in (0, 10] eV");
    }
    const auto reference_k = reference_temperature_c + 273.15;
    const auto temperature_k = temperature_c + 273.15;
    const auto exponent = activation_energy_ev / kBoltzmannEvPerK *
                          (1.0 / reference_k - 1.0 / temperature_k);
    const auto result = std::exp(exponent);
    if (!std::isfinite(result) || result <= 0.0) {
        throw RetentionError("retention acceleration factor overflows");
    }
    return result;
}

RefreshDemand RetentionRefreshModel::advance(std::uint64_t start_time_ns,
                                              std::uint64_t end_time_ns,
                                              double temperature_c)
{
    if (end_time_ns <= start_time_ns) {
        throw RetentionError("retention step must have positive duration");
    }
    if (started_ && start_time_ns < last_end_time_ns_) {
        throw RetentionError("retention steps overlap or move backward");
    }
    const auto duration_ns = end_time_ns - start_time_ns;
    const auto factor = acceleration_factor(config_.reference_temperature_c,
                                            temperature_c,
                                            config_.activation_energy_ev);
    const auto increment_ns = static_cast<long double>(duration_ns) * factor;
    if (!std::isfinite(static_cast<double>(increment_ns))) {
        throw RetentionError("equivalent retention age overflows");
    }
    equivalent_age_remainder_ns_ += increment_ns;
    const auto cycles_ld = std::floor(
        equivalent_age_remainder_ns_ / config_.reference_interval_ns);
    if (cycles_ld > std::numeric_limits<std::uint64_t>::max()) {
        throw RetentionError("refresh cycle count overflows");
    }
    const auto cycles = static_cast<std::uint64_t>(cycles_ld);
    equivalent_age_remainder_ns_ -=
        static_cast<long double>(cycles) * config_.reference_interval_ns;

    const auto pages_per_cycle = ceil_div(config_.protected_bytes,
                                          config_.page_bytes);
    const auto pages = checked_multiply(cycles, pages_per_cycle,
                                        "refresh page count");
    const auto read_bytes = checked_multiply(cycles, config_.protected_bytes,
                                             "refresh read bytes");
    const auto program_bytes = checked_multiply(cycles, config_.protected_bytes,
                                                "refresh program bytes");
    const auto page_service_ns = checked_add(config_.read_latency_ns,
                                             config_.program_latency_ns,
                                             "refresh page service time");
    const auto serialized = checked_multiply(pages, page_service_ns,
                                             "serialized refresh busy time");
    const auto waves_per_cycle = ceil_div(pages_per_cycle,
                                          config_.independent_channels);
    const auto channel_critical = checked_multiply(
        checked_multiply(cycles, waves_per_cycle, "refresh channel waves"),
        page_service_ns, "refresh channel critical path");
    const long double energy_per_cycle =
        static_cast<long double>(pages_per_cycle) *
            (config_.read_command_j + config_.program_command_j) +
        static_cast<long double>(config_.protected_bytes) *
            (config_.read_joules_per_byte +
             config_.program_joules_per_byte);
    const auto energy = static_cast<double>(energy_per_cycle * cycles);
    if (!std::isfinite(energy) || energy < 0.0) {
        throw RetentionError("refresh energy overflows");
    }

    stats_.observed_time_ns = checked_add(stats_.observed_time_ns, duration_ns,
                                          "observed retention time");
    stats_.equivalent_age_s += static_cast<double>(increment_ns / 1.0e9L);
    stats_.equivalent_age_remainder_s =
        static_cast<double>(equivalent_age_remainder_ns_ / 1.0e9L);
    stats_.refresh_cycles = checked_add(stats_.refresh_cycles, cycles,
                                        "total refresh cycles");
    stats_.refresh_pages = checked_add(stats_.refresh_pages, pages,
                                       "total refresh pages");
    stats_.extra_read_bytes = checked_add(stats_.extra_read_bytes, read_bytes,
                                          "total refresh read bytes");
    stats_.extra_program_bytes = checked_add(
        stats_.extra_program_bytes, program_bytes,
        "total refresh program bytes");
    stats_.extra_media_energy_j += energy;
    stats_.serialized_media_busy_ns = checked_add(
        stats_.serialized_media_busy_ns, serialized,
        "total serialized refresh busy time");
    stats_.channel_critical_path_ns = checked_add(
        stats_.channel_critical_path_ns, channel_critical,
        "total refresh channel critical path");
    started_ = true;
    last_end_time_ns_ = end_time_ns;

    return RefreshDemand{
        .start_time_ns = start_time_ns,
        .end_time_ns = end_time_ns,
        .temperature_c = temperature_c,
        .acceleration_factor = factor,
        .equivalent_age_increment_s =
            static_cast<double>(increment_ns / 1.0e9L),
        .equivalent_age_remainder_s = stats_.equivalent_age_remainder_s,
        .refresh_cycles = cycles,
        .refresh_pages = pages,
        .extra_read_bytes = read_bytes,
        .extra_program_bytes = program_bytes,
        .extra_media_energy_j = energy,
        .serialized_media_busy_ns = serialized,
        .channel_critical_path_ns = channel_critical,
        .same_die_collision_upper_bound_ns = cycles == 0 ? 0 : page_service_ns,
    };
}

const RetentionRefreshConfig& RetentionRefreshModel::config() const noexcept
{
    return config_;
}

const RetentionRefreshStats& RetentionRefreshModel::stats() const noexcept
{
    return stats_;
}

}  // namespace hbfsim::reliability

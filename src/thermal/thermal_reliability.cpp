#include <hbfsim/thermal_reliability.hpp>

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace hbfsim {
namespace {

constexpr long double kBoltzmannEvPerKelvin = 8.617333262145e-5L;
constexpr long double kCelsiusToKelvin = 273.15L;
constexpr long double kNormalServicePpm = 1'000'000.0L;

long double kelvin(std::int64_t temperature_millic)
{
    const auto value = static_cast<long double>(temperature_millic) / 1000.0L +
                       kCelsiusToKelvin;
    if (!std::isfinite(value) || value <= 0.0L) {
        throw std::invalid_argument("temperature must convert to positive Kelvin");
    }
    return value;
}

void require_finite(long double value, const char* message)
{
    if (!std::isfinite(value)) {
        throw std::overflow_error(message);
    }
}

struct TransitionResult {
    ThermalMode mode;
    std::vector<ThermalMode> edges;
};

TransitionResult transition(ThermalMode current,
                            std::int64_t temperature_millic,
                            const ThermalReliabilityProfile& profile)
{
    if (current == ThermalMode::Shutdown) {
        return {.mode = current, .edges = {}};
    }

    std::vector<ThermalMode> edges;
    for (;;) {
        if (current == ThermalMode::Normal &&
            temperature_millic >= profile.ltt_millic) {
            current = ThermalMode::Light;
            edges.push_back(current);
            continue;
        }
        if (current == ThermalMode::Light &&
            temperature_millic >= profile.stt_millic) {
            current = ThermalMode::Severe;
            edges.push_back(current);
            continue;
        }
        if (current == ThermalMode::Severe &&
            temperature_millic >= profile.shutdown_millic) {
            current = ThermalMode::Shutdown;
            edges.push_back(current);
            continue;
        }
        if (current == ThermalMode::Severe &&
            temperature_millic <= profile.ltt_millic) {
            current = ThermalMode::Light;
            edges.push_back(current);
            continue;
        }
        if (current == ThermalMode::Light &&
            temperature_millic <= profile.rtt_millic) {
            current = ThermalMode::Normal;
            edges.push_back(current);
            continue;
        }
        return {.mode = current, .edges = std::move(edges)};
    }
}

std::uint32_t service_for(ThermalMode mode,
                          const ThermalReliabilityProfile& profile) noexcept
{
    if (mode == ThermalMode::Normal) {
        return static_cast<std::uint32_t>(kNormalServicePpm);
    }
    if (mode == ThermalMode::Light) {
        return profile.light_service_ppm;
    }
    return 0;
}

long double elapsed_hours(std::chrono::nanoseconds elapsed)
{
    if (elapsed.count() < 0) {
        throw std::invalid_argument("elapsed duration must be nonnegative");
    }
    return std::chrono::duration<long double, std::ratio<3600>>(elapsed).count();
}

}  // namespace

ThermalReliabilityModel::ThermalReliabilityModel(
    ThermalReliabilityProfile profile, std::int64_t initial_junction_millic)
    : profile_(std::move(profile)),
      junction_millic_(static_cast<long double>(initial_junction_millic)),
      mode_(transition(ThermalMode::Normal, initial_junction_millic, profile_)
                .mode)
{
    require_finite(junction_millic_, "initial HBF junction is not finite");
}

ThermalSnapshot ThermalReliabilityModel::advance(const ThermalInput& input)
{
    if (input.elapsed_ns == 0) {
        throw std::invalid_argument("thermal interval must be positive");
    }
    const auto dt_seconds =
        static_cast<long double>(input.elapsed_ns) / 1'000'000'000.0L;
    const auto read_power = profile_.read_energy_j_per_byte *
                            static_cast<long double>(input.read_bytes) /
                            dt_seconds;
    const auto write_power = profile_.write_energy_j_per_byte *
                             static_cast<long double>(input.write_bytes) /
                             dt_seconds;
    const auto hbf_power = profile_.idle_power_w + read_power + write_power;
    const auto ambient_c =
        static_cast<long double>(profile_.ambient_millic) / 1000.0L;
    const auto gpu_c = static_cast<long double>(input.gpu_millic) / 1000.0L;
    const auto target_c = ambient_c +
                          profile_.gpu_coupling_ratio * (gpu_c - ambient_c) +
                          profile_.thermal_resistance_c_per_w * hbf_power;
    const auto current_c = junction_millic_ / 1000.0L;
    const auto alpha = -std::expm1(-dt_seconds / profile_.tau_seconds);
    const auto next_c = current_c + (target_c - current_c) * alpha;
    require_finite(hbf_power, "HBF power is not finite");
    require_finite(target_c, "target HBF junction is not finite");
    require_finite(next_c, "next HBF junction is not finite");
    if (next_c < 0.0L || next_c > 105.0L) {
        throw std::out_of_range("modeled HBF junction is outside 0C to 105C");
    }

    junction_millic_ = next_c * 1000.0L;
    const auto rounded = static_cast<std::int64_t>(std::llround(junction_millic_));
    const auto transitioned = transition(mode_, rounded, profile_);
    if (transitioned.edges.size() >
        std::numeric_limits<std::uint64_t>::max() - transition_count_) {
        throw std::overflow_error("thermal transition counter overflow");
    }
    mode_ = transitioned.mode;
    last_transitions_ = transitioned.edges;
    transition_count_ += last_transitions_.size();
    if (generation_ == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("thermal generation overflow");
    }
    ++generation_;
    return snapshot();
}

std::uint64_t ThermalReliabilityModel::transition_count() const noexcept
{
    return transition_count_;
}

const std::vector<ThermalMode>&
ThermalReliabilityModel::last_transitions() const noexcept
{
    return last_transitions_;
}

ThermalSnapshot ThermalReliabilityModel::snapshot() const noexcept
{
    return {
        .generation = generation_,
        .mode = mode_,
        .junction_millic =
            static_cast<std::int64_t>(std::llround(junction_millic_)),
        .service_ppm = service_for(mode_, profile_),
    };
}

long double retention_hours(const ThermalReliabilityProfile& profile,
                            std::int64_t temperature_millic)
{
    const auto temperature = kelvin(temperature_millic);
    const auto reference = kelvin(profile.reference_retention_millic);
    const auto exponent = profile.retention_ea_ev / kBoltzmannEvPerKelvin *
                          (1.0L / temperature - 1.0L / reference);
    const auto lifetime = profile.reference_retention_hours * std::exp(exponent);
    if (!std::isfinite(lifetime) || lifetime <= 0.0L) {
        throw std::overflow_error("retention lifetime is not finite and positive");
    }
    return lifetime;
}

void integrate_zone_damage(ZoneReliability& zone,
                           const ThermalReliabilityProfile& profile,
                           std::int64_t temperature_millic,
                           std::chrono::nanoseconds elapsed)
{
    if (!zone.valid) {
        return;
    }
    const auto increment = profile.reliability_time_acceleration *
                           elapsed_hours(elapsed) /
                           retention_hours(profile, temperature_millic);
    require_finite(increment, "retention damage increment is not finite");
    const auto next = zone.retention_damage + increment;
    require_finite(next, "retention damage is not finite");
    zone.retention_damage = next;
}

bool refresh_eligible(const ZoneReliability& zone,
                      const ThermalReliabilityProfile& profile) noexcept
{
    return zone.valid &&
           (zone.retention_damage >= profile.refresh_damage_threshold ||
            zone.read_disturb_count >= profile.read_disturb_limit);
}

void commit_refresh(ZoneReliability& zone)
{
    if (zone.current_pec == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("PEC overflow");
    }
    ++zone.current_pec;
    zone.maximum_pec = std::max(zone.maximum_pec, zone.current_pec);
    zone.retention_damage = 0.0L;
    zone.read_disturb_count = 0;
}

std::vector<MtbfSensitivityPoint> integrate_mtbf_sensitivity(
    const ThermalReliabilityProfile& profile,
    std::span<const TemperatureInterval> intervals)
{
    std::vector<MtbfSensitivityPoint> result;
    const auto reference = kelvin(profile.mtbf_reference_millic);
    long double total_elapsed_hours = 0.0L;
    for (const auto& interval : intervals) {
        total_elapsed_hours += elapsed_hours(interval.elapsed);
    }

    constexpr std::size_t kMaximumSweepPoints = 1'000'000;
    for (std::size_t index = 0; index < kMaximumSweepPoints; ++index) {
        const auto activation = profile.mtbf_ea_min_ev +
                                static_cast<long double>(index) *
                                    profile.mtbf_ea_step_ev;
        const auto tolerance =
            std::max(1.0L, std::fabs(profile.mtbf_ea_max_ev)) * 1e-15L;
        if (activation > profile.mtbf_ea_max_ev + tolerance) {
            break;
        }

        long double hazard = 0.0L;
        for (const auto& interval : intervals) {
            const auto duration_hours = elapsed_hours(interval.elapsed);
            const auto temperature = kelvin(interval.temperature_millic);
            const auto exponent = activation / kBoltzmannEvPerKelvin *
                                  (1.0L / reference - 1.0L / temperature);
            const auto acceleration = std::exp(exponent);
            const auto increment = duration_hours * acceleration /
                                   profile.reference_mtbf_hours;
            require_finite(increment, "MTBF hazard increment is not finite");
            hazard += increment;
            require_finite(hazard, "MTBF hazard is not finite");
        }

        result.push_back({
            .activation_energy_ev = activation,
            .hazard = hazard,
            .failure_probability = -std::expm1(-hazard),
            .equivalent_mtbf_hours =
                hazard > 0.0L && total_elapsed_hours > 0.0L
                    ? std::optional<long double>(total_elapsed_hours / hazard)
                    : std::nullopt,
        });
    }
    if (result.empty()) {
        throw std::invalid_argument("MTBF activation-energy sweep is empty");
    }
    return result;
}

}  // namespace hbfsim

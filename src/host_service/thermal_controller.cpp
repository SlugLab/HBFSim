#include "thermal_controller.hpp"

#include <limits>
#include <optional>
#include <stdexcept>
#include <utility>

namespace hbfsim::host_service {
namespace {

std::optional<std::uint64_t> checked_delta_sum(
    std::uint64_t first_current, std::uint64_t first_previous,
    std::uint64_t second_current, std::uint64_t second_previous) noexcept
{
    if (first_current < first_previous || second_current < second_previous) {
        return std::nullopt;
    }
    const auto first = first_current - first_previous;
    const auto second = second_current - second_previous;
    if (first > std::numeric_limits<std::uint64_t>::max() - second) {
        return std::nullopt;
    }
    return first + second;
}

}  // namespace

ThermalController::ThermalController(ThermalReliabilityProfile profile,
                                     ControlView control,
                                     std::chrono::nanoseconds start_time)
    : control_(control),
      model_(profile, profile.initial_hbf_junction_millic),
      last_time_(start_time)
{
    if (!control_.valid() || start_time.count() < 0) {
        throw std::invalid_argument("invalid thermal controller construction");
    }
}

std::optional<ThermalController::TelemetrySnapshot>
ThermalController::read_telemetry() const noexcept
{
    const auto* header = control_.header();
    for (std::uint32_t attempt = 0; attempt < 16; ++attempt) {
        const auto before = atomic_load(header->telemetry_generation,
                                        std::memory_order_acquire);
        if ((before & 1U) != 0) {
            continue;
        }
        const TelemetrySnapshot snapshot{
            .generation = before,
            .host_ns = atomic_load(header->telemetry_host_ns,
                                   std::memory_order_relaxed),
            .gpu_millic = atomic_load(header->telemetry_gpu_millic,
                                      std::memory_order_relaxed),
            .gpu_power_mw = atomic_load(header->telemetry_gpu_power_mw,
                                        std::memory_order_relaxed),
            .status = static_cast<runtime::TelemetryStatus>(
                atomic_load(header->telemetry_status,
                            std::memory_order_relaxed)),
        };
        const auto after = atomic_load(header->telemetry_generation,
                                       std::memory_order_acquire);
        if (before == after && (after & 1U) == 0) {
            return snapshot;
        }
    }
    return std::nullopt;
}

ThermalController::ByteCounters ThermalController::read_counters() const noexcept
{
    const auto* header = control_.header();
    return {
        .read_bytes = atomic_load(header->thermal_read_bytes,
                                  std::memory_order_acquire),
        .write_bytes = atomic_load(header->thermal_write_bytes,
                                   std::memory_order_acquire),
        .refresh_read_bytes = atomic_load(header->thermal_refresh_read_bytes,
                                          std::memory_order_acquire),
        .refresh_write_bytes = atomic_load(header->thermal_refresh_write_bytes,
                                           std::memory_order_acquire),
    };
}

void ThermalController::publish(const ThermalSnapshot& snapshot) noexcept
{
    auto* header = control_.header();
    const auto generation =
        atomic_load(header->thermal_generation, std::memory_order_acquire);
    if ((generation & 1U) != 0 ||
        generation > std::numeric_limits<std::uint64_t>::max() - 2) {
        return;
    }
    atomic_store(header->thermal_generation, generation + 1,
                 std::memory_order_release);
    atomic_store(header->thermal_mode,
                 static_cast<std::uint32_t>(snapshot.mode),
                 std::memory_order_relaxed);
    atomic_store(header->thermal_junction_millic, snapshot.junction_millic,
                 std::memory_order_relaxed);
    atomic_store(header->thermal_service_ppm, snapshot.service_ppm,
                 std::memory_order_relaxed);
    const auto admission = snapshot.mode == ThermalMode::Normal ||
                                   snapshot.mode == ThermalMode::Light
                               ? 1U
                               : 0U;
    atomic_store(header->thermal_admission_open, admission,
                 std::memory_order_relaxed);
    atomic_store(header->thermal_transitions, model_.transition_count(),
                 std::memory_order_relaxed);
    atomic_store(header->thermal_generation, generation + 2,
                 std::memory_order_release);
}

ThermalControllerStatus ThermalController::tick_at(
    std::chrono::nanoseconds now) noexcept
{
    if (!control_.valid() || now <= last_time_) {
        return ThermalControllerStatus::ModelError;
    }
    const auto telemetry = read_telemetry();
    if (!telemetry || telemetry->generation == 0 ||
        telemetry->generation < last_telemetry_generation_ ||
        telemetry->host_ns == 0 ||
        (last_telemetry_host_ns_ != 0 &&
         telemetry->host_ns <= last_telemetry_host_ns_)) {
        return telemetry &&
                       telemetry->generation == last_telemetry_generation_
                   ? ThermalControllerStatus::StaleTelemetry
                   : ThermalControllerStatus::InvalidTelemetry;
    }
    if (telemetry->status != runtime::TelemetryStatus::Ready) {
        return ThermalControllerStatus::SourceFailed;
    }
    if (telemetry->gpu_millic < 0 || telemetry->gpu_millic > 105'000) {
        return ThermalControllerStatus::InvalidTelemetry;
    }

    const auto counters = read_counters();
    if (counters.read_bytes < last_counters_.read_bytes ||
        counters.write_bytes < last_counters_.write_bytes ||
        counters.refresh_read_bytes < last_counters_.refresh_read_bytes ||
        counters.refresh_write_bytes < last_counters_.refresh_write_bytes) {
        return ThermalControllerStatus::CounterRegression;
    }
    const auto read_delta = checked_delta_sum(
        counters.read_bytes, last_counters_.read_bytes,
        counters.refresh_read_bytes, last_counters_.refresh_read_bytes);
    const auto write_delta = checked_delta_sum(
        counters.write_bytes, last_counters_.write_bytes,
        counters.refresh_write_bytes, last_counters_.refresh_write_bytes);
    if (!read_delta || !write_delta) {
        return ThermalControllerStatus::CounterOverflow;
    }

    const auto elapsed = now - last_time_;
    try {
        const auto previous_mode = model_.snapshot().mode;
        const auto thermal = model_.advance({
            .elapsed_ns = static_cast<std::uint64_t>(elapsed.count()),
            .gpu_millic = telemetry->gpu_millic,
            .read_bytes = *read_delta,
            .write_bytes = *write_delta,
        });
        publish(thermal);
        samples_.push_back({
            .host_ns = telemetry->host_ns,
            .gpu_millic = telemetry->gpu_millic,
            .gpu_power_mw = telemetry->gpu_power_mw,
            .hbf_junction_millic = thermal.junction_millic,
        });
        temperature_intervals_.push_back({
            .temperature_millic = thermal.junction_millic,
            .elapsed = elapsed,
        });
        auto from = previous_mode;
        for (const auto to : model_.last_transitions()) {
            transitions_.push_back({
                .host_ns = telemetry->host_ns,
                .from = from,
                .to = to,
                .junction_millic = thermal.junction_millic,
            });
            from = to;
        }
        accounting_.application_read_bytes = counters.read_bytes;
        accounting_.application_write_bytes = counters.write_bytes;
        accounting_.refresh_read_bytes = counters.refresh_read_bytes;
        accounting_.refresh_write_bytes = counters.refresh_write_bytes;
        const auto elapsed_ns = static_cast<std::uint64_t>(elapsed.count());
        auto* residency = &accounting_.normal_residency_ns;
        if (previous_mode == ThermalMode::Light) {
            residency = &accounting_.light_residency_ns;
        } else if (previous_mode == ThermalMode::Severe) {
            residency = &accounting_.severe_residency_ns;
        } else if (previous_mode == ThermalMode::Shutdown) {
            residency = &accounting_.shutdown_residency_ns;
        }
        if (*residency > std::numeric_limits<std::uint64_t>::max() - elapsed_ns) {
            return ThermalControllerStatus::CounterOverflow;
        }
        *residency += elapsed_ns;
        last_time_ = now;
        last_telemetry_generation_ = telemetry->generation;
        last_telemetry_host_ns_ = telemetry->host_ns;
        last_counters_ = counters;
        return thermal.mode == ThermalMode::Shutdown
                   ? ThermalControllerStatus::Shutdown
                   : ThermalControllerStatus::Ready;
    } catch (...) {
        return ThermalControllerStatus::ModelError;
    }
}

ThermalSnapshot ThermalController::snapshot() const noexcept
{
    return model_.snapshot();
}

const std::vector<ThermalSampleRecord>& ThermalController::samples() const noexcept
{
    return samples_;
}

const std::vector<ThermalTransition>&
ThermalController::transitions() const noexcept
{
    return transitions_;
}

const std::vector<TemperatureInterval>&
ThermalController::temperature_intervals() const noexcept
{
    return temperature_intervals_;
}

ThermalAccounting ThermalController::accounting() const noexcept
{
    auto result = accounting_;
    const auto* header = control_.header();
    result.refresh_debt_bytes = atomic_load(header->refresh_debt_bytes,
                                            std::memory_order_acquire);
    result.refresh_claimed_bytes = atomic_load(
        header->thermal_refresh_claimed_bytes, std::memory_order_acquire);
    result.refresh_background_drained_bytes = atomic_load(
        header->thermal_refresh_background_drained_bytes,
        std::memory_order_acquire);
    result.completed_refresh_blocks = atomic_load(
        header->thermal_completed_refresh_blocks, std::memory_order_acquire);
    result.maximum_pec = atomic_load(header->thermal_max_pec,
                                     std::memory_order_acquire);
    result.average_pec_millionths = atomic_load(
        header->thermal_average_pec_millionths, std::memory_order_acquire);
    return result;
}

}  // namespace hbfsim::host_service

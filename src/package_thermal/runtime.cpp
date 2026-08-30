#include <hbfsim/package_thermal.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>

namespace hbfsim::package_thermal {
namespace {

std::uint64_t exact_u64(double value, const char* field)
{
    if (!std::isfinite(value) || value <= 0.0 ||
        value > static_cast<double>(std::numeric_limits<std::uint64_t>::max()) ||
        std::floor(value) != value) {
        throw ThermalError(std::string(field) +
                           " must be an exact positive integer");
    }
    return static_cast<std::uint64_t>(value);
}

std::size_t named_index(std::span<const std::string> names,
                        const std::string& name,
                        const char* field)
{
    const auto found = std::find(names.begin(), names.end(), name);
    if (found == names.end()) {
        throw ThermalError(std::string(field) + " lacks required node " + name);
    }
    return static_cast<std::size_t>(std::distance(names.begin(), found));
}

void require_exact_names(std::span<const std::string> actual,
                         std::span<const std::string> expected,
                         const char* field)
{
    if (actual.size() != expected.size() ||
        !std::equal(actual.begin(), actual.end(), expected.begin(),
                    expected.end())) {
        throw ThermalError(std::string(field) +
                           " must exactly match package topology nodes");
    }
}

std::uint64_t host_monotonic_ns()
{
    const auto value = std::chrono::duration_cast<std::chrono::nanoseconds>(
                           std::chrono::steady_clock::now().time_since_epoch())
                           .count();
    if (value < 0) {
        throw ThermalError("host monotonic clock returned a negative value");
    }
    return static_cast<std::uint64_t>(value);
}

}  // namespace

PackageThermalRuntime::PackageThermalRuntime(
    PackageThermalProfile profile, std::unique_ptr<ThermalModel> model)
    : profile_(std::move(profile)),
      model_(std::move(model)),
      accumulator_(exact_u64(profile_.bin_width_ns.value, "thermal bin width"),
                   profile_.topology, profile_.nand_energy,
                   profile_.base_die),
      policy_(profile_.stage, profile_.policy)
{
    validate_package_thermal_profile(profile_);
    if (!model_) {
        throw ThermalError("package thermal runtime requires a model");
    }
    const auto& nodes = profile_.topology.node_names();
    require_exact_names(model_->input_names(), nodes,
                        "thermal model input names");
    require_exact_names(model_->output_names(), nodes,
                        "thermal model output names");
    const auto period = exact_u64(profile_.bin_width_ns.value,
                                  "thermal bin width");
    if (model_->sample_period_ns() != period) {
        throw ThermalError(
            "thermal model sample period does not match power bin width");
    }
    gpu_node_ = named_index(nodes, "gpu", "package topology");
    gpu_provider_ = make_power_provider(profile_.gpu_provider);
    if (profile_.legacy_power_schema) {
        near_memory_nodes_.push_back(
            named_index(nodes, "hbm", "package topology"));
        near_memory_providers_.push_back(
            make_power_provider(profile_.hbm_provider));
    } else {
        for (const auto& source : profile_.near_memory.power_sources) {
            near_memory_nodes_.push_back(
                named_index(nodes, source.thermal_node,
                            "package topology"));
            near_memory_providers_.push_back(
                make_power_provider(source.provider));
        }
    }
    for (std::size_t index = 0; index < nodes.size(); ++index) {
        if (nodes[index].starts_with("hbf.")) {
            hbf_outputs_.push_back(index);
        }
    }
    if (hbf_outputs_.empty()) {
        throw ThermalError("package topology has no hbf.* thermal outputs");
    }
    model_->reset(profile_.ambient_c.value);
    decision_ = PolicyDecision{
        .raw_mode = ThermalMode::Normal,
        .effective_mode = ThermalMode::Normal,
        .service_scale = 1.0,
        .admission_open = true,
        .generation = 0,
        .changed = false,
        .debounce_counter = 0,
        .dwell_counter = 0,
    };
}

void PackageThermalRuntime::record(const NandMediaActivity& activity)
{
    const auto cost_start = std::chrono::steady_clock::now();
    if (finished_) {
        throw ThermalError("cannot record media activity after thermal finish");
    }
    if (last_activity_start_ns_.has_value() &&
        activity.start_time_ns < *last_activity_start_ns_) {
        throw ThermalError("media activity timestamps are non-monotonic");
    }
    last_activity_start_ns_ = activity.start_time_ns;
    accumulator_.add(activity);
    stats_.media_observer_cost_ns += static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - cost_start)
            .count());
}

std::vector<ThermalObservation> PackageThermalRuntime::advance_to(
    std::uint64_t relative_time_ns)
{
    if (finished_) {
        throw ThermalError("cannot advance thermal runtime after finish");
    }
    if (last_advance_ns_.has_value() &&
        relative_time_ns < *last_advance_ns_) {
        throw ThermalError("thermal runtime timestamp is non-monotonic");
    }
    last_advance_ns_ = relative_time_ns;
    const auto start = std::chrono::steady_clock::now();
    auto bins = accumulator_.drain_complete(relative_time_ns);
    stats_.power_aggregation_cost_ns += static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - start)
            .count());
    return process(std::move(bins));
}

std::vector<ThermalObservation> PackageThermalRuntime::finish(
    std::uint64_t relative_time_ns)
{
    if (finished_) {
        throw ThermalError("thermal runtime was already finished");
    }
    if (last_advance_ns_.has_value() &&
        relative_time_ns < *last_advance_ns_) {
        throw ThermalError("thermal finish timestamp is non-monotonic");
    }
    finished_ = true;
    if (relative_time_ns == 0) {
        return {};
    }
    const auto start = std::chrono::steady_clock::now();
    auto bins = accumulator_.finish(relative_time_ns);
    stats_.power_aggregation_cost_ns += static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now() - start)
            .count());
    return process(std::move(bins));
}

std::vector<ThermalObservation> PackageThermalRuntime::process(
    std::vector<PackagePowerBin> bins)
{
    std::vector<ThermalObservation> result;
    result.reserve(bins.size());
    const auto& names = profile_.topology.node_names();
    for (auto& bin : bins) {
        auto power = std::move(bin.node_power_w);
        if (power.size() != names.size()) {
            throw ThermalError("binned power vector has the wrong dimension");
        }
        const auto sample_time = bin.start_time_ns;
        const auto host_sample_time = host_monotonic_ns();
        const auto telemetry_start = std::chrono::steady_clock::now();
        const auto raw_accelerator = gpu_provider_->sample_w(sample_time);
        std::vector<double> near_memory_power;
        near_memory_power.reserve(near_memory_providers_.size());
        for (auto& provider : near_memory_providers_) {
            near_memory_power.push_back(provider->sample_w(sample_time));
        }
        stats_.telemetry_sampling_cost_ns += static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - telemetry_start)
                .count());
        if (profile_.legacy_power_schema) {
            const auto hbm = near_memory_power.front();
            const auto composed = compose_gpu_hbm_power(
                raw_accelerator, hbm, profile_.gpu_power_semantics,
                profile_.gpu_power_semantics == GpuPowerSemantics::BoardTotal
                    ? hbm
                    : 0.0);
            power[gpu_node_] += composed.allocated_gpu_compute_w;
            power[near_memory_nodes_.front()] += composed.hbm_w;
        } else {
            power[gpu_node_] += raw_accelerator;
            for (std::size_t index = 0;
                 index < near_memory_power.size(); ++index) {
                power[near_memory_nodes_[index]] += near_memory_power[index];
            }
        }
        const auto model_start = std::chrono::steady_clock::now();
        auto temperatures = model_->step(power);
        stats_.model_advance_cost_ns += static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - model_start)
                .count());
        if (temperatures.size() != names.size()) {
            throw ThermalError(
                "thermal model returned the wrong output dimension");
        }
        auto hotspot_index = hbf_outputs_.front();
        for (const auto index : hbf_outputs_) {
            if (!std::isfinite(temperatures[index])) {
                throw ThermalError("thermal model returned non-finite HBF state");
            }
            if (temperatures[index] > temperatures[hotspot_index]) {
                hotspot_index = index;
            }
        }
        const auto hotspot = temperatures[hotspot_index];
        const auto previous = decision_.raw_mode;
        const auto policy_start = std::chrono::steady_clock::now();
        decision_ = policy_.observe(hotspot);
        stats_.policy_cost_ns += static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - policy_start)
                .count());
        const auto duration = bin.end_time_ns - bin.start_time_ns;
        switch (decision_.raw_mode) {
        case ThermalMode::Normal: stats_.time_normal_ns += duration; break;
        case ThermalMode::Light: stats_.time_light_ns += duration; break;
        case ThermalMode::Severe: stats_.time_severe_ns += duration; break;
        case ThermalMode::Shutdown: stats_.time_shutdown_ns += duration; break;
        }
        if (decision_.changed && decision_.raw_mode != previous) {
            switch (decision_.raw_mode) {
            case ThermalMode::Light: ++stats_.light_transitions; break;
            case ThermalMode::Severe: ++stats_.severe_transitions; break;
            case ThermalMode::Shutdown: ++stats_.shutdown_transitions; break;
            case ThermalMode::Normal: break;
            }
        }
        ++stats_.thermal_steps;
        if (stats_.thermal_steps == 1 ||
            hotspot > stats_.maximum_hbf_temperature_c) {
            stats_.maximum_hbf_temperature_c = hotspot;
            stats_.maximum_time_ns = bin.end_time_ns;
            stats_.maximum_node = names[hotspot_index];
        }
        ThermalObservation observation{
            .start_time_ns = bin.start_time_ns,
            .end_time_ns = bin.end_time_ns,
            .host_sample_time_ns = host_sample_time,
            .accelerator_power_w = raw_accelerator,
            .near_memory_power_w = std::move(near_memory_power),
            .input_power_w = std::move(power),
            .temperatures_c = std::move(temperatures),
            .hbf_hotspot_c = hotspot,
            .hbf_hotspot_node = names[hotspot_index],
            .policy = decision_,
            .media_event_count = bin.media_event_count,
            .media_read_bytes = bin.read_bytes,
            .media_program_bytes = bin.program_bytes,
            .media_erase_count = bin.erase_count,
        };
        latest_ = observation;
        result.push_back(std::move(observation));
    }
    return result;
}

const ThermalRuntimeStats& PackageThermalRuntime::stats() const noexcept
{
    return stats_;
}

const PolicyDecision& PackageThermalRuntime::decision() const noexcept
{
    return decision_;
}

const std::optional<ThermalObservation>&
PackageThermalRuntime::latest() const noexcept
{
    return latest_;
}

const PackageThermalProfile& PackageThermalRuntime::profile() const noexcept
{
    return profile_;
}

std::string PackageThermalRuntime::model_identity() const
{
    return model_->identity();
}

}  // namespace hbfsim::package_thermal

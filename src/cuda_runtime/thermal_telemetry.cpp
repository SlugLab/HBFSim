#include "thermal_telemetry.hpp"

#include <hbfsim/exact_artifact.hpp>
#include <hbfsim/exact_environment.hpp>

#include <json.hpp>

#include <chrono>
#include <fstream>
#include <iterator>
#include <limits>
#include <span>
#include <stdexcept>
#include <string>
#include <utility>

namespace hbfsim::runtime {

ConstantThermalSource::ConstantThermalSource(
    ThermalTelemetrySample sample) noexcept
    : sample_(sample)
{
}

std::optional<ThermalTelemetrySample>
ConstantThermalSource::sample() noexcept
{
    const auto result = sample_;
    if (sample_.host_ns == std::numeric_limits<std::uint64_t>::max()) {
        return std::nullopt;
    }
    ++sample_.host_ns;
    return result;
}

TraceThermalSource::TraceThermalSource(
    std::vector<ThermalTelemetrySample> samples)
    : samples_(std::move(samples))
{
    std::uint64_t previous = 0;
    for (const auto& sample : samples_) {
        if (sample.host_ns == 0 || sample.host_ns <= previous ||
            sample.gpu_millic < 0 || sample.gpu_millic > 105'000) {
            throw std::invalid_argument(
                "thermal trace must contain increasing valid samples");
        }
        previous = sample.host_ns;
    }
}

std::optional<ThermalTelemetrySample> TraceThermalSource::sample() noexcept
{
    if (next_ == samples_.size()) {
        return std::nullopt;
    }
    return samples_[next_++];
}

NvmlThermalSource::NvmlThermalSource(std::string pci_bus_id)
    : pci_bus_id_(std::move(pci_bus_id))
{
    if (pci_bus_id_.empty()) {
        throw std::invalid_argument("NVML thermal source requires a PCI bus ID");
    }
}

std::optional<ThermalTelemetrySample> NvmlThermalSource::sample() noexcept
{
    const auto now = std::chrono::duration_cast<std::chrono::nanoseconds>(
                         std::chrono::steady_clock::now().time_since_epoch())
                         .count();
    if (now <= 0) {
        return std::nullopt;
    }
    const auto result = collect_live_nvml_thermal_sample(
        pci_bus_id_, static_cast<std::uint64_t>(now));
    if (!result.sample) {
        return std::nullopt;
    }
    return ThermalTelemetrySample{
        .host_ns = result.sample->host_ns,
        .gpu_millic = result.sample->gpu_millic,
        .gpu_power_mw = result.sample->gpu_power_mw,
    };
}

std::vector<ThermalTelemetrySample> load_thermal_trace(
    const std::filesystem::path& path, std::string_view expected_sha256)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("could not open thermal trace: " +
                                 path.string());
    }
    const std::string document(std::istreambuf_iterator<char>(input), {});
    const auto bytes = std::as_bytes(std::span(document));
    if (sha256_hex(bytes) != expected_sha256) {
        throw std::runtime_error("thermal trace SHA256 mismatch");
    }

    try {
        const auto parsed = nlohmann::json::parse(document);
        if (parsed.at("schema_version").get<std::uint32_t>() != 1) {
            throw std::runtime_error("thermal trace schema_version must be 1");
        }
        const auto& samples = parsed.at("samples");
        if (!samples.is_array() || samples.empty()) {
            throw std::runtime_error("thermal trace samples must be nonempty");
        }
        std::vector<ThermalTelemetrySample> result;
        result.reserve(samples.size());
        for (const auto& sample : samples) {
            result.push_back({
                .host_ns = sample.at("host_ns").get<std::uint64_t>(),
                .gpu_millic = sample.at("gpu_millic").get<std::int64_t>(),
                .gpu_power_mw =
                    sample.at("gpu_power_mw").get<std::uint64_t>(),
            });
        }
        TraceThermalSource validation(result);
        (void)validation;
        return result;
    } catch (const nlohmann::json::exception& error) {
        throw std::runtime_error("invalid thermal trace JSON: " +
                                 std::string(error.what()));
    }
}

ThermalTelemetryPublisher::ThermalTelemetryPublisher(
    host_service::ControlView control, ThermalTelemetrySource& source) noexcept
    : control_(control), source_(source)
{
}

ThermalTelemetryPublisher::~ThermalTelemetryPublisher()
{
    stop();
}

void ThermalTelemetryPublisher::publish(
    TelemetryStatus status, const ThermalTelemetrySample* sample) noexcept
{
    if (!control_.valid()) {
        terminal_failure_ = true;
        return;
    }
    auto* header = control_.header();
    const auto generation = host_service::atomic_load(
        header->telemetry_generation, std::memory_order_acquire);
    if ((generation & 1U) != 0 ||
        generation > std::numeric_limits<std::uint64_t>::max() - 2) {
        terminal_failure_ = true;
        return;
    }
    host_service::atomic_store(header->telemetry_generation, generation + 1,
                               std::memory_order_release);
    if (sample != nullptr) {
        host_service::atomic_store(header->telemetry_host_ns, sample->host_ns,
                                   std::memory_order_relaxed);
        host_service::atomic_store(header->telemetry_gpu_millic,
                                   sample->gpu_millic,
                                   std::memory_order_relaxed);
        host_service::atomic_store(header->telemetry_gpu_power_mw,
                                   sample->gpu_power_mw,
                                   std::memory_order_relaxed);
    }
    host_service::atomic_store(header->telemetry_status,
                               static_cast<std::uint32_t>(status),
                               std::memory_order_relaxed);
    host_service::atomic_store(header->telemetry_generation, generation + 2,
                               std::memory_order_release);
}

TelemetryStatus ThermalTelemetryPublisher::publish_once() noexcept
{
    if (terminal_failure_) {
        return TelemetryStatus::SourceFailed;
    }
    const auto sample = source_.sample();
    if (!sample || sample->host_ns == 0 || sample->gpu_millic < 0 ||
        sample->gpu_millic > 105'000) {
        publish(TelemetryStatus::SourceFailed, nullptr);
        terminal_failure_ = true;
        return TelemetryStatus::SourceFailed;
    }
    publish(TelemetryStatus::Ready, &*sample);
    return terminal_failure_ ? TelemetryStatus::SourceFailed
                             : TelemetryStatus::Ready;
}

bool ThermalTelemetryPublisher::start(
    std::chrono::milliseconds period) noexcept
{
    if (period.count() <= 0 || worker_.joinable() || terminal_failure_) {
        return false;
    }
    try {
        worker_ = std::jthread(
            [this, period](std::stop_token token) { run(token, period); });
        return true;
    } catch (...) {
        publish(TelemetryStatus::SourceFailed, nullptr);
        terminal_failure_ = true;
        return false;
    }
}

void ThermalTelemetryPublisher::run(std::stop_token stop_token,
                                    std::chrono::milliseconds period) noexcept
{
    while (!stop_token.stop_requested()) {
        if (publish_once() != TelemetryStatus::Ready) {
            return;
        }
        std::unique_lock lock(wait_mutex_);
        wait_condition_.wait_for(lock, stop_token, period,
                                 [] { return false; });
    }
}

void ThermalTelemetryPublisher::stop() noexcept
{
    if (!worker_.joinable()) {
        return;
    }
    worker_.request_stop();
    wait_condition_.notify_all();
    worker_.join();
    if (!terminal_failure_) {
        publish(TelemetryStatus::Stopped, nullptr);
    }
}

}  // namespace hbfsim::runtime

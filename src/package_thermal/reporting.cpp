#include <hbfsim/package_thermal.hpp>

#include <json.hpp>
#include <openssl/evp.h>

#include <chrono>
#include <array>
#include <algorithm>
#include <cctype>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <system_error>
#include <unistd.h>

namespace hbfsim::package_thermal {
namespace {

using Json = nlohmann::json;

std::string digest_bytes(std::span<const std::byte> bytes)
{
    auto* context = EVP_MD_CTX_new();
    if (context == nullptr) throw ThermalError("failed to allocate SHA-256 context");
    unsigned char digest[EVP_MAX_MD_SIZE]{};
    unsigned int size = 0;
    const bool ok = EVP_DigestInit_ex(context, EVP_sha256(), nullptr) == 1 &&
                    EVP_DigestUpdate(context, bytes.data(), bytes.size()) == 1 &&
                    EVP_DigestFinal_ex(context, digest, &size) == 1;
    EVP_MD_CTX_free(context);
    if (!ok || size != 32) throw ThermalError("failed to compute SHA-256");
    std::ostringstream result;
    result << std::hex << std::setfill('0');
    for (unsigned int index = 0; index < size; ++index) {
        result << std::setw(2) << static_cast<unsigned int>(digest[index]);
    }
    return result.str();
}

std::string sha256_text(const std::string& text)
{
    return digest_bytes(std::span<const std::byte>{
        reinterpret_cast<const std::byte*>(text.data()), text.size()});
}

Json load_json(const std::filesystem::path& path)
{
    std::ifstream stream(path);
    if (!stream) throw ThermalError("failed to open report input: " + path.string());
    Json value;
    try {
        stream >> value;
    } catch (const std::exception& error) {
        throw ThermalError("failed to parse report input: " +
                           std::string(error.what()));
    }
    return value;
}

std::string power_model_hash(const std::filesystem::path& profile_path)
{
    const auto profile = load_json(profile_path);
    Json power{{"gpu_provider", profile.at("gpu_provider")},
               {"nand_energy", profile.at("nand_energy")},
               {"base_die", profile.at("base_die")}};
    if (profile.contains("near_memory")) {
        power["package_architecture"] = profile.at("package_architecture");
        power["near_memory"] = profile.at("near_memory");
        power["accelerator_power_semantics"] =
            profile.at("accelerator_power_semantics");
        power["power_model_evidence_level"] =
            profile.at("power_model_evidence_level");
    } else {
        power["gpu_power_semantics"] = profile.at("gpu_power_semantics");
        power["hbm_provider"] = profile.at("hbm_provider");
    }
    return sha256_text(power.dump());
}

Json provenance_json(const Provenance& value)
{
    return Json{
        {"class", to_string(value.evidence)},
        {"source", value.source},
        {"locator", value.locator},
        {"note", value.note},
        {"dataset_sha256", value.dataset_sha256.empty()
                               ? Json{nullptr}
                               : Json{value.dataset_sha256}},
        {"calibration_sha256", value.calibration_sha256.empty()
                                   ? Json{nullptr}
                                   : Json{value.calibration_sha256}},
    };
}

Json sourced_json(const SourcedScalar& value)
{
    return Json{{"value", value.value}, {"unit", value.unit},
                {"provenance", provenance_json(value.provenance)}};
}

Json parameter_provenance(const PackageThermalProfile& profile)
{
    Json result{
        {"ambient_c", sourced_json(profile.ambient_c)},
        {"thermal_step_ns", sourced_json(profile.bin_width_ns)},
        {"gpu_provider", provenance_json(profile.gpu_provider.provenance)},
        {"topology", provenance_json(profile.topology.provenance())},
        {"nand_energy",
         {{"read_command", sourced_json(profile.nand_energy.read.command_j)},
          {"read_byte", sourced_json(profile.nand_energy.read.joules_per_byte)},
          {"program_command",
           sourced_json(profile.nand_energy.program.command_j)},
          {"program_byte",
           sourced_json(profile.nand_energy.program.joules_per_byte)},
          {"erase_command", sourced_json(profile.nand_energy.erase.command_j)},
          {"erase_byte",
           sourced_json(profile.nand_energy.erase.joules_per_byte)}}},
        {"base_die",
         {{"idle", sourced_json(profile.base_die.idle_w)},
          {"command", sourced_json(profile.base_die.command_j)},
          {"byte", sourced_json(profile.base_die.joules_per_byte)}}},
        {"policy",
         {{"light_on", sourced_json(profile.policy.light_on_c)},
          {"light_off", sourced_json(profile.policy.light_off_c)},
          {"severe_on", sourced_json(profile.policy.severe_on_c)},
          {"severe_off", sourced_json(profile.policy.severe_off_c)},
          {"shutdown_on", sourced_json(profile.policy.shutdown_on_c)},
          {"shutdown_off", sourced_json(profile.policy.shutdown_off_c)},
          {"light_scale", sourced_json(profile.policy.light_scale)},
          {"timing", provenance_json(profile.policy.timing_provenance)}}},
    };
    if (profile.legacy_power_schema) {
        result["power_schema"] = "legacy_compatibility";
        result["gpu_power_semantics"] = Json{
            {"value", profile.gpu_power_semantics ==
                          GpuPowerSemantics::BoardTotal
                          ? "board_total"
                          : "compute_only"},
            {"provenance",
             provenance_json(profile.gpu_power_semantics_provenance)}};
        result["hbm_provider"] =
            provenance_json(profile.hbm_provider.provenance);
    } else {
        Json sources = Json::array();
        for (const auto& source : profile.near_memory.power_sources) {
            sources.push_back(
                {{"thermal_node", source.thermal_node},
                 {"provider", provenance_json(source.provider.provenance)}});
        }
        result["power_schema"] = "phase2_explicit_semantics";
        result["package_architecture"] = Json{
            {"value", to_string(profile.package_architecture)},
            {"provenance", provenance_json(profile.near_memory.provenance)}};
        result["near_memory"] = Json{
            {"kind", to_string(profile.near_memory.kind)},
            {"placement", to_string(profile.near_memory.placement)},
            {"provenance", provenance_json(profile.near_memory.provenance)},
            {"power_sources", std::move(sources)}};
        result["accelerator_power_semantics"] = Json{
            {"value", to_string(profile.accelerator_power_semantics)},
            {"provenance", provenance_json(
                               profile.accelerator_power_semantics_provenance)}};
        result["power_model_evidence_level"] = Json{
            {"value", to_string(profile.power_model_evidence_level)},
            {"provenance", provenance_json(
                               profile.power_model_evidence_provenance)}};
    }
    return result;
}

std::string csv_quote(std::string_view value)
{
    if (value.find_first_of(",\"\r\n") == std::string_view::npos) {
        return std::string(value);
    }
    std::string result{"\""};
    for (const auto character : value) {
        if (character == '\"') result.push_back('\"');
        result.push_back(character);
    }
    result.push_back('\"');
    return result;
}

std::string sanitized_node(std::string_view node)
{
    std::string result;
    result.reserve(node.size());
    for (const auto character : node) {
        result.push_back(std::isalnum(static_cast<unsigned char>(character))
                             ? character
                             : '_');
    }
    return result;
}

std::string node_column(std::string_view prefix, std::string_view node)
{
    if (node == "gpu") return std::string(prefix) + "_gpu";
    if (node == "gddr") return std::string(prefix) + "_gddr";
    if (node.starts_with("gddr.s")) {
        return std::string(prefix) + "_gddr_" +
               std::string(node.substr(6));
    }
    if (node.starts_with("hbm.s")) {
        return std::string(prefix) + "_hbm_stack_" +
               std::string(node.substr(5));
    }
    if (node == "hbf.base") return std::string(prefix) + "_hbf_base";
    if (node.starts_with("hbf.s")) {
        const auto layer = node.find(".l", 5);
        if (layer != std::string_view::npos) {
            return std::string(prefix) + "_hbf_stack_" +
                   std::string(node.substr(5, layer - 5)) + "_layer_" +
                   std::string(node.substr(layer + 2));
        }
    }
    if (node.starts_with("hbf.l")) {
        return std::string(prefix) + "_hbf_layer_" +
               std::string(node.substr(5));
    }
    return std::string(prefix) + "_node_" + sanitized_node(node);
}

bool lumped_gddr_accelerator(const PackageThermalProfile& profile)
{
    return !profile.legacy_power_schema &&
           profile.near_memory.kind == NearMemoryKind::Gddr7 &&
           (profile.accelerator_power_semantics ==
                AcceleratorPowerSemantics::GpuPlusGddrLumped ||
            profile.accelerator_power_semantics ==
                AcceleratorPowerSemantics::BoardTotalLumped);
}

double counterfactual_scale(const PackageThermalProfile& profile,
                            ThermalMode mode)
{
    if (mode == ThermalMode::Light) return profile.policy.light_scale.value;
    if (mode == ThermalMode::Shutdown) return 0.0;
    return 1.0;
}

bool counterfactual_gate(ThermalMode mode)
{
    return mode != ThermalMode::Severe && mode != ThermalMode::Shutdown;
}

}  // namespace

std::string sha256_file(const std::filesystem::path& path)
{
    std::ifstream stream(path, std::ios::binary);
    if (!stream) throw ThermalError("failed to open SHA-256 input: " + path.string());
    std::vector<std::byte> bytes;
    std::array<char, 64 * 1024> buffer{};
    while (stream) {
        stream.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
        const auto count = stream.gcount();
        const auto* begin = reinterpret_cast<const std::byte*>(buffer.data());
        bytes.insert(bytes.end(), begin, begin + count);
    }
    if (!stream.eof()) throw ThermalError("failed to read SHA-256 input");
    return digest_bytes(bytes);
}

class PackageThermalTimelineWriter::Impl {
public:
    Impl(const std::filesystem::path& path,
         const PackageThermalProfile& profile)
        : profile_(profile), nodes_(profile.topology.node_names())
    {
        if (!profile_.timeline.enabled) return;
        std::filesystem::create_directories(path.parent_path());
        output_.open(path, std::ios::trunc);
        if (!output_) {
            throw ThermalError("failed to create package thermal timeline: " +
                               path.string());
        }
        std::vector<std::string> header{
            "thermal_time_ns", "host_sample_time_ns", "P_accelerator",
            "P_gddr", "P_hbf_total"};
        for (const auto& node : nodes_) {
            header.push_back(node_column("P", node));
        }
        for (const auto& node : nodes_) {
            header.push_back(node_column("T", node));
        }
        const std::vector<std::string> suffix{
            "T_hbf_hotspot", "hotspot_node", "raw_policy",
            "effective_policy", "debounce_counter", "dwell_counter",
            "gate_open", "service_scale", "would_have_policy",
            "would_have_gate", "would_have_scale",
            "hypothetical_block_time_ns", "MQSim_events_this_bin",
            "MQSim_read_bytes", "MQSim_program_bytes", "MQSim_erase_count",
            "submitted_requests", "admitted_requests", "completed_requests",
            "queue_depth", "blocked_episode_count", "gate_closed_ns",
            "admission_wait_ns", "thermal_blocked_requests",
            "requests_delayed", "admission_retry_count", "queue_depth_peak"};
        header.insert(header.end(), suffix.begin(), suffix.end());
        for (std::size_t index = 0; index < header.size(); ++index) {
            if (index != 0) output_ << ',';
            output_ << csv_quote(header[index]);
        }
        output_ << '\n';
    }

    void append(const ThermalObservation& observation,
                const ThermalServiceSnapshot& service)
    {
        const auto duration = observation.end_time_ns -
                              observation.start_time_ns;
        const bool gate_open = observation.policy.admission_open;
        if (!gate_open) {
            if (previous_gate_open_) ++metrics_.block_episode_count;
            metrics_.gate_closed_ns += duration;
        }
        previous_gate_open_ = gate_open;
        metrics_.thermal_blocked_requests = service.thermal_blocked_requests;
        metrics_.requests_delayed = service.thermal_blocked_requests;
        metrics_.queue_depth_peak =
            std::max(metrics_.queue_depth_peak, service.queue_depth);
        const auto would_gate =
            counterfactual_gate(observation.policy.raw_mode);
        const auto hypothetical =
            profile_.stage == ThermalStage::Shadow && !would_gate ? duration : 0;
        metrics_.hypothetical_block_time_ns += hypothetical;
        if (!profile_.timeline.enabled) return;
        if (observation.input_power_w.size() != nodes_.size() ||
            observation.temperatures_c.size() != nodes_.size()) {
            throw ThermalError("timeline observation dimension mismatch");
        }

        bool first = true;
        const auto separator = [&] {
            if (!first) output_ << ',';
            first = false;
        };
        const auto number = [&](const auto value) {
            separator();
            output_ << std::setprecision(17) << value;
        };
        const auto text = [&](std::string_view value) {
            separator();
            output_ << csv_quote(value);
        };
        const auto missing = [&] { separator(); };

        number(observation.end_time_ns);
        number(observation.host_sample_time_ns);
        number(observation.accelerator_power_w);
        // GDDR7 board power is deliberately left inseparable when the
        // accelerator provider is a lumped GPU+GDDR source.
        missing();
        double hbf_total = 0.0;
        for (std::size_t index = 0; index < nodes_.size(); ++index) {
            if (nodes_[index].starts_with("hbf.")) {
                hbf_total += observation.input_power_w[index];
            }
        }
        number(hbf_total);
        const auto hide_gpu = lumped_gddr_accelerator(profile_);
        for (std::size_t index = 0; index < nodes_.size(); ++index) {
            if (hide_gpu && nodes_[index] == "gpu") missing();
            else number(observation.input_power_w[index]);
        }
        for (const auto value : observation.temperatures_c) number(value);
        number(observation.hbf_hotspot_c);
        text(observation.hbf_hotspot_node);
        text(to_string(observation.policy.raw_mode));
        text(to_string(observation.policy.effective_mode));
        number(observation.policy.debounce_counter);
        number(observation.policy.dwell_counter);
        number(gate_open ? 1 : 0);
        number(observation.policy.service_scale);
        if (profile_.stage == ThermalStage::Shadow ||
            profile_.stage == ThermalStage::Active) {
            text(to_string(observation.policy.raw_mode));
            number(would_gate ? 1 : 0);
            number(counterfactual_scale(profile_, observation.policy.raw_mode));
            number(hypothetical);
        } else {
            missing();
            missing();
            missing();
            missing();
        }
        number(observation.media_event_count);
        number(observation.media_read_bytes);
        number(observation.media_program_bytes);
        number(observation.media_erase_count);
        number(service.submitted_requests);
        number(service.admitted_requests);
        number(service.completed_requests);
        number(service.queue_depth);
        number(metrics_.block_episode_count);
        number(metrics_.gate_closed_ns);
        missing();
        number(service.thermal_blocked_requests);
        number(metrics_.requests_delayed);
        missing();
        number(metrics_.queue_depth_peak);
        output_ << '\n';
        if (!output_) throw ThermalError("failed to write package thermal timeline");
    }

    void finish()
    {
        if (finished_) return;
        finished_ = true;
        if (output_) {
            output_.flush();
            if (!output_) {
                throw ThermalError("failed to flush package thermal timeline");
            }
        }
    }

    PackageThermalProfile profile_;
    std::vector<std::string> nodes_;
    std::ofstream output_;
    ThermalServiceMetrics metrics_{};
    bool previous_gate_open_{true};
    bool finished_{false};
};

PackageThermalTimelineWriter::PackageThermalTimelineWriter(
    const std::filesystem::path& path, const PackageThermalProfile& profile)
    : impl_(std::make_unique<Impl>(path, profile))
{
}

PackageThermalTimelineWriter::~PackageThermalTimelineWriter() = default;

void PackageThermalTimelineWriter::append(
    const ThermalObservation& observation,
    const ThermalServiceSnapshot& service)
{
    impl_->append(observation, service);
}

void PackageThermalTimelineWriter::finish() { impl_->finish(); }

const ThermalServiceMetrics& PackageThermalTimelineWriter::metrics() const noexcept
{
    return impl_->metrics_;
}

void write_package_thermal_report(
    const std::filesystem::path& path,
    const PackageThermalRuntime& runtime,
    const ThermalReportMetadata& metadata,
    const ThermalServiceMetrics& service_metrics)
{
    const auto serialization_start = std::chrono::steady_clock::now();
    const auto& profile = runtime.profile();
    const auto& stats = runtime.stats();
    Json temperatures = Json::object();
    Json hbf_dies = Json::object();
    Json hbf_base = nullptr;
    if (runtime.latest().has_value()) {
        const auto& latest = *runtime.latest();
        const auto& nodes = profile.topology.node_names();
        for (std::size_t index = 0; index < nodes.size(); ++index) {
            temperatures[nodes[index]] = latest.temperatures_c[index];
            if (nodes[index] == "hbf.base") hbf_base = latest.temperatures_c[index];
            if (nodes[index].starts_with("hbf.s")) {
                hbf_dies[nodes[index]] = latest.temperatures_c[index];
            }
        }
    }
    const auto backpressure_ns =
        profile.stage == ThermalStage::Active
            ? stats.time_severe_ns + stats.time_shutdown_ns
            : 0;
    Json report{
        {"schema_version", 1},
        {"thermal_enabled", true},
        {"thermal_mode", "package_rc"},
        {"thermal_stage", to_string(profile.stage)},
        {"package_profile_sha256", sha256_file(metadata.package_profile_path)},
        {"rom_sha256", metadata.model_kind == "rom"
                           ? Json{sha256_file(metadata.model_path)}
                           : Json{nullptr}},
        {"plugin_sha256", metadata.model_kind == "plugin"
                              ? Json{sha256_file(metadata.model_path)}
                              : Json{nullptr}},
        {"power_model_sha256", power_model_hash(metadata.package_profile_path)},
        {"thermal_clock", profile.clock_mode == ClockMode::ModelTimeReplay
                              ? "model_time_replay"
                              : "live_monotonic"},
        {"timeline_enabled", profile.timeline.enabled},
        {"ambient_c", profile.ambient_c.value},
        {"thermal_step_ns", profile.bin_width_ns.value},
        {"model_identity", runtime.model_identity()},
        {"evidence_label", profile.evidence_label},
        {"temperature_evidence", "model_based_projection"},
        {"parameter_provenance", parameter_provenance(profile)},
        {"max_hbf_temperature_c", stats.maximum_hbf_temperature_c},
        {"hotspot_node", stats.maximum_node},
        {"hotspot_time_ns", stats.maximum_time_ns},
        {"hbf_base_temperature_c", hbf_base},
        {"hbf_die_temperatures_c", std::move(hbf_dies)},
        {"latest_temperatures_c", std::move(temperatures)},
        {"time_normal_ns", stats.time_normal_ns},
        {"time_light_ns", stats.time_light_ns},
        {"time_severe_ns", stats.time_severe_ns},
        {"time_shutdown_ns", stats.time_shutdown_ns},
        {"light_transitions", stats.light_transitions},
        {"severe_transitions", stats.severe_transitions},
        {"shutdown_transitions", stats.shutdown_transitions},
        {"thermal_backpressure_ns", backpressure_ns},
        {"thermal_blocked_requests",
         service_metrics.thermal_blocked_requests},
        {"block_episode_count", service_metrics.block_episode_count},
        {"gate_closed_ns", service_metrics.gate_closed_ns},
        {"total_admission_wait_ns",
         service_metrics.total_admission_wait_ns.has_value()
             ? Json{*service_metrics.total_admission_wait_ns}
             : Json{nullptr}},
        {"requests_delayed", service_metrics.requests_delayed},
        {"admission_retry_count",
         service_metrics.admission_retry_count.has_value()
             ? Json{*service_metrics.admission_retry_count}
             : Json{nullptr}},
        {"queue_depth_peak", service_metrics.queue_depth_peak},
        {"hypothetical_block_time_ns",
         service_metrics.hypothetical_block_time_ns},
        {"service_metric_availability",
         {{"thermal_blocked_requests", "exact_shared_counter"},
          {"block_episode_count", "exact_host_policy_transition"},
          {"gate_closed_ns", "exact_thermal_bin_duration"},
          {"total_admission_wait_ns", "unavailable_without_request_timestamps"},
          {"requests_delayed", "exact_distinct_blocked_request_counter"},
          {"admission_retry_count", "unavailable_without_client_retry_counter"},
          {"queue_depth_peak", "host_sampled_shared_ring_depth"}}},
        {"thermal_steps", stats.thermal_steps},
        {"rom_validation_id", runtime.model_identity()},
        {"contribution_decomposition",
         {{"status", "offline_linear_decomposition_required"},
          {"gpu_to_hbf_delta_c", nullptr},
          {"hbm_to_hbf_delta_c", nullptr},
          {"hbf_self_delta_c", nullptr}}},
        {"overhead_ns",
         {{"media_observer", stats.media_observer_cost_ns},
          {"power_aggregation", stats.power_aggregation_cost_ns},
          {"telemetry_sampling", stats.telemetry_sampling_cost_ns},
          {"model_advance", stats.model_advance_cost_ns},
          {"policy", stats.policy_cost_ns}}},
    };
    report["overhead_ns"]["report_serialization"] =
        static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - serialization_start)
                .count());

    std::filesystem::create_directories(path.parent_path());
    const auto temporary = path.string() + ".tmp." +
                           std::to_string(static_cast<long long>(::getpid()));
    {
        std::ofstream output(temporary, std::ios::trunc);
        if (!output) throw ThermalError("failed to create thermal report");
        output << report.dump(2) << '\n';
        output.flush();
        if (!output) throw ThermalError("failed to write thermal report");
    }
    std::error_code error;
    std::filesystem::rename(temporary, path, error);
    if (error) {
        std::filesystem::remove(temporary);
        throw ThermalError("failed to publish thermal report: " + error.message());
    }
}

}  // namespace hbfsim::package_thermal

#include <hbfsim/package_thermal.hpp>

#include <json.hpp>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <set>
#include <unordered_set>

namespace hbfsim::package_thermal {
namespace {

using Json = nlohmann::json;

void require_finite(double value, const std::string& field)
{
    if (!std::isfinite(value)) {
        throw ThermalError(field + " must be finite");
    }
}

void require_nonempty(const std::string& value, const std::string& field)
{
    if (value.empty()) {
        throw ThermalError(field + " must not be empty");
    }
}

void require_keys(const Json& value,
                  std::initializer_list<const char*> allowed,
                  const std::string& context)
{
    if (!value.is_object()) {
        throw ThermalError(context + " must be an object");
    }
    std::set<std::string> names;
    for (const auto* name : allowed) {
        names.emplace(name);
    }
    for (const auto& [name, ignored] : value.items()) {
        (void)ignored;
        if (!names.contains(name)) {
            throw ThermalError(context + " contains unknown field " + name);
        }
    }
}

const Json& require_field(const Json& value,
                          const char* field,
                          const std::string& context)
{
    if (!value.contains(field)) {
        throw ThermalError(context + " is missing " + field);
    }
    return value.at(field);
}

EvidenceClass evidence_from_string(const std::string& value)
{
    if (value == "S") return EvidenceClass::Specification;
    if (value == "L") return EvidenceClass::Literature;
    if (value == "C") return EvidenceClass::CalibrationOrSensitivity;
    if (value == "M") return EvidenceClass::Measurement;
    throw ThermalError("provenance.class must be S, L, C, or M");
}

Provenance parse_provenance(const Json& value, const std::string& context)
{
    require_keys(value,
                 {"class", "source", "locator", "note", "dataset_sha256",
                  "calibration_sha256"},
                 context);
    Provenance result{
        .evidence = evidence_from_string(
            require_field(value, "class", context).get<std::string>()),
        .source = require_field(value, "source", context).get<std::string>(),
        .locator = require_field(value, "locator", context).get<std::string>(),
        .note = value.value("note", std::string{}),
        .dataset_sha256 = value.value("dataset_sha256", std::string{}),
        .calibration_sha256 =
            value.value("calibration_sha256", std::string{}),
    };
    require_nonempty(result.source, context + ".source");
    require_nonempty(result.locator, context + ".locator");
    const auto has_dataset = !result.dataset_sha256.empty();
    const auto has_calibration = !result.calibration_sha256.empty();
    if (result.evidence == EvidenceClass::CalibrationOrSensitivity &&
        ((has_dataset != has_calibration) ||
         (has_dataset && (result.dataset_sha256.size() != 64 ||
                          result.calibration_sha256.size() != 64)))) {
        throw ThermalError(
            context + " calibrated provenance requires both SHA-256 hashes; "
                      "an unhashed C value is sensitivity-only");
    }
    if (result.evidence == EvidenceClass::Measurement &&
        result.dataset_sha256.size() != 64) {
        throw ThermalError(
            context + " measured provenance requires a dataset SHA-256 hash");
    }
    return result;
}

SourcedScalar parse_scalar(const Json& value, const std::string& context)
{
    require_keys(value, {"value", "unit", "provenance"}, context);
    SourcedScalar result{
        .value = require_field(value, "value", context).get<double>(),
        .unit = require_field(value, "unit", context).get<std::string>(),
        .provenance = parse_provenance(
            require_field(value, "provenance", context),
            context + ".provenance"),
    };
    require_finite(result.value, context + ".value");
    require_nonempty(result.unit, context + ".unit");
    return result;
}

ThermalStage stage_from_string(const std::string& value)
{
    if (value == "off") return ThermalStage::Off;
    if (value == "read_only") return ThermalStage::ReadOnly;
    if (value == "shadow") return ThermalStage::Shadow;
    if (value == "active") return ThermalStage::Active;
    throw ThermalError("stage must be off, read_only, shadow, or active");
}

ClockMode clock_from_string(const std::string& value)
{
    if (value == "model_time_replay") return ClockMode::ModelTimeReplay;
    if (value == "live_monotonic") return ClockMode::LiveMonotonic;
    throw ThermalError(
        "clock_mode must be model_time_replay or live_monotonic");
}

GpuPowerSemantics semantics_from_string(const std::string& value)
{
    if (value == "board_total") return GpuPowerSemantics::BoardTotal;
    if (value == "compute_only") return GpuPowerSemantics::ComputeOnly;
    throw ThermalError(
        "gpu_power_semantics must be board_total or compute_only");
}

NearMemoryKind near_memory_kind_from_string(const std::string& value)
{
    if (value == "none") return NearMemoryKind::None;
    if (value == "gddr7") return NearMemoryKind::Gddr7;
    if (value == "hbm2") return NearMemoryKind::Hbm2;
    if (value == "hbm2e") return NearMemoryKind::Hbm2e;
    if (value == "hbm3") return NearMemoryKind::Hbm3;
    if (value == "hbm3e") return NearMemoryKind::Hbm3e;
    if (value == "hbm4") return NearMemoryKind::Hbm4;
    if (value == "synthetic") return NearMemoryKind::Synthetic;
    throw ThermalError("near_memory.kind is not recognized");
}

NearMemoryPlacement near_memory_placement_from_string(
    const std::string& value)
{
    if (value == "board") return NearMemoryPlacement::Board;
    if (value == "interposer") return NearMemoryPlacement::Interposer;
    if (value == "stacked") return NearMemoryPlacement::Stacked;
    if (value == "external") return NearMemoryPlacement::External;
    throw ThermalError("near_memory.placement is not recognized");
}

PackageArchitecture package_architecture_from_string(
    const std::string& value)
{
    if (value == "gddr_gpu_hbf") return PackageArchitecture::GddrGpuHbf;
    if (value == "hbm_gpu_hbf") return PackageArchitecture::HbmGpuHbf;
    if (value == "synthetic") return PackageArchitecture::Synthetic;
    throw ThermalError("package_architecture is not recognized");
}

AcceleratorPowerSemantics accelerator_semantics_from_string(
    const std::string& value)
{
    if (value == "board_total_lumped") {
        return AcceleratorPowerSemantics::BoardTotalLumped;
    }
    if (value == "gpu_compute_only") {
        return AcceleratorPowerSemantics::GpuComputeOnly;
    }
    if (value == "gpu_plus_gddr_lumped") {
        return AcceleratorPowerSemantics::GpuPlusGddrLumped;
    }
    if (value == "gpu_compute_plus_explicit_hbm") {
        return AcceleratorPowerSemantics::GpuComputePlusExplicitHbm;
    }
    throw ThermalError("accelerator_power_semantics is not recognized");
}

PowerModelEvidenceLevel power_evidence_from_string(const std::string& value)
{
    if (value == "sensitivity_only") {
        return PowerModelEvidenceLevel::SensitivityOnly;
    }
    if (value == "literature_bounded") {
        return PowerModelEvidenceLevel::LiteratureBounded;
    }
    if (value == "calibrated") return PowerModelEvidenceLevel::Calibrated;
    if (value == "measured") return PowerModelEvidenceLevel::Measured;
    throw ThermalError("power_model_evidence_level is not recognized");
}

bool is_hbm(NearMemoryKind kind)
{
    return kind == NearMemoryKind::Hbm2 ||
           kind == NearMemoryKind::Hbm2e ||
           kind == NearMemoryKind::Hbm3 ||
           kind == NearMemoryKind::Hbm3e ||
           kind == NearMemoryKind::Hbm4;
}

PowerProviderKind provider_kind_from_string(const std::string& value)
{
    if (value == "synthetic") return PowerProviderKind::Synthetic;
    if (value == "trace") return PowerProviderKind::Trace;
    if (value == "nvml") return PowerProviderKind::Nvml;
    throw ThermalError("power provider must be synthetic, trace, or nvml");
}

Interpolation interpolation_from_string(const std::string& value)
{
    if (value == "hold") return Interpolation::Hold;
    if (value == "linear") return Interpolation::Linear;
    throw ThermalError("interpolation must be hold or linear");
}

PowerProviderConfig parse_provider(const Json& value,
                                   const std::string& context)
{
    require_keys(value,
                 {"kind", "provenance", "interpolation", "samples",
                  "trace_path", "nvml_library", "device_index"},
                 context);
    PowerProviderConfig result{
        .kind = provider_kind_from_string(
            require_field(value, "kind", context).get<std::string>()),
        .provenance = parse_provenance(
            require_field(value, "provenance", context),
            context + ".provenance"),
        .interpolation = interpolation_from_string(
            value.value("interpolation", std::string{"hold"})),
        .trace_path = value.value("trace_path", std::string{}),
        .nvml_library = value.value("nvml_library", std::string{}),
        .device_index = value.value("device_index", std::uint32_t{0}),
    };
    if (value.contains("samples")) {
        if (!value.at("samples").is_array()) {
            throw ThermalError(context + ".samples must be an array");
        }
        for (const auto& item : value.at("samples")) {
            require_keys(item, {"relative_time_ns", "watts"},
                         context + ".samples[]");
            PowerSample sample{
                .relative_time_ns = require_field(
                    item, "relative_time_ns", context + ".samples[]")
                                        .get<std::uint64_t>(),
                .watts = require_field(item, "watts", context + ".samples[]")
                             .get<double>(),
            };
            require_finite(sample.watts, context + ".samples[].watts");
            result.samples.push_back(sample);
        }
    }
    return result;
}

NearMemoryConfig parse_near_memory(const Json& value,
                                   const std::string& context)
{
    require_keys(value, {"kind", "placement", "provenance", "power_sources"},
                 context);
    NearMemoryConfig result{
        .kind = near_memory_kind_from_string(
            require_field(value, "kind", context).get<std::string>()),
        .placement = near_memory_placement_from_string(
            require_field(value, "placement", context).get<std::string>()),
        .provenance = parse_provenance(
            require_field(value, "provenance", context),
            context + ".provenance"),
        .power_sources = {},
    };
    const auto& sources = require_field(value, "power_sources", context);
    if (!sources.is_array()) {
        throw ThermalError(context + ".power_sources must be an array");
    }
    for (const auto& source : sources) {
        require_keys(source, {"thermal_node", "provider"},
                     context + ".power_sources[]");
        result.power_sources.push_back(NearMemoryPowerSource{
            .thermal_node = require_field(
                source, "thermal_node", context + ".power_sources[]")
                                .get<std::string>(),
            .provider = parse_provider(
                require_field(source, "provider",
                              context + ".power_sources[]"),
                context + ".power_sources[].provider"),
        });
    }
    return result;
}

OperationEnergy parse_operation_energy(const Json& value,
                                       const std::string& context)
{
    require_keys(value, {"command_j", "joules_per_byte"}, context);
    return OperationEnergy{
        .command_j = parse_scalar(require_field(value, "command_j", context),
                                  context + ".command_j"),
        .joules_per_byte = parse_scalar(
            require_field(value, "joules_per_byte", context),
            context + ".joules_per_byte"),
    };
}

std::uint64_t checked_mapping_count(const PhysicalGeometry& geometry)
{
    const auto count = static_cast<unsigned __int128>(geometry.channels) *
                       geometry.chips_per_channel * geometry.dies_per_chip;
    if (count > std::numeric_limits<std::uint64_t>::max()) {
        throw ThermalError("topology mapping count overflows");
    }
    return static_cast<std::uint64_t>(count);
}

std::uint64_t die_key(std::uint32_t channel,
                      std::uint32_t chip,
                      std::uint32_t die)
{
    if (channel > 0xffff || chip > 0xffff || die > 0xffff) {
        throw ThermalError("topology coordinate exceeds 16-bit key range");
    }
    return (static_cast<std::uint64_t>(channel) << 32) |
           (static_cast<std::uint64_t>(chip) << 16) | die;
}

std::uint64_t plane_key(const PhysicalCoordinate& coordinate)
{
    if (coordinate.channel > 0xffff || coordinate.chip > 0xffff ||
        coordinate.die > 0xffff || coordinate.plane > 0xffff) {
        throw ThermalError("topology coordinate exceeds 16-bit key range");
    }
    return (static_cast<std::uint64_t>(coordinate.channel) << 48) |
           (static_cast<std::uint64_t>(coordinate.chip) << 32) |
           (static_cast<std::uint64_t>(coordinate.die) << 16) |
           coordinate.plane;
}

void validate_nonnegative(const SourcedScalar& value,
                          const std::string& field)
{
    require_finite(value.value, field);
    if (value.value < 0.0) {
        throw ThermalError(field + " must be non-negative");
    }
    require_nonempty(value.unit, field + ".unit");
    require_nonempty(value.provenance.source, field + ".provenance.source");
    require_nonempty(value.provenance.locator,
                     field + ".provenance.locator");
}

void validate_provider(const PowerProviderConfig& provider,
                       const std::string& field)
{
    require_nonempty(provider.provenance.source,
                     field + ".provenance.source");
    if (provider.kind == PowerProviderKind::Synthetic) {
        if (provider.samples.empty()) {
            throw ThermalError(field + " synthetic provider needs samples");
        }
    } else if (provider.kind == PowerProviderKind::Trace) {
        if (provider.trace_path.empty()) {
            throw ThermalError(field + " trace provider needs trace_path");
        }
    }
    std::uint64_t previous = 0;
    bool first = true;
    for (const auto& sample : provider.samples) {
        require_finite(sample.watts, field + ".samples.watts");
        if (sample.watts < 0.0) {
            throw ThermalError(field + " sample power must be non-negative");
        }
        if (!first && sample.relative_time_ns <= previous) {
            throw ThermalError(
                field + " sample timestamps must be strictly increasing");
        }
        first = false;
        previous = sample.relative_time_ns;
    }
}

}  // namespace

PackageTopology::PackageTopology(PhysicalGeometry geometry,
                                 std::uint32_t stack_height,
                                 std::vector<std::string> node_names,
                                 std::vector<DieMapping> die_mappings,
                                 Provenance provenance)
    : geometry_(geometry),
      stack_height_(stack_height),
      node_names_(std::move(node_names)),
      mappings_(std::move(die_mappings)),
      provenance_(std::move(provenance))
{
    if (geometry_.channels == 0 || geometry_.chips_per_channel == 0 ||
        geometry_.dies_per_chip == 0 || geometry_.planes_per_die == 0) {
        throw ThermalError("topology physical geometry must be non-zero");
    }
    if ((stack_height_ != 8 && stack_height_ != 16) ||
        geometry_.dies_per_chip != stack_height_) {
        throw ThermalError(
            "topology requires exactly 8Hi or 16Hi and one mapped layer per die");
    }
    require_nonempty(provenance_.source, "topology.provenance.source");
    if (mappings_.size() != checked_mapping_count(geometry_)) {
        throw ThermalError("topology must map every physical die exactly once");
    }

    std::unordered_map<std::string, std::size_t> nodes;
    for (std::size_t index = 0; index < node_names_.size(); ++index) {
        require_nonempty(node_names_[index], "topology.node_names[]");
        if (!nodes.emplace(node_names_[index], index).second) {
            throw ThermalError("topology node names must be unique");
        }
    }

    std::unordered_set<std::uint64_t> physical_dies;
    std::map<std::pair<std::uint32_t, std::uint32_t>, std::size_t>
        stack_layers;
    std::unordered_map<std::string,
                       std::pair<std::uint32_t, std::uint32_t>> nand_nodes;
    std::set<std::uint32_t> stacks;
    for (const auto& mapping : mappings_) {
        if (mapping.channel >= geometry_.channels ||
            mapping.chip >= geometry_.chips_per_channel ||
            mapping.die >= geometry_.dies_per_chip) {
            throw ThermalError("topology mapping has out-of-range physical die");
        }
        if (mapping.vertical_layer >= stack_height_) {
            throw ThermalError("topology mapping has out-of-range vertical layer");
        }
        if (!physical_dies
                 .emplace(die_key(mapping.channel, mapping.chip, mapping.die))
                 .second) {
            throw ThermalError("topology contains a duplicate physical die");
        }
        const auto node = nodes.find(mapping.thermal_node);
        if (node == nodes.end()) {
            throw ThermalError("topology mapping names an unknown thermal node");
        }
        const auto package_location =
            std::pair{mapping.package_stack, mapping.vertical_layer};
        const auto [layer, new_layer] =
            stack_layers.emplace(package_location, node->second);
        if (!new_layer && layer->second != node->second) {
            throw ThermalError(
                "topology maps one package stack/layer to conflicting nodes");
        }
        const auto [nand, new_nand] =
            nand_nodes.emplace(mapping.thermal_node, package_location);
        if (!new_nand && nand->second != package_location) {
            throw ThermalError(
                "topology maps one NAND thermal node to conflicting locations");
        }
        stacks.emplace(mapping.package_stack);
        for (std::uint32_t plane = 0; plane < geometry_.planes_per_die;
             ++plane) {
            const PhysicalCoordinate coordinate{
                mapping.channel, mapping.chip, mapping.die, plane};
            if (!lookup_
                     .emplace(plane_key(coordinate),
                              ThermalLocation{mapping.package_stack,
                                              mapping.vertical_layer,
                                              node->second})
                     .second) {
                throw ThermalError("topology contains a duplicate physical tuple");
            }
        }
    }

    package_stack_count_ = static_cast<std::uint32_t>(stacks.size());
    for (std::uint32_t stack = 0; stack < package_stack_count_; ++stack) {
        if (!stacks.contains(stack)) {
            throw ThermalError("topology package stacks must be contiguous");
        }
        for (std::uint32_t layer = 0; layer < stack_height_; ++layer) {
            const auto found = stack_layers.find({stack, layer});
            if (found == stack_layers.end()) {
                throw ThermalError(
                    "topology package stack is missing a vertical layer");
            }
        }
    }
}

const PhysicalGeometry& PackageTopology::geometry() const noexcept
{
    return geometry_;
}

std::uint32_t PackageTopology::stack_height() const noexcept
{
    return stack_height_;
}

std::uint32_t PackageTopology::package_stack_count() const noexcept
{
    return package_stack_count_;
}

const std::vector<std::string>& PackageTopology::node_names() const noexcept
{
    return node_names_;
}

const Provenance& PackageTopology::provenance() const noexcept
{
    return provenance_;
}

ThermalLocation PackageTopology::locate(
    const PhysicalCoordinate& coordinate) const
{
    if (coordinate.channel >= geometry_.channels ||
        coordinate.chip >= geometry_.chips_per_channel ||
        coordinate.die >= geometry_.dies_per_chip ||
        coordinate.plane >= geometry_.planes_per_die) {
        throw ThermalError("physical media coordinate is out of range");
    }
    const auto found = lookup_.find(plane_key(coordinate));
    if (found == lookup_.end()) {
        throw ThermalError("physical media coordinate is unmapped");
    }
    return found->second;
}

PackageThermalProfile load_package_thermal_profile(
    const std::filesystem::path& path)
{
    std::ifstream stream(path);
    if (!stream) {
        throw ThermalError("failed to open package thermal profile: " +
                           path.string());
    }
    Json document;
    try {
        stream >> document;
    } catch (const std::exception& error) {
        throw ThermalError("failed to parse package thermal profile: " +
                           std::string(error.what()));
    }
    require_keys(document,
                 {"schema_version", "name", "stage", "clock_mode",
                  "ambient_c", "bin_width_ns", "gpu_power_semantics", "gpu_provider",
                  "hbm_provider", "topology", "nand_energy", "base_die",
                  "policy", "evidence_label", "near_memory",
                  "accelerator_power_semantics", "package_architecture",
                  "power_model_evidence_level", "timeline"},
                 "profile");
    const bool phase2_semantics =
        document.contains("near_memory") ||
        document.contains("accelerator_power_semantics") ||
        document.contains("package_architecture") ||
        document.contains("power_model_evidence_level");
    if (phase2_semantics) {
        for (const auto* field : {"near_memory", "accelerator_power_semantics",
                                  "package_architecture",
                                  "power_model_evidence_level"}) {
            (void)require_field(document, field, "profile");
        }
    } else {
        (void)require_field(document, "gpu_power_semantics", "profile");
        (void)require_field(document, "hbm_provider", "profile");
    }

    const Provenance legacy_provenance{
        .evidence = EvidenceClass::CalibrationOrSensitivity,
        .source = "legacy package thermal schema compatibility",
        .locator = "implicit near-memory semantics",
        .note = "Legacy hbm naming is preserved without asserting a physical HBM technology.",
    };
    auto legacy_gpu_semantics = GpuPowerSemantics::ComputeOnly;
    auto legacy_gpu_semantics_provenance = legacy_provenance;
    PowerProviderConfig legacy_hbm_provider{
        .kind = PowerProviderKind::Synthetic,
        .provenance = legacy_provenance,
        .interpolation = Interpolation::Hold,
        .samples = {{0, 0.0}},
    };
    if (document.contains("gpu_power_semantics")) {
        const auto& semantics = document.at("gpu_power_semantics");
        require_keys(semantics, {"value", "provenance"},
                     "profile.gpu_power_semantics");
        legacy_gpu_semantics = semantics_from_string(
            require_field(semantics, "value", "profile.gpu_power_semantics")
                .get<std::string>());
        legacy_gpu_semantics_provenance = parse_provenance(
            require_field(semantics, "provenance",
                          "profile.gpu_power_semantics"),
            "profile.gpu_power_semantics.provenance");
    }
    if (document.contains("hbm_provider")) {
        legacy_hbm_provider = parse_provider(
            document.at("hbm_provider"), "profile.hbm_provider");
    }

    auto package_architecture = PackageArchitecture::Synthetic;
    NearMemoryConfig near_memory{
        .kind = NearMemoryKind::Synthetic,
        .placement = NearMemoryPlacement::External,
        .provenance = legacy_provenance,
        .power_sources = {},
    };
    auto accelerator_semantics =
        legacy_gpu_semantics == GpuPowerSemantics::BoardTotal
            ? AcceleratorPowerSemantics::BoardTotalLumped
            : AcceleratorPowerSemantics::GpuComputePlusExplicitHbm;
    auto accelerator_semantics_provenance =
        legacy_gpu_semantics_provenance;
    auto power_evidence = PowerModelEvidenceLevel::SensitivityOnly;
    auto power_evidence_provenance = legacy_provenance;
    TimelineConfig timeline{};
    if (document.contains("timeline")) {
        const auto& value = document.at("timeline");
        require_keys(value, {"enabled"}, "profile.timeline");
        timeline.enabled = require_field(value, "enabled", "profile.timeline")
                               .get<bool>();
    }
    if (phase2_semantics) {
        package_architecture = package_architecture_from_string(
            document.at("package_architecture").get<std::string>());
        near_memory = parse_near_memory(document.at("near_memory"),
                                        "profile.near_memory");
        const auto& accelerator = document.at("accelerator_power_semantics");
        require_keys(accelerator, {"value", "provenance"},
                     "profile.accelerator_power_semantics");
        accelerator_semantics = accelerator_semantics_from_string(
            require_field(accelerator, "value",
                          "profile.accelerator_power_semantics")
                .get<std::string>());
        accelerator_semantics_provenance = parse_provenance(
            require_field(accelerator, "provenance",
                          "profile.accelerator_power_semantics"),
            "profile.accelerator_power_semantics.provenance");
        const auto& evidence = document.at("power_model_evidence_level");
        require_keys(evidence, {"value", "provenance"},
                     "profile.power_model_evidence_level");
        power_evidence = power_evidence_from_string(
            require_field(evidence, "value",
                          "profile.power_model_evidence_level")
                .get<std::string>());
        power_evidence_provenance = parse_provenance(
            require_field(evidence, "provenance",
                          "profile.power_model_evidence_level"),
            "profile.power_model_evidence_level.provenance");
    }

    const auto& topology_json = require_field(document, "topology", "profile");
    require_keys(topology_json,
                 {"physical", "stack_height", "node_names", "die_mappings",
                  "provenance"},
                 "profile.topology");
    const auto& physical_json = require_field(
        topology_json, "physical", "profile.topology");
    require_keys(physical_json,
                 {"channels", "chips_per_channel", "dies_per_chip",
                  "planes_per_die"},
                 "profile.topology.physical");
    const PhysicalGeometry geometry{
        .channels = require_field(physical_json, "channels",
                                  "profile.topology.physical")
                        .get<std::uint32_t>(),
        .chips_per_channel = require_field(
            physical_json, "chips_per_channel", "profile.topology.physical")
                                 .get<std::uint32_t>(),
        .dies_per_chip = require_field(
            physical_json, "dies_per_chip", "profile.topology.physical")
                             .get<std::uint32_t>(),
        .planes_per_die = require_field(
            physical_json, "planes_per_die", "profile.topology.physical")
                              .get<std::uint32_t>(),
    };
    auto node_names = require_field(topology_json, "node_names",
                                    "profile.topology")
                          .get<std::vector<std::string>>();
    std::vector<DieMapping> mappings;
    for (const auto& item : require_field(
             topology_json, "die_mappings", "profile.topology")) {
        require_keys(item,
                     {"channel", "chip", "die", "package_stack",
                      "vertical_layer", "thermal_node"},
                     "profile.topology.die_mappings[]");
        mappings.push_back(DieMapping{
            .channel = item.at("channel").get<std::uint32_t>(),
            .chip = item.at("chip").get<std::uint32_t>(),
            .die = item.at("die").get<std::uint32_t>(),
            .package_stack = item.at("package_stack").get<std::uint32_t>(),
            .vertical_layer = item.at("vertical_layer").get<std::uint32_t>(),
            .thermal_node = item.at("thermal_node").get<std::string>(),
        });
    }

    const auto& nand = require_field(document, "nand_energy", "profile");
    require_keys(nand, {"read", "program", "erase"},
                 "profile.nand_energy");
    const auto& base = require_field(document, "base_die", "profile");
    require_keys(base,
                 {"idle_w", "command_j", "joules_per_byte", "thermal_node"},
                 "profile.base_die");
    const auto& policy = require_field(document, "policy", "profile");
    require_keys(policy,
                 {"light_on_c", "light_off_c", "severe_on_c",
                  "severe_off_c", "shutdown_on_c", "shutdown_off_c",
                  "light_scale", "debounce_samples", "minimum_dwell_samples",
                  "timing_provenance"},
                 "profile.policy");

    PackageThermalProfile result{
        .schema_version = document.at("schema_version").get<std::uint32_t>(),
        .name = document.at("name").get<std::string>(),
        .stage = stage_from_string(document.at("stage").get<std::string>()),
        .clock_mode =
            clock_from_string(document.at("clock_mode").get<std::string>()),
        .ambient_c =
            parse_scalar(document.at("ambient_c"), "profile.ambient_c"),
        .bin_width_ns =
            parse_scalar(document.at("bin_width_ns"), "profile.bin_width_ns"),
        .gpu_power_semantics = legacy_gpu_semantics,
        .gpu_power_semantics_provenance =
            std::move(legacy_gpu_semantics_provenance),
        .gpu_provider = parse_provider(document.at("gpu_provider"),
                                       "profile.gpu_provider"),
        .hbm_provider = std::move(legacy_hbm_provider),
        .legacy_power_schema = !phase2_semantics,
        .package_architecture = package_architecture,
        .near_memory = std::move(near_memory),
        .accelerator_power_semantics = accelerator_semantics,
        .accelerator_power_semantics_provenance =
            std::move(accelerator_semantics_provenance),
        .power_model_evidence_level = power_evidence,
        .power_model_evidence_provenance =
            std::move(power_evidence_provenance),
        .topology = PackageTopology{
            geometry,
            topology_json.at("stack_height").get<std::uint32_t>(),
            std::move(node_names), std::move(mappings),
            parse_provenance(topology_json.at("provenance"),
                             "profile.topology.provenance")},
        .nand_energy = NandEnergyModel{
            .read = parse_operation_energy(nand.at("read"),
                                           "profile.nand_energy.read"),
            .program = parse_operation_energy(nand.at("program"),
                                              "profile.nand_energy.program"),
            .erase = parse_operation_energy(nand.at("erase"),
                                            "profile.nand_energy.erase"),
        },
        .base_die = BaseDiePowerModel{
            .idle_w = parse_scalar(base.at("idle_w"),
                                   "profile.base_die.idle_w"),
            .command_j = parse_scalar(base.at("command_j"),
                                      "profile.base_die.command_j"),
            .joules_per_byte = parse_scalar(
                base.at("joules_per_byte"),
                "profile.base_die.joules_per_byte"),
            .thermal_node = base.at("thermal_node").get<std::string>(),
        },
        .policy = PolicyConfig{
            .light_on_c = parse_scalar(policy.at("light_on_c"),
                                       "profile.policy.light_on_c"),
            .light_off_c = parse_scalar(policy.at("light_off_c"),
                                        "profile.policy.light_off_c"),
            .severe_on_c = parse_scalar(policy.at("severe_on_c"),
                                        "profile.policy.severe_on_c"),
            .severe_off_c = parse_scalar(policy.at("severe_off_c"),
                                         "profile.policy.severe_off_c"),
            .shutdown_on_c = parse_scalar(policy.at("shutdown_on_c"),
                                          "profile.policy.shutdown_on_c"),
            .shutdown_off_c = parse_scalar(policy.at("shutdown_off_c"),
                                           "profile.policy.shutdown_off_c"),
            .light_scale = parse_scalar(policy.at("light_scale"),
                                        "profile.policy.light_scale"),
            .debounce_samples =
                policy.at("debounce_samples").get<std::uint32_t>(),
            .minimum_dwell_samples =
                policy.at("minimum_dwell_samples").get<std::uint32_t>(),
            .timing_provenance = parse_provenance(
                policy.at("timing_provenance"),
                "profile.policy.timing_provenance"),
        },
        .timeline = timeline,
        .evidence_label = document.at("evidence_label").get<std::string>(),
    };
    validate_package_thermal_profile(result);
    return result;
}

void validate_package_thermal_profile(const PackageThermalProfile& profile)
{
    if (profile.schema_version != kProfileSchemaVersion) {
        throw ThermalError("unsupported package thermal profile schema");
    }
    require_nonempty(profile.name, "profile.name");
    require_nonempty(profile.evidence_label, "profile.evidence_label");
    const std::set<std::string> labels{
        "synthetic_fixture", "literature_parameterized",
        "calibrated_external_solver", "measured"};
    if (!labels.contains(profile.evidence_label)) {
        throw ThermalError("profile.evidence_label is not recognized");
    }
    validate_nonnegative(profile.ambient_c, "profile.ambient_c");
    if (profile.ambient_c.value > 200.0) {
        throw ThermalError("profile.ambient_c exceeds sanity bounds");
    }
    validate_nonnegative(profile.bin_width_ns, "profile.bin_width_ns");
    if (profile.bin_width_ns.value < 1.0 ||
        std::floor(profile.bin_width_ns.value) != profile.bin_width_ns.value ||
        profile.bin_width_ns.value >
            static_cast<double>(std::numeric_limits<std::uint64_t>::max())) {
        throw ThermalError("profile.bin_width_ns must be an integer >= 1");
    }
    validate_provider(profile.gpu_provider, "profile.gpu_provider");
    if (profile.legacy_power_schema) {
        validate_provider(profile.hbm_provider, "profile.hbm_provider");
    } else {
        require_nonempty(profile.near_memory.provenance.source,
                         "profile.near_memory.provenance.source");
        require_nonempty(profile.accelerator_power_semantics_provenance.source,
                         "profile.accelerator_power_semantics.provenance.source");
        require_nonempty(profile.power_model_evidence_provenance.source,
                         "profile.power_model_evidence_level.provenance.source");

        const auto kind = profile.near_memory.kind;
        const auto semantics = profile.accelerator_power_semantics;
        const bool hbm = is_hbm(kind);
        if (profile.package_architecture == PackageArchitecture::GddrGpuHbf &&
            kind != NearMemoryKind::Gddr7) {
            throw ThermalError(
                "gddr_gpu_hbf requires near_memory.kind gddr7");
        }
        if (profile.package_architecture == PackageArchitecture::HbmGpuHbf &&
            !hbm) {
            throw ThermalError(
                "hbm_gpu_hbf requires an HBM near-memory technology");
        }
        if (profile.package_architecture == PackageArchitecture::Synthetic &&
            kind != NearMemoryKind::Synthetic && kind != NearMemoryKind::None) {
            throw ThermalError(
                "synthetic architecture requires synthetic or none near memory");
        }
        if (kind == NearMemoryKind::Gddr7 &&
            profile.near_memory.placement != NearMemoryPlacement::Board) {
            throw ThermalError("gddr7 near memory must use board placement");
        }
        if (hbm && profile.near_memory.placement != NearMemoryPlacement::Interposer &&
            profile.near_memory.placement != NearMemoryPlacement::Stacked) {
            throw ThermalError(
                "HBM near memory must use interposer or stacked placement");
        }

        if (semantics == AcceleratorPowerSemantics::GpuPlusGddrLumped) {
            if (kind != NearMemoryKind::Gddr7 ||
                !profile.near_memory.power_sources.empty()) {
                throw ThermalError(
                    "gpu_plus_gddr_lumped requires gddr7 with no explicit memory power sources");
            }
        } else if (semantics ==
                   AcceleratorPowerSemantics::GpuComputePlusExplicitHbm) {
            if (!hbm || profile.near_memory.power_sources.empty()) {
                throw ThermalError(
                    "gpu_compute_plus_explicit_hbm requires explicit HBM stack sources");
            }
        } else if (semantics == AcceleratorPowerSemantics::GpuComputeOnly) {
            if (kind != NearMemoryKind::None ||
                !profile.near_memory.power_sources.empty()) {
                throw ThermalError(
                    "gpu_compute_only requires near_memory.kind none");
            }
        } else if (!profile.near_memory.power_sources.empty()) {
            throw ThermalError(
                "board_total_lumped cannot add explicit near-memory power");
        }

        std::unordered_set<std::string> source_nodes;
        for (std::size_t index = 0;
             index < profile.near_memory.power_sources.size(); ++index) {
            const auto& source = profile.near_memory.power_sources[index];
            const auto field = "profile.near_memory.power_sources[" +
                               std::to_string(index) + "]";
            require_nonempty(source.thermal_node, field + ".thermal_node");
            if (hbm && !source.thermal_node.starts_with("hbm.s")) {
                throw ThermalError(
                    field + ".thermal_node must use hbm.sN naming");
            }
            if (!source_nodes.insert(source.thermal_node).second) {
                throw ThermalError("near-memory power source nodes must be unique");
            }
            if (std::find(profile.topology.node_names().begin(),
                          profile.topology.node_names().end(),
                          source.thermal_node) ==
                profile.topology.node_names().end()) {
                throw ThermalError(field + ".thermal_node is unknown");
            }
            validate_provider(source.provider, field + ".provider");
        }

        const auto declared = profile.power_model_evidence_provenance.evidence;
        const bool evidence_matches =
            (profile.power_model_evidence_level ==
                 PowerModelEvidenceLevel::SensitivityOnly &&
             declared == EvidenceClass::CalibrationOrSensitivity) ||
            (profile.power_model_evidence_level ==
                 PowerModelEvidenceLevel::LiteratureBounded &&
             declared == EvidenceClass::Literature) ||
            (profile.power_model_evidence_level ==
                 PowerModelEvidenceLevel::Calibrated &&
             declared == EvidenceClass::CalibrationOrSensitivity) ||
            (profile.power_model_evidence_level ==
                 PowerModelEvidenceLevel::Measured &&
             declared == EvidenceClass::Measurement);
        if (!evidence_matches) {
            throw ThermalError(
                "power_model_evidence_level conflicts with provenance.class");
        }
    }

    const auto validate_operation = [](const OperationEnergy& operation,
                                       const std::string& field) {
        validate_nonnegative(operation.command_j, field + ".command_j");
        validate_nonnegative(operation.joules_per_byte,
                             field + ".joules_per_byte");
    };
    validate_operation(profile.nand_energy.read, "profile.nand_energy.read");
    validate_operation(profile.nand_energy.program,
                       "profile.nand_energy.program");
    validate_operation(profile.nand_energy.erase,
                       "profile.nand_energy.erase");
    validate_nonnegative(profile.base_die.idle_w, "profile.base_die.idle_w");
    validate_nonnegative(profile.base_die.command_j,
                         "profile.base_die.command_j");
    validate_nonnegative(profile.base_die.joules_per_byte,
                         "profile.base_die.joules_per_byte");
    if (std::find(profile.topology.node_names().begin(),
                  profile.topology.node_names().end(),
                  profile.base_die.thermal_node) ==
        profile.topology.node_names().end()) {
        throw ThermalError("profile base die thermal node is unknown");
    }

    const auto& policy = profile.policy;
    for (const auto* value : {&policy.light_on_c, &policy.light_off_c,
                              &policy.severe_on_c, &policy.severe_off_c,
                              &policy.shutdown_on_c, &policy.shutdown_off_c}) {
        require_finite(value->value, "profile.policy temperature");
    }
    validate_nonnegative(policy.light_scale, "profile.policy.light_scale");
    if (!(policy.light_scale.value > 0.0 && policy.light_scale.value <= 1.0)) {
        throw ThermalError("profile.policy.light_scale must be in (0, 1]");
    }
    if (!(policy.light_off_c.value < policy.light_on_c.value &&
          policy.light_on_c.value < policy.severe_on_c.value &&
          policy.severe_off_c.value < policy.severe_on_c.value &&
          policy.light_off_c.value <= policy.severe_off_c.value &&
          policy.severe_on_c.value < policy.shutdown_on_c.value &&
          policy.shutdown_off_c.value < policy.shutdown_on_c.value &&
          policy.severe_off_c.value <= policy.shutdown_off_c.value)) {
        throw ThermalError("profile policy thresholds violate hysteresis order");
    }
    if (policy.debounce_samples == 0) {
        throw ThermalError("profile policy debounce_samples must be non-zero");
    }
    require_nonempty(policy.timing_provenance.source,
                     "profile.policy.timing_provenance.source");
}

std::string to_string(EvidenceClass value)
{
    switch (value) {
    case EvidenceClass::Specification: return "S";
    case EvidenceClass::Literature: return "L";
    case EvidenceClass::CalibrationOrSensitivity: return "C";
    case EvidenceClass::Measurement: return "M";
    }
    throw ThermalError("unknown evidence class");
}

std::string to_string(ThermalStage value)
{
    switch (value) {
    case ThermalStage::Off: return "off";
    case ThermalStage::ReadOnly: return "read_only";
    case ThermalStage::Shadow: return "shadow";
    case ThermalStage::Active: return "active";
    }
    throw ThermalError("unknown thermal stage");
}

std::string to_string(ThermalMode value)
{
    switch (value) {
    case ThermalMode::Normal: return "normal";
    case ThermalMode::Light: return "light";
    case ThermalMode::Severe: return "severe";
    case ThermalMode::Shutdown: return "shutdown";
    }
    throw ThermalError("unknown thermal mode");
}

std::string to_string(NearMemoryKind value)
{
    switch (value) {
    case NearMemoryKind::None: return "none";
    case NearMemoryKind::Gddr7: return "gddr7";
    case NearMemoryKind::Hbm2: return "hbm2";
    case NearMemoryKind::Hbm2e: return "hbm2e";
    case NearMemoryKind::Hbm3: return "hbm3";
    case NearMemoryKind::Hbm3e: return "hbm3e";
    case NearMemoryKind::Hbm4: return "hbm4";
    case NearMemoryKind::Synthetic: return "synthetic";
    }
    throw ThermalError("unknown near-memory kind");
}

std::string to_string(NearMemoryPlacement value)
{
    switch (value) {
    case NearMemoryPlacement::Board: return "board";
    case NearMemoryPlacement::Interposer: return "interposer";
    case NearMemoryPlacement::Stacked: return "stacked";
    case NearMemoryPlacement::External: return "external";
    }
    throw ThermalError("unknown near-memory placement");
}

std::string to_string(PackageArchitecture value)
{
    switch (value) {
    case PackageArchitecture::GddrGpuHbf: return "gddr_gpu_hbf";
    case PackageArchitecture::HbmGpuHbf: return "hbm_gpu_hbf";
    case PackageArchitecture::Synthetic: return "synthetic";
    }
    throw ThermalError("unknown package architecture");
}

std::string to_string(AcceleratorPowerSemantics value)
{
    switch (value) {
    case AcceleratorPowerSemantics::BoardTotalLumped:
        return "board_total_lumped";
    case AcceleratorPowerSemantics::GpuComputeOnly:
        return "gpu_compute_only";
    case AcceleratorPowerSemantics::GpuPlusGddrLumped:
        return "gpu_plus_gddr_lumped";
    case AcceleratorPowerSemantics::GpuComputePlusExplicitHbm:
        return "gpu_compute_plus_explicit_hbm";
    }
    throw ThermalError("unknown accelerator power semantics");
}

std::string to_string(PowerModelEvidenceLevel value)
{
    switch (value) {
    case PowerModelEvidenceLevel::SensitivityOnly:
        return "sensitivity_only";
    case PowerModelEvidenceLevel::LiteratureBounded:
        return "literature_bounded";
    case PowerModelEvidenceLevel::Calibrated: return "calibrated";
    case PowerModelEvidenceLevel::Measured: return "measured";
    }
    throw ThermalError("unknown power-model evidence level");
}

}  // namespace hbfsim::package_thermal

#include "hbfsim/coverage.hpp"

#include <json.hpp>

#include <algorithm>
#include <iomanip>
#include <ranges>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace hbfsim {
namespace {

std::string module_key(const std::string& module_id, const std::string& kernel)
{
    return module_id + '\n' + kernel;
}

ParameterKind parameter_kind(const std::string& kind)
{
    if (kind == "pointer") {
        return ParameterKind::Pointer;
    }
    if (kind == "opaque_aggregate") {
        return ParameterKind::OpaqueAggregate;
    }
    if (kind == "scalar") {
        return ParameterKind::Scalar;
    }
    throw std::invalid_argument("unknown coverage parameter kind: " + kind);
}

GateDecision rejected(const KernelLaunch& launch, const std::string& reason)
{
    return {
        .allowed = false,
        .module_id = launch.module_id,
        .kernel = launch.kernel,
        .reason = reason,
        .inspected_parameters = launch.parameters.size(),
    };
}

}  // namespace

std::string
module_id_from_identity(const std::array<std::uint8_t, 32>& identity)
{
    std::ostringstream output;
    output << "ptx:sha256:" << std::hex << std::setfill('0');
    for (const auto byte : identity) {
        output << std::setw(2) << static_cast<unsigned>(byte);
    }
    return output.str();
}

GateDecision uninspectable_launch_decision(bool has_hbf_ranges,
                                           std::string kind)
{
    return has_hbf_ranges ? GateDecision{.allowed = false,
                                         .kernel = kind,
                                         .reason = "uninspectable_launch_path",
                                         .operation = std::move(kind)}
                          : GateDecision{.allowed = true,
                                         .kernel = std::move(kind),
                                         .reason = "allowed"};
}

ModuleManifest module_manifest_from_json(const std::string& text)
{
    const auto json = nlohmann::json::parse(text);
    ModuleManifest manifest{
        .module_id = json.at("module_id").get<std::string>(),
        .kernel = json.at("kernel").get<std::string>(),
        .ptx_target = json.value("ptx_target", ""),
        .instrumented = json.value("instrumented", false),
        .cubin_only = json.value("cubin_only", false),
    };
    for (const auto& parameter :
         json.value("parameters", nlohmann::json::array())) {
        manifest.parameters.push_back({
            .index = parameter.at("index").get<std::size_t>(),
            .offset = parameter.at("offset").get<std::size_t>(),
            .width = parameter.at("width").get<std::size_t>(),
            .kind = parameter_kind(parameter.at("kind").get<std::string>()),
        });
    }
    for (const auto& parameter :
         json.value("unsupported_parameters", nlohmann::json::array())) {
        manifest.unsupported_parameters.push_back({
            .index = parameter.at("index").get<std::size_t>(),
            .operation = parameter.at("operation").get<std::string>(),
        });
    }
    // Reuse add_module's validation contract at the parse boundary.
    CoverageGate validator;
    validator.add_module(manifest);
    return manifest;
}

void CoverageGate::add_module(ModuleManifest manifest)
{
    if (manifest.module_id.empty() || manifest.kernel.empty()) {
        throw std::invalid_argument(
            "coverage module and kernel identity are required");
    }
    std::vector<std::size_t> indices;
    for (const auto& parameter : manifest.parameters) {
        if (parameter.width == 0 ||
            std::ranges::find(indices, parameter.index) != indices.end()) {
            throw std::invalid_argument("coverage parameters require unique "
                                        "indices and nonzero widths");
        }
        indices.push_back(parameter.index);
    }
    std::unique_lock lock(mutex_);
    const auto key = module_key(manifest.module_id, manifest.kernel);
    modules_.insert_or_assign(key, std::move(manifest));
}

void CoverageGate::add_range(std::uintptr_t begin, std::uintptr_t end)
{
    if (begin >= end) {
        throw std::invalid_argument("coverage range must be non-empty");
    }
    std::unique_lock lock(mutex_);
    ranges_.push_back({begin, end});
}

bool CoverageGate::has_ranges() const
{
    std::shared_lock lock(mutex_);
    return !ranges_.empty();
}

bool CoverageGate::contains(std::uintptr_t address) const
{
    return std::ranges::any_of(ranges_, [address](const AddressRange& range) {
        return address >= range.begin && address < range.end;
    });
}

GateDecision CoverageGate::check_launch(const KernelLaunch& launch) const
{
    std::shared_lock lock(mutex_);
    const bool opaque_aggregate = std::ranges::any_of(
        launch.parameters, [](const LaunchParameter& parameter) {
            return parameter.opaque_aggregate;
        });
    const bool has_hbf = std::ranges::any_of(
        launch.parameters, [this](const LaunchParameter& parameter) {
            return std::ranges::any_of(parameter.slots,
                                       [this](const ArgumentSlot& slot) {
                                           return contains(slot.value);
                                       });
        });
    if (!has_hbf && !(opaque_aggregate && !ranges_.empty())) {
        return {.allowed = true,
                .module_id = launch.module_id,
                .kernel = launch.kernel,
                .inspected_parameters = launch.parameters.size()};
    }

    if (launch.module_id.empty()) {
        return rejected(launch, "exact_module_identity_required");
    }
    const ModuleManifest* manifest = nullptr;
    const auto found =
        modules_.find(module_key(launch.module_id, launch.kernel));
    if (found != modules_.end()) {
        manifest = &found->second;
    }
    if (manifest == nullptr) {
        return rejected(launch, "uninstrumented_module");
    }

    GateDecision decision{
        .allowed = true,
        .module_id = manifest->module_id,
        .kernel = manifest->kernel,
        .ptx_target = manifest->ptx_target,
        .cubin_only = manifest->cubin_only,
        .inspected_parameters = launch.parameters.size(),
    };
    const bool exact_parameter_layout =
        launch.parameters.size() == manifest->parameters.size() &&
        std::ranges::all_of(
            launch.parameters, [&manifest](const LaunchParameter& parameter) {
                const auto metadata = std::ranges::find_if(
                    manifest->parameters,
                    [&parameter](const ParameterMetadata& item) {
                        return item.index == parameter.index;
                    });
                return metadata != manifest->parameters.end() &&
                       metadata->offset == parameter.offset &&
                       metadata->width == parameter.width;
            });
    // Exact ABI agreement is an authorization precondition for transformed
    // PTX. Opaque/uninstrumented modules are rejected by their more specific
    // policy reason below and cannot be authorized by this exception.
    if (manifest->instrumented && !exact_parameter_layout) {
        decision.allowed = false;
        decision.reason = "parameter_layout_mismatch";
        decision.operation = "unproven_parameter_layout";
        return decision;
    }
    for (const auto& parameter : launch.parameters) {
        if (parameter.opaque_aggregate && !ranges_.empty()) {
            decision.allowed = false;
            decision.reason = "opaque_aggregate_parameter";
            decision.operation = "unproven_aggregate_pointer_slots";
            decision.parameter_index = parameter.index;
            decision.parameter_offset = parameter.offset;
            return decision;
        }
        for (const auto& slot : parameter.slots) {
            if (!contains(slot.value)) {
                continue;
            }
            decision.parameter_index = parameter.index;
            decision.parameter_offset = parameter.offset + slot.offset;
            decision.address = slot.value;

            const auto unsupported = std::ranges::find_if(
                manifest->unsupported_parameters,
                [&parameter](const UnsupportedParameter& item) {
                    return item.index == parameter.index;
                });
            if (unsupported != manifest->unsupported_parameters.end()) {
                decision.allowed = false;
                decision.reason = "unsupported_operation";
                decision.operation = unsupported->operation;
                return decision;
            }
            if (manifest->cubin_only) {
                decision.allowed = false;
                decision.reason = "cubin_only_module";
                decision.operation = "opaque_pointer_access";
                return decision;
            }
            if (!manifest->instrumented) {
                decision.allowed = false;
                decision.reason = "uninstrumented_module";
                decision.operation = "opaque_pointer_access";
                return decision;
            }
            const auto metadata = std::ranges::find_if(
                manifest->parameters,
                [&parameter](const ParameterMetadata& item) {
                    return item.index == parameter.index;
                });
            if (metadata == manifest->parameters.end() ||
                metadata->kind != ParameterKind::Pointer) {
                decision.allowed = false;
                decision.reason =
                    metadata != manifest->parameters.end() &&
                            metadata->kind == ParameterKind::OpaqueAggregate
                        ? "opaque_aggregate_parameter"
                        : "uninstrumented_pointer_parameter";
                decision.operation = "unrecognized_pointer_access";
                return decision;
            }
        }
    }
    return decision;
}

}  // namespace hbfsim

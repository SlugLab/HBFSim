#include "hbfsim/coverage.hpp"

#include <json.hpp>

#include <algorithm>
#include <cctype>
#include <iomanip>
#include <ranges>
#include <set>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace hbfsim {
namespace {

bool strict_policy(RangePolicy policy)
{
    return policy == RangePolicy::LegacyStrict ||
           policy == RangePolicy::CapacityUnbacked;
}

RangePolicy stricter_policy(RangePolicy left, RangePolicy right)
{
    if (left == RangePolicy::CapacityUnbacked ||
        right == RangePolicy::CapacityUnbacked) {
        return RangePolicy::CapacityUnbacked;
    }
    if (left == RangePolicy::LegacyStrict ||
        right == RangePolicy::LegacyStrict) {
        return RangePolicy::LegacyStrict;
    }
    if (left == RangePolicy::TimingBacked ||
        right == RangePolicy::TimingBacked) {
        return RangePolicy::TimingBacked;
    }
    return RangePolicy::None;
}

std::string module_key(const std::string& module_id, const std::string& kernel)
{
    return module_id + '\n' + kernel;
}

bool sha256_hex(std::string_view value)
{
    return value.size() == 64 &&
           std::ranges::all_of(value, [](unsigned char character) {
               return std::isxdigit(character) != 0;
           });
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

GateDecision rejected(const KernelLaunch& launch, const std::string& reason,
                      RangePolicy policy)
{
    auto decision = rejected(launch, reason);
    decision.range_policy = policy;
    return decision;
}

GateDecision unmodeled_timing(const KernelLaunch& launch,
                              std::string operation)
{
    return {
        .allowed = true,
        .module_id = launch.module_id,
        .kernel = launch.kernel,
        .reason = "opaque_unmodeled_timing",
        .operation = std::move(operation),
        .inspected_parameters = launch.parameters.size(),
        .range_policy = RangePolicy::TimingBacked,
        .modeled = false,
        .opaque_unmodeled = true,
    };
}

}  // namespace

const char* range_policy_name(RangePolicy policy) noexcept
{
    switch (policy) {
    case RangePolicy::None:
        return "none";
    case RangePolicy::LegacyStrict:
        return "legacy_strict";
    case RangePolicy::TimingBacked:
        return "timing_backed";
    case RangePolicy::CapacityUnbacked:
        return "capacity_unbacked";
    }
    return "unknown";
}

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

GateDecision uninspectable_launch_decision(bool has_hbf_ranges,
                                           bool has_capacity_ranges,
                                           std::string kind)
{
    if (!has_hbf_ranges) {
        return {.allowed = true,
                .kernel = std::move(kind),
                .reason = "allowed"};
    }
    if (has_capacity_ranges) {
        return {.allowed = false,
                .kernel = kind,
                .reason = "uninspectable_launch_path",
                .operation = std::move(kind),
                .range_policy = RangePolicy::CapacityUnbacked};
    }
    KernelLaunch launch{.kernel = kind};
    return unmodeled_timing(launch, std::move(kind));
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
    manifest.manifest_schema_version =
        json.value("manifest_schema_version", 1U);
    if (manifest.manifest_schema_version == 2 ||
        manifest.manifest_schema_version == 3) {
        manifest.original_ptx_sha256 =
            json.at("original_ptx_sha256").get<std::string>();
        manifest.transformed_ptx_sha256 =
            json.at("transformed_ptx_sha256").get<std::string>();
        manifest.aot_required_for_exact =
            json.at("aot_required_for_exact").get<bool>();
        if (!sha256_hex(manifest.original_ptx_sha256) ||
            !sha256_hex(manifest.transformed_ptx_sha256) ||
            !manifest.aot_required_for_exact) {
            throw std::invalid_argument("invalid exact pass manifest evidence");
        }
        if (manifest.manifest_schema_version == 3) {
            manifest.future_manifest.manifest_schema_version = 3;
            manifest.future_manifest.async_transform_version =
                json.at("async_transform_version").get<std::string>();
            manifest.future_manifest.ir_sha256 =
                json.at("ir_sha256").get<std::string>();
            if (manifest.future_manifest.async_transform_version !=
                    "sm120-future-v1" ||
                !sha256_hex(manifest.future_manifest.ir_sha256)) {
                throw std::invalid_argument(
                    "invalid async transform manifest evidence");
            }
            std::set<std::uint32_t> instruction_ids;
            for (const auto& instruction : json.at("instruction_table")) {
                FutureInstructionEvidence record{
                    .instruction_id =
                        instruction.at("instruction_id").get<std::uint32_t>(),
                    .source_line =
                        instruction.at("source_line").get<std::uint32_t>(),
                    .bytes = instruction.at("bytes").get<std::uint32_t>(),
                    .opcode = instruction.at("opcode").get<std::string>(),
                    .memory_kind =
                        instruction.at("memory_kind").get<std::string>(),
                };
                if (record.instruction_id == 0 || record.source_line == 0 ||
                    record.bytes == 0 || record.opcode.empty() ||
                    (record.memory_kind != "load" &&
                     record.memory_kind != "store" &&
                     record.memory_kind != "atomic_rmw") ||
                    !instruction_ids.insert(record.instruction_id).second) {
                    throw std::invalid_argument(
                        "invalid async instruction table");
                }
                manifest.future_manifest.instructions.push_back(
                    std::move(record));
            }
            const auto& maximum = json.at("maximum_live_futures");
            manifest.future_manifest.maximum_live = {
                .thread_futures = maximum.at("thread").get<std::uint32_t>(),
                .warp_futures = maximum.at("warp").get<std::uint32_t>(),
                .cta_futures = maximum.at("cta").get<std::uint32_t>(),
                .cluster_futures = maximum.at("cluster").get<std::uint32_t>(),
            };
            manifest.future_manifest.ambiguities =
                json.at("ambiguities").get<std::vector<std::string>>();
            if (manifest.future_manifest.instructions.empty() ||
                manifest.future_manifest.maximum_live.thread_futures == 0 ||
                manifest.future_manifest.maximum_live.warp_futures == 0 ||
                manifest.future_manifest.maximum_live.cta_futures == 0 ||
                manifest.future_manifest.maximum_live.cluster_futures == 0) {
                throw std::invalid_argument(
                    "empty async transform manifest evidence");
            }
        }
    } else if (manifest.manifest_schema_version != 1) {
        throw std::invalid_argument("unsupported coverage manifest schema");
    }
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
    add_range(begin, end, RangePolicy::LegacyStrict);
}

void CoverageGate::add_range(std::uintptr_t begin, std::uintptr_t end,
                             RangePolicy policy)
{
    if (begin >= end || policy == RangePolicy::None) {
        throw std::invalid_argument("coverage range must be non-empty");
    }
    std::unique_lock lock(mutex_);
    ranges_.push_back({begin, end, policy});
}

void CoverageGate::remove_range(std::uintptr_t begin,
                                std::uintptr_t end) noexcept
{
    std::unique_lock lock(mutex_);
    const auto found = std::find_if(
        ranges_.rbegin(), ranges_.rend(),
        [=](const AddressRange& range) {
            return range.begin == begin && range.end == end;
        });
    if (found != ranges_.rend()) {
        ranges_.erase(std::next(found).base());
    }
}

void CoverageGate::clear_ranges()
{
    std::unique_lock lock(mutex_);
    ranges_.clear();
}

bool CoverageGate::has_ranges() const
{
    std::shared_lock lock(mutex_);
    return !ranges_.empty();
}

bool CoverageGate::has_capacity_ranges() const
{
    std::shared_lock lock(mutex_);
    return std::ranges::any_of(ranges_, [](const AddressRange& range) {
        return range.policy == RangePolicy::CapacityUnbacked;
    });
}

bool CoverageGate::has_strict_ranges() const
{
    std::shared_lock lock(mutex_);
    return std::ranges::any_of(ranges_, [](const AddressRange& range) {
        return strict_policy(range.policy);
    });
}

RangePolicy CoverageGate::policy_for(std::uintptr_t address) const
{
    RangePolicy result = RangePolicy::None;
    for (const auto& range : ranges_) {
        if (address >= range.begin && address < range.end) {
            result = stricter_policy(result, range.policy);
        }
    }
    return result;
}

GateDecision CoverageGate::check_launch(const KernelLaunch& launch) const
{
    std::shared_lock lock(mutex_);
    const bool opaque_aggregate = std::ranges::any_of(
        launch.parameters, [](const LaunchParameter& parameter) {
            return parameter.opaque_aggregate;
        });
    RangePolicy launch_policy = RangePolicy::None;
    for (const auto& parameter : launch.parameters) {
        for (const auto& slot : parameter.slots) {
            launch_policy =
                stricter_policy(launch_policy, policy_for(slot.value));
        }
    }
    const bool has_hbf = launch_policy != RangePolicy::None;
    RangePolicy aggregate_policy = RangePolicy::None;
    if (opaque_aggregate && !ranges_.empty()) {
        aggregate_policy = std::ranges::any_of(
                               ranges_, [](const AddressRange& range) {
                                   return strict_policy(range.policy);
                               })
                               ? RangePolicy::CapacityUnbacked
                               : RangePolicy::TimingBacked;
        launch_policy = stricter_policy(launch_policy, aggregate_policy);
    }
    if (!has_hbf && !(opaque_aggregate && !ranges_.empty())) {
        return {.allowed = true,
                .module_id = launch.module_id,
                .kernel = launch.kernel,
                .inspected_parameters = launch.parameters.size()};
    }

    if (launch.module_id.empty()) {
        return launch_policy == RangePolicy::TimingBacked
                   ? unmodeled_timing(launch, "opaque_pointer_access")
                   : rejected(launch, "exact_module_identity_required",
                              launch_policy);
    }
    const ModuleManifest* manifest = nullptr;
    const auto found =
        modules_.find(module_key(launch.module_id, launch.kernel));
    if (found != modules_.end()) {
        manifest = &found->second;
    }
    if (manifest == nullptr) {
        return launch_policy == RangePolicy::TimingBacked
                   ? unmodeled_timing(launch, "opaque_pointer_access")
                   : rejected(launch, "uninstrumented_module", launch_policy);
    }

    GateDecision decision{
        .allowed = true,
        .module_id = manifest->module_id,
        .kernel = manifest->kernel,
        .ptx_target = manifest->ptx_target,
        .cubin_only = manifest->cubin_only,
        .inspected_parameters = launch.parameters.size(),
        .range_policy = launch_policy,
    };
    decision.manifest_schema_version = manifest->manifest_schema_version;
    decision.original_ptx_sha256 = manifest->original_ptx_sha256;
    decision.transformed_ptx_sha256 = manifest->transformed_ptx_sha256;
    decision.aot_required_for_exact = manifest->aot_required_for_exact;
    decision.future_manifest = manifest->future_manifest;
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
            if (aggregate_policy == RangePolicy::TimingBacked &&
                !strict_policy(launch_policy)) {
                auto fallback = unmodeled_timing(
                    launch, "unproven_aggregate_pointer_slots");
                fallback.parameter_index = parameter.index;
                fallback.parameter_offset = parameter.offset;
                return fallback;
            }
            decision.allowed = false;
            decision.reason = "opaque_aggregate_parameter";
            decision.operation = "unproven_aggregate_pointer_slots";
            decision.parameter_index = parameter.index;
            decision.parameter_offset = parameter.offset;
            return decision;
        }
        for (const auto& slot : parameter.slots) {
            const auto slot_policy = policy_for(slot.value);
            if (slot_policy == RangePolicy::None) {
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
                if (slot_policy == RangePolicy::TimingBacked &&
                    !strict_policy(launch_policy)) {
                    auto fallback =
                        unmodeled_timing(launch, unsupported->operation);
                    fallback.parameter_index = parameter.index;
                    fallback.parameter_offset = parameter.offset + slot.offset;
                    fallback.address = slot.value;
                    return fallback;
                }
                decision.allowed = false;
                decision.reason = "unsupported_operation";
                decision.operation = unsupported->operation;
                return decision;
            }
            if (manifest->cubin_only) {
                if (slot_policy == RangePolicy::TimingBacked &&
                    !strict_policy(launch_policy)) {
                    auto fallback =
                        unmodeled_timing(launch, "opaque_pointer_access");
                    fallback.cubin_only = true;
                    fallback.parameter_index = parameter.index;
                    fallback.parameter_offset = parameter.offset + slot.offset;
                    fallback.address = slot.value;
                    return fallback;
                }
                decision.allowed = false;
                decision.reason = "cubin_only_module";
                decision.operation = "opaque_pointer_access";
                return decision;
            }
            if (!manifest->instrumented) {
                if (slot_policy == RangePolicy::TimingBacked &&
                    !strict_policy(launch_policy)) {
                    auto fallback =
                        unmodeled_timing(launch, "opaque_pointer_access");
                    fallback.parameter_index = parameter.index;
                    fallback.parameter_offset = parameter.offset + slot.offset;
                    fallback.address = slot.value;
                    return fallback;
                }
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
                if (slot_policy == RangePolicy::TimingBacked &&
                    !strict_policy(launch_policy)) {
                    auto fallback =
                        unmodeled_timing(launch, "unrecognized_pointer_access");
                    fallback.parameter_index = parameter.index;
                    fallback.parameter_offset = parameter.offset + slot.offset;
                    fallback.address = slot.value;
                    return fallback;
                }
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
    decision.modeled = has_hbf;
    return decision;
}

}  // namespace hbfsim

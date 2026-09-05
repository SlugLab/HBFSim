#include "hbfsim/coverage.hpp"

#include <json.hpp>
#include <openssl/sha.h>

#include <algorithm>
#include <iomanip>
#include <ranges>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace hbfsim {
std::string future_contract_json(const std::string& original_sha256,
                                 const std::string& helper_sha256)
{
    return nlohmann::json{{"identity_domain","hbfsim.timing-load-future.v1"},
        {"original_ptx_sha256",original_sha256},{"transform_mode",timing_future::kMode},
        {"device_future_abi",1},{"device_future_bytes",64},{"lane_metadata_abi",1},
        {"lane_metadata_bytes",32},{"shared_control_abi",4},{"helper_sha256",helper_sha256},
        {"subset","straight_line_scalar_read_v1"},{"time_scale",1},{"maximum_thread_futures",16},
        {"maximum_block_threads",1024},{"trace_capacity",timing_future::kTraceCapacity},
        {"trace_record_bytes",sizeof(timing_future::Trace)},
        {"maximum_records_per_producer",timing_future::kMaximumRecordsPerProducer}}.dump();
}

std::string future_contract_identity(const std::string& contract)
{
    unsigned char digest[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char*>(contract.data()),contract.size(),digest);
    std::ostringstream out;out<<std::hex<<std::setfill('0');
    for (auto byte:digest) out<<std::setw(2)<<static_cast<unsigned>(byte);
    return out.str();
}

std::string future_kernel_contract_symbol(const std::string& kernel)
{
    return "__hbfsim_timing_future_kernel_"+future_contract_identity(kernel)+"_v1";
}

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
    for (const auto& parameter :
         json.value("parameters", nlohmann::json::array())) {
        manifest.parameters.push_back({
            .index = parameter.at("index").get<std::size_t>(),
            .offset = parameter.at("offset").get<std::size_t>(),
            .width = parameter.at("width").get<std::size_t>(),
            .kind = parameter_kind(parameter.at("kind").get<std::string>()),
        });
    }
    manifest.transform_mode=json.value("transform_mode","synchronous");
    if (json.contains("future_requirements")) {
        const auto& r=json.at("future_requirements");
        if (!r.is_object() || r.size()!=10) throw std::invalid_argument("invalid future requirements schema");
        const auto integer=[&](const char* field,std::uint64_t maximum) {
            const auto& value=r.at(field);
            if (!value.is_number_integer() ||
                (!value.is_number_unsigned() && value.get<std::int64_t>()<0) ||
                value.get<std::uint64_t>()>maximum)
                throw std::invalid_argument("invalid future requirement integer");
            return value.get<std::uint64_t>();
        };
        const auto u32=[&](const char* field) { return static_cast<std::uint32_t>(integer(field,UINT32_MAX)); };
        manifest.future_requirements=timing_future::ModuleRequirements{
            .abi_version=u32("abi_version"),.struct_bytes=u32("struct_bytes"),
            .token_bytes=u32("token_bytes"),.metadata_version=u32("metadata_version"),
            .metadata_bytes=u32("metadata_bytes"),.shared_control_abi=u32("shared_control_abi"),
            .maximum_thread_futures=u32("maximum_thread_futures"),
            .maximum_block_threads=u32("maximum_block_threads"),
            .required_capabilities=integer("required_capabilities",UINT64_MAX),.reserved=integer("reserved",UINT64_MAX)};
    }
    if(json.contains("future_contract")) manifest.future_contract=json.at("future_contract").dump();
    if(json.contains("future_kernel")) {
        const auto& k=json.at("future_kernel");
        if(!k.is_object() || k.size()!=4)throw std::invalid_argument("invalid future kernel contract");
        const auto u32=[](const auto& v) {
            if(!v.is_number_unsigned() || v.template get<std::uint64_t>()>UINT32_MAX)
                throw std::invalid_argument("invalid future geometry integer");
            return v.template get<std::uint32_t>();
        };
        FutureKernelContract c{u32(k.at("static_producers")),u32(k.at("maximum_block_threads"))};
        const auto dims=[&](const char* key,auto& output) {
            const auto& a=k.at(key);if(!a.is_array() || a.size()!=3)throw std::invalid_argument("invalid future geometry axes");
            for(unsigned i=0;i<3;++i)output[i]=u32(a[i]);
        };
        dims("required_threads",c.required_threads);dims("maximum_threads",c.maximum_threads);
        manifest.future_kernel=c;
    }
    for (const auto& parameter :
         json.value("unsupported_parameters", nlohmann::json::array())) {
        manifest.unsupported_parameters.push_back({
            .index = parameter.at("index").get<std::size_t>(),
            .operation = parameter.at("operation").get<std::string>(),
        });
    }
    // Reuse add_module's validation contract at the parse boundary.
    if(manifest.future_requirements)manifest.future_manifest_sha256=future_contract_identity(json.dump());
    CoverageGate validator;
    validator.add_module(manifest);
    return manifest;
}

void CoverageGate::add_module(ModuleManifest manifest)
{
    const bool future=manifest.transform_mode==timing_future::kMode;
    if ((!future && manifest.transform_mode!="synchronous") ||
        future!=manifest.future_requirements.has_value() ||
        (future && !timing_future::valid_requirements(*manifest.future_requirements)))
        throw std::invalid_argument("invalid future module contract");
    if(!future && (manifest.future_kernel || !manifest.future_contract.empty()))
        throw std::invalid_argument("future metadata on synchronous manifest");
    if(future && (!manifest.future_contract.empty() || manifest.future_kernel)) {
        const auto c=nlohmann::json::parse(manifest.future_contract);
        const auto hex=[](const std::string& s) { return s.size()==64 && s.find_first_not_of("0123456789abcdef")==std::string::npos; };
        const auto original=c.at("original_ptx_sha256").get<std::string>();
        const auto helper=c.at("helper_sha256").get<std::string>();
        if(!hex(original) || !hex(helper) || manifest.future_contract!=future_contract_json(original,helper) ||
            manifest.module_id!="ptx:sha256:"+future_contract_identity(manifest.future_contract) || !manifest.future_kernel)
            throw std::invalid_argument("future identity contract mismatch");
        const auto& k=*manifest.future_kernel;
        const auto valid_dims=[](const auto& d) {
            if(d==std::array<std::uint32_t,3>{})return true;
            std::uint64_t count=1;
            for(auto axis:d){if(!axis || count>UINT64_MAX/axis)return false;count*=axis;}
            return true;
        };
        if(!k.static_producers || k.static_producers>manifest.future_requirements->maximum_thread_futures ||
            !k.maximum_block_threads || k.maximum_block_threads>manifest.future_requirements->maximum_block_threads ||
            !valid_dims(k.required_threads) || !valid_dims(k.maximum_threads) ||
            (k.required_threads!=std::array<std::uint32_t,3>{} && k.maximum_threads!=std::array<std::uint32_t,3>{}))
            throw std::invalid_argument("invalid future resource contract");
    }
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
    for(const auto& [_,old]:modules_) if(old.module_id==manifest.module_id &&
        (future || old.future_requirements) &&
        (old.transform_mode!=manifest.transform_mode || old.future_contract!=manifest.future_contract))
            throw std::invalid_argument("conflicting module-wide future identity");
    const auto key = module_key(manifest.module_id, manifest.kernel);
    if (const auto old=modules_.find(key); old!=modules_.end() &&
        (future || old->second.future_requirements)) {
        const auto& previous=old->second;
        const bool same_parameters=previous.parameters.size()==manifest.parameters.size() &&
            std::ranges::equal(previous.parameters,manifest.parameters,[](const auto& a,const auto& b) {
                return a.index==b.index && a.offset==b.offset && a.width==b.width && a.kind==b.kind;
            });
        const bool same_unsupported=previous.unsupported_parameters.size()==manifest.unsupported_parameters.size() &&
            std::ranges::equal(previous.unsupported_parameters,manifest.unsupported_parameters,[](const auto& a,const auto& b) {
                return a.index==b.index && a.operation==b.operation;
            });
        if (previous.transform_mode!=manifest.transform_mode ||
            previous.instrumented!=manifest.instrumented || previous.cubin_only!=manifest.cubin_only ||
            previous.ptx_target!=manifest.ptx_target || !same_parameters || !same_unsupported ||
            !previous.future_requirements || !manifest.future_requirements ||
            previous.future_requirements->maximum_thread_futures!=manifest.future_requirements->maximum_thread_futures ||
            previous.future_requirements->maximum_block_threads!=manifest.future_requirements->maximum_block_threads)
            throw std::invalid_argument("conflicting immutable future module manifest");
        if(previous.future_kernel!=manifest.future_kernel || previous.future_contract!=manifest.future_contract ||
            previous.future_manifest_sha256!=manifest.future_manifest_sha256)
            throw std::invalid_argument("conflicting immutable future kernel contract");
        return;
    }
    modules_.insert_or_assign(key, std::move(manifest));
}

std::optional<ModuleManifest> CoverageGate::manifest(const std::string& module,
                                                   const std::string& kernel) const
{
    std::shared_lock lock(mutex_);
    auto it=modules_.find(module_key(module,kernel));
    return it==modules_.end() ? std::nullopt : std::optional<ModuleManifest>{it->second};
}

bool CoverageGate::future_module_contract(const std::string& module,
    const timing_future::ModuleRequirements& requirements,const std::string& helper) const
{
    std::shared_lock lock(mutex_);bool found=false;
    for(const auto& [_,m]:modules_)if(m.module_id==module) {
        if(!m.future_kernel || !m.future_requirements || m.future_contract.empty() ||
            !m.instrumented || m.cubin_only || !m.unsupported_parameters.empty() ||
            (m.ptx_target!="sm_120" && m.ptx_target!="sm_120a") ||
            requirements.maximum_thread_futures!=m.future_requirements->maximum_thread_futures ||
            requirements.maximum_block_threads!=m.future_requirements->maximum_block_threads ||
            nlohmann::json::parse(m.future_contract).at("helper_sha256")!=helper)return false;
        found=true;
    }
    return found;
}

std::vector<ModuleManifest> CoverageGate::module_manifests(const std::string& module) const
{
    std::shared_lock lock(mutex_);std::vector<ModuleManifest> result;
    for(const auto& [_,m]:modules_)if(m.module_id==module)result.push_back(m);
    return result;
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
    // Future contracts must not reach either the native early return or the
    // synchronous TIMING unmodeled fallback while the unit is incomplete.
    const auto future=modules_.find(module_key(launch.module_id,launch.kernel));
    if (future!=modules_.end() && future->second.future_requirements) {
        const auto& m=future->second;
        if(!timing_future::kUnitComplete)return rejected(launch,"timing_future_unit_incomplete");
        if(!m.future_kernel || m.future_contract.empty() || !m.instrumented || m.cubin_only ||
            !m.unsupported_parameters.empty())return rejected(launch,"timing_future_manifest_unavailable");
        if(std::ranges::any_of(ranges_,[](const auto& r){return r.policy!=RangePolicy::TimingBacked;}))
            return rejected(launch,"timing_future_capacity_unsupported");
        const auto& k=*m.future_kernel;
        std::uint64_t threads=1,blocks=1;
        for(unsigned i=0;i<3;++i) {
            if(!launch.block[i] || !launch.grid[i] || threads>UINT64_MAX/launch.block[i] || blocks>UINT64_MAX/launch.grid[i] ||
                (k.required_threads[i] && launch.block[i]!=k.required_threads[i]) ||
                (k.maximum_threads[i] && launch.block[i]>k.maximum_threads[i]))
                return rejected(launch,"timing_future_launch_geometry");
            threads*=launch.block[i];blocks*=launch.grid[i];
        }
        if(threads>k.maximum_block_threads || blocks>timing_future::kTraceCapacity/threads/
            k.static_producers/timing_future::kMaximumRecordsPerProducer)
            return rejected(launch,"timing_future_launch_budget");
        // Future helpers validate every computed address; no synchronous
        // parameter-based TIMING fallback can authorize this unit.
        return {.allowed=true,.module_id=launch.module_id,.kernel=launch.kernel,
            .ptx_target=m.ptx_target,.reason="timing_future_validated",.modeled=true};
    }
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

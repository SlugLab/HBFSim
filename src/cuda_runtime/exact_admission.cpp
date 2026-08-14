#include <hbfsim/exact_admission.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <set>

namespace hbfsim {
namespace {

constexpr std::array<std::string_view, 7> kRequiredValidationClasses{
    "ordinary_load", "ordinary_store", "tma_load", "tma_store",
    "unicast", "multicast", "mixed_hbm_hbf"};

bool sha256_hex(std::string_view value);

bool validation_classes_complete(const ExactProfile& profile)
{
    if (profile.validation.classes.size() !=
        kRequiredValidationClasses.size()) {
        return false;
    }
    std::set<std::string_view> names;
    for (const auto& record : profile.validation.classes) {
        if (!names.insert(record.operation_class).second || !record.passed ||
            record.p50_error_percent > profile.thresholds.p50_percent ||
            record.p95_error_percent > profile.thresholds.p95_percent ||
            record.counter_error_percent >
                profile.thresholds.counter_percent) {
            return false;
        }
    }
    return std::all_of(
        kRequiredValidationClasses.begin(),
        kRequiredValidationClasses.end(),
        [&](std::string_view name) { return names.contains(name); });
}

bool channel_profile_complete(const ExactProfile& profile)
{
    const auto& calibration = profile.calibration;
    return profile.schema_version == 2 &&
           calibration.label_semantics == "contention_equivalent" &&
           calibration.gnic.count == 4 && calibration.gpc.count == 2 &&
           calibration.gnic.depth != 0 && calibration.gpc.depth != 0 &&
           calibration.gnic.service_ns_by_class.size() == 7 &&
           calibration.gpc.service_ns_by_class.size() == 7 &&
           calibration.routing.version != 0 &&
           sha256_hex(calibration.routing.program_sha256) &&
           !calibration.routing.smsp_proxy_lut.empty() &&
           !calibration.routing.gnic_lut.empty() &&
           !calibration.routing.gpc_lut.empty() &&
           sha256_hex(calibration.raw_training_sha256) &&
           sha256_hex(calibration.raw_holdout_sha256);
}

template <class Record>
const Record* find_named(const std::vector<Record>& records,
                         std::string_view name)
{
    const auto found = std::find_if(
        records.begin(), records.end(),
        [&](const Record& record) { return record.name == name; });
    return found == records.end() ? nullptr : &*found;
}

const ExactModuleArtifact* find_module(const ExactProfile& profile,
                                       std::string_view module_id)
{
    const auto found = std::find_if(
        profile.modules.begin(), profile.modules.end(),
        [&](const ExactModuleArtifact& module) {
            return module.module_id == module_id;
        });
    return found == profile.modules.end() ? nullptr : &*found;
}

bool same_shape(const ExactClusterShape& left,
                const ExactClusterShape& right)
{
    return left.x == right.x && left.y == right.y && left.z == right.z;
}

bool sha256_hex(std::string_view value)
{
    return value.size() == 64 &&
           std::all_of(value.begin(), value.end(), [](unsigned char byte) {
               return std::isxdigit(byte) != 0;
           });
}

bool future_manifest_complete(const FutureManifestEvidence& evidence,
                              bool empty_plan_allowed = false)
{
    return evidence.manifest_schema_version == 3 &&
           evidence.async_transform_version == "sm120-future-v1" &&
           sha256_hex(evidence.ir_sha256) &&
           (empty_plan_allowed ||
            (!evidence.instructions.empty() &&
             evidence.maximum_live.thread_futures != 0 &&
             evidence.maximum_live.warp_futures != 0 &&
             evidence.maximum_live.cta_futures != 0 &&
             evidence.maximum_live.cluster_futures != 0));
}

bool tma_manifest_complete(const TmaManifestEvidence& evidence)
{
    if (evidence.manifest_schema_version != 4 ||
        evidence.async_transform_version != "sm120-tma-v1" ||
        !sha256_hex(evidence.ir_sha256) || evidence.instructions.empty() ||
        evidence.maximum_live_async_objects == 0 ||
        !evidence.ambiguities.empty()) {
        return false;
    }
    return std::all_of(
        evidence.instructions.begin(), evidence.instructions.end(),
        [](const TmaInstructionEvidence& instruction) {
            return instruction.instruction_id != 0 &&
                   instruction.source_line != 0 &&
                   instruction.dimensions >= 1 && instruction.dimensions <= 5;
        });
}

bool tensormap_provenance_complete(const TmaManifestEvidence& evidence)
{
    return evidence.provenance_required &&
           !evidence.tensormap_parameters.empty() &&
           std::all_of(evidence.instructions.begin(),
                       evidence.instructions.end(),
                       [](const TmaInstructionEvidence& instruction) {
                           return instruction.descriptor_generation != 0;
                       });
}

bool future_budget_exceeded(const FutureManifestEvidence& evidence,
                            const ExactFutureLimits& limits)
{
    return evidence.maximum_live.thread_futures >
               limits.max_thread_futures ||
           evidence.maximum_live.warp_futures > limits.max_warp_futures ||
           evidence.maximum_live.cta_futures > limits.max_cta_futures ||
           evidence.maximum_live.cluster_futures >
               limits.max_cluster_futures;
}

}  // namespace

ExactAdmissionDecision ExactAdmissionEvaluator::evaluate(
    const ExactProfile& profile, const LoadedModuleEvidence& evidence,
    const ExactLiveEnvironment& live, const ExactRunContract& contract,
    std::string_view kernel) const
{
    ExactAdmissionDecision decision{
        .profile_id = profile.profile_id,
        .module_id = evidence.module_id,
        .kernel = std::string(kernel),
    };
    const auto reject = [&](bool mismatch, std::string_view reason) {
        if (mismatch) {
            decision.reasons.emplace_back(reason);
        }
    };

    reject(profile.validation.status != ValidationStatus::Passed,
           "profile_not_validated");
    reject(!validation_classes_complete(profile),
           "validation_class_missing");
    const auto channel_profile_valid = channel_profile_complete(profile);
    reject(!channel_profile_valid || !evidence.channel_runtime.observed,
           "queue_profile_missing");
    reject(channel_profile_valid && evidence.channel_runtime.observed &&
               (evidence.channel_runtime.routing_version !=
                    profile.calibration.routing.version ||
                evidence.channel_runtime.routing_program_sha256 !=
                    profile.calibration.routing.program_sha256 ||
                evidence.channel_runtime.gnic_count != 4 ||
                evidence.channel_runtime.gpc_count != 2),
           "routing_program_mismatch");
    reject(channel_profile_valid && evidence.channel_runtime.observed &&
               (evidence.channel_runtime.maximum_gnic_outstanding >
                    profile.calibration.gnic.depth ||
                evidence.channel_runtime.maximum_gpc_outstanding >
                    profile.calibration.gpc.depth ||
                evidence.channel_runtime.saturated_requests != 0),
           "outstanding_depth_exceeded");
    reject(evidence.channel_runtime.migration_visible_sm_mismatch,
           "migration_visible_sm_mismatch");
    reject(evidence.channel_runtime.counter_residual_failed,
           "counter_residual_failure");
    reject(live.gpu_name != profile.target.gpu_name, "gpu_name_mismatch");
    reject(live.gpu_uuid != profile.target.gpu_uuid, "gpu_uuid_mismatch");
    reject(live.pci_vendor_id != profile.target.pci_vendor_id ||
               live.pci_device_id != profile.target.pci_device_id,
           "pci_device_mismatch");
    reject(live.compute_capability_major !=
                   profile.target.compute_capability_major ||
               live.compute_capability_minor !=
                   profile.target.compute_capability_minor,
           "compute_capability_mismatch");
    reject(live.cuda_driver_version != profile.target.driver_version,
           "driver_version_mismatch");
    reject(live.captured_unix_ns == 0, "live_environment_missing");

    const auto* module = find_module(profile, evidence.module_id);
    reject(module == nullptr, "module_artifact_missing");
    if (module != nullptr) {
        reject(evidence.ptx_target != module->ptx_target,
               "ptx_target_mismatch");
    }
    reject(profile.toolchain.cuda_version !=
                   evidence.toolchain.cuda_release ||
               profile.toolchain.ptxas_version !=
                   evidence.toolchain.ptxas_version ||
               profile.toolchain.nvdisasm_version !=
                   evidence.toolchain.nvdisasm_version ||
               profile.toolchain.cuobjdump_version !=
                   evidence.toolchain.cuobjdump_version,
           "toolchain_mismatch");
    reject(!evidence.aot_verified, "aot_evidence_missing");
    const bool tma_required =
        evidence.tma_manifest.manifest_schema_version != 0 ||
        !evidence.tma_manifest.instructions.empty() ||
        evidence.tma_runtime.observed;
    reject(!future_manifest_complete(evidence.future_manifest, tma_required),
           "async_transform_missing");
    reject(!evidence.future_manifest.ambiguities.empty(),
           "async_transform_ambiguous");
    reject(future_manifest_complete(evidence.future_manifest, tma_required) &&
               future_budget_exceeded(evidence.future_manifest,
                                      profile.limits),
           "future_budget_exceeded");
    reject(evidence.future_runtime.leaked != 0 ||
               (evidence.future_runtime.observed &&
                (evidence.future_runtime.drained >
                     evidence.future_runtime.issued ||
                 evidence.future_runtime.drained +
                         evidence.future_runtime.leaked !=
                     evidence.future_runtime.issued)),
           "future_leak_detected");
    reject(tma_required && !tma_manifest_complete(evidence.tma_manifest),
           "tma_transform_missing");
    reject(tma_required &&
               !tensormap_provenance_complete(evidence.tma_manifest),
           "tensormap_provenance_missing");
    reject(tma_required &&
               (evidence.tma_runtime.leaked != 0 ||
                evidence.tma_manifest.maximum_live_async_objects >
                    profile.limits.max_cluster_async_objects),
           "tma_async_object_leak");
    reject(tma_required && evidence.tma_runtime.mixed_bytes != 0 &&
               !evidence.tma_runtime.mixed_tiles_proved,
           "mixed_tile_unproven");
    if (module != nullptr) {
        reject(evidence.original_ptx_sha256 != module->original_ptx_sha256,
               "original_ptx_sha256_mismatch");
        reject(evidence.transformed_ptx_sha256 !=
                   module->transformed_ptx_sha256,
               "transformed_ptx_sha256_mismatch");
        reject(evidence.cubin_sha256 != module->cubin_sha256,
               "cubin_sha256_mismatch");
        reject(evidence.sass_sha256 != module->sass_sha256,
               "sass_sha256_mismatch");

        const auto* expected_kernel = find_named(module->kernels, kernel);
        const auto* loaded_kernel = find_named(evidence.kernels, kernel);
        reject(expected_kernel == nullptr || loaded_kernel == nullptr,
               "kernel_resource_missing");
        if (expected_kernel != nullptr && loaded_kernel != nullptr) {
            reject(expected_kernel->registers != loaded_kernel->registers,
                   "register_count_mismatch");
            reject(expected_kernel->spill_store_bytes !=
                           loaded_kernel->spill_store_bytes ||
                       expected_kernel->spill_load_bytes !=
                           loaded_kernel->spill_load_bytes,
                   "spill_bytes_mismatch");
            reject(expected_kernel->static_shared_bytes !=
                           loaded_kernel->static_shared_bytes ||
                       expected_kernel->max_dynamic_shared_bytes !=
                           loaded_kernel->max_dynamic_shared_bytes,
                   "shared_memory_mismatch");
            reject(expected_kernel->block_threads !=
                           loaded_kernel->block_threads ||
                       expected_kernel->occupancy_blocks_per_sm !=
                           loaded_kernel->occupancy_blocks_per_sm,
                   "occupancy_tier_mismatch");
        }
    }

    reject(live.sm_clock_mhz != profile.conditions.sm_clock_mhz,
           "sm_clock_mismatch");
    reject(live.memory_clock_mhz != profile.conditions.memory_clock_mhz,
           "memory_clock_mismatch");
    reject(live.power_limit_mw != profile.conditions.power_limit_mw,
           "power_limit_mismatch");
    reject(live.temperature_c < profile.conditions.temperature_min_c ||
               live.temperature_c > profile.conditions.temperature_max_c,
           "temperature_out_of_range");
    reject(contract.cache_condition != profile.conditions.cache_condition ||
               contract.cache_condition_epoch == 0 ||
               contract.cache_condition_epoch <=
                   contract.latest_relevant_mutation_epoch,
           "cache_condition_unproven");
    reject(contract.concurrency_condition !=
                   profile.conditions.concurrency_condition ||
               (profile.conditions.concurrency_condition ==
                    "exclusive_process" &&
                !live.current_process_is_exclusive),
           "concurrency_condition_unproven");
    reject(!same_shape(contract.cluster_shape,
                       profile.conditions.cluster_shape),
           "cluster_shape_mismatch");

    decision.allowed = decision.reasons.empty();
    return decision;
}

}  // namespace hbfsim

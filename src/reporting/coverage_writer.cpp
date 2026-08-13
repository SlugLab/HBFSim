#include "hbfsim/coverage.hpp"
#include "hbfsim/durable_append.hpp"

#include <json.hpp>

#include <stdexcept>

namespace hbfsim {
namespace {

nlohmann::json to_json(const GateDecision& decision)
{
    if (decision.admitted_fidelity == "exact" &&
        (!decision.allowed || decision.requested_fidelity != "exact" ||
         !decision.aot_verified || !decision.validation_passed ||
         decision.exact_profile_id.empty() || decision.cubin_sha256.empty() ||
         decision.sass_sha256.empty() ||
         !decision.exact_rejection_reasons.empty() ||
         decision.manifest_schema_version != 3 ||
         decision.future_manifest.async_transform_version !=
             "sm120-future-v1" ||
         !decision.future_manifest.ambiguities.empty() ||
         decision.future_runtime.leaked != 0)) {
        throw std::logic_error("invalid exact coverage decision");
    }
    return {
        {"allowed", decision.allowed},
        {"module_id", decision.module_id},
        {"kernel", decision.kernel},
        {"ptx_target", decision.ptx_target},
        {"cubin_only", decision.cubin_only},
        {"reason", decision.reason},
        {"operation", decision.operation},
        {"inspected_parameters", decision.inspected_parameters},
        {"parameter_index", decision.parameter_index},
        {"parameter_offset", decision.parameter_offset},
        {"address", decision.address},
        {"range_policy", range_policy_name(decision.range_policy)},
        {"modeled", decision.modeled},
        {"opaque_unmodeled", decision.opaque_unmodeled},
        {"manifest_schema_version", decision.manifest_schema_version},
        {"original_ptx_sha256", decision.original_ptx_sha256},
        {"transformed_ptx_sha256", decision.transformed_ptx_sha256},
        {"aot_required_for_exact", decision.aot_required_for_exact},
        {"requested_fidelity", decision.requested_fidelity},
        {"admitted_fidelity", decision.admitted_fidelity},
        {"exact_profile_id", decision.exact_profile_id},
        {"cubin_sha256", decision.cubin_sha256},
        {"sass_sha256", decision.sass_sha256},
        {"exact_rejection_reasons", decision.exact_rejection_reasons},
        {"aot_verified", decision.aot_verified},
        {"validation_passed", decision.validation_passed},
        {"async_transform_version",
         decision.future_manifest.async_transform_version},
        {"ir_sha256", decision.future_manifest.ir_sha256},
        {"future_issued", decision.future_runtime.issued},
        {"future_issue_throttle_ns",
         decision.future_runtime.issue_throttle_ns},
        {"future_dependency_wait_ns",
         decision.future_runtime.dependency_wait_ns},
        {"future_ordering_wait_ns",
         decision.future_runtime.ordering_wait_ns},
        {"future_drained", decision.future_runtime.drained},
        {"future_leaked", decision.future_runtime.leaked},
        {"future_runtime_observed", decision.future_runtime.observed},
    };
}

}  // namespace

CoverageWriter::CoverageWriter(std::filesystem::path path)
    : path_(std::move(path))
{
}

void CoverageWriter::append(const GateDecision& decision)
{
    std::scoped_lock lock(mutex_);
    const std::string line = to_json(decision).dump() + '\n';
    append_durable_line(path_, line);
}

bool try_append_coverage(CoverageWriter& writer,
                         const GateDecision& decision) noexcept
{
    try {
        writer.append(decision);
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

bool coverage_decision_permits_launch(CoverageWriter& writer,
                                      const GateDecision& decision) noexcept
{
    // Auditability is part of the launch policy: a launch is approved only
    // when its decision is safe and durably reportable.
    return try_append_coverage(writer, decision) && decision.allowed;
}

}  // namespace hbfsim

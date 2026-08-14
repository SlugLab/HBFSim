#include "hbfsim/coverage.hpp"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <future>
#include <initializer_list>
#include <iterator>
#include <stdexcept>
#include <string>
#include <unistd.h>

namespace {

hbfsim::ModuleManifest
manifest(std::string module_id, std::string kernel,
         std::initializer_list<hbfsim::ParameterMetadata> parameters)
{
    return {
        .module_id = std::move(module_id),
        .kernel = std::move(kernel),
        .ptx_target = "sm_120",
        .instrumented = true,
        .cubin_only = false,
        .parameters = parameters,
    };
}

hbfsim::KernelLaunch
launch(std::string module_id, std::string kernel,
       std::initializer_list<hbfsim::LaunchParameter> parameters)
{
    return {
        .module_id = std::move(module_id),
        .kernel = std::move(kernel),
        .parameters = parameters,
    };
}

hbfsim::LaunchParameter pointer(std::size_t index, std::uintptr_t value)
{
    return {
        .index = index, .offset = index * 8, .width = 8, .slots = {{0, value}}};
}

void require(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

#define CHECK(expression) require(static_cast<bool>(expression), #expression)

}  // namespace

int main()
{
    std::array<std::uint8_t, 32> identity{};
    identity.front() = 0x12;
    identity.back() = 0x34;
    CHECK(hbfsim::module_id_from_identity(identity) ==
          "ptx:sha256:"
          "1200000000000000000000000000000000000000000000000000000000000034");
    CHECK(!hbfsim::uninspectable_launch_decision(true, "graph_launch").allowed);
    CHECK(hbfsim::uninspectable_launch_decision(false, "graph_launch").allowed);
    const auto timing_graph = hbfsim::uninspectable_launch_decision(
        true, false, "graph_launch");
    CHECK(timing_graph.allowed);
    CHECK(timing_graph.reason == "opaque_unmodeled_timing");
    CHECK(timing_graph.opaque_unmodeled);
    const auto capacity_graph = hbfsim::uninspectable_launch_decision(
        true, true, "graph_launch");
    CHECK(!capacity_graph.allowed);
    CHECK(capacity_graph.reason == "uninspectable_launch_path");
    hbfsim::LaunchRangeSynchronizer synchronizer;
    auto launch_lock = synchronizer.launch_guard();
    std::promise<void> registration_started;
    auto started = registration_started.get_future();
    auto registration =
        std::async(std::launch::async, [&synchronizer, &registration_started] {
            registration_started.set_value();
            auto lock = synchronizer.registration_guard();
            return true;
        });
    started.wait();
    CHECK(registration.wait_for(std::chrono::milliseconds(20)) ==
          std::future_status::timeout);
    launch_lock.unlock();
    CHECK(registration.wait_for(std::chrono::seconds(1)) ==
          std::future_status::ready);

    hbfsim::LaunchRangeSynchronizer launch_state;
    {
        auto registration_guard = launch_state.registration_guard();
    }
    auto active_launch = launch_state.launch_guard();
    launch_state.mark_launch_seen();
    auto late_registration = std::async(std::launch::async, [&launch_state] {
        try {
            auto guard = launch_state.registration_guard();
            return false;
        } catch (const std::logic_error&) {
            return true;
        }
    });
    CHECK(late_registration.wait_for(std::chrono::milliseconds(20)) ==
          std::future_status::timeout);
    active_launch.unlock();
    CHECK(late_registration.wait_for(std::chrono::seconds(1)) ==
          std::future_status::ready);
    CHECK(late_registration.get());

    {
        auto retirement = launch_state.retirement_guard();
        launch_state.reset_launch_seen();
    }
    {
        auto registration_after_retire = launch_state.registration_guard();
    }
    hbfsim::CoverageGate gate;
    gate.add_module(manifest("ptx:aaa", "same_name",
                             {{.index = 0,
                               .offset = 0,
                               .width = 8,
                               .kind = hbfsim::ParameterKind::Pointer}}));
    gate.add_module(manifest("ptx:bbb", "same_name",
                             {{.index = 0,
                               .offset = 0,
                               .width = 8,
                               .kind = hbfsim::ParameterKind::Scalar}}));
    gate.add_module({
        .module_id = "cubin:no-marker",
        .kernel = "same_name",
        .instrumented = false,
        .cubin_only = true,
    });
    gate.add_range(0x100000, 0x200000);
    CHECK(gate.has_ranges());
    gate.clear_ranges();
    CHECK(!gate.has_ranges());
    gate.add_range(0x100000, 0x200000);

    const auto safe = gate.check_launch(
        launch("ptx:aaa", "same_name", {pointer(0, 0x100100)}));
    CHECK(safe.allowed);
    CHECK(safe.module_id == "ptx:aaa");
    CHECK(safe.ptx_target == "sm_120");

    const auto collision =
        gate.check_launch(launch("", "same_name", {pointer(0, 0x100100)}));
    CHECK(!collision.allowed);
    CHECK(collision.reason == "exact_module_identity_required");

    hbfsim::CoverageGate unique_name_gate;
    unique_name_gate.add_module(
        manifest("ptx:only", "unique_name",
                 {{.index = 0,
                   .offset = 0,
                   .width = 8,
                   .kind = hbfsim::ParameterKind::Pointer}}));
    unique_name_gate.add_range(0x100000, 0x200000);
    const auto no_id = unique_name_gate.check_launch(
        launch("", "unique_name", {pointer(0, 0x100100)}));
    CHECK(!no_id.allowed);
    CHECK(no_id.reason == "exact_module_identity_required");

    const auto scalar = gate.check_launch(
        launch("ptx:bbb", "same_name", {pointer(0, 0x100100)}));
    CHECK(!scalar.allowed);
    CHECK(scalar.reason == "uninstrumented_pointer_parameter");

    const auto layout_mismatch = gate.check_launch(launch(
        "ptx:aaa", "same_name",
        {{.index = 0, .offset = 8, .width = 8, .slots = {{0, 0x100100}}}}));
    CHECK(!layout_mismatch.allowed);
    CHECK(layout_mismatch.reason == "parameter_layout_mismatch");

    gate.add_module(manifest("ptx:exact-abi", "exact_abi",
                             {{.index = 0,
                               .offset = 0,
                               .width = 8,
                               .kind = hbfsim::ParameterKind::Pointer},
                              {.index = 1,
                               .offset = 8,
                               .width = 4,
                               .kind = hbfsim::ParameterKind::Scalar}}));
    const auto incomplete_abi = gate.check_launch(
        launch("ptx:exact-abi", "exact_abi", {pointer(0, 0x100100)}));
    require(!incomplete_abi.allowed,
            "HBF launch with incomplete runtime ABI must be rejected");
    require(incomplete_abi.reason == "parameter_layout_mismatch",
            "incomplete runtime ABI must report parameter_layout_mismatch");

    bool invalid_manifest = false;
    try {
        gate.add_module(manifest("ptx:bad", "bad",
                                 {{.index = 0,
                                   .offset = 0,
                                   .width = 0,
                                   .kind = hbfsim::ParameterKind::Pointer}}));
    } catch (const std::invalid_argument&) {
        invalid_manifest = true;
    }
    CHECK(invalid_manifest);

    auto atomic = manifest("ptx:atom", "atomic_kernel",
                           {{.index = 0,
                             .offset = 0,
                             .width = 8,
                             .kind = hbfsim::ParameterKind::Pointer}});
    atomic.instrumented = false;
    atomic.unsupported_parameters.push_back(
        {.index = 0, .operation = "atom.global"});
    gate.add_module(std::move(atomic));
    const auto unsupported = gate.check_launch(
        launch("ptx:atom", "atomic_kernel", {pointer(0, 0x100200)}));
    CHECK(!unsupported.allowed);
    CHECK(unsupported.reason == "unsupported_operation");
    CHECK(unsupported.operation == "atom.global");

    gate.add_module(
        manifest("ptx:aggregate", "aggregate_kernel",
                 {{.index = 0,
                   .offset = 0,
                   .width = 24,
                   .kind = hbfsim::ParameterKind::OpaqueAggregate}}));
    const auto aggregate = gate.check_launch(launch(
        "ptx:aggregate", "aggregate_kernel",
        {{.index = 0, .offset = 0, .width = 24, .opaque_aggregate = true}}));
    CHECK(!aggregate.allowed);
    CHECK(aggregate.reason == "opaque_aggregate_parameter");

    hbfsim::ModuleManifest cubin{
        .module_id = "cubin:123",
        .kernel = "opaque_kernel",
        .ptx_target = "",
        .instrumented = false,
        .cubin_only = true,
    };
    gate.add_module(std::move(cubin));
    const auto opaque = gate.check_launch(
        launch("cubin:123", "opaque_kernel", {pointer(0, 0x100300)}));
    CHECK(!opaque.allowed);
    CHECK(opaque.cubin_only);
    CHECK(opaque.reason == "cubin_only_module");

    hbfsim::CoverageGate timing_gate;
    timing_gate.add_module(manifest(
        "ptx:timing", "timing_kernel",
        {{.index = 0,
          .offset = 0,
          .width = 8,
          .kind = hbfsim::ParameterKind::Pointer}}));
    timing_gate.add_module(manifest(
        "ptx:timing-scalar", "timing_scalar",
        {{.index = 0,
          .offset = 0,
          .width = 8,
          .kind = hbfsim::ParameterKind::Scalar}}));
    timing_gate.add_module(manifest(
        "ptx:timing-aggregate", "timing_aggregate",
        {{.index = 0,
          .offset = 0,
          .width = 24,
          .kind = hbfsim::ParameterKind::OpaqueAggregate}}));
    auto timing_atomic = manifest(
        "ptx:timing-atomic", "timing_atomic",
        {{.index = 0,
          .offset = 0,
          .width = 8,
          .kind = hbfsim::ParameterKind::Pointer}});
    timing_atomic.instrumented = false;
    timing_atomic.unsupported_parameters.push_back(
        {.index = 0, .operation = "atom.global"});
    timing_gate.add_module(std::move(timing_atomic));
    timing_gate.add_module({
        .module_id = "cubin:timing",
        .kernel = "timing_opaque",
        .instrumented = false,
        .cubin_only = true,
    });
    timing_gate.add_range(0x300000, 0x400000,
                          hbfsim::RangePolicy::TimingBacked);
    CHECK(!timing_gate.has_capacity_ranges());

    const auto timing_modeled = timing_gate.check_launch(
        launch("ptx:timing", "timing_kernel", {pointer(0, 0x300100)}));
    CHECK(timing_modeled.allowed);
    CHECK(timing_modeled.modeled);
    CHECK(!timing_modeled.opaque_unmodeled);
    CHECK(timing_modeled.range_policy == hbfsim::RangePolicy::TimingBacked);

    const auto timing_missing_identity = timing_gate.check_launch(
        launch("", "timing_kernel", {pointer(0, 0x300100)}));
    CHECK(timing_missing_identity.allowed);
    CHECK(!timing_missing_identity.modeled);
    CHECK(timing_missing_identity.opaque_unmodeled);
    CHECK(timing_missing_identity.reason == "opaque_unmodeled_timing");

    const auto timing_missing_module = timing_gate.check_launch(
        launch("cubin:missing", "missing", {pointer(0, 0x300100)}));
    CHECK(timing_missing_module.allowed);
    CHECK(timing_missing_module.opaque_unmodeled);
    CHECK(timing_missing_module.operation == "opaque_pointer_access");

    const auto timing_cubin = timing_gate.check_launch(
        launch("cubin:timing", "timing_opaque", {pointer(0, 0x300100)}));
    CHECK(timing_cubin.allowed);
    CHECK(timing_cubin.opaque_unmodeled);
    CHECK(timing_cubin.cubin_only);

    const auto timing_unsupported = timing_gate.check_launch(launch(
        "ptx:timing-atomic", "timing_atomic", {pointer(0, 0x300100)}));
    CHECK(timing_unsupported.allowed);
    CHECK(timing_unsupported.opaque_unmodeled);
    CHECK(timing_unsupported.operation == "atom.global");

    const auto timing_scalar = timing_gate.check_launch(launch(
        "ptx:timing-scalar", "timing_scalar", {pointer(0, 0x300100)}));
    CHECK(timing_scalar.allowed);
    CHECK(timing_scalar.opaque_unmodeled);

    const auto timing_aggregate = timing_gate.check_launch(launch(
        "ptx:timing-aggregate", "timing_aggregate",
        {{.index = 0,
          .offset = 0,
          .width = 24,
          .opaque_aggregate = true}}));
    CHECK(timing_aggregate.allowed);
    CHECK(timing_aggregate.opaque_unmodeled);

    hbfsim::CoverageGate capacity_gate;
    capacity_gate.add_module({
        .module_id = "cubin:capacity",
        .kernel = "capacity_opaque",
        .instrumented = false,
        .cubin_only = true,
    });
    capacity_gate.add_range(0x500000, 0x600000,
                            hbfsim::RangePolicy::CapacityUnbacked);
    CHECK(capacity_gate.has_capacity_ranges());
    const auto capacity_opaque = capacity_gate.check_launch(launch(
        "cubin:capacity", "capacity_opaque", {pointer(0, 0x500100)}));
    CHECK(!capacity_opaque.allowed);
    CHECK(!capacity_opaque.opaque_unmodeled);
    CHECK(capacity_opaque.reason == "cubin_only_module");
    CHECK(capacity_opaque.range_policy ==
          hbfsim::RangePolicy::CapacityUnbacked);

    const auto hbm = gate.check_launch(
        launch("missing", "opaque_kernel", {pointer(0, 0x900000)}));
    CHECK(hbm.allowed);

    const auto parsed = hbfsim::module_manifest_from_json(R"({
      "manifest_schema_version":2,
      "module_id":"ptx:json", "kernel":"json_kernel", "ptx_target":"sm_120",
      "original_ptx_sha256":"2222222222222222222222222222222222222222222222222222222222222222",
      "transformed_ptx_sha256":"3333333333333333333333333333333333333333333333333333333333333333",
      "aot_required_for_exact":true,
      "instrumented":false, "cubin_only":false,
      "parameters":[{"index":0,"offset":0,"width":8,"kind":"pointer"}],
      "unsupported_parameters":[{"index":0,"operation":"atom.global"}]
    })");
    CHECK(parsed.module_id == "ptx:json");
    CHECK(parsed.manifest_schema_version == 2);
    CHECK(parsed.original_ptx_sha256 == std::string(64, '2'));
    CHECK(parsed.transformed_ptx_sha256 == std::string(64, '3'));
    CHECK(parsed.aot_required_for_exact);
    CHECK(parsed.parameters.front().kind == hbfsim::ParameterKind::Pointer);

    const auto parsed_v3 = hbfsim::module_manifest_from_json(R"({
      "manifest_schema_version":3,
      "module_id":"ptx:v3", "kernel":"v3_kernel", "ptx_target":"sm_120",
      "original_ptx_sha256":"2222222222222222222222222222222222222222222222222222222222222222",
      "transformed_ptx_sha256":"3333333333333333333333333333333333333333333333333333333333333333",
      "aot_required_for_exact":true,
      "instrumented":true, "cubin_only":false,
      "async_transform_version":"sm120-future-v1",
      "ir_sha256":"6666666666666666666666666666666666666666666666666666666666666666",
      "instruction_table":[{"instruction_id":1,"source_line":8,"bytes":4,
        "opcode":"ld.global.u32","memory_kind":"load"}],
      "maximum_live_futures":{"thread":2,"warp":64,"cta":256,"cluster":2048},
      "ambiguities":[],
      "parameters":[{"index":0,"offset":0,"width":8,"kind":"pointer"}],
      "unsupported_parameters":[]
    })");
    CHECK(parsed_v3.future_manifest.manifest_schema_version == 3);
    CHECK(parsed_v3.future_manifest.async_transform_version ==
          "sm120-future-v1");
    CHECK(parsed_v3.future_manifest.instructions.size() == 1);
    CHECK(parsed_v3.future_manifest.maximum_live.thread_futures == 2);
    CHECK(parsed_v3.future_manifest.ambiguities.empty());

    const auto parsed_v4 = hbfsim::module_manifest_from_json(R"({
      "manifest_schema_version":4,
      "module_id":"ptx:v4", "kernel":"v4_kernel", "ptx_target":"sm_120",
      "original_ptx_sha256":"2222222222222222222222222222222222222222222222222222222222222222",
      "transformed_ptx_sha256":"3333333333333333333333333333333333333333333333333333333333333333",
      "aot_required_for_exact":true,
      "instrumented":true, "cubin_only":false,
      "async_transform_version":"sm120-future-v1",
      "ir_sha256":"6666666666666666666666666666666666666666666666666666666666666666",
      "instruction_table":[{"instruction_id":1,"source_line":8,"bytes":4,
        "opcode":"ld.global.u32","memory_kind":"load"}],
      "maximum_live_futures":{"thread":2,"warp":64,"cta":256,"cluster":2048},
      "ambiguities":[],
      "tma_transform_version":"sm120-tma-v1",
      "tma_ir_sha256":"7777777777777777777777777777777777777777777777777777777777777777",
      "tensormap_parameters":[0],
      "descriptor_instruction_ids":[5],
      "barrier_instruction_ids":[6,8,9],
      "bulk_group_instruction_ids":[11,12],
      "tma_instruction_table":[{"instruction_id":7,"source_line":20,
        "direction":"global_to_shared","mode":"tile","dimensions":1,
        "completion":"mbarrier","multicast_mask":0,
        "descriptor_generation":1}],
      "maximum_live_async_objects":1,
      "tma_ambiguities":[], "tensormap_provenance_required":true,
      "parameters":[{"index":0,"offset":0,"width":8,"kind":"pointer"}],
      "unsupported_parameters":[]
    })");
    CHECK(parsed_v4.manifest_schema_version == 4);
    CHECK(parsed_v4.tma_manifest.async_transform_version == "sm120-tma-v1");
    CHECK(parsed_v4.tma_manifest.tensormap_parameters ==
          std::vector<std::uint32_t>{0});
    CHECK(parsed_v4.tma_manifest.instructions.size() == 1);
    CHECK(parsed_v4.tma_manifest.maximum_live_async_objects == 1);
    CHECK(parsed_v4.tma_manifest.ambiguities.empty());

    const auto test_id = std::to_string(getpid());
    const auto report = std::filesystem::temp_directory_path() /
                        ("hbfsim-coverage-gate-test-" + test_id + ".json");
    std::filesystem::remove(report);
    hbfsim::CoverageWriter writer(report);
    writer.append(unsupported);
    writer.append(timing_cubin);
    auto admitted_exact = safe;
    admitted_exact.requested_fidelity = "exact";
    admitted_exact.admitted_fidelity = "exact";
    admitted_exact.exact_profile_id = "profile";
    admitted_exact.cubin_sha256 = std::string(64, '4');
    admitted_exact.sass_sha256 = std::string(64, '5');
    admitted_exact.aot_verified = true;
    admitted_exact.validation_passed = true;
    admitted_exact.post_run_validation_passed = true;
    admitted_exact.routing_program_sha256 = std::string(64, '8');
    admitted_exact.raw_training_sha256 = std::string(64, '9');
    admitted_exact.raw_holdout_sha256 = std::string(64, 'a');
    admitted_exact.manifest_schema_version = 4;
    admitted_exact.future_manifest = parsed_v4.future_manifest;
    admitted_exact.future_runtime = {
        .issued = 9,
        .issue_throttle_ns = 10,
        .dependency_wait_ns = 11,
        .ordering_wait_ns = 12,
        .drained = 9,
        .leaked = 0,
        .faults = 0,
        .observed = true,
    };
    admitted_exact.tma_manifest = parsed_v4.tma_manifest;
    admitted_exact.tma_runtime = {
        .issued = 4,
        .hbm_bytes = 32,
        .hbf_bytes = 64,
        .oob_bytes = 0,
        .mixed_bytes = 16,
        .fanout_targets = 7,
        .barrier_waits = 3,
        .group_read_waits = 2,
        .group_full_waits = 1,
        .stale_generations = 0,
        .faults = 0,
        .leaked = 0,
        .mixed_tiles_proved = true,
        .observed = true,
    };
    admitted_exact.channel_runtime = {
        .routing_version = 1,
        .routing_program_sha256 = std::string(64, '8'),
        .gnic_count = 4,
        .gpc_count = 2,
        .maximum_gnic_outstanding = 4,
        .maximum_gpc_outstanding = 2,
        .gnic_requests = 7,
        .gpc_requests = 5,
        .observed = true,
    };
    writer.append(admitted_exact);
    std::ifstream input(report);
    const std::string json{std::istreambuf_iterator<char>(input), {}};
    CHECK(json.find("ptx:atom") != std::string::npos);
    CHECK(json.find("atom.global") != std::string::npos);
    CHECK(json.find("opaque_unmodeled_timing") != std::string::npos);
    CHECK(json.find("timing_backed") != std::string::npos);
    CHECK(json.find("\"modeled\":false") != std::string::npos);
    CHECK(json.find("\"requested_fidelity\":\"exact\"") !=
          std::string::npos);
    CHECK(json.find("\"admitted_fidelity\":\"exact\"") !=
          std::string::npos);
    CHECK(json.find("\"post_run_validation_passed\":true") !=
          std::string::npos);
    CHECK(json.find("\"channel_gnic_count\":4") != std::string::npos);
    CHECK(json.find("\"exact_profile_id\":\"profile\"") !=
          std::string::npos);
    CHECK(json.find("\"exact_rejection_reasons\":[]") !=
          std::string::npos);
    CHECK(json.find("\"future_issued\":9") != std::string::npos);
    CHECK(json.find("\"future_issue_throttle_ns\":10") !=
          std::string::npos);
    CHECK(json.find("\"future_dependency_wait_ns\":11") !=
          std::string::npos);
    CHECK(json.find("\"future_ordering_wait_ns\":12") !=
          std::string::npos);
    CHECK(json.find("\"future_drained\":9") != std::string::npos);
    CHECK(json.find("\"future_leaked\":0") != std::string::npos);
    CHECK(json.find("\"tma_hbm_bytes\":32") != std::string::npos);
    CHECK(json.find("\"tma_hbf_bytes\":64") != std::string::npos);
    CHECK(json.find("\"tma_oob_bytes\":0") != std::string::npos);
    CHECK(json.find("\"tma_fanout_targets\":7") != std::string::npos);
    CHECK(json.find("\"tma_barrier_waits\":3") != std::string::npos);
    CHECK(json.find("\"tma_group_read_waits\":2") != std::string::npos);
    CHECK(json.find("\"tma_group_full_waits\":1") != std::string::npos);
    CHECK(json.find("\"tma_stale_generations\":0") != std::string::npos);
    std::filesystem::remove(report);

    auto false_exact = admitted_exact;
    false_exact.aot_verified = false;
    bool false_exact_rejected = false;
    try {
        hbfsim::CoverageWriter invariant_writer(report);
        invariant_writer.append(false_exact);
    } catch (const std::logic_error&) {
        false_exact_rejected = true;
    }
    CHECK(false_exact_rejected);

    auto prelaunch_only = admitted_exact;
    prelaunch_only.post_run_validation_passed = false;
    bool prelaunch_exact_rejected = false;
    try {
        hbfsim::CoverageWriter invariant_writer(report);
        invariant_writer.append(prelaunch_only);
    } catch (const std::logic_error&) {
        prelaunch_exact_rejected = true;
    }
    CHECK(prelaunch_exact_rejected);

    auto prelaunch_candidate = admitted_exact;
    prelaunch_candidate.admitted_fidelity = "calibrated_emulation";
    prelaunch_candidate.post_run_validation_passed = false;
    prelaunch_candidate.future_runtime = {};
    prelaunch_candidate.tma_runtime = {};
    prelaunch_candidate.channel_runtime = admitted_exact.channel_runtime;
    prelaunch_candidate.channel_runtime.maximum_gnic_outstanding = 0;
    prelaunch_candidate.channel_runtime.maximum_gpc_outstanding = 0;
    prelaunch_candidate.channel_runtime.gnic_requests = 0;
    prelaunch_candidate.channel_runtime.gpc_requests = 0;
    const hbfsim::ExactPostRunEvidence post_run{
        .future_runtime = admitted_exact.future_runtime,
        .tma_runtime = admitted_exact.tma_runtime,
        .channel_runtime = admitted_exact.channel_runtime,
    };
    const auto finalized =
        hbfsim::exact_post_run_decision(prelaunch_candidate, post_run);
    CHECK(finalized.allowed);
    CHECK(finalized.admitted_fidelity == "exact");
    CHECK(finalized.post_run_validation_passed);
    writer.append(finalized);
    auto failed_post_run = post_run;
    failed_post_run.future_runtime.faults = 1;
    const auto rejected_post_run =
        hbfsim::exact_post_run_decision(prelaunch_candidate, failed_post_run);
    CHECK(!rejected_post_run.allowed);
    CHECK(rejected_post_run.admitted_fidelity == "calibrated_emulation");
    CHECK(rejected_post_run.exact_rejection_reasons ==
          std::vector<std::string>{"post_run_future_failure"});

    auto pending = safe;
    pending.allowed = false;
    pending.reason = "exact_admission_failed";
    pending.requested_fidelity = "exact";
    pending.admitted_fidelity = "calibrated_emulation";
    pending.exact_profile_id = "pending-profile";
    pending.exact_rejection_reasons = {"profile_not_validated"};
    pending.validation_passed = false;
    pending.post_run_validation_passed = false;
    hbfsim::CoverageWriter pending_writer(report);
    pending_writer.append(pending);
    std::ifstream pending_input(report);
    const std::string pending_json{std::istreambuf_iterator<char>(pending_input),
                                   {}};
    CHECK(pending_json.find("\"admitted_fidelity\":\"calibrated_emulation\"") !=
          std::string::npos);
    CHECK(pending_json.find("\"exact_rejection_reasons\":[\"profile_not_validated\"]") !=
          std::string::npos);
    std::filesystem::remove(report);

    bool write_failed = false;
    try {
        hbfsim::CoverageWriter unwritable("/proc/1/hbfsim-coverage.json");
        unwritable.append(safe);
    } catch (const std::runtime_error&) {
        write_failed = true;
    }
    CHECK(write_failed);
    hbfsim::CoverageWriter still_unwritable("/proc/1/hbfsim-coverage.json");
    CHECK(!hbfsim::try_append_coverage(still_unwritable, safe));
    CHECK(!hbfsim::coverage_decision_permits_launch(still_unwritable, safe));

    const auto launch_report =
        std::filesystem::temp_directory_path() /
        ("hbfsim-launch-policy-test-" + test_id + ".json");
    std::filesystem::remove(launch_report);
    hbfsim::CoverageWriter launch_writer(launch_report);
    CHECK(hbfsim::coverage_decision_permits_launch(launch_writer, safe));
    CHECK(
        !hbfsim::coverage_decision_permits_launch(launch_writer, unsupported));
    for (int index = 0; index < 128; ++index) {
        launch_writer.append(safe);
    }
    {
        std::ifstream jsonl(launch_report);
        std::string line;
        std::size_t lines = 0;
        while (std::getline(jsonl, line)) {
            CHECK(!line.empty() && line.front() == '{' && line.back() == '}');
            CHECK(line.find("\"allowed\":") != std::string::npos);
            CHECK(line.find("\"reason\":") != std::string::npos);
            ++lines;
        }
        CHECK(lines == 130);
    }
    hbfsim::CoverageWriter reopened_writer(launch_report);
    reopened_writer.append(unsupported);
    std::ifstream appended_jsonl(launch_report);
    CHECK(std::count(std::istreambuf_iterator<char>(appended_jsonl),
                     std::istreambuf_iterator<char>(), '\n') == 131);
    std::filesystem::remove(launch_report);
}

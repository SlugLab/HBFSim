#include "hbfsim/coverage.hpp"

#include <cassert>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <iterator>
#include <stdexcept>
#include <string>

namespace {

hbfsim::ModuleManifest manifest(
    std::string module_id, std::string kernel,
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

hbfsim::KernelLaunch launch(
    std::string module_id, std::string kernel,
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
    return {.index = index, .offset = index * 8, .width = 8, .slots = {{0, value}}};
}

}  // namespace

int main()
{
    hbfsim::CoverageGate gate;
    gate.add_module(manifest(
        "ptx:aaa", "same_name",
        {{.index = 0, .offset = 0, .width = 8,
          .kind = hbfsim::ParameterKind::Pointer}}));
    gate.add_module(manifest(
        "ptx:bbb", "same_name",
        {{.index = 0, .offset = 0, .width = 8,
          .kind = hbfsim::ParameterKind::Scalar}}));
    gate.add_range(0x100000, 0x200000);

    const auto safe = gate.check_launch(
        launch("ptx:aaa", "same_name", {pointer(0, 0x100100)}));
    assert(safe.allowed);
    assert(safe.module_id == "ptx:aaa");
    assert(safe.ptx_target == "sm_120");

    const auto collision =
        gate.check_launch(launch("", "same_name", {pointer(0, 0x100100)}));
    assert(!collision.allowed);
    assert(collision.reason == "ambiguous_module_identity");

    const auto scalar = gate.check_launch(
        launch("ptx:bbb", "same_name", {pointer(0, 0x100100)}));
    assert(!scalar.allowed);
    assert(scalar.reason == "uninstrumented_pointer_parameter");

    auto atomic = manifest(
        "ptx:atom", "atomic_kernel",
        {{.index = 0, .offset = 0, .width = 8,
          .kind = hbfsim::ParameterKind::Pointer}});
    atomic.instrumented = false;
    atomic.unsupported_parameters.push_back({.index = 0, .operation = "atom.global"});
    gate.add_module(std::move(atomic));
    const auto unsupported = gate.check_launch(
        launch("ptx:atom", "atomic_kernel", {pointer(0, 0x100200)}));
    assert(!unsupported.allowed);
    assert(unsupported.reason == "unsupported_operation");
    assert(unsupported.operation == "atom.global");

    gate.add_module(manifest(
        "ptx:aggregate", "aggregate_kernel",
        {{.index = 0, .offset = 0, .width = 24,
          .kind = hbfsim::ParameterKind::OpaqueAggregate}}));
    const auto aggregate = gate.check_launch(launch(
        "ptx:aggregate", "aggregate_kernel",
        {{.index = 0, .offset = 0, .width = 24, .opaque_aggregate = true}}));
    assert(!aggregate.allowed);
    assert(aggregate.reason == "opaque_aggregate_parameter");

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
    assert(!opaque.allowed);
    assert(opaque.cubin_only);
    assert(opaque.reason == "cubin_only_module");

    const auto hbm = gate.check_launch(
        launch("missing", "opaque_kernel", {pointer(0, 0x900000)}));
    assert(hbm.allowed);

    const auto parsed = hbfsim::module_manifest_from_json(R"({
      "module_id":"ptx:json", "kernel":"json_kernel", "ptx_target":"sm_120",
      "instrumented":false, "cubin_only":false,
      "parameters":[{"index":0,"offset":0,"width":8,"kind":"pointer"}],
      "unsupported_parameters":[{"index":0,"operation":"atom.global"}]
    })");
    assert(parsed.module_id == "ptx:json");
    assert(parsed.parameters.front().kind == hbfsim::ParameterKind::Pointer);

    const auto report =
        std::filesystem::temp_directory_path() / "hbfsim-coverage-gate-test.json";
    hbfsim::CoverageWriter writer(report);
    writer.append(unsupported);
    std::ifstream input(report);
    const std::string json{std::istreambuf_iterator<char>(input), {}};
    assert(json.find("ptx:atom") != std::string::npos);
    assert(json.find("atom.global") != std::string::npos);
    std::filesystem::remove(report);

    bool write_failed = false;
    try {
        hbfsim::CoverageWriter unwritable("/proc/1/hbfsim-coverage.json");
        unwritable.append(safe);
    } catch (const std::runtime_error&) {
        write_failed = true;
    }
    assert(write_failed);
    hbfsim::CoverageWriter still_unwritable("/proc/1/hbfsim-coverage.json");
    assert(!hbfsim::try_append_coverage(still_unwritable, safe));
    assert(!hbfsim::coverage_decision_permits_launch(still_unwritable, safe));

    const auto launch_report = std::filesystem::temp_directory_path() /
                               "hbfsim-launch-policy-test.json";
    hbfsim::CoverageWriter launch_writer(launch_report);
    assert(hbfsim::coverage_decision_permits_launch(launch_writer, safe));
    assert(!hbfsim::coverage_decision_permits_launch(launch_writer, unsupported));
    std::filesystem::remove(launch_report);
}

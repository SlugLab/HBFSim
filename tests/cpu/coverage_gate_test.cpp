#include "hbfsim/coverage.hpp"

#include <cassert>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <initializer_list>
#include <iterator>
#include <string>
#include <vector>

namespace {

hbfsim::ModuleManifest instrumented_manifest(
    std::string name,
    std::initializer_list<std::size_t> pointer_parameters)
{
    return {
        .name = std::move(name),
        .instrumented = true,
        .pointer_parameters = pointer_parameters,
    };
}

hbfsim::KernelLaunch launch(
    std::string module,
    std::initializer_list<std::uintptr_t> arguments)
{
    return {
        .module = std::move(module),
        .kernel = module,
        .arguments = arguments,
    };
}

}  // namespace

int main()
{
    hbfsim::CoverageGate gate;
    assert(!gate.has_ranges());
    gate.add_module(instrumented_manifest("safe", {0, 2}));
    gate.add_range(0x100000, 0x200000);
    assert(gate.has_ranges());

    const auto safe = gate.check_launch(launch("safe", {0x100100}));
    assert(safe.allowed);
    assert(safe.inspected_parameters == 1);
    const auto cubin = gate.check_launch(launch("cubin_only", {0x100100}));
    assert(!cubin.allowed);
    assert(cubin.reason == "uninstrumented_module");
    assert(gate.check_launch(launch("cubin_only", {0x900000})).allowed);

    const auto unknown_parameter =
        gate.check_launch(launch("safe", {0x900000, 0x100200}));
    assert(!unknown_parameter.allowed);
    assert(unknown_parameter.reason == "uninstrumented_pointer_parameter");

    const auto later_unknown_parameter =
        gate.check_launch(launch("safe", {0x100100, 0x100200}));
    assert(!later_unknown_parameter.allowed);
    assert(later_unknown_parameter.parameter_index == 1);

    auto unsupported = instrumented_manifest("atomic", {0});
    unsupported.unsupported_parameters.push_back(
        {.index = 0, .operation = "atom.global"});
    gate.add_module(std::move(unsupported));
    const auto atomic = gate.check_launch(launch("atomic", {0x100300}));
    assert(!atomic.allowed);
    assert(atomic.reason == "unsupported_operation");
    assert(atomic.operation == "atom.global");

    const auto report =
        std::filesystem::temp_directory_path() / "hbfsim-coverage-gate-test.json";
    hbfsim::CoverageWriter writer(report);
    writer.append(cubin);
    writer.append(atomic);
    std::ifstream input(report);
    const std::string json{std::istreambuf_iterator<char>(input), {}};
    assert(json.find("cubin_only") != std::string::npos);
    assert(json.find("atom.global") != std::string::npos);
    assert(json.find("\"unsafe_launches\":2") != std::string::npos);
    std::filesystem::remove(report);
}

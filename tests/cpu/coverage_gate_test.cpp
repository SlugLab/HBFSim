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

    const auto hbm = gate.check_launch(
        launch("missing", "opaque_kernel", {pointer(0, 0x900000)}));
    CHECK(hbm.allowed);

    const auto parsed = hbfsim::module_manifest_from_json(R"({
      "module_id":"ptx:json", "kernel":"json_kernel", "ptx_target":"sm_120",
      "instrumented":false, "cubin_only":false,
      "parameters":[{"index":0,"offset":0,"width":8,"kind":"pointer"}],
      "unsupported_parameters":[{"index":0,"operation":"atom.global"}]
    })");
    CHECK(parsed.module_id == "ptx:json");
    CHECK(parsed.parameters.front().kind == hbfsim::ParameterKind::Pointer);

    const auto test_id = std::to_string(getpid());
    const auto report = std::filesystem::temp_directory_path() /
                        ("hbfsim-coverage-gate-test-" + test_id + ".json");
    std::filesystem::remove(report);
    hbfsim::CoverageWriter writer(report);
    writer.append(unsupported);
    std::ifstream input(report);
    const std::string json{std::istreambuf_iterator<char>(input), {}};
    CHECK(json.find("ptx:atom") != std::string::npos);
    CHECK(json.find("atom.global") != std::string::npos);
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

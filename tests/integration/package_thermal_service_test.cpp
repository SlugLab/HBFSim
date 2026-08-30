#include "../../src/cuda_runtime/context.hpp"
#include "../../src/host_service/control_layout.hpp"

#include <hbfsim/api.h>
#include <hbfsim/protocol.hpp>

#include <cassert>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <iostream>
#include <string>
#include <sys/mman.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

namespace {

using hbfsim::HbfCompletion;
using hbfsim::HbfRequest;

void replace_once(std::string& text, const std::string& from,
                  const std::string& to)
{
    const auto position = text.find(from);
    assert(position != std::string::npos);
    text.replace(position, from.size(), to);
}

std::filesystem::path write_fixture(const std::filesystem::path& source,
                                    const std::filesystem::path& target,
                                    bool package_profile)
{
    std::ifstream input(source);
    assert(input.good());
    std::string text{std::istreambuf_iterator<char>(input), {}};
    if (package_profile) {
        replace_once(text, "\"stage\": \"read_only\"",
                     "\"stage\": \"active\"");
    } else {
        replace_once(text, "\"channels\": 32", "\"channels\": 2");
        replace_once(text, "\"planes_per_die\": 4",
                     "\"planes_per_die\": 2");
    }
    std::ofstream output(target, std::ios::trunc);
    output << text;
    assert(output.good());
    return target;
}

std::filesystem::path write_shutdown_fixture(
    const std::filesystem::path& source,
    const std::filesystem::path& target)
{
    std::ifstream input(source);
    assert(input.good());
    std::string text{std::istreambuf_iterator<char>(input), {}};
    replace_once(text, "\"stage\": \"read_only\"",
                 "\"stage\": \"active\"");
    replace_once(text, "\"shutdown_on_c\": {\"value\": 100.0",
                 "\"shutdown_on_c\": {\"value\": 94.0");
    std::ofstream output(target, std::ios::trunc);
    output << text;
    assert(output.good());
    return target;
}

hbfsim_options base_options(const std::string& profile,
                            const std::string& report,
                            std::uint64_t timeout_ns = 500'000'000)
{
    return hbfsim_options{
        .profile_path = profile.c_str(),
        .report_dir = report.c_str(),
        .mode = HBFSIM_MODEL_REFERENCE,
        .ring_capacity = 8,
        .request_timeout_ns = timeout_ns,
    };
}

HbfRequest request(std::uint64_t id)
{
    return HbfRequest{
        .request_id = id,
        .sequence = 0,
        .arrival_ns = 0,
        .logical_address = 0,
        .deadline_ns = 0,
        .bytes = 16'384,
        .range_id = 1,
        .stream_id = 0,
        .operation = static_cast<std::uint32_t>(
            hbfsim::RequestOperation::Read),
        .page_generation = 1,
        .flags = 0,
    };
}

HbfCompletion submit(hbfsim_context* context, std::uint64_t id,
                     const char* phase)
{
    HbfCompletion completion{};
    const auto status = hbfsim::runtime::submit_for_test(
        context, request(id), &completion);
    if (status != HBFSIM_OK) {
        std::cerr << "submit failed in " << phase << ": " << status << '\n';
    }
    assert(status == HBFSIM_OK);
    assert(completion.status ==
           static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready));
    return completion;
}

bool same_timing(const HbfCompletion& left, const HbfCompletion& right)
{
    return left.modeled_completion_ns == right.modeled_completion_ns &&
           left.modeled_ns == right.modeled_ns &&
           left.service_ns == right.service_ns &&
           left.status == right.status;
}

}  // namespace

int main(int argc, char** argv)
{
    assert(argc == 4);
    const std::filesystem::path root = argv[1];
    const std::string daemon = argv[2];
    const std::string plugin = argv[3];
    const auto temporary = std::filesystem::temp_directory_path() /
                           ("hbfsim-package-service-" +
                            std::to_string(static_cast<long long>(::getpid())));
    std::filesystem::create_directories(temporary);
    const auto device_profile_path = write_fixture(
        root / "configs/profiles/nominal.json",
        temporary / "device.json", false);
    const auto package_profile_path = write_fixture(
        root / "configs/package_thermal/synthetic-8hi.json",
        temporary / "package-active.json", true);
    const std::string device_profile = device_profile_path.string();
    const std::string report = temporary.string();

    auto base = base_options(device_profile, report);
    hbfsim_context* legacy = nullptr;
    assert(hbfsim::runtime::create_cpu_test_context(
               &base, daemon.c_str(), &legacy) == HBFSIM_OK);
    const auto legacy_completion = submit(legacy, 1, "legacy");
    hbfsim_context_destroy(legacy);

    const std::string absent_profile =
        (temporary / "intentionally-absent-profile.json").string();
    const std::string absent_model =
        (temporary / "intentionally-absent-model.so").string();
    hbfsim_options_v2 off{
        .struct_size = sizeof(hbfsim_options_v2),
        .version = HBFSIM_OPTIONS_V2_VERSION,
        .base = base,
        .package_thermal_mode = HBFSIM_PACKAGE_THERMAL_OFF,
        .package_thermal_stage = 999,
        .package_thermal_model_kind = 999,
        .reserved = 999,
        .package_thermal_profile_path = absent_profile.c_str(),
        .package_thermal_model_path = absent_model.c_str(),
    };
    hbfsim_context* disabled = nullptr;
    assert(hbfsim::runtime::create_cpu_test_context_v2(
               &off, daemon.c_str(), &disabled) == HBFSIM_OK);
    const auto off_completion = submit(disabled, 1, "v2-off");
    assert(same_timing(legacy_completion, off_completion));
    hbfsim_context_destroy(disabled);

    base = base_options(device_profile, report, 100'000'000);
    const std::string package_profile = package_profile_path.string();
    hbfsim_options_v2 active{
        .struct_size = sizeof(hbfsim_options_v2),
        .version = HBFSIM_OPTIONS_V2_VERSION,
        .base = base,
        .package_thermal_mode = HBFSIM_PACKAGE_THERMAL_PACKAGE_RC,
        .package_thermal_stage = HBFSIM_PACKAGE_THERMAL_ACTIVE,
        .package_thermal_model_kind = HBFSIM_PACKAGE_THERMAL_MODEL_PLUGIN,
        .reserved = 0,
        .package_thermal_profile_path = package_profile.c_str(),
        .package_thermal_model_path = plugin.c_str(),
    };
    hbfsim_context* coupled = nullptr;
    assert(hbfsim::runtime::create_cpu_test_context_v2(
               &active, daemon.c_str(), &coupled) == HBFSIM_OK);
    const auto first_active_completion = submit(coupled, 1, "active-first");

    const auto fd = hbfsim::runtime::control_fd_for_test(coupled);
    struct stat status {};
    assert(::fstat(fd, &status) == 0);
    auto* mapping = ::mmap(nullptr, static_cast<std::size_t>(status.st_size),
                           PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(mapping != MAP_FAILED);
    hbfsim::host_service::ControlView control(
        mapping, static_cast<std::size_t>(status.st_size));
    assert(control.valid());
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(2);
    while (std::chrono::steady_clock::now() < deadline &&
           hbfsim::host_service::atomic_load(
               control.header()->thermal_admission_open,
               std::memory_order_acquire) != 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    assert(hbfsim::host_service::atomic_load(
               control.header()->thermal_mode,
               std::memory_order_acquire) == 1);
    assert(hbfsim::host_service::atomic_load(
               control.header()->thermal_stage,
               std::memory_order_acquire) == 3);
    assert(hbfsim::host_service::atomic_load(
               control.header()->thermal_policy_state,
               std::memory_order_acquire) == 2);
    assert(hbfsim::host_service::atomic_load(
               control.header()->thermal_admission_open,
               std::memory_order_acquire) == 0);
    const auto recovered_completion = submit(coupled, 2, "active-recovered");
    assert(recovered_completion.modeled_completion_ns >
           first_active_completion.modeled_completion_ns);
    assert(hbfsim::host_service::atomic_load(
               control.header()->thermal_blocked_requests,
               std::memory_order_acquire) >= 1);
    assert(hbfsim::host_service::atomic_load(
               control.header()->thermal_admission_open,
               std::memory_order_acquire) == 1);
    assert(hbfsim::host_service::atomic_load(
               control.header()->thermal_policy_state,
               std::memory_order_acquire) < 2);
    ::munmap(mapping, static_cast<std::size_t>(status.st_size));
    hbfsim_context_destroy(coupled);

    std::ifstream report_input(temporary / "package-thermal.json");
    assert(report_input.good());
    const std::string thermal_report{
        std::istreambuf_iterator<char>(report_input),
        std::istreambuf_iterator<char>()};
    assert(thermal_report.find("\"thermal_mode\": \"package_rc\"") !=
           std::string::npos);
    assert(thermal_report.find("\"thermal_blocked_requests\"") !=
           std::string::npos);
    assert(thermal_report.find("\"package_profile_sha256\"") !=
           std::string::npos);
    assert(thermal_report.find("\"parameter_provenance\"") !=
           std::string::npos);
    assert(thermal_report.find(
               "\"temperature_evidence\": \"model_based_projection\"") !=
           std::string::npos);
    assert(thermal_report.find("\"severe_transitions\": 0") ==
           std::string::npos);
    assert(thermal_report.find("\"time_severe_ns\": 0") ==
           std::string::npos);

    const auto shutdown_profile_path = write_shutdown_fixture(
        root / "configs/package_thermal/synthetic-8hi.json",
        temporary / "package-shutdown.json");
    const std::string shutdown_profile = shutdown_profile_path.string();
    base = base_options(device_profile, report);
    hbfsim_options_v2 shutdown_options{
        .struct_size = sizeof(hbfsim_options_v2),
        .version = HBFSIM_OPTIONS_V2_VERSION,
        .base = base,
        .package_thermal_mode = HBFSIM_PACKAGE_THERMAL_PACKAGE_RC,
        .package_thermal_stage = HBFSIM_PACKAGE_THERMAL_ACTIVE,
        .package_thermal_model_kind = HBFSIM_PACKAGE_THERMAL_MODEL_PLUGIN,
        .reserved = 0,
        .package_thermal_profile_path = shutdown_profile.c_str(),
        .package_thermal_model_path = plugin.c_str(),
    };
    hbfsim_context* terminal = nullptr;
    assert(hbfsim::runtime::create_cpu_test_context_v2(
               &shutdown_options, daemon.c_str(), &terminal) == HBFSIM_OK);
    (void)submit(terminal, 1, "shutdown-in-flight");
    const auto terminal_fd = hbfsim::runtime::control_fd_for_test(terminal);
    assert(::fstat(terminal_fd, &status) == 0);
    mapping = ::mmap(nullptr, static_cast<std::size_t>(status.st_size),
                     PROT_READ | PROT_WRITE, MAP_SHARED, terminal_fd, 0);
    assert(mapping != MAP_FAILED);
    control = hbfsim::host_service::ControlView(
        mapping, static_cast<std::size_t>(status.st_size));
    const auto shutdown_deadline = std::chrono::steady_clock::now() +
                                   std::chrono::seconds(2);
    while (std::chrono::steady_clock::now() < shutdown_deadline &&
           hbfsim::host_service::atomic_load(
               control.header()->fault, std::memory_order_acquire) == 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    assert(hbfsim::host_service::atomic_load(
               control.header()->thermal_policy_state,
               std::memory_order_acquire) == 3);
    assert(hbfsim::host_service::atomic_load(
               control.header()->thermal_flags,
               std::memory_order_acquire) ==
           hbfsim::host_service::kThermalFlagTerminalFault);
    assert(hbfsim::host_service::atomic_load(
               control.header()->fault, std::memory_order_acquire) ==
           HBFSIM_IO_ERROR);
    HbfCompletion rejected{};
    assert(hbfsim::runtime::submit_for_test(terminal, request(2), &rejected) ==
           HBFSIM_IO_ERROR);
    ::munmap(mapping, static_cast<std::size_t>(status.st_size));
    hbfsim_context_destroy(terminal);

    std::filesystem::remove_all(temporary);
    return 0;
}

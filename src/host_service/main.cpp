#include "control_layout.hpp"
#include "request_dispatcher.hpp"

#include <hbfsim/api.h>
#include <hbfsim/profile.hpp>

#if defined(HBFSIM_ENABLE_PACKAGE_THERMAL)
#include <hbfsim/package_thermal.hpp>
#endif

#if defined(HBFSIM_ENABLE_MQSIM_RUNTIME)
#include <hbfsim/mqsim_online.hpp>
#endif

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <fcntl.h>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/mman.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>

namespace {

struct Arguments {
    std::string profile;
    std::string report_dir;
    int control_fd{-1};
    std::string thermal{"off"};
    std::string thermal_stage;
    std::string package_thermal_profile;
    std::string thermal_model_kind;
    std::string thermal_model;
};

Arguments parse_arguments(int argc, char** argv)
{
    Arguments result;
    for (int index = 1; index < argc; index += 2) {
        if (index + 1 >= argc) {
            throw std::invalid_argument("missing daemon option value");
        }
        const std::string_view option(argv[index]);
        if (option == "--profile") {
            result.profile = argv[index + 1];
        } else if (option == "--report-dir") {
            result.report_dir = argv[index + 1];
        } else if (option == "--control-fd") {
            std::size_t consumed = 0;
            const auto value = std::stoll(argv[index + 1], &consumed, 10);
            if (consumed != std::string(argv[index + 1]).size() || value < 0 ||
                value > std::numeric_limits<int>::max()) {
                throw std::invalid_argument("invalid control fd");
            }
            result.control_fd = static_cast<int>(value);
        } else if (option == "--thermal") {
            result.thermal = argv[index + 1];
        } else if (option == "--thermal-stage") {
            result.thermal_stage = argv[index + 1];
        } else if (option == "--package-thermal-profile") {
            result.package_thermal_profile = argv[index + 1];
        } else if (option == "--thermal-model-kind") {
            result.thermal_model_kind = argv[index + 1];
        } else if (option == "--thermal-model") {
            result.thermal_model = argv[index + 1];
        } else {
            throw std::invalid_argument("unknown daemon option");
        }
    }
    if (result.profile.empty() || result.report_dir.empty() ||
        result.control_fd < 0) {
        throw std::invalid_argument("required daemon option is missing");
    }
    if (result.thermal != "off" && result.thermal != "package_rc") {
        throw std::invalid_argument("thermal mode must be off or package_rc");
    }
    // The off path intentionally does not validate, stat, open, or otherwise
    // inspect any package-thermal dependency path.
    if (result.thermal == "package_rc" &&
        ((result.thermal_stage != "read_only" &&
          result.thermal_stage != "shadow" &&
          result.thermal_stage != "active") ||
         (result.thermal_model_kind != "rom" &&
          result.thermal_model_kind != "plugin") ||
         result.package_thermal_profile.empty() ||
         result.thermal_model.empty())) {
        throw std::invalid_argument(
            "package_rc thermal configuration is incomplete");
    }
    return result;
}

std::uint64_t monotonic_ns()
{
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
}

hbfsim::HbfCompletion unsupported_completion(
    const hbfsim::HbfRequest& request)
{
    return hbfsim::HbfCompletion{
        .request_id = request.request_id,
        .modeled_completion_ns = 0,
        .modeled_ns = 0,
        .service_ns = 0,
        .cache_frame_address = 0,
        .page_generation = request.page_generation,
        .status = static_cast<std::uint32_t>(
            hbfsim::RequestStatus::Unsupported),
        .checksum = 0,
        .reserved = 0,
    };
}

#if defined(HBFSIM_ENABLE_PACKAGE_THERMAL)
namespace thermal = hbfsim::package_thermal;

thermal::ThermalStage thermal_stage(std::string_view value)
{
    if (value == "read_only") return thermal::ThermalStage::ReadOnly;
    if (value == "shadow") return thermal::ThermalStage::Shadow;
    if (value == "active") return thermal::ThermalStage::Active;
    throw thermal::ThermalError("invalid package thermal stage");
}

#if defined(HBFSIM_ENABLE_MQSIM_RUNTIME)
thermal::NandOperation nand_operation(hbfsim::MediaActivityKind kind)
{
    switch (kind) {
    case hbfsim::MediaActivityKind::Read:
        return thermal::NandOperation::Read;
    case hbfsim::MediaActivityKind::Program:
        return thermal::NandOperation::Program;
    case hbfsim::MediaActivityKind::Erase:
        return thermal::NandOperation::Erase;
    }
    throw thermal::ThermalError("unknown MQSim media activity kind");
}
#endif

std::uint32_t policy_state(thermal::ThermalMode mode)
{
    return static_cast<std::uint32_t>(mode);
}

void publish_thermal_decision(
    hbfsim::host_service::ControlView control,
    thermal::ThermalStage stage,
    const thermal::PolicyDecision& decision,
    std::uint64_t& next_generation)
{
    if (next_generation == 0 || (next_generation & 1U) != 0 ||
        next_generation > std::numeric_limits<std::uint64_t>::max() - 2) {
        throw thermal::ThermalError("thermal control generation exhausted");
    }
    const auto scale = decision.service_scale <= 0.0
                           ? 0U
                           : static_cast<std::uint32_t>(std::llround(
                                 decision.service_scale * 1'000'000.0));
    if (scale > hbfsim::host_service::kThermalScaleOnePpm ||
        (scale == 0 && decision.admission_open)) {
        throw thermal::ThermalError("thermal policy service scale is invalid");
    }
    const auto terminal =
        stage == thermal::ThermalStage::Active &&
        decision.effective_mode == thermal::ThermalMode::Shutdown;
    auto* header = control.header();
    hbfsim::host_service::atomic_store(
        header->thermal_generation, next_generation - 1,
        std::memory_order_release);
    hbfsim::host_service::atomic_store(
        header->thermal_mode,
        static_cast<std::uint32_t>(
            hbfsim::host_service::PackageThermalMode::PackageRc),
        std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->thermal_stage,
        static_cast<std::uint32_t>(stage), std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->thermal_policy_state, policy_state(decision.effective_mode),
        std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(header->thermal_scale_ppm, scale,
                                       std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->thermal_admission_open,
        decision.admission_open ? 1U : 0U, std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->thermal_flags,
        terminal ? hbfsim::host_service::kThermalFlagTerminalFault : 0U,
        std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(header->thermal_generation,
                                       next_generation,
                                       std::memory_order_release);
    if (terminal) {
        hbfsim::host_service::atomic_store(
            header->fault, static_cast<std::uint64_t>(HBFSIM_IO_ERROR),
            std::memory_order_release);
    }
    next_generation += 2;
}

thermal::ThermalServiceSnapshot thermal_service_snapshot(
    hbfsim::host_service::ControlView control)
{
    const auto* header = control.header();
    const auto submitted = hbfsim::host_service::atomic_load(
        header->request_producer, std::memory_order_acquire);
    const auto admitted = hbfsim::host_service::atomic_load(
        header->request_consumer, std::memory_order_acquire);
    return thermal::ThermalServiceSnapshot{
        .submitted_requests = submitted,
        .admitted_requests = admitted,
        .completed_requests = hbfsim::host_service::atomic_load(
            header->completion_producer, std::memory_order_acquire),
        .queue_depth = submitted >= admitted ? submitted - admitted : 0,
        .thermal_blocked_requests = hbfsim::host_service::atomic_load(
            header->thermal_blocked_requests, std::memory_order_acquire),
    };
}
#endif

}  // namespace

int main(int argc, char** argv)
{
    void* mapping = MAP_FAILED;
    std::size_t mapping_bytes = 0;
    try {
        const auto arguments = parse_arguments(argc, argv);
        const auto profile = hbfsim::load_profile(arguments.profile);
        if (!std::filesystem::is_directory(arguments.report_dir)) {
            throw std::runtime_error("report directory does not exist");
        }

        struct stat status {};
        if (::fstat(arguments.control_fd, &status) != 0 ||
            !S_ISREG(status.st_mode) ||
            status.st_size <
                static_cast<off_t>(
                    sizeof(hbfsim::host_service::SharedControlHeader))) {
            throw std::runtime_error("invalid control fd size");
        }
        constexpr int required_seals =
            F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_SEAL;
        const auto seals = ::fcntl(arguments.control_fd, F_GET_SEALS);
        if (seals < 0 || (seals & required_seals) != required_seals) {
            throw std::runtime_error("control fd is not a sealed memfd");
        }
        mapping_bytes = static_cast<std::size_t>(status.st_size);
        mapping = ::mmap(nullptr, mapping_bytes, PROT_READ | PROT_WRITE,
                         MAP_SHARED, arguments.control_fd, 0);
        if (mapping == MAP_FAILED) {
            throw std::runtime_error("failed to map control fd");
        }

        hbfsim::host_service::ControlView control(mapping, mapping_bytes);
        if (!control.valid()) {
            throw std::runtime_error("invalid control region ABI");
        }

        std::function<void()> thermal_tick = [] {};
        std::function<void()> thermal_finish = [] {};

#if defined(HBFSIM_ENABLE_MQSIM_RUNTIME)
        std::uint64_t model_thermal_time_ns = 0;
#if defined(HBFSIM_ENABLE_PACKAGE_THERMAL)
        std::unique_ptr<thermal::PackageThermalRuntime> package_runtime;
        std::unique_ptr<thermal::ThermalClock> package_clock;
        std::unique_ptr<thermal::PackageThermalTimelineWriter>
            package_timeline;
        std::uint64_t model_thermal_step_ns = 0;
        std::uint64_t thermal_generation = 2;
        hbfsim::MediaActivitySink media_sink;
        if (arguments.thermal == "package_rc") {
            auto package_profile = thermal::load_package_thermal_profile(
                arguments.package_thermal_profile);
            const auto& geometry = package_profile.topology.geometry();
            if (geometry.channels != profile.channels ||
                geometry.chips_per_channel != 1 ||
                geometry.dies_per_chip != profile.dies_per_channel ||
                geometry.planes_per_die != profile.planes_per_die) {
                throw thermal::ThermalError(
                    "package topology does not match MQSim physical geometry");
            }
            const auto selected_stage = thermal_stage(arguments.thermal_stage);
            if (package_profile.stage != selected_stage) {
                throw thermal::ThermalError(
                    "selected thermal stage does not match package profile");
            }
            const auto& nodes = package_profile.topology.node_names();
            std::unique_ptr<thermal::ThermalModel> thermal_model;
            if (arguments.thermal_model_kind == "rom") {
                auto artifact =
                    thermal::load_rom_artifact(arguments.thermal_model);
                thermal::validate_rom_artifact(artifact, nodes, nodes);
                thermal_model = thermal::make_rom_model(std::move(artifact));
            } else {
                thermal_model = thermal::load_thermal_plugin(
                    arguments.thermal_model, nodes, nodes);
            }
            model_thermal_step_ns = thermal_model->sample_period_ns();
            package_clock = std::make_unique<thermal::ThermalClock>(
                package_profile.clock_mode,
                package_profile.clock_mode == thermal::ClockMode::LiveMonotonic
                    ? monotonic_ns()
                    : 0);
            const auto clock_mode = package_profile.clock_mode;
            package_runtime = std::make_unique<thermal::PackageThermalRuntime>(
                std::move(package_profile), std::move(thermal_model));
            package_timeline =
                std::make_unique<thermal::PackageThermalTimelineWriter>(
                    std::filesystem::path(arguments.report_dir) /
                        "package-thermal-timeline.csv",
                    package_runtime->profile());
            publish_thermal_decision(control, selected_stage,
                                     package_runtime->decision(),
                                     thermal_generation);
            media_sink = [&, clock_mode](
                             const hbfsim::MediaActivity& activity) {
                auto start = activity.start_time_ns;
                auto end = activity.end_time_ns;
                if (clock_mode == thermal::ClockMode::LiveMonotonic) {
                    start = package_clock->relative_ns(
                        thermal::ClockSource::HostMonotonic, monotonic_ns());
                    const auto duration = activity.end_time_ns -
                                          activity.start_time_ns;
                    end = start > std::numeric_limits<std::uint64_t>::max() -
                                      duration
                              ? std::numeric_limits<std::uint64_t>::max()
                              : start + duration;
                }
                package_runtime->record(thermal::NandMediaActivity{
                    .operation = nand_operation(activity.kind),
                    .start_time_ns = start,
                    .end_time_ns = end,
                    .coordinate = {activity.channel, activity.chip,
                                   activity.die, activity.plane},
                    .block = activity.block,
                    .page = activity.page,
                    .bytes = activity.bytes,
                });
            };
        }
#else
        if (arguments.thermal == "package_rc") {
            throw std::runtime_error(
                "package_rc requested but package thermal support is not built");
        }
#endif
        hbfsim::MqsimOnlineEngine engine(
#if defined(HBFSIM_ENABLE_PACKAGE_THERMAL)
            media_sink ? hbfsim::MqsimOnlineEngine(profile, media_sink)
                       : hbfsim::MqsimOnlineEngine(profile)
#else
            profile
#endif
        );
#if defined(HBFSIM_ENABLE_PACKAGE_THERMAL)
        if (package_runtime) {
            thermal_tick = [&] {
                std::uint64_t time = 0;
                if (package_runtime->profile().clock_mode ==
                    thermal::ClockMode::LiveMonotonic) {
                    time = package_clock->relative_ns(
                        thermal::ClockSource::HostMonotonic, monotonic_ns());
                } else {
                    time = std::max(engine.current_time_ns(),
                                    model_thermal_time_ns);
                    if (package_runtime->decision().effective_mode ==
                            thermal::ThermalMode::Severe &&
                        time == model_thermal_time_ns) {
                        if (time > std::numeric_limits<std::uint64_t>::max() -
                                       model_thermal_step_ns) {
                            throw thermal::ThermalError(
                                "model-time cooling tick overflows");
                        }
                        time += model_thermal_step_ns;
                    }
                    model_thermal_time_ns = time;
                }
                const auto observations = package_runtime->advance_to(time);
                if (!observations.empty()) {
                    const auto service = thermal_service_snapshot(control);
                    for (const auto& observation : observations) {
                        package_timeline->append(observation, service);
                    }
                    publish_thermal_decision(
                        control, package_runtime->profile().stage,
                        observations.back().policy, thermal_generation);
                }
            };
            thermal_finish = [&] {
                auto time =
                    package_runtime->profile().clock_mode ==
                            thermal::ClockMode::LiveMonotonic
                        ? package_clock->relative_ns(
                              thermal::ClockSource::HostMonotonic,
                              monotonic_ns())
                        : std::max(engine.current_time_ns(),
                                   model_thermal_time_ns);
                const auto observations = package_runtime->finish(time);
                if (!observations.empty()) {
                    const auto service = thermal_service_snapshot(control);
                    for (const auto& observation : observations) {
                        package_timeline->append(observation, service);
                    }
                    publish_thermal_decision(
                        control, package_runtime->profile().stage,
                        observations.back().policy, thermal_generation);
                }
                package_timeline->finish();
                thermal::write_package_thermal_report(
                    std::filesystem::path(arguments.report_dir) /
                        "package-thermal.json",
                    *package_runtime,
                    thermal::ThermalReportMetadata{
                        .package_profile_path =
                            arguments.package_thermal_profile,
                        .model_path = arguments.thermal_model,
                        .model_kind = arguments.thermal_model_kind,
                    }, package_timeline->metrics());
            };
        }
#endif
        hbfsim::host_service::atomic_store(
            control.header()->reserved0,
            hbfsim::host_service::kControlCapabilityCapacityMedia,
            std::memory_order_release);
        hbfsim::host_service::RequestDispatcher dispatcher(
            control, hbfsim::host_service::RequestDispatcher::Engine{
                         .prepare = [&control](
                                        const hbfsim::HbfRequest& request) {
                             return hbfsim::host_service::prepare_host_dispatch(
                                 control, request, monotonic_ns, [] {
                                     std::this_thread::sleep_for(
                                         std::chrono::microseconds(50));
                                 });
                         },
                         .submit = [&](const hbfsim::HbfRequest& request) {
                             auto scheduled = request;
                             scheduled.arrival_ns = std::max(
                                 {scheduled.arrival_ns,
                                  engine.current_time_ns(),
                                  model_thermal_time_ns});
                             engine.submit(scheduled);
                         },
                         .run_next_completion = [&engine] {
                             try {
                                 return engine.run_next_completion();
                             } catch (const std::exception& error) {
                                 std::cerr << "hbfsimd timing engine: "
                                           << error.what() << '\n';
                                 throw;
                             }
                         },
            });
#else
        if (arguments.thermal == "package_rc") {
            throw std::runtime_error(
                "package_rc requires the MQSim runtime observer");
        }
        (void)profile;
        std::deque<hbfsim::HbfCompletion> disabled_completions;
        hbfsim::host_service::RequestDispatcher dispatcher(
            control, hbfsim::host_service::RequestDispatcher::Engine{
                         .prepare = [&control](
                                        const hbfsim::HbfRequest& request) {
                             auto prepared =
                                 hbfsim::host_service::prepare_host_dispatch(
                                 control, request, monotonic_ns, [] {
                                     std::this_thread::sleep_for(
                                         std::chrono::microseconds(50));
                                 }, false);
                             if (prepared.media_action_count != 0) {
                                 prepared.completion =
                                     unsupported_completion(request);
                                 prepared.media_action_count = 0;
                             }
                             return prepared;
                         },
                         .submit = [&](const hbfsim::HbfRequest& request) {
                             disabled_completions.push_back(
                                 unsupported_completion(request));
                         },
                         .run_next_completion = [&]()
                             -> std::optional<hbfsim::HbfCompletion> {
                             if (disabled_completions.empty()) {
                                 return std::nullopt;
                             }
                             auto completion = disabled_completions.front();
                             disabled_completions.pop_front();
                             return completion;
                         },
            });
#endif

        hbfsim::host_service::atomic_store(
            control.header()->daemon_pid,
            static_cast<std::uint64_t>(::getpid()), std::memory_order_release);
        // The first heartbeat is also the startup-ready publication, so it
        // must remain after timing-engine and dispatcher construction.
        std::jthread heartbeat([&control](std::stop_token stop) {
            auto next = std::chrono::steady_clock::now();
            while (!stop.stop_requested()) {
                hbfsim::host_service::atomic_store(
                    control.header()->heartbeat_ns, monotonic_ns(),
                    std::memory_order_release);
                next += std::chrono::milliseconds(5);
                std::this_thread::sleep_until(next);
            }
        });
        for (;;) {
            const bool progressed = dispatcher.poll_once();
            thermal_tick();
            if (hbfsim::host_service::atomic_load(
                    control.header()->shutdown, std::memory_order_acquire) !=
                0) {
                break;
            }
            if (!progressed) {
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
        }

        thermal_finish();

        heartbeat.request_stop();
        heartbeat.join();
        ::munmap(mapping, mapping_bytes);
        ::close(arguments.control_fd);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "hbfsimd: " << error.what() << '\n';
        if (mapping != MAP_FAILED) {
            if (mapping_bytes >=
                    sizeof(hbfsim::host_service::SharedControlHeader) &&
                static_cast<hbfsim::host_service::SharedControlHeader*>(
                    mapping)->magic == hbfsim::kControlMagic) {
                auto* header = static_cast<
                    hbfsim::host_service::SharedControlHeader*>(mapping);
                hbfsim::host_service::atomic_store(
                    header->thermal_admission_open, 0U,
                    std::memory_order_release);
                hbfsim::host_service::atomic_store(
                    header->fault, static_cast<std::uint64_t>(HBFSIM_IO_ERROR),
                    std::memory_order_release);
            }
            ::munmap(mapping, mapping_bytes);
        }
        return 2;
    }
}

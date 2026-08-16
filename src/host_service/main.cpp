#include "control_layout.hpp"
#include "request_dispatcher.hpp"
#include "refresh_scheduler.hpp"
#include "thermal_controller.hpp"

#include <hbfsim/api.h>
#include <hbfsim/profile.hpp>
#include <hbfsim/thermal_report.hpp>

#include <openssl/evp.h>

#if defined(HBFSIM_ENABLE_MQSIM_RUNTIME)
#include <hbfsim/mqsim_online.hpp>
#endif

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <fstream>
#include <fcntl.h>
#include <iostream>
#include <limits>
#include <memory>
#include <optional>
#include <sstream>
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
        } else {
            throw std::invalid_argument("unknown daemon option");
        }
    }
    if (result.profile.empty() || result.report_dir.empty() ||
        result.control_fd < 0) {
        throw std::invalid_argument("required daemon option is missing");
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

std::string sha256_file(const std::filesystem::path& path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("unable to open profile for hashing");
    }
    std::ostringstream bytes;
    bytes << input.rdbuf();
    const auto payload = bytes.str();
    unsigned char digest[EVP_MAX_MD_SIZE]{};
    unsigned int digest_bytes = 0;
    if (EVP_Digest(payload.data(), payload.size(), digest, &digest_bytes,
                   EVP_sha256(), nullptr) != 1 || digest_bytes != 32) {
        throw std::runtime_error("unable to hash profile");
    }
    constexpr char hex[] = "0123456789abcdef";
    std::string result(64, '0');
    for (std::size_t index = 0; index < digest_bytes; ++index) {
        result[index * 2] = hex[digest[index] >> 4];
        result[index * 2 + 1] = hex[digest[index] & 0xf];
    }
    return result;
}

std::string thermal_source_name(hbfsim::ThermalTemperatureSource source)
{
    switch (source) {
    case hbfsim::ThermalTemperatureSource::LiveGpu:
        return "live_gpu";
    case hbfsim::ThermalTemperatureSource::Trace:
        return "trace";
    case hbfsim::ThermalTemperatureSource::Constant:
        return "constant";
    }
    throw std::invalid_argument("unknown thermal source");
}

void publish_summary_status(hbfsim::host_service::ControlView control,
                            std::uint32_t status) noexcept
{
    auto* header = control.header();
    const auto generation = hbfsim::host_service::atomic_load(
        header->thermal_summary_generation, std::memory_order_acquire);
    if ((generation & 1U) != 0 ||
        generation > std::numeric_limits<std::uint64_t>::max() - 2) {
        return;
    }
    hbfsim::host_service::atomic_store(
        header->thermal_summary_generation, generation + 1,
        std::memory_order_release);
    hbfsim::host_service::atomic_store(
        header->thermal_summary_status, status, std::memory_order_relaxed);
    hbfsim::host_service::atomic_store(
        header->thermal_summary_generation, generation + 2,
        std::memory_order_release);
}

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

        std::unique_ptr<hbfsim::host_service::ThermalController>
            thermal_controller;
        std::unique_ptr<hbfsim::host_service::RefreshScheduler>
            refresh_scheduler;
        std::uint32_t registered_refresh_ranges = 0;
        std::deque<hbfsim::host_service::RefreshAction> fast_refresh_actions;
        std::uint64_t observed_refresh_claimed = 0;
        std::uint64_t observed_refresh_drained = 0;
        std::uint64_t refresh_completion_credit = 0;
        auto last_debt_update = std::chrono::steady_clock::now();
        std::optional<hbfsim::ThermalReliabilityProfile> thermal_profile;
        std::filesystem::path thermal_report_path;
        std::string profile_sha256;
        std::string terminal_status = "clean";
        auto next_thermal_tick = std::chrono::steady_clock::now();
        if (profile.thermal_reliability) {
            thermal_profile = *profile.thermal_reliability;
            profile_sha256 = sha256_file(arguments.profile);
            thermal_report_path = std::filesystem::path(arguments.report_dir) /
                                  "thermal-reliability-summary.json";
            const auto start = std::chrono::nanoseconds(monotonic_ns());
            thermal_controller = std::make_unique<
                hbfsim::host_service::ThermalController>(
                *thermal_profile, control, start);
            refresh_scheduler = std::make_unique<
                hbfsim::host_service::RefreshScheduler>(profile,
                                                        *thermal_profile);
            thermal_controller->attach_refresh_scheduler(
                refresh_scheduler.get());
            hbfsim::host_service::atomic_store(
                control.header()->thermal_refresh_quantum_bytes,
                thermal_profile->refresh_quantum_bytes,
                std::memory_order_release);

            // A thermal daemon is not ready until it has consumed one stable
            // sample and published the initial admission state.
            const auto readiness_deadline =
                std::chrono::steady_clock::now() + std::chrono::seconds(10);
            for (;;) {
                if (std::chrono::steady_clock::now() >= readiness_deadline) {
                    throw std::runtime_error(
                        "thermal telemetry did not become ready");
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
                const auto now = std::chrono::nanoseconds(monotonic_ns());
                const auto result = thermal_controller->tick_at(now);
                if (result == hbfsim::host_service::ThermalControllerStatus::Ready) {
                    break;
                }
                if (result != hbfsim::host_service::ThermalControllerStatus::StaleTelemetry &&
                    result != hbfsim::host_service::ThermalControllerStatus::InvalidTelemetry) {
                    throw std::runtime_error(
                        "thermal controller failed before startup readiness");
                }
            }
            next_thermal_tick = std::chrono::steady_clock::now() +
                std::chrono::milliseconds(thermal_profile->controller_period_ms);
        }

#if defined(HBFSIM_ENABLE_MQSIM_RUNTIME)
        hbfsim::MqsimOnlineEngine engine(profile);
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
                         .submit = [&engine](const hbfsim::HbfRequest& request) {
                             auto scheduled = request;
                             scheduled.arrival_ns = std::max(
                                 scheduled.arrival_ns,
                                 engine.current_time_ns());
                             engine.submit(scheduled);
                         },
                         .run_next_completion = [&engine] {
                             return engine.run_next_completion();
                         },
            });
#else
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

        if (refresh_scheduler) {
            dispatcher.attach_refresh_scheduler(refresh_scheduler.get());
        }

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
            bool progressed = false;
            if (thermal_controller &&
                std::chrono::steady_clock::now() >= next_thermal_tick) {
                const auto debt_now = std::chrono::steady_clock::now();
                if (control.header()->timing_model != 0) {
                    auto debt = hbfsim::host_service::atomic_load(
                        control.header()->refresh_debt_bytes,
                        std::memory_order_acquire);
                    hbfsim::host_service::RefreshDebtDecay decay{};
                    for (;;) {
                        decay = hbfsim::host_service::decay_refresh_debt(
                            debt, std::chrono::duration_cast<std::chrono::nanoseconds>(
                                      debt_now - last_debt_update),
                            profile.aggregate_bandwidth_bytes_per_s,
                            thermal_controller->snapshot().service_ppm);
                        auto expected = debt;
                        if (hbfsim::host_service::atomic_compare_exchange_weak(
                                control.header()->refresh_debt_bytes, expected,
                                decay.remaining, std::memory_order_acq_rel,
                                std::memory_order_acquire)) {
                            break;
                        }
                        debt = expected;
                    }
                    if (decay.drained != 0) {
                        (void)hbfsim::host_service::atomic_fetch_add(
                            control.header()->thermal_refresh_background_drained_bytes,
                            decay.drained);
                    }
                    const auto claimed = hbfsim::host_service::atomic_load(
                        control.header()->thermal_refresh_claimed_bytes,
                        std::memory_order_acquire);
                    const auto drained = hbfsim::host_service::atomic_load(
                        control.header()->thermal_refresh_background_drained_bytes,
                        std::memory_order_acquire);
                    if (claimed < observed_refresh_claimed ||
                        drained < observed_refresh_drained) {
                        throw std::runtime_error(
                            "refresh service counter regressed");
                    }
                    const auto claimed_delta = claimed - observed_refresh_claimed;
                    const auto drained_delta = drained - observed_refresh_drained;
                    if (claimed_delta >
                        std::numeric_limits<std::uint64_t>::max() - drained_delta ||
                        refresh_completion_credit >
                        std::numeric_limits<std::uint64_t>::max() -
                            (claimed_delta + drained_delta)) {
                        throw std::runtime_error("refresh service credit overflow");
                    }
                    refresh_completion_credit += claimed_delta + drained_delta;
                    observed_refresh_claimed = claimed;
                    observed_refresh_drained = drained;
                    while (!fast_refresh_actions.empty() &&
                           refresh_completion_credit >=
                               fast_refresh_actions.front().bytes) {
                        const auto action = fast_refresh_actions.front();
                        fast_refresh_actions.pop_front();
                        refresh_completion_credit -= action.bytes;
                        if (!refresh_scheduler->complete(action, true)) {
                            throw std::runtime_error(
                                "fast refresh completion order diverged");
                        }
                        auto& bytes = action.kind ==
                                              hbfsim::host_service::RefreshActionKind::Read
                                          ? control.header()->thermal_refresh_read_bytes
                                          : control.header()->thermal_refresh_write_bytes;
                        (void)hbfsim::host_service::atomic_fetch_add(
                            bytes, static_cast<std::uint64_t>(action.bytes));
                    }
                    hbfsim::host_service::atomic_store(
                        control.header()->thermal_completed_refresh_blocks,
                        refresh_scheduler->completed_blocks(),
                        std::memory_order_release);
                    hbfsim::host_service::atomic_store(
                        control.header()->thermal_max_pec,
                        refresh_scheduler->maximum_pec(),
                        std::memory_order_release);
                    hbfsim::host_service::atomic_store(
                        control.header()->thermal_average_pec_millionths,
                        refresh_scheduler->average_pec_millionths(),
                        std::memory_order_release);
                }
                last_debt_update = debt_now;
                const auto range_count = hbfsim::host_service::atomic_load(
                    control.header()->range_count, std::memory_order_acquire);
                if (range_count < registered_refresh_ranges ||
                    range_count > hbfsim::host_service::kRangeCapacity) {
                    throw std::runtime_error("thermal range publication regressed");
                }
                const auto* ranges = control.ranges();
                while (registered_refresh_ranges < range_count) {
                    const auto& range = ranges[registered_refresh_ranges++];
                    refresh_scheduler->register_published_range(
                        range,
                        thermal_profile->registered_ranges_contain_valid_data);
                }
                const auto result = thermal_controller->tick_at(
                    std::chrono::nanoseconds(monotonic_ns()));
                next_thermal_tick = std::chrono::steady_clock::now() +
                    std::chrono::milliseconds(
                        thermal_profile->controller_period_ms);
                if (result != hbfsim::host_service::ThermalControllerStatus::Ready) {
                    const auto thermal_shutdown =
                        result == hbfsim::host_service::ThermalControllerStatus::Shutdown;
                    terminal_status =
                        hbfsim::host_service::thermal_report_terminal_status(
                            result);
                    hbfsim::host_service::atomic_store(
                        control.header()->fault,
                        static_cast<std::uint64_t>(
                            thermal_shutdown ? HBFSIM_THERMAL_SHUTDOWN
                                             : HBFSIM_IO_ERROR),
                        std::memory_order_release);
                    break;
                }
                if (control.header()->timing_model == 0) {
                    const auto refresh = refresh_scheduler->plan(
                        thermal_controller->snapshot().generation, {});
                    for (const auto& action : refresh) {
                        if (!dispatcher.enqueue_background(action)) {
                            throw std::runtime_error(
                                "unable to enqueue background refresh");
                        }
                    }
                } else {
                    const auto refresh = refresh_scheduler->plan(
                        thermal_controller->snapshot().generation, {});
                    std::uint64_t added = 0;
                    for (const auto& action : refresh) {
                        if (added > std::numeric_limits<std::uint64_t>::max() -
                                        action.bytes) {
                            throw std::runtime_error("refresh debt overflow");
                        }
                        added += action.bytes;
                        fast_refresh_actions.push_back(action);
                    }
                    if (added != 0) {
                        auto debt = hbfsim::host_service::atomic_load(
                            control.header()->refresh_debt_bytes,
                            std::memory_order_acquire);
                        for (;;) {
                            if (debt > std::numeric_limits<std::uint64_t>::max() -
                                           added) {
                                throw std::runtime_error("refresh debt overflow");
                            }
                            auto expected = debt;
                            if (hbfsim::host_service::atomic_compare_exchange_weak(
                                    control.header()->refresh_debt_bytes,
                                    expected, debt + added,
                                    std::memory_order_acq_rel,
                                    std::memory_order_acquire)) {
                                break;
                            }
                            debt = expected;
                        }
                        (void)hbfsim::host_service::atomic_fetch_add(
                            control.header()->refresh_debt_generation, 1);
                    }
                }
            }
            while (dispatcher.poll_once()) {
                progressed = true;
            }
            const auto fault = hbfsim::host_service::atomic_load(
                control.header()->fault, std::memory_order_acquire);
            if (fault != 0) {
                terminal_status =
                    fault == static_cast<std::uint64_t>(
                                 HBFSIM_THERMAL_SHUTDOWN)
                        ? "thermal_shutdown"
                        : "media_error";
                break;
            }
            if (hbfsim::host_service::atomic_load(
                    control.header()->shutdown, std::memory_order_acquire) !=
                0) {
                break;
            }
            if (!progressed) {
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
        }

        heartbeat.request_stop();
        heartbeat.join();
        if (thermal_controller) {
            hbfsim::ThermalRunSummary summary{
                .profile_sha256 = profile_sha256,
                .profile = *thermal_profile,
                .temperature_source = thermal_source_name(
                    thermal_profile->temperature_source),
                .samples = thermal_controller->samples(),
                .transitions = thermal_controller->transitions(),
                .accounting = thermal_controller->accounting(),
                .mtbf = hbfsim::integrate_mtbf_sensitivity(
                    *thermal_profile,
                    thermal_controller->temperature_intervals()),
                .terminal_status = terminal_status,
            };
            try {
                hbfsim::write_thermal_summary(thermal_report_path, summary);
                publish_summary_status(control, 1);
            } catch (...) {
                publish_summary_status(control, 2);
                throw;
            }
        }
        ::munmap(mapping, mapping_bytes);
        ::close(arguments.control_fd);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "hbfsimd: " << error.what() << '\n';
        if (mapping != MAP_FAILED) {
            ::munmap(mapping, mapping_bytes);
        }
        return 2;
    }
}

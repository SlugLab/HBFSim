#if defined(HBFSIM_ENABLE_UCIE_BACKEND)
#include "../ucie/private_observer_gate.hpp"
#endif
#include "control_layout.hpp"
#include "request_dispatcher.hpp"

#if defined(HBFSIM_ENABLE_UCIE_BACKEND)
#include "ucie_backend_adapter.hpp"
#endif

#include <hbfsim/api.h>
#include <hbfsim/profile.hpp>

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
#include <fcntl.h>
#include <functional>
#include <iostream>
#include <limits>
#include <memory>
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
    std::string backend{"legacy-hbf"};
    std::string ucie_top_profile;
    std::string ucie_worker;
    std::string ucie_placement_manifest;
    std::string ucie_wait_mode;
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
        } else if (option == "--backend") {
            result.backend = argv[index + 1];
        } else if (option == "--ucie-top-profile") {
            result.ucie_top_profile = argv[index + 1];
        } else if (option == "--ucie-worker") {
            result.ucie_worker = argv[index + 1];
        } else if (option == "--ucie-placement-manifest") {
            result.ucie_placement_manifest = argv[index + 1];
        } else if (option == "--ucie-wait-mode") {
            result.ucie_wait_mode = argv[index + 1];
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
    if (result.backend == "legacy-hbf") {
        if (!result.ucie_top_profile.empty() || !result.ucie_worker.empty() ||
            !result.ucie_placement_manifest.empty() ||
            !result.ucie_wait_mode.empty()) {
            throw std::invalid_argument("UCIe options require UCIe backend");
        }
    } else if (result.backend == "ucie") {
        if (result.ucie_top_profile.empty() || result.ucie_worker.empty() ||
            result.ucie_placement_manifest.empty() ||
            (result.ucie_wait_mode != "nominal" &&
             result.ucie_wait_mode != "zero")) {
            throw std::invalid_argument("incomplete UCIe daemon options");
        }
    } else {
        throw std::invalid_argument("unknown daemon backend");
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

#if defined(HBFSIM_ENABLE_MQSIM_RUNTIME)
        std::unique_ptr<hbfsim::MqsimOnlineEngine> engine;
#else
        (void)profile;
        std::deque<hbfsim::HbfCompletion> disabled_completions;
#endif
        std::unique_ptr<hbfsim::host_service::RequestDispatcher> dispatcher;
        std::function<bool()> poll_once;
#if defined(HBFSIM_ENABLE_UCIE_BACKEND)
        std::unique_ptr<hbfsim::host_service::UcieBackendAdapter> ucie;
#endif
        if (arguments.backend == "ucie") {
#if defined(HBFSIM_ENABLE_UCIE_BACKEND)
            if (control.header()->timing_model != HBFSIM_MODEL_REFERENCE &&
                control.header()->timing_model != HBFSIM_MODEL_HYBRID) {
                throw std::invalid_argument("UCIe requires reference or hybrid timing mode");
            }
            const auto wait_mode = arguments.ucie_wait_mode == "zero"
                ? hbfsim::host_service::UcieWaitMode::ZeroInjected
                : hbfsim::host_service::UcieWaitMode::Nominal;
            const auto control_flags = hbfsim::host_service::atomic_load(
                control.header()->reserved0, std::memory_order_acquire);
            if (((control_flags &
                  hbfsim::host_service::kControlZeroInjectedWait) != 0) !=
                (wait_mode == hbfsim::host_service::UcieWaitMode::ZeroInjected)) {
                throw std::invalid_argument("UCIe zero-wait control mismatch");
            }
            ucie = std::make_unique<hbfsim::host_service::UcieBackendAdapter>(
                control, hbfsim::host_service::UcieBackendOptions{
                    .top_profile = arguments.ucie_top_profile,
                    .worker_executable = arguments.ucie_worker,
                    .placement_manifest = arguments.ucie_placement_manifest,
                    .wait_mode = wait_mode,
                });
            hbfsim::host_service::atomic_store(
                control.header()->reserved0,
                control_flags |
                    hbfsim::host_service::kControlCapabilityUcieBackend,
                std::memory_order_release);
            poll_once = [&ucie] { return ucie->poll_once(); };
#else
            throw std::invalid_argument("UCIe backend unavailable in this daemon");
#endif
        } else {
            if ((hbfsim::host_service::atomic_load(
                     control.header()->reserved0, std::memory_order_acquire) &
                 hbfsim::host_service::kControlZeroInjectedWait) != 0) {
                throw std::invalid_argument("zero wait requires UCIe backend");
            }
#if defined(HBFSIM_ENABLE_MQSIM_RUNTIME)
        engine = std::make_unique<hbfsim::MqsimOnlineEngine>(profile);
        hbfsim::host_service::atomic_store(
            control.header()->reserved0,
            hbfsim::host_service::kControlCapabilityCapacityMedia,
            std::memory_order_release);
        dispatcher = std::make_unique<hbfsim::host_service::RequestDispatcher>(
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
                                 engine->current_time_ns());
                             engine->submit(scheduled);
                         },
                         .run_next_completion = [&engine] {
                             return engine->run_next_completion();
                         },
            });
#else
        dispatcher = std::make_unique<hbfsim::host_service::RequestDispatcher>(
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
            poll_once = [&dispatcher] { return dispatcher->poll_once(); };
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
#if defined(HBFSIM_ENABLE_UCIE_BACKEND)
        hbfsim::ucie::short_qkv_observer::MappedGate private_gate;
        const auto gate_tick=[&] {
            private_gate.tick(control.header(),[&] {
#if defined(HBFSIM_ENABLE_UCIE_BACKEND)
                if(!ucie)throw std::runtime_error("observer needs actual UCIe backend");
                ucie->write_report(std::filesystem::path(arguments.report_dir)/"short-postgate-backend.json");
#endif
            });
        };
#else
        const auto gate_tick=[] {};
#endif
        for (;;) {
            gate_tick();
            bool progressed = false;
            while (poll_once()) {
                progressed = true;
                gate_tick();
            }
            gate_tick();
            if (hbfsim::host_service::atomic_load(
                    control.header()->shutdown, std::memory_order_acquire) !=
                0) {
                break;
            }
            if (!progressed) {
                gate_tick();
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
        }

        heartbeat.request_stop();
        heartbeat.join();
#if defined(HBFSIM_ENABLE_UCIE_BACKEND)
        if (ucie) {
            if(private_gate.exported()) {
                std::filesystem::copy_file(std::filesystem::path(arguments.report_dir)/"short-postgate-backend.json",
                    std::filesystem::path(arguments.report_dir)/"ucie-backend-report.json");
            } else ucie->write_report(std::filesystem::path(arguments.report_dir) /
                               "ucie-backend-report.json");
        }
#endif
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

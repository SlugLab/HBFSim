#include "control_layout.hpp"
#include "request_dispatcher.hpp"

#include <hbfsim/api.h>
#include <hbfsim/profile.hpp>

#if defined(HBFSIM_ENABLE_MQSIM_RUNTIME)
#include <hbfsim/mqsim_online.hpp>
#endif

#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <deque>
#include <filesystem>
#include <fcntl.h>
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

const hbfsim::host_service::SharedRangeRecord* find_range_by_id(
    hbfsim::host_service::ControlView control, std::uint32_t range_id)
{
    const auto count = hbfsim::host_service::atomic_load(
        control.header()->range_count, std::memory_order_acquire);
    if (count > hbfsim::host_service::kRangeCapacity) {
        return nullptr;
    }
    for (std::uint32_t index = 0; index < count; ++index) {
        if (control.ranges()[index].range_id == range_id) {
            return &control.ranges()[index];
        }
    }
    return nullptr;
}

void finalize_capacity_completion(
    hbfsim::host_service::ControlView control,
    const hbfsim::HbfRequest& request, hbfsim::HbfCompletion& completion)
{
    const auto* range = find_range_by_id(control, request.range_id);
    if (range == nullptr ||
        range->mode != HBFSIM_RANGE_MODE_CAPACITY ||
        completion.status !=
            static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready)) {
        return;
    }
    if (request.bytes != range->page_bytes ||
        !control.begin_capacity_handoff(request)) {
        completion.status = static_cast<std::uint32_t>(
            hbfsim::RequestStatus::IoError);
        completion.cache_frame_address = 0;
        return;
    }

    const auto started = monotonic_ns();
    hbfsim::host_service::CapacityHandoffResult result{};
    while (!control.capacity_handoff_result(request, result)) {
        const auto fault = hbfsim::host_service::atomic_load(
            control.header()->fault, std::memory_order_acquire);
        const auto shutdown = hbfsim::host_service::atomic_load(
            control.header()->shutdown, std::memory_order_acquire);
        const auto now = monotonic_ns();
        if (fault != 0 || shutdown != 0 || now < started ||
            now - started >= control.header()->request_timeout_ns) {
            const auto failure =
                fault != 0 || shutdown != 0
                    ? hbfsim::RequestStatus::DaemonLost
                    : hbfsim::RequestStatus::Timeout;
            if (control.complete_capacity_handoff(
                    request.sequence, request.request_id, 0, failure)) {
                result.status = failure;
                result.frame_address = 0;
            } else if (!control.capacity_handoff_result(request, result)) {
                result.status = hbfsim::RequestStatus::IoError;
                result.frame_address = 0;
            }
            break;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(50));
    }
    completion.status = static_cast<std::uint32_t>(result.status);
    completion.cache_frame_address = result.frame_address;
    const auto finished = monotonic_ns();
    completion.service_ns = finished >= started ? finished - started : 0;
    if (!control.release_capacity_handoff(request)) {
        completion.status = static_cast<std::uint32_t>(
            hbfsim::RequestStatus::IoError);
        completion.cache_frame_address = 0;
    }
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
        hbfsim::MqsimOnlineEngine engine(profile);
        hbfsim::host_service::RequestDispatcher dispatcher(
            control, hbfsim::host_service::RequestDispatcher::Engine{
                         .submit = [&engine](const hbfsim::HbfRequest& request) {
                             engine.submit(request);
                         },
                         .run_next_completion = [&engine] {
                             return engine.run_next_completion();
                         },
                         .finalize = [&control](
                                         const hbfsim::HbfRequest& request,
                                         hbfsim::HbfCompletion& completion) {
                             finalize_capacity_completion(control, request,
                                                          completion);
                         },
            });
#else
        (void)profile;
        std::deque<hbfsim::HbfCompletion> disabled_completions;
        hbfsim::host_service::RequestDispatcher dispatcher(
            control, hbfsim::host_service::RequestDispatcher::Engine{
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
                         .finalize = [&control](
                                         const hbfsim::HbfRequest& request,
                                         hbfsim::HbfCompletion& completion) {
                             finalize_capacity_completion(control, request,
                                                          completion);
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
            bool progressed = false;
            while (dispatcher.poll_once()) {
                progressed = true;
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

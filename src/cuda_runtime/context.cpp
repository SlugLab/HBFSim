#include "context.hpp"

#include "../host_service/control_layout.hpp"

#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
#include <cuda_runtime_api.h>
#endif

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <limits>
#include <linux/memfd.h>
#include <memory>
#include <mutex>
#include <new>
#include <string>
#include <string_view>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

extern char** environ;

struct hbfsim_context {
    void* control_mapping{MAP_FAILED};
    void* device_control{nullptr};
    std::size_t control_bytes{0};
    int control_fd{-1};
    pid_t daemon_pid{-1};
    std::uint64_t request_timeout_ns{0};
    bool cuda_registered{false};
    std::mutex process_mutex;
};

namespace hbfsim::runtime {
namespace {

using host_service::ControlView;

// Startup readiness is distinct from per-request timeout. The MQSim engine is
// constructed before hbfsimd publishes its first heartbeat and can require
// several seconds on a loaded host.
constexpr auto kDaemonStartupTimeout = std::chrono::seconds(10);

std::uint64_t monotonic_ns()
{
    const auto now = std::chrono::steady_clock::now().time_since_epoch();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(now).count());
}

bool valid_options(const hbfsim_options* options,
                   hbfsim_context** out) noexcept
{
    return out != nullptr && options != nullptr &&
           options->profile_path != nullptr && options->profile_path[0] != '\0' &&
           options->report_dir != nullptr && options->report_dir[0] != '\0' &&
           host_service::valid_ring_capacity(options->ring_capacity) &&
           options->request_timeout_ns != 0;
}

int create_memfd()
{
#if defined(SYS_memfd_create)
    return static_cast<int>(
        ::syscall(SYS_memfd_create, "hbfsim-control",
                  MFD_CLOEXEC | MFD_ALLOW_SEALING));
#else
    errno = ENOSYS;
    return -1;
#endif
}

std::string resolve_executable(const char* requested)
{
    if (requested == nullptr || requested[0] == '\0') {
        return {};
    }
    const std::string name(requested);
    if (name.find('/') != std::string::npos) {
        std::error_code error;
        const auto absolute = std::filesystem::absolute(name, error);
        if (!error && ::access(absolute.c_str(), X_OK) == 0) {
            return absolute.string();
        }
        return {};
    }
    const char* path_value = std::getenv("PATH");
    if (path_value == nullptr) {
        return {};
    }
    std::string_view path(path_value);
    while (!path.empty()) {
        const auto separator = path.find(':');
        const auto directory = path.substr(0, separator);
        const auto candidate =
            std::filesystem::path(directory.empty() ? "." : directory) / name;
        if (::access(candidate.c_str(), X_OK) == 0) {
            std::error_code error;
            const auto absolute = std::filesystem::absolute(candidate, error);
            return error ? candidate.string() : absolute.string();
        }
        if (separator == std::string_view::npos) {
            break;
        }
        path.remove_prefix(separator + 1);
    }
    return {};
}

void reap_or_terminate(hbfsim_context* context) noexcept
{
    if (context == nullptr || context->daemon_pid <= 0) {
        return;
    }
    int status = 0;
    // Reserve time inside the five-second signaling schedule for TERM and
    // KILL. A final blocking wait reaps every normally schedulable child;
    // kernel-uninterruptible sleep can extend destruction beyond that bound.
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::milliseconds(4'600);
    for (;;) {
        const auto result = ::waitpid(context->daemon_pid, &status, WNOHANG);
        if (result == context->daemon_pid || (result < 0 && errno == ECHILD)) {
            context->daemon_pid = -1;
            return;
        }
        if (result < 0 && errno != EINTR) {
            break;
        }
        if (std::chrono::steady_clock::now() >= deadline) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }

    (void)::kill(context->daemon_pid, SIGTERM);
    const auto term_deadline = std::chrono::steady_clock::now() +
                               std::chrono::milliseconds(100);
    while (std::chrono::steady_clock::now() < term_deadline) {
        const auto result = ::waitpid(context->daemon_pid, &status, WNOHANG);
        if (result == context->daemon_pid || (result < 0 && errno == ECHILD)) {
            context->daemon_pid = -1;
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    (void)::kill(context->daemon_pid, SIGKILL);
    while (::waitpid(context->daemon_pid, &status, 0) < 0 && errno == EINTR) {
    }
    context->daemon_pid = -1;
}

void release_context(hbfsim_context* context, bool request_shutdown) noexcept
{
    if (context == nullptr) {
        return;
    }
    if (request_shutdown && context->control_mapping != MAP_FAILED) {
        ControlView control(context->control_mapping, context->control_bytes);
        if (control.valid()) {
            host_service::atomic_store(control.header()->shutdown, 1,
                                       std::memory_order_release);
        }
    }
    reap_or_terminate(context);
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    if (context->cuda_registered) {
        (void)::cudaDeviceSynchronize();
        (void)::cudaHostUnregister(context->control_mapping);
        context->cuda_registered = false;
    }
#endif
    if (context->control_mapping != MAP_FAILED) {
        (void)::munmap(context->control_mapping, context->control_bytes);
        context->control_mapping = MAP_FAILED;
    }
    if (context->control_fd >= 0) {
        (void)::close(context->control_fd);
        context->control_fd = -1;
    }
    delete context;
}

int process_status(hbfsim_context* context) noexcept
{
    if (context == nullptr || context->control_mapping == MAP_FAILED) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    std::lock_guard lock(context->process_mutex);
    ControlView control(context->control_mapping, context->control_bytes);
    const auto fault = host_service::atomic_load(control.header()->fault,
                                                  std::memory_order_acquire);
    if (fault != 0) {
        return static_cast<int>(fault);
    }
    if (context->daemon_pid <= 0) {
        return HBFSIM_DAEMON_LOST;
    }
    int status = 0;
    const auto result = ::waitpid(context->daemon_pid, &status, WNOHANG);
    if (result == context->daemon_pid || (result < 0 && errno == ECHILD)) {
        context->daemon_pid = -1;
        host_service::atomic_store(control.header()->fault,
                                   HBFSIM_DAEMON_LOST,
                                   std::memory_order_release);
        return HBFSIM_DAEMON_LOST;
    }
    if (result < 0 && errno != EINTR) {
        return HBFSIM_IO_ERROR;
    }
    return HBFSIM_OK;
}

int wait_for_heartbeat(hbfsim_context* context,
                       std::chrono::milliseconds timeout)
{
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    ControlView control(context->control_mapping, context->control_bytes);
    while (std::chrono::steady_clock::now() < deadline) {
        const auto status = process_status(context);
        if (status != HBFSIM_OK) {
            return status;
        }
        if (host_service::atomic_load(control.header()->heartbeat_ns,
                                      std::memory_order_acquire) != 0) {
            return HBFSIM_OK;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return HBFSIM_TIMEOUT;
}

bool daemon_environment_variable_is_instrumentation(
    std::string_view entry) noexcept
{
    const auto separator = entry.find('=');
    const auto name = entry.substr(0, separator);
    return name == "LD_PRELOAD" || name == "LD_AUDIT" ||
           name == "HBFSIM_GATE" || name == "HBFSIM_PASS" ||
           name.starts_with("BPFTIME_") ||
           name.starts_with("HBFSIM_BPFTIME_") ||
           name.starts_with("HBFSIM_COVERAGE_") ||
           name.starts_with("HBFSIM_PASS_") ||
           name.starts_with("PTX_PASS_") ||
           name.starts_with("PTX_COMPILER_") ||
           name.starts_with("DEFAULT_PTX_");
}

int spawn_daemon(hbfsim_context* context, const hbfsim_options* options,
                 const std::string& executable, BeforeForkHook before_fork,
                 void* hook_state)
{
    const auto descriptor_flags = ::fcntl(context->control_fd, F_GETFD);
    if (descriptor_flags < 0 || (descriptor_flags & FD_CLOEXEC) == 0) {
        return HBFSIM_IO_ERROR;
    }
    const std::string fd_text = std::to_string(context->control_fd);
    std::array<char*, 8> arguments{
        const_cast<char*>(executable.c_str()),
        const_cast<char*>("--profile"),
        const_cast<char*>(options->profile_path),
        const_cast<char*>("--control-fd"),
        const_cast<char*>(fd_text.c_str()),
        const_cast<char*>("--report-dir"),
        const_cast<char*>(options->report_dir),
        nullptr,
    };
    std::vector<std::string> environment_storage;
    for (char** item = environ; item != nullptr && *item != nullptr; ++item) {
        if (!daemon_environment_variable_is_instrumentation(*item)) {
            environment_storage.emplace_back(*item);
        }
    }
    std::vector<char*> environment;
    environment.reserve(environment_storage.size() + 1);
    for (auto& item : environment_storage) {
        environment.push_back(item.data());
    }
    environment.push_back(nullptr);
    auto* const executable_path = executable.c_str();
    auto* const argument_data = arguments.data();
    auto* const environment_data = environment.data();
    const auto control_fd = context->control_fd;
    if (before_fork != nullptr) {
        before_fork(control_fd, hook_state);
    }
    const auto expected_parent = ::getpid();
    const auto child = ::fork();
    if (child == 0) {
        if (::syscall(SYS_prctl, PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0) != 0 ||
            ::getppid() != expected_parent) {
            ::_exit(125);
        }
        close_inherited_fds_for_test(control_fd, false);
        const auto child_descriptor_flags = ::fcntl(control_fd, F_GETFD);
        if (child_descriptor_flags < 0 ||
            ::fcntl(control_fd, F_SETFD,
                    child_descriptor_flags & ~FD_CLOEXEC) != 0) {
            ::_exit(126);
        }
        ::execve(executable_path, argument_data, environment_data);
        ::_exit(127);
    }
    if (child < 0) {
        return HBFSIM_IO_ERROR;
    }
    context->daemon_pid = child;
    return HBFSIM_OK;
}

int create_context(const hbfsim_options* options, const char* daemon_path,
                   bool register_with_cuda, BeforeForkHook before_fork,
                   void* hook_state, hbfsim_context** out)
{
    if (out != nullptr) {
        *out = nullptr;
    }
    if (!valid_options(options, out)) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    const auto executable = resolve_executable(daemon_path);
    if (executable.empty()) {
        return HBFSIM_IO_ERROR;
    }
    std::error_code filesystem_error;
    std::filesystem::create_directories(options->report_dir, filesystem_error);
    if (filesystem_error) {
        return HBFSIM_IO_ERROR;
    }

    auto context = std::unique_ptr<hbfsim_context>(
        new (std::nothrow) hbfsim_context{});
    if (!context) {
        return HBFSIM_IO_ERROR;
    }
    context->request_timeout_ns = options->request_timeout_ns;
    context->control_bytes =
        host_service::control_region_bytes(options->ring_capacity);
    context->control_fd = create_memfd();
    if (context->control_fd < 0 ||
        ::ftruncate(context->control_fd,
                    static_cast<off_t>(context->control_bytes)) != 0) {
        release_context(context.release(), false);
        return HBFSIM_IO_ERROR;
    }
    constexpr int required_seals =
        F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_SEAL;
    if (::fcntl(context->control_fd, F_ADD_SEALS, required_seals) != 0) {
        release_context(context.release(), false);
        return HBFSIM_IO_ERROR;
    }
    context->control_mapping =
        ::mmap(nullptr, context->control_bytes, PROT_READ | PROT_WRITE,
               MAP_SHARED, context->control_fd, 0);
    if (context->control_mapping == MAP_FAILED) {
        release_context(context.release(), false);
        return HBFSIM_IO_ERROR;
    }
    ControlView control(context->control_mapping, context->control_bytes);
    if (!control.initialize(options->ring_capacity)) {
        release_context(context.release(), false);
        return HBFSIM_IO_ERROR;
    }

    if (register_with_cuda) {
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
        if (::cudaHostRegister(context->control_mapping, context->control_bytes,
                               cudaHostRegisterMapped |
                                   cudaHostRegisterPortable) != cudaSuccess) {
            release_context(context.release(), false);
            return HBFSIM_CUDA_ERROR;
        }
        context->cuda_registered = true;
        if (::cudaHostGetDevicePointer(&context->device_control,
                                       context->control_mapping, 0) !=
            cudaSuccess) {
            release_context(context.release(), false);
            return HBFSIM_CUDA_ERROR;
        }
#else
        release_context(context.release(), false);
        return HBFSIM_CUDA_ERROR;
#endif
    }

    const auto spawn_status = spawn_daemon(context.get(), options, executable,
                                           before_fork, hook_state);
    if (spawn_status != HBFSIM_OK) {
        release_context(context.release(), false);
        return spawn_status;
    }
    const auto heartbeat_status =
        wait_for_heartbeat(context.get(), kDaemonStartupTimeout);
    if (heartbeat_status != HBFSIM_OK) {
        release_context(context.release(), true);
        return heartbeat_status == HBFSIM_DAEMON_LOST ? HBFSIM_IO_ERROR
                                                      : heartbeat_status;
    }
    *out = context.release();
    return HBFSIM_OK;
}

int enqueue_with_deadline(hbfsim_context* context, const HbfRequest& request,
                          std::uint64_t* ticket)
{
    if (context == nullptr || ticket == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    ControlView control(context->control_mapping, context->control_bytes);
    const auto deadline = monotonic_ns() + context->request_timeout_ns;
    std::chrono::nanoseconds backoff(1'000);
    for (;;) {
        const auto status = process_status(context);
        if (status != HBFSIM_OK) {
            return status;
        }
        if (control.try_push_request(request, *ticket)) {
            return HBFSIM_OK;
        }
        if (monotonic_ns() >= deadline) {
            return HBFSIM_TIMEOUT;
        }
        std::this_thread::sleep_for(backoff);
        backoff = std::min(backoff * 2, std::chrono::nanoseconds(1'000'000));
    }
}

}  // namespace

int create_cpu_test_context(const hbfsim_options* options,
                            const char* daemon_path,
                            hbfsim_context** out)
{
    return create_context(options, daemon_path, false, nullptr, nullptr, out);
}

int create_cpu_test_context_with_spawn_hook(
    const hbfsim_options* options, const char* daemon_path,
    BeforeForkHook before_fork, void* hook_state, hbfsim_context** out)
{
    return create_context(options, daemon_path, false, before_fork, hook_state,
                          out);
}

pid_t daemon_pid_for_test(hbfsim_context* context) noexcept
{
    return context == nullptr ? -1 : context->daemon_pid;
}

int wait_for_heartbeat_for_test(hbfsim_context* context,
                                std::chrono::milliseconds timeout)
{
    if (context == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    return wait_for_heartbeat(context, timeout);
}

int wait_for_fault_for_test(hbfsim_context* context,
                            std::chrono::milliseconds timeout)
{
    if (context == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        const auto status = process_status(context);
        if (status != HBFSIM_OK) {
            return status;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return HBFSIM_TIMEOUT;
}

std::uint64_t heartbeat_ns_for_test(hbfsim_context* context) noexcept
{
    if (context == nullptr || context->control_mapping == MAP_FAILED) {
        return 0;
    }
    ControlView control(context->control_mapping, context->control_bytes);
    return host_service::atomic_load(control.header()->heartbeat_ns,
                                     std::memory_order_acquire);
}

int submit_without_wait_for_test(hbfsim_context* context,
                                 const HbfRequest& request,
                                 std::uint64_t* ticket)
{
    return enqueue_with_deadline(context, request, ticket);
}

int wait_for_completion_for_test(hbfsim_context* context,
                                 std::uint64_t ticket,
                                 std::uint64_t request_id,
                                 HbfCompletion* completion)
{
    if (context == nullptr || completion == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    ControlView control(context->control_mapping, context->control_bytes);
    const auto deadline = monotonic_ns() + context->request_timeout_ns;
    std::chrono::nanoseconds backoff(1'000);
    for (;;) {
        if (control.try_consume_completion(ticket, *completion)) {
            if (completion->request_id != request_id) {
                return HBFSIM_IO_ERROR;
            }
            if (completion->status ==
                static_cast<std::uint32_t>(RequestStatus::IoError)) {
                return HBFSIM_IO_ERROR;
            }
            return HBFSIM_OK;
        }
        const auto status = process_status(context);
        if (status != HBFSIM_OK) {
            if (control.try_consume_completion(ticket, *completion) &&
                completion->request_id == request_id) {
                return completion->status == static_cast<std::uint32_t>(
                                                 RequestStatus::IoError)
                           ? HBFSIM_IO_ERROR
                           : HBFSIM_OK;
            }
            return status;
        }
        if (monotonic_ns() >= deadline) {
            return HBFSIM_TIMEOUT;
        }
        std::this_thread::sleep_for(backoff);
        backoff = std::min(backoff * 2, std::chrono::nanoseconds(1'000'000));
    }
}

int submit_for_test(hbfsim_context* context, const HbfRequest& request,
                    HbfCompletion* completion)
{
    std::uint64_t ticket = 0;
    const auto enqueue_status =
        enqueue_with_deadline(context, request, &ticket);
    if (enqueue_status != HBFSIM_OK) {
        return enqueue_status;
    }
    return wait_for_completion_for_test(context, ticket, request.request_id,
                                        completion);
}

int control_fd_for_test(hbfsim_context* context) noexcept
{
    return context == nullptr ? -1 : context->control_fd;
}

void close_inherited_fds_for_test(int control_fd,
                                  bool force_fallback) noexcept
{
    bool closed = false;
#if defined(SYS_close_range)
    if (!force_fallback) {
        bool lower_closed = true;
        if (control_fd > 3) {
            lower_closed =
                ::syscall(SYS_close_range, 3U,
                          static_cast<unsigned int>(control_fd - 1), 0U) == 0;
        }
        const auto upper_start =
            control_fd >= 3 ? static_cast<unsigned int>(control_fd + 1) : 3U;
        const bool upper_closed =
            ::syscall(SYS_close_range, upper_start,
                      std::numeric_limits<unsigned int>::max(), 0U) == 0;
        closed = lower_closed && upper_closed;
    }
#endif
    if (closed) {
        return;
    }
    struct rlimit limit {};
    std::uint64_t maximum = 65'536;
#if defined(SYS_getrlimit)
    if (::syscall(SYS_getrlimit, RLIMIT_NOFILE, &limit) == 0) {
        maximum = limit.rlim_cur == RLIM_INFINITY
                      ? 1'048'576
                      : static_cast<std::uint64_t>(limit.rlim_cur);
    }
#endif
    for (int descriptor = 3;
         static_cast<std::uint64_t>(descriptor) < maximum; ++descriptor) {
        if (descriptor != control_fd) {
            (void)::close(descriptor);
        }
    }
}

void pause_daemon_for_test(hbfsim_context* context, bool paused) noexcept
{
    if (context != nullptr && context->daemon_pid > 0) {
        (void)::kill(context->daemon_pid, paused ? SIGSTOP : SIGCONT);
    }
}

}  // namespace hbfsim::runtime

extern "C" int hbfsim_context_create(const hbfsim_options* options,
                                      hbfsim_context** out)
{
    const char* daemon = std::getenv("HBFSIM_DAEMON_PATH");
    if (daemon == nullptr || daemon[0] == '\0') {
        daemon = "hbfsimd";
    }
    return hbfsim::runtime::create_context(options, daemon, true, nullptr,
                                           nullptr, out);
}

extern "C" int hbfsim_register_device(
    hbfsim_context* context, void* device_ptr, size_t length,
    const hbfsim_range_options* options)
{
    if (context == nullptr || device_ptr == nullptr || length == 0 ||
        options == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    const auto status = hbfsim::runtime::process_status(context);
    return status == HBFSIM_OK ? HBFSIM_UNSUPPORTED : status;
}

extern "C" int hbfsim_map_file(hbfsim_context* context, const char* path,
                               uint64_t, size_t length,
                               const hbfsim_range_options* options,
                               void** logical_device_ptr_out)
{
    if (logical_device_ptr_out != nullptr) {
        *logical_device_ptr_out = nullptr;
    }
    if (context == nullptr || path == nullptr || path[0] == '\0' ||
        length == 0 || options == nullptr || logical_device_ptr_out == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    const auto status = hbfsim::runtime::process_status(context);
    return status == HBFSIM_OK ? HBFSIM_UNSUPPORTED : status;
}

extern "C" int hbfsim_flush(hbfsim_context* context)
{
    return hbfsim::runtime::process_status(context);
}

extern "C" int hbfsim_unregister(hbfsim_context* context, void* range_base)
{
    if (context == nullptr || range_base == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    const auto status = hbfsim::runtime::process_status(context);
    return status == HBFSIM_OK ? HBFSIM_UNSUPPORTED : status;
}

extern "C" void hbfsim_context_destroy(hbfsim_context* context)
{
    hbfsim::runtime::release_context(context, true);
}

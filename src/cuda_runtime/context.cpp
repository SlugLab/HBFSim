#include "context.hpp"

#include "capacity_runtime.hpp"
#include "device_tensormap.hpp"
#include "range_table.hpp"

#include "../host_service/control_layout.hpp"
#include "../host_service/request_dispatcher.hpp"

#include <hbfsim/profile.hpp>
#include <hbfsim/exact_profile.hpp>
#include <hbfsim/launch_gate_abi.hpp>

#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
#include <cuda.h>
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
#include <condition_variable>
#include <dlfcn.h>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
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
#include <unordered_map>
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
    const hbfsim::LaunchGateApiV4* launch_gate_api_v4{nullptr};
    const hbfsim::LaunchGateApiV2* launch_gate_api_v2{nullptr};
    const hbfsim::LaunchGateApiV3* launch_gate_api_v3{nullptr};
    std::uint64_t control_generation{0};
    std::uintptr_t cuda_context{0};
    int device_ordinal{-1};
    bool timing_owner_active{false};
    bool daemon_ready{false};
    hbfsim::runtime::AfterBeginRetireHook after_begin_retire{nullptr};
    void* after_begin_retire_state{nullptr};
    std::unique_ptr<hbfsim::Profile> profile;
    std::unique_ptr<hbfsim::runtime::CapacityRuntime> capacity;
    std::array<std::unique_ptr<hbfsim::runtime::CapacityMapping>,
               hbfsim::host_service::kRangeCapacity>
        capacity_mappings;
    std::unique_ptr<hbfsim::runtime::RangeTable> ranges;
    std::uint64_t next_capacity_request_id{1};
    std::mutex capacity_mutex;
    std::mutex process_mutex;
};

namespace hbfsim::runtime {
namespace {

using host_service::ControlView;

bool launch_gate_available(const hbfsim_context* context) noexcept
{
    return context != nullptr &&
           (context->launch_gate_api_v4 != nullptr ||
            context->launch_gate_api_v3 != nullptr ||
            context->launch_gate_api_v2 != nullptr);
}

int launch_gate_activate(hbfsim_context* context, std::uintptr_t owner,
                         std::uintptr_t control_alias,
                         std::uintptr_t cuda_context, int device_ordinal,
                         std::uint64_t* generation) noexcept
{
    return context->launch_gate_api_v4 != nullptr
               ? context->launch_gate_api_v4->activate(
                     owner, control_alias, cuda_context, device_ordinal,
                     generation)
           : context->launch_gate_api_v3 != nullptr
               ? context->launch_gate_api_v3->activate(
                     owner, control_alias, cuda_context, device_ordinal,
                     generation)
               : context->launch_gate_api_v2->activate(
                     owner, control_alias, cuda_context, device_ordinal,
                     generation);
}

int launch_gate_register(hbfsim_context* context,
                         hbfsim::LaunchGateRangePolicy policy,
                         std::uintptr_t begin, std::uintptr_t end,
                         hbfsim::LaunchGatePublishRange publish,
                         void* publish_state) noexcept
{
    const auto owner = reinterpret_cast<std::uintptr_t>(context);
    if (context->launch_gate_api_v4 != nullptr) {
        return context->launch_gate_api_v4->register_range_with_policy(
            owner, context->control_generation, begin, end, policy, publish,
            publish_state);
    }
    if (context->launch_gate_api_v3 != nullptr) {
        return context->launch_gate_api_v3->register_range_with_policy(
            owner, context->control_generation, begin, end, policy, publish,
            publish_state);
    }
    return context->launch_gate_api_v2->register_range(
        owner, context->control_generation, begin, end, publish,
        publish_state);
}

int launch_gate_unregister(hbfsim_context* context, std::uintptr_t begin,
                           std::uintptr_t end,
                           hbfsim::LaunchGatePublishRange publish,
                           void* publish_state) noexcept
{
    const auto owner = reinterpret_cast<std::uintptr_t>(context);
    return context->launch_gate_api_v4 != nullptr
               ? context->launch_gate_api_v4->unregister_range(
                     owner, context->control_generation, begin, end, publish,
                     publish_state)
           : context->launch_gate_api_v3 != nullptr
               ? context->launch_gate_api_v3->unregister_range(
                     owner, context->control_generation, begin, end, publish,
                     publish_state)
               : context->launch_gate_api_v2->unregister_range(
                     owner, context->control_generation, begin, end, publish,
                     publish_state);
}

int launch_gate_begin_retire(hbfsim_context* context,
                             std::uintptr_t* token) noexcept
{
    const auto owner = reinterpret_cast<std::uintptr_t>(context);
    return context->launch_gate_api_v4 != nullptr
               ? context->launch_gate_api_v4->begin_retire(
                     owner, context->control_generation, token)
           : context->launch_gate_api_v3 != nullptr
               ? context->launch_gate_api_v3->begin_retire(
                     owner, context->control_generation, token)
               : context->launch_gate_api_v2->begin_retire(
                     owner, context->control_generation, token);
}

int launch_gate_invalidate(hbfsim_context* context,
                           std::uintptr_t token) noexcept
{
    return context->launch_gate_api_v4 != nullptr
               ? context->launch_gate_api_v4->invalidate_retire(token)
           : context->launch_gate_api_v3 != nullptr
               ? context->launch_gate_api_v3->invalidate_retire(token)
               : context->launch_gate_api_v2->invalidate_retire(token);
}

int launch_gate_finish(hbfsim_context* context,
                       std::uintptr_t token) noexcept
{
    return context->launch_gate_api_v4 != nullptr
               ? context->launch_gate_api_v4->finish_retire(token)
           : context->launch_gate_api_v3 != nullptr
               ? context->launch_gate_api_v3->finish_retire(token)
               : context->launch_gate_api_v2->finish_retire(token);
}

void launch_gate_quarantine(hbfsim_context* context,
                            std::uintptr_t token) noexcept
{
    if (context->launch_gate_api_v4 != nullptr) {
        (void)context->launch_gate_api_v4->quarantine_retire(token);
    } else if (context->launch_gate_api_v3 != nullptr) {
        (void)context->launch_gate_api_v3->quarantine_retire(token);
    } else {
        (void)context->launch_gate_api_v2->quarantine_retire(token);
    }
}

// Startup readiness is distinct from per-request timeout. The MQSim engine is
// constructed before hbfsimd publishes its first heartbeat and can require
// several seconds on a loaded host.
// MQSim construction can exceed ten seconds on a cold filesystem or a busy
// validation host. This timeout covers startup only; per-request deadlines
// remain governed by request_timeout_ns.
constexpr auto kDaemonStartupTimeout = std::chrono::seconds(30);

struct ContextAdmission {
    std::mutex mutex;
    std::condition_variable drained;
    std::size_t active_operations{0};
    bool closing{false};
    bool destroying{false};
};

std::mutex context_admissions_mutex;
std::unordered_map<hbfsim_context*, std::shared_ptr<ContextAdmission>>
    context_admissions;

bool register_context_admission(hbfsim_context* context) noexcept
{
    try {
        auto admission = std::make_shared<ContextAdmission>();
        std::lock_guard lock(context_admissions_mutex);
        return context_admissions.emplace(context, std::move(admission)).second;
    } catch (...) {
        return false;
    }
}

std::shared_ptr<ContextAdmission>
lookup_context_admission(hbfsim_context* context) noexcept
{
    std::lock_guard lock(context_admissions_mutex);
    const auto found = context_admissions.find(context);
    return found == context_admissions.end() ? nullptr : found->second;
}

class ContextOperation {
  public:
    explicit ContextOperation(hbfsim_context* context) noexcept
        : admission_(lookup_context_admission(context))
    {
        if (admission_ == nullptr) {
            return;
        }
        bool admitted = false;
        {
            std::lock_guard lock(admission_->mutex);
            if (!admission_->closing) {
                ++admission_->active_operations;
                admitted = true;
            }
        }
        if (!admitted) {
            admission_.reset();
        }
    }

    ~ContextOperation()
    {
        if (admission_ == nullptr) {
            return;
        }
        std::lock_guard lock(admission_->mutex);
        if (--admission_->active_operations == 0) {
            admission_->drained.notify_all();
        }
    }

    ContextOperation(const ContextOperation&) = delete;
    ContextOperation& operator=(const ContextOperation&) = delete;

    [[nodiscard]] explicit operator bool() const noexcept
    {
        return admission_ != nullptr;
    }

  private:
    std::shared_ptr<ContextAdmission> admission_;
};

std::shared_ptr<ContextAdmission>
close_context_admission(hbfsim_context* context) noexcept
{
    auto admission = lookup_context_admission(context);
    if (admission == nullptr) {
        return nullptr;
    }
    std::unique_lock lock(admission->mutex);
    if (admission->closing || admission->destroying) {
        return nullptr;
    }
    admission->closing = true;
    admission->destroying = true;
    admission->drained.notify_all();
    admission->drained.wait(
        lock, [&] { return admission->active_operations == 0; });
    return admission;
}

void reopen_context_admission(
    const std::shared_ptr<ContextAdmission>& admission) noexcept
{
    std::lock_guard lock(admission->mutex);
    admission->destroying = false;
    admission->closing = false;
}

void quarantine_context_admission(
    const std::shared_ptr<ContextAdmission>& admission) noexcept
{
    std::lock_guard lock(admission->mutex);
    admission->destroying = false;
}

void erase_context_admission(
    hbfsim_context* context,
    const std::shared_ptr<ContextAdmission>& admission) noexcept
{
    std::lock_guard lock(context_admissions_mutex);
    const auto found = context_admissions.find(context);
    if (found != context_admissions.end() && found->second == admission) {
        context_admissions.erase(found);
    }
}

enum class ReleaseResult { destroyed, retryable, quarantined };

int process_status(hbfsim_context* context) noexcept;
std::uint64_t monotonic_ns();
RequestStatus submit_capacity_program(hbfsim_context* context,
                                      std::uint32_t range_id,
                                      std::uint64_t global_page) noexcept;

int public_capacity_status(RequestStatus status) noexcept
{
    switch (status) {
    case RequestStatus::Ready:
        return HBFSIM_OK;
    case RequestStatus::CopyError:
        return HBFSIM_CUDA_ERROR;
    case RequestStatus::Timeout:
        return HBFSIM_TIMEOUT;
    case RequestStatus::Unsupported:
        return HBFSIM_UNSUPPORTED;
    case RequestStatus::DaemonLost:
        return HBFSIM_DAEMON_LOST;
    case RequestStatus::Pending:
    case RequestStatus::IoError:
    case RequestStatus::ChecksumError:
        return HBFSIM_IO_ERROR;
    }
    return HBFSIM_IO_ERROR;
}

int retirement_liveness(hbfsim_context* context) noexcept
{
    const auto status = process_status(context);
    if (status != HBFSIM_OK) {
        return status;
    }
    ControlView control(context->control_mapping, context->control_bytes);
    const auto heartbeat = host_service::atomic_load(
        control.header()->heartbeat_ns, std::memory_order_acquire);
    const auto timeout = control.header()->heartbeat_timeout_ns;
    const auto now = monotonic_ns();
    if (heartbeat == 0 || timeout == 0 || heartbeat > now ||
        now - heartbeat > timeout) {
        return HBFSIM_DAEMON_LOST;
    }
    return HBFSIM_OK;
}

bool cuda_domain_is_current(std::uintptr_t expected_context,
                            int expected_device) noexcept
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    CUcontext current_context = nullptr;
    CUdevice current_device = -1;
    return expected_context != 0 && expected_device >= 0 &&
           ::cuCtxGetCurrent(&current_context) == CUDA_SUCCESS &&
           reinterpret_cast<std::uintptr_t>(current_context) ==
               expected_context &&
           ::cuCtxGetDevice(&current_device) == CUDA_SUCCESS &&
           static_cast<int>(current_device) == expected_device;
#else
    (void)expected_context;
    (void)expected_device;
    return false;
#endif
}

int begin_retire_with_cuda(
    std::uintptr_t expected_context, int expected_device,
    std::uintptr_t owner, std::uint64_t generation,
    int (*begin_retire)(std::uintptr_t, std::uint64_t,
                        std::uintptr_t*) noexcept,
    std::uintptr_t* token_out) noexcept
{
    if (token_out == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    *token_out = 0;
    if (owner == 0 || generation == 0 || begin_retire == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (!cuda_domain_is_current(expected_context, expected_device)) {
        return HBFSIM_CUDA_ERROR;
    }
    return begin_retire(owner, generation, token_out) == 0 ? HBFSIM_OK
                                                           : HBFSIM_CUDA_ERROR;
}

int normalize_launch_gate_status(int gate_status) noexcept
{
    return gate_status == 0 ? HBFSIM_OK : HBFSIM_IO_ERROR;
}

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
           options->mode <= HBFSIM_MODEL_HYBRID &&
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

ReleaseResult release_context(hbfsim_context* context,
                              bool request_shutdown) noexcept
{
    if (context == nullptr) {
        return ReleaseResult::destroyed;
    }
    std::uintptr_t retire_token = 0;
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    if (context->timing_owner_active) {
        if (!launch_gate_available(context)) {
            return ReleaseResult::quarantined;
        }
        if (!cuda_domain_is_current(context->cuda_context,
                                    context->device_ordinal)) {
            // Fail closed: retaining the mapping and daemon is safer than
            // invalidating a control alias that an in-flight launch may use.
            return ReleaseResult::retryable;
        }
        const auto initial_liveness =
            context->daemon_ready ? retirement_liveness(context) : HBFSIM_OK;
        if (launch_gate_begin_retire(context, &retire_token) != 0) {
            return ReleaseResult::quarantined;
        }
        const auto quarantine = [&]() noexcept {
            launch_gate_quarantine(context, retire_token);
            retire_token = 0;
        };
        if (initial_liveness != HBFSIM_OK) {
            quarantine();
            return ReleaseResult::quarantined;
        }
        if (context->after_begin_retire != nullptr) {
            context->after_begin_retire(context->after_begin_retire_state);
        }
        if ((context->daemon_ready &&
             retirement_liveness(context) != HBFSIM_OK) ||
            !cuda_domain_is_current(context->cuda_context,
                                    context->device_ordinal)) {
            quarantine();
            return ReleaseResult::quarantined;
        }
        if (::cudaDeviceSynchronize() != cudaSuccess) {
            quarantine();
            return ReleaseResult::quarantined;
        }
        if (context->capacity &&
            context->capacity->flush(
                [context](std::uint32_t range_id,
                          std::uint64_t global_page) {
                    return submit_capacity_program(context, range_id,
                                                   global_page);
                }) != RequestStatus::Ready) {
            quarantine();
            return ReleaseResult::quarantined;
        }
        if (launch_gate_invalidate(context, retire_token) != 0) {
            quarantine();
            return ReleaseResult::quarantined;
        }
        ControlView control(context->control_mapping, context->control_bytes);
        control.header()->control_generation = 0;
    }
#endif
    if (context->capacity) {
        context->capacity->stop();
        for (auto& mapping : context->capacity_mappings) {
            if (mapping) {
                if (!mapping->logical_range.release()) {
                    if (retire_token != 0) {
                        launch_gate_quarantine(context, retire_token);
                        retire_token = 0;
                    }
                    return ReleaseResult::quarantined;
                }
            }
            if (mapping && mapping->active) {
                if (context->capacity->router().deactivate(
                        mapping->range_id) != RequestStatus::Ready) {
                    if (retire_token != 0) {
                        launch_gate_quarantine(context, retire_token);
                        retire_token = 0;
                    }
                    return ReleaseResult::quarantined;
                }
                mapping->active = false;
            }
            mapping.reset();
        }
        if (!context->capacity->release_cuda_resources()) {
            if (retire_token != 0) {
                launch_gate_quarantine(context, retire_token);
                retire_token = 0;
            }
            return ReleaseResult::quarantined;
        }
        context->capacity.reset();
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
        unbind_device_tensormap_domain(context->cuda_context,
                                       context->device_ordinal);
        if (::cudaHostUnregister(context->control_mapping) != cudaSuccess) {
            if (retire_token != 0) {
                launch_gate_quarantine(context, retire_token);
            }
            return ReleaseResult::quarantined;
        }
        context->cuda_registered = false;
        if (context->timing_owner_active) {
            if (launch_gate_finish(context, retire_token) != 0) {
                launch_gate_quarantine(context, retire_token);
                return ReleaseResult::quarantined;
            }
            retire_token = 0;
            context->timing_owner_active = false;
            context->control_generation = 0;
        }
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
    return ReleaseResult::destroyed;
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
                   void* hook_state, hbfsim_context** out,
                   const std::string* exact_profile_json = nullptr)
{
    if (out != nullptr) {
        *out = nullptr;
    }
    if (!valid_options(options, out)) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    hbfsim::Profile profile;
    try {
        profile = hbfsim::load_profile(options->profile_path);
    } catch (const hbfsim::ProfileError&) {
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
    context->profile = std::unique_ptr<Profile>(
        new (std::nothrow) Profile(profile));
    if (!context->profile) {
        release_context(context.release(), false);
        return HBFSIM_IO_ERROR;
    }
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
    control.header()->request_timeout_ns = options->request_timeout_ns;
    control.header()->heartbeat_timeout_ns =
        std::max<std::uint64_t>(options->request_timeout_ns, 50'000'000);
    control.header()->time_scale = profile.time_scale;
    control.header()->read_latency_ns = profile.read_latency_ns;
    control.header()->program_latency_ns = profile.program_latency_ns;
    control.header()->aggregate_bandwidth_bytes_per_s =
        profile.aggregate_bandwidth_bytes_per_s;
    control.header()->reference_warmup_requests =
        profile.reference_warmup_requests;
    control.header()->timing_model = options->mode;
    control.header()->empirical_burst_state = 0;
    std::fill(std::begin(control.header()->empirical_cumulative_ns),
              std::end(control.header()->empirical_cumulative_ns), 0);
    std::fill(std::begin(control.header()->empirical_breakpoint_pages),
              std::end(control.header()->empirical_breakpoint_pages), 0);
    control.header()->empirical_point_count = 0;
    control.header()->empirical_flags = 0;
    if (profile.empirical_vmem) {
        control.header()->empirical_flags = 1;
        control.header()->empirical_point_count =
            static_cast<std::uint32_t>(
                profile.empirical_vmem->read_curve.size());
        for (std::size_t index = 0;
             index < profile.empirical_vmem->read_curve.size(); ++index) {
            control.header()->empirical_breakpoint_pages[index] =
                profile.empirical_vmem->read_curve[index].pages;
            control.header()->empirical_cumulative_ns[index] =
                profile.empirical_vmem->read_curve[index].cumulative_ns;
        }
    }
    control.header()->reference_sample_threshold =
        profile.reference_sample_rate >= 1.0
            ? std::numeric_limits<std::uint64_t>::max()
            : static_cast<std::uint64_t>(
                  profile.reference_sample_rate *
                  static_cast<long double>(
                      std::numeric_limits<std::uint64_t>::max()));
    try {
        context->ranges = std::make_unique<RangeTable>(
            control, profile.page_bytes, profile.capacity_bytes);
    } catch (const std::bad_alloc&) {
        release_context(context.release(), false);
        return HBFSIM_IO_ERROR;
    }
    if (!context->ranges) {
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
        CUcontext cuda_context = nullptr;
        CUdevice device = -1;
        if (::cuCtxGetCurrent(&cuda_context) != CUDA_SUCCESS ||
            cuda_context == nullptr ||
            ::cuCtxGetDevice(&device) != CUDA_SUCCESS || device < 0) {
            release_context(context.release(), false);
            return HBFSIM_CUDA_ERROR;
        }
        using get_api_type = hbfsim::LaunchGateGetApi;
        auto get_api = reinterpret_cast<get_api_type>(
            ::dlsym(RTLD_DEFAULT, "hbfsim_launch_gate_get_api"));
        if (get_api != nullptr) {
            context->launch_gate_api_v4 =
                static_cast<const hbfsim::LaunchGateApiV4*>(
                    get_api(hbfsim::kLaunchGateAbiVersion));
            if (exact_profile_json == nullptr &&
                context->launch_gate_api_v4 == nullptr) {
                context->launch_gate_api_v3 =
                    static_cast<const hbfsim::LaunchGateApiV3*>(
                        get_api(hbfsim::kLaunchGateAbiVersionV3));
            }
            if (exact_profile_json == nullptr &&
                context->launch_gate_api_v4 == nullptr &&
                context->launch_gate_api_v3 == nullptr) {
                context->launch_gate_api_v2 =
                    static_cast<const hbfsim::LaunchGateApiV2*>(
                        get_api(hbfsim::kLaunchGateAbiVersionV2));
            }
        }
        const auto valid_v4 = context->launch_gate_api_v4 != nullptr &&
                              context->launch_gate_api_v4->abi_version ==
                                  hbfsim::kLaunchGateAbiVersion &&
                              context->launch_gate_api_v4->struct_bytes >=
                                  sizeof(hbfsim::LaunchGateApiV4) &&
                              context->launch_gate_api_v4->activate != nullptr &&
                              context->launch_gate_api_v4->register_range !=
                                  nullptr &&
                              context->launch_gate_api_v4->unregister_range !=
                                  nullptr &&
                              context->launch_gate_api_v4->begin_retire !=
                                  nullptr &&
                              context->launch_gate_api_v4->invalidate_retire !=
                                  nullptr &&
                              context->launch_gate_api_v4->finish_retire !=
                                  nullptr &&
                              context->launch_gate_api_v4->quarantine_retire !=
                                  nullptr &&
                              context->launch_gate_api_v4
                                      ->register_range_with_policy != nullptr &&
                              context->launch_gate_api_v4->configure_exact !=
                                  nullptr &&
                              context->launch_gate_api_v4
                                      ->publish_run_contract != nullptr;
        const auto valid_v3 = context->launch_gate_api_v3 != nullptr &&
                              context->launch_gate_api_v3->abi_version ==
                                  hbfsim::kLaunchGateAbiVersionV3 &&
                              context->launch_gate_api_v3->struct_bytes >=
                                  sizeof(hbfsim::LaunchGateApiV3) &&
                              context->launch_gate_api_v3->activate != nullptr &&
                              context->launch_gate_api_v3->register_range !=
                                  nullptr &&
                              context->launch_gate_api_v3->unregister_range !=
                                  nullptr &&
                              context->launch_gate_api_v3->begin_retire !=
                                  nullptr &&
                              context->launch_gate_api_v3->invalidate_retire !=
                                  nullptr &&
                              context->launch_gate_api_v3->finish_retire !=
                                  nullptr &&
                              context->launch_gate_api_v3->quarantine_retire !=
                                  nullptr &&
                              context->launch_gate_api_v3
                                      ->register_range_with_policy != nullptr;
        const auto valid_v2 = context->launch_gate_api_v3 == nullptr &&
                              context->launch_gate_api_v2 != nullptr &&
                              context->launch_gate_api_v2->abi_version ==
                                  hbfsim::kLaunchGateAbiVersionV2 &&
                              context->launch_gate_api_v2->struct_bytes >=
                                  sizeof(hbfsim::LaunchGateApiV2) &&
                              context->launch_gate_api_v2->activate != nullptr &&
                              context->launch_gate_api_v2->register_range !=
                                  nullptr &&
                              context->launch_gate_api_v2->unregister_range !=
                                  nullptr &&
                              context->launch_gate_api_v2->begin_retire !=
                                  nullptr &&
                              context->launch_gate_api_v2->invalidate_retire !=
                                  nullptr &&
                              context->launch_gate_api_v2->finish_retire !=
                                  nullptr &&
                              context->launch_gate_api_v2->quarantine_retire !=
                                  nullptr;
        const auto exact_requested = exact_profile_json != nullptr;
        if ((exact_requested ? !valid_v4
                             : (!valid_v4 && !valid_v3 && !valid_v2)) ||
            launch_gate_activate(
                context.get(), reinterpret_cast<std::uintptr_t>(context.get()),
                reinterpret_cast<std::uintptr_t>(context->device_control),
                reinterpret_cast<std::uintptr_t>(cuda_context),
                static_cast<int>(device), &context->control_generation) != 0 ||
            context->control_generation == 0) {
            release_context(context.release(), false);
            return HBFSIM_CUDA_ERROR;
        }
        context->cuda_context =
            reinterpret_cast<std::uintptr_t>(cuda_context);
        context->device_ordinal = static_cast<int>(device);
        context->timing_owner_active = true;
        control.header()->control_generation = context->control_generation;
        if (!bind_device_tensormap_domain(context->cuda_context,
                                          context->device_ordinal,
                                          control)) {
            release_context(context.release(), false);
            return HBFSIM_IO_ERROR;
        }
        if (exact_requested &&
            context->launch_gate_api_v4->configure_exact(
                reinterpret_cast<std::uintptr_t>(context.get()),
                context->control_generation, exact_profile_json->data(),
                exact_profile_json->size()) != 0) {
            release_context(context.release(), false);
            return HBFSIM_INVALID_ARGUMENT;
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
    context->daemon_ready = true;
    if (!register_context_admission(context.get())) {
        (void)release_context(context.release(), true);
        return HBFSIM_IO_ERROR;
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
        const auto status = retirement_liveness(context);
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

struct RangeGateInvocation {
    hbfsim_context* context;
    std::uintptr_t control_alias;
    TimingRangeGateHook hook;
    void* hook_state;
};

int publish_range_to_gate(const host_service::SharedRangeRecord& record,
                          PublishRange publish, void* publish_state,
                          void* opaque) noexcept
{
    auto& invocation = *static_cast<RangeGateInvocation*>(opaque);
    return invocation.hook(
        invocation.context, invocation.control_alias, record.base,
        static_cast<std::uintptr_t>(record.base + record.length),
        publish, publish_state, invocation.hook_state);
}

int register_device_with_gate(
    hbfsim_context* context, void* device_ptr, std::size_t length,
    const hbfsim_range_options* options, std::uintptr_t control_alias,
    TimingRangeGateHook gate, void* gate_state) noexcept
{
    if (context == nullptr || device_ptr == nullptr || length == 0 ||
        options == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (options->mode != HBFSIM_RANGE_MODE_TIMING) {
        return options->mode == HBFSIM_RANGE_MODE_CAPACITY
                   ? HBFSIM_UNSUPPORTED
                   : HBFSIM_INVALID_ARGUMENT;
    }
    const auto process = process_status(context);
    if (process != HBFSIM_OK) {
        return process;
    }
    if (gate == nullptr || control_alias == 0 || !context->ranges) {
        return HBFSIM_IO_ERROR;
    }
    RangeGateInvocation invocation{
        .context = context,
        .control_alias = control_alias,
        .hook = gate,
        .hook_state = gate_state,
    };
    return context->ranges->add(reinterpret_cast<std::uintptr_t>(device_ptr),
                                length, *options, publish_range_to_gate,
                                &invocation);
}

int validate_device_range_with_cuda(std::uintptr_t expected_context,
                                    int expected_device, void* device_ptr,
                                    std::size_t length) noexcept
{
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    const auto address = reinterpret_cast<std::uintptr_t>(device_ptr);
    if (expected_context == 0 || expected_device < 0 || address == 0 ||
        length == 0 ||
        length > std::numeric_limits<std::uintptr_t>::max() - address) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    CUcontext current_context = nullptr;
    CUdevice current_device = -1;
    CUcontext pointer_context = nullptr;
    int pointer_device = -1;
    CUmemorytype memory_type = CU_MEMORYTYPE_HOST;
    unsigned int is_managed = 1;
    const auto pointer = static_cast<CUdeviceptr>(address);
    if (::cuCtxGetCurrent(&current_context) != CUDA_SUCCESS ||
        reinterpret_cast<std::uintptr_t>(current_context) != expected_context ||
        ::cuCtxGetDevice(&current_device) != CUDA_SUCCESS ||
        static_cast<int>(current_device) != expected_device ||
        ::cuPointerGetAttribute(&pointer_context, CU_POINTER_ATTRIBUTE_CONTEXT,
                                pointer) != CUDA_SUCCESS ||
        reinterpret_cast<std::uintptr_t>(pointer_context) != expected_context ||
        ::cuPointerGetAttribute(&pointer_device,
                                CU_POINTER_ATTRIBUTE_DEVICE_ORDINAL,
                                pointer) != CUDA_SUCCESS ||
        pointer_device != expected_device ||
        ::cuPointerGetAttribute(&memory_type,
                                CU_POINTER_ATTRIBUTE_MEMORY_TYPE,
                                pointer) != CUDA_SUCCESS ||
        memory_type != CU_MEMORYTYPE_DEVICE ||
        ::cuPointerGetAttribute(&is_managed, CU_POINTER_ATTRIBUTE_IS_MANAGED,
                                pointer) != CUDA_SUCCESS ||
        is_managed != 0) {
        return HBFSIM_CUDA_ERROR;
    }
    CUdeviceptr allocation_base = 0;
    std::size_t allocation_bytes = 0;
    if (::cuMemGetAddressRange(&allocation_base, &allocation_bytes, pointer) !=
        CUDA_SUCCESS) {
        return HBFSIM_CUDA_ERROR;
    }
    const auto base = static_cast<std::uintptr_t>(allocation_base);
    if (address < base || address - base > allocation_bytes ||
        length > allocation_bytes - (address - base)) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    return HBFSIM_OK;
#else
    (void)expected_context;
    (void)expected_device;
    (void)device_ptr;
    (void)length;
    return HBFSIM_CUDA_ERROR;
#endif
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
        const auto status = retirement_liveness(context);
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

int register_device_with_gate_for_test(
    hbfsim_context* context, void* device_ptr, std::size_t length,
    const hbfsim_range_options* options, std::uintptr_t control_alias,
    TimingRangeGateHook gate, void* gate_state) noexcept
{
    return register_device_with_gate(context, device_ptr, length, options,
                                     control_alias, gate, gate_state);
}

std::uint32_t range_count_for_test(hbfsim_context* context) noexcept
{
    if (context == nullptr || context->control_mapping == MAP_FAILED) {
        return 0;
    }
    ControlView control(context->control_mapping, context->control_bytes);
    return host_service::atomic_load(control.header()->range_count,
                                     std::memory_order_acquire);
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

int validate_device_range_with_cuda_for_test(
    std::uintptr_t expected_context, int expected_device, void* device_ptr,
    std::size_t length) noexcept
{
    return validate_device_range_with_cuda(expected_context, expected_device,
                                           device_ptr, length);
}

int begin_retire_with_cuda_for_test(
    std::uintptr_t expected_context, int expected_device,
    std::uintptr_t owner, std::uint64_t generation,
    int (*begin_retire)(std::uintptr_t, std::uint64_t,
                        std::uintptr_t*) noexcept,
    std::uintptr_t* token_out) noexcept
{
    return begin_retire_with_cuda(expected_context, expected_device, owner,
                                  generation, begin_retire, token_out);
}

int normalize_launch_gate_status_for_test(int gate_status) noexcept
{
    return normalize_launch_gate_status(gate_status);
}

int retirement_liveness_for_test(hbfsim_context* context) noexcept
{
    return retirement_liveness(context);
}

bool wait_for_context_closing_for_test(
    hbfsim_context* context, std::chrono::milliseconds timeout) noexcept
{
    auto admission = lookup_context_admission(context);
    if (admission == nullptr) {
        return false;
    }
    std::unique_lock lock(admission->mutex);
    return admission->drained.wait_for(
        lock, timeout, [&] { return admission->closing; });
}

void set_after_begin_retire_hook_for_test(
    hbfsim_context* context, AfterBeginRetireHook hook, void* state) noexcept
{
    if (context != nullptr) {
        context->after_begin_retire = hook;
        context->after_begin_retire_state = state;
    }
}

namespace {

RequestStatus submit_capacity_program(hbfsim_context* context,
                                      std::uint32_t range_id,
                                      std::uint64_t global_page) noexcept
{
    if (context == nullptr || !context->profile || range_id == 0 ||
        context->profile->page_bytes == 0 ||
        global_page > std::numeric_limits<std::uint64_t>::max() /
                          context->profile->page_bytes ||
        context->next_capacity_request_id == 0) {
        return RequestStatus::IoError;
    }
    const auto request_id = context->next_capacity_request_id;
    context->next_capacity_request_id =
        request_id == std::numeric_limits<std::uint64_t>::max()
            ? 0
            : request_id + 1;
    const HbfRequest request{
        .request_id = request_id,
        .sequence = 0,
        .arrival_ns = monotonic_ns(),
        .logical_address =
            global_page * context->profile->page_bytes,
        .deadline_ns = 0,
        .bytes = context->profile->page_bytes,
        .range_id = range_id,
        .stream_id = 0,
        .operation =
            static_cast<std::uint32_t>(RequestOperation::Write),
        .page_generation = 1,
        .flags = host_service::kRequestFlagExplicitCapacityProgram,
    };
    std::uint64_t ticket = 0;
    const auto enqueued = enqueue_with_deadline(context, request, &ticket);
    if (enqueued != HBFSIM_OK) {
        switch (enqueued) {
        case HBFSIM_TIMEOUT:
            return RequestStatus::Timeout;
        case HBFSIM_DAEMON_LOST:
            return RequestStatus::DaemonLost;
        case HBFSIM_UNSUPPORTED:
            return RequestStatus::Unsupported;
        default:
            return RequestStatus::IoError;
        }
    }
    HbfCompletion completion{};
    const auto waited = wait_for_completion_for_test(
        context, ticket, request.request_id, &completion);
    if (waited != HBFSIM_OK) {
        switch (waited) {
        case HBFSIM_TIMEOUT:
            return RequestStatus::Timeout;
        case HBFSIM_DAEMON_LOST:
            return RequestStatus::DaemonLost;
        default:
            return RequestStatus::IoError;
        }
    }
    if (completion.status >
        static_cast<std::uint32_t>(RequestStatus::DaemonLost)) {
        return RequestStatus::IoError;
    }
    return static_cast<RequestStatus>(completion.status);
}

}  // namespace

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

extern "C" int hbfsim_context_create_v2(const hbfsim_options_v2* options,
                                         hbfsim_context** out)
{
    if (out != nullptr) {
        *out = nullptr;
    }
    if (out == nullptr || options == nullptr ||
        options->struct_bytes != sizeof(hbfsim_options_v2)) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (options->fidelity != HBFSIM_FIDELITY_EMULATION &&
        options->fidelity != HBFSIM_FIDELITY_EXACT_SM120) {
        return HBFSIM_INVALID_ARGUMENT;
    }

    std::string exact_profile_json;
    if (options->fidelity == HBFSIM_FIDELITY_EXACT_SM120) {
        if (options->exact_profile_path == nullptr ||
            options->exact_profile_path[0] == '\0') {
            return HBFSIM_INVALID_ARGUMENT;
        }
        std::ifstream input(options->exact_profile_path, std::ios::binary);
        if (!input.good()) {
            return HBFSIM_IO_ERROR;
        }
        exact_profile_json.assign(std::istreambuf_iterator<char>(input), {});
        if (input.bad()) {
            return HBFSIM_IO_ERROR;
        }
        try {
            (void)hbfsim::parse_exact_profile(exact_profile_json);
        } catch (const hbfsim::ExactProfileError&) {
            return HBFSIM_INVALID_ARGUMENT;
        }
    }

    const char* daemon = std::getenv("HBFSIM_DAEMON_PATH");
    if (daemon == nullptr || daemon[0] == '\0') {
        daemon = "hbfsimd";
    }
    const auto* exact = options->fidelity == HBFSIM_FIDELITY_EXACT_SM120
                            ? &exact_profile_json
                            : nullptr;
    return hbfsim::runtime::create_context(&options->base, daemon, true,
                                           nullptr, nullptr, out, exact);
}

extern "C" int hbfsim_register_device(
    hbfsim_context* context, void* device_ptr, size_t length,
    const hbfsim_range_options* options)
{
    if (context == nullptr || device_ptr == nullptr || length == 0 ||
        options == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (options->mode != HBFSIM_RANGE_MODE_TIMING) {
        return options->mode == HBFSIM_RANGE_MODE_CAPACITY
                   ? HBFSIM_UNSUPPORTED
                   : HBFSIM_INVALID_ARGUMENT;
    }
    hbfsim::runtime::ContextOperation operation(context);
    if (!operation) {
        return HBFSIM_IO_ERROR;
    }
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    if (!context->cuda_registered || context->device_control == nullptr) {
        return HBFSIM_CUDA_ERROR;
    }
    const auto validation = hbfsim::runtime::validate_device_range_with_cuda(
        context->cuda_context, context->device_ordinal, device_ptr, length);
    if (validation != HBFSIM_OK) {
        return validation;
    }
    if (!context->timing_owner_active ||
        !hbfsim::runtime::launch_gate_available(context) ||
        context->control_generation == 0) {
        return HBFSIM_IO_ERROR;
    }
    const auto adapter = +[](void* owner, std::uintptr_t,
                             std::uintptr_t begin, std::uintptr_t end,
                             hbfsim::runtime::PublishRange publish,
                             void* publish_state,
                             void* state) noexcept -> int {
        auto& context = *static_cast<hbfsim_context*>(state);
        struct Publication {
            hbfsim::runtime::PublishRange publish;
            void* state;
        } publication{publish, publish_state};
        const auto acknowledge = +[](void* opaque) noexcept {
            auto& item = *static_cast<Publication*>(opaque);
            item.publish(item.state);
            return 0;
        };
        (void)owner;
        const auto gate_status = hbfsim::runtime::launch_gate_register(
            &context, hbfsim::LaunchGateRangePolicy::TimingBacked, begin, end,
            acknowledge, &publication);
        return hbfsim::runtime::normalize_launch_gate_status(gate_status);
    };
    return hbfsim::runtime::register_device_with_gate(
        context, device_ptr, length, options,
        reinterpret_cast<std::uintptr_t>(context->device_control), adapter,
        context);
#else
    return HBFSIM_CUDA_ERROR;
#endif
}

extern "C" int hbfsim_map_file(hbfsim_context* context, const char* path,
                               uint64_t file_offset, size_t length,
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
    hbfsim::runtime::ContextOperation operation(context);
    if (!operation) {
        return HBFSIM_IO_ERROR;
    }
    if (options->mode != HBFSIM_RANGE_MODE_CAPACITY) {
        return options->mode == HBFSIM_RANGE_MODE_TIMING
                   ? HBFSIM_UNSUPPORTED
                   : HBFSIM_INVALID_ARGUMENT;
    }
    if ((options->permissions != HBFSIM_RANGE_READ &&
         options->permissions != HBFSIM_RANGE_READ_WRITE) ||
        options->cache_policy != HBFSIM_CACHE_POLICY_NONE ||
        file_offset > std::numeric_limits<std::uint64_t>::max() - length) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    const auto process = hbfsim::runtime::retirement_liveness(context);
    if (process != HBFSIM_OK) {
        return process;
    }
    hbfsim::host_service::ControlView control(context->control_mapping,
                                               context->control_bytes);
    if ((hbfsim::host_service::atomic_load(
             control.header()->reserved0, std::memory_order_acquire) &
         hbfsim::host_service::kControlCapabilityCapacityMedia) == 0) {
        return HBFSIM_UNSUPPORTED;
    }
    if (!context->cuda_registered) {
        return HBFSIM_UNSUPPORTED;
    }
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    if (!context->cuda_registered || context->device_control == nullptr ||
        !context->timing_owner_active ||
        !hbfsim::runtime::launch_gate_available(context) ||
        context->control_generation == 0 || !context->profile ||
        !context->ranges ||
        !hbfsim::runtime::cuda_domain_is_current(context->cuda_context,
                                                 context->device_ordinal)) {
        return HBFSIM_CUDA_ERROR;
    }

    std::lock_guard lifecycle(context->capacity_mutex);
    auto vacant = context->capacity_mappings.end();
    for (auto item = context->capacity_mappings.begin();
         item != context->capacity_mappings.end(); ++item) {
        if (!*item) {
            vacant = item;
            break;
        }
    }
    if (vacant == context->capacity_mappings.end()) {
        return HBFSIM_UNSUPPORTED;
    }

    try {
        auto backing =
            std::make_shared<hbfsim::host_service::BackingStore>(
                path, file_offset, length,
                options->permissions == HBFSIM_RANGE_READ_WRITE);
        if (!context->capacity) {
            context->capacity = hbfsim::runtime::CapacityRuntime::create(
                *context->profile, control, context->cuda_context,
                context->device_ordinal);
            if (!context->capacity) {
                return HBFSIM_CUDA_ERROR;
            }
        }

        auto mapping = std::make_unique<hbfsim::runtime::CapacityMapping>();
        mapping->backing = std::move(backing);
        mapping->logical_range = hbfsim::runtime::VmmRange::reserve_logical(
            context->capacity->vmm(), length, 0, context->device_ordinal);

        struct MapTransaction {
            hbfsim_context* context;
            hbfsim::runtime::CapacityMapping* mapping;
            std::uint32_t permissions;
        } transaction{context, mapping.get(), options->permissions};
        const auto publish = +[](
                                 const hbfsim::host_service::SharedRangeRecord&
                                     record,
                                 hbfsim::runtime::PublishRange publish_range,
                                 void* publish_state,
                                 void* opaque) noexcept -> int {
            auto& item = *static_cast<MapTransaction*>(opaque);
            auto& runtime = *item.context->capacity;
            auto& mapped = *item.mapping;
            const auto page_bytes = record.page_bytes;
            if (page_bytes == 0 || record.file_offset % page_bytes != 0 ||
                record.length >
                    std::numeric_limits<std::uint64_t>::max() -
                        (page_bytes - 1)) {
                return HBFSIM_INVALID_ARGUMENT;
            }
            const auto first_page = record.file_offset / page_bytes;
            const auto page_count =
                (record.length + page_bytes - 1) / page_bytes;
            hbfsim::host_service::CapacityBackingRouter::Token token = 0;
            try {
                token = runtime.router().stage(
                    record.range_id, first_page, page_count,
                    item.permissions == HBFSIM_RANGE_READ_WRITE,
                    mapped.backing);
            } catch (...) {
                return HBFSIM_IO_ERROR;
            }
            if (token == 0) {
                return HBFSIM_IO_ERROR;
            }
            struct GatePublication {
                hbfsim::runtime::CapacityMapping* mapping;
                hbfsim::host_service::CapacityBackingRouter* router;
                hbfsim::runtime::PublishRange publish;
                void* publish_state;
                std::uint32_t range_id;
                std::uint64_t first_page;
                std::uint64_t page_count;
                hbfsim::host_service::CapacityBackingRouter::Token token;
            } publication{&mapped,
                          &runtime.router(),
                          publish_range,
                          publish_state,
                          record.range_id,
                          first_page,
                          page_count,
                          token};
            const auto acknowledge = +[](void* state) noexcept -> int {
                auto& staged = *static_cast<GatePublication*>(state);
                if (!staged.router->activate(staged.token)) {
                    return -1;
                }
                staged.mapping->range_id = staged.range_id;
                staged.mapping->first_page = staged.first_page;
                staged.mapping->page_count = staged.page_count;
                staged.mapping->router_token = staged.token;
                staged.mapping->active = true;
                staged.publish(staged.publish_state);
                return 0;
            };
            const auto gate_status = hbfsim::runtime::launch_gate_register(
                item.context,
                hbfsim::LaunchGateRangePolicy::CapacityUnbacked, record.base,
                record.base + record.length, acknowledge, &publication);
            if (!mapped.active) {
                runtime.router().cancel(token);
            }
            return hbfsim::runtime::normalize_launch_gate_status(gate_status);
        };
        const auto status = context->ranges->add(
            mapping->logical_range.base(), length, *options, publish,
            &transaction);
        if (status != HBFSIM_OK) {
            if (!mapping->logical_range.release()) {
                *vacant = std::move(mapping);
                return HBFSIM_CUDA_ERROR;
            }
            return status;
        }
        *logical_device_ptr_out =
            reinterpret_cast<void*>(mapping->logical_range.base());
        *vacant = std::move(mapping);
        return HBFSIM_OK;
    } catch (const hbfsim::host_service::BackingStoreError&) {
        return HBFSIM_IO_ERROR;
    } catch (const hbfsim::runtime::VmmError&) {
        return HBFSIM_CUDA_ERROR;
    } catch (...) {
        return HBFSIM_IO_ERROR;
    }
#else
    return HBFSIM_CUDA_ERROR;
#endif
}

extern "C" int hbfsim_flush(hbfsim_context* context)
{
    if (context == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    hbfsim::runtime::ContextOperation operation(context);
    if (!operation) {
        return HBFSIM_IO_ERROR;
    }
    const auto process = hbfsim::runtime::retirement_liveness(context);
    if (process != HBFSIM_OK) {
        return process;
    }
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    if (context->cuda_registered &&
        !hbfsim::runtime::cuda_domain_is_current(context->cuda_context,
                                                 context->device_ordinal)) {
        return HBFSIM_CUDA_ERROR;
    }
#endif
    std::lock_guard lifecycle(context->capacity_mutex);
    if (!context->capacity) {
        return HBFSIM_OK;
    }
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    const auto status = context->capacity->flush(
        [context](std::uint32_t range_id, std::uint64_t global_page) {
            return hbfsim::runtime::submit_capacity_program(
                context, range_id, global_page);
        });
    return hbfsim::runtime::public_capacity_status(status);
#else
    return HBFSIM_CUDA_ERROR;
#endif
}

extern "C" int hbfsim_get_stats(hbfsim_context* context,
                                  hbfsim_stats* out)
{
    if (context == nullptr || out == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    hbfsim::runtime::ContextOperation operation(context);
    if (!operation || context->control_mapping == MAP_FAILED) {
        return HBFSIM_IO_ERROR;
    }
    hbfsim::host_service::ControlView control(context->control_mapping,
                                               context->control_bytes);
    if (!control.valid()) {
        return HBFSIM_IO_ERROR;
    }
    const auto* header = control.header();
    *out = {
        .requests_submitted = hbfsim::host_service::atomic_load(
            header->request_producer, std::memory_order_acquire),
        .requests_completed = hbfsim::host_service::atomic_load(
            header->completion_consumer, std::memory_order_acquire),
        .fast_requests = hbfsim::host_service::atomic_load(
            header->fast_requests, std::memory_order_acquire),
        .reference_requests = hbfsim::host_service::atomic_load(
            header->reference_requests, std::memory_order_acquire),
        .fast_modeled_ns = hbfsim::host_service::atomic_load(
            header->fast_modeled_ns, std::memory_order_acquire),
    };
    return HBFSIM_OK;
}

extern "C" int hbfsim_get_future_stats(hbfsim_context* context,
                                         hbfsim_future_stats* out)
{
    if (context == nullptr || out == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    hbfsim::runtime::ContextOperation operation(context);
    if (!operation || context->control_mapping == MAP_FAILED) {
        return HBFSIM_IO_ERROR;
    }
    hbfsim::host_service::ControlView control(context->control_mapping,
                                               context->control_bytes);
    if (!control.valid()) {
        return HBFSIM_IO_ERROR;
    }
    const auto* header = control.header();
    const auto issued = hbfsim::host_service::atomic_load(
        header->future_issued, std::memory_order_acquire);
    const auto drained = hbfsim::host_service::atomic_load(
        header->future_drained, std::memory_order_acquire);
    *out = {
        .issued = issued,
        .issue_throttle_ns = hbfsim::host_service::atomic_load(
            header->future_issue_throttle_ns, std::memory_order_acquire),
        .dependency_wait_ns = hbfsim::host_service::atomic_load(
            header->future_dependency_wait_ns, std::memory_order_acquire),
        .ordering_wait_ns = hbfsim::host_service::atomic_load(
            header->future_ordering_wait_ns, std::memory_order_acquire),
        .drained = drained,
        .leaked = issued > drained ? issued - drained : 0,
        .faults = hbfsim::host_service::atomic_load(
            header->future_faults, std::memory_order_acquire),
    };
    return HBFSIM_OK;
}

extern "C" int hbfsim_get_tma_stats(hbfsim_context* context,
                                      hbfsim_tma_stats* out)
{
    if (context == nullptr || out == nullptr) return HBFSIM_INVALID_ARGUMENT;
    hbfsim::runtime::ContextOperation operation(context);
    if (!operation || context->control_mapping == MAP_FAILED) {
        return HBFSIM_IO_ERROR;
    }
    hbfsim::host_service::ControlView control(context->control_mapping,
                                               context->control_bytes);
    if (!control.valid()) return HBFSIM_IO_ERROR;
    const auto* header = control.header();
    *out = {
        .issued = hbfsim::host_service::atomic_load(
            header->tma_issued, std::memory_order_acquire),
        .hbm_bytes = hbfsim::host_service::atomic_load(
            header->tma_hbm_bytes, std::memory_order_acquire),
        .hbf_bytes = hbfsim::host_service::atomic_load(
            header->tma_hbf_bytes, std::memory_order_acquire),
        .oob_bytes = hbfsim::host_service::atomic_load(
            header->tma_oob_bytes, std::memory_order_acquire),
        .fanout_targets = hbfsim::host_service::atomic_load(
            header->tma_fanout_targets, std::memory_order_acquire),
        .barrier_wait_ns = hbfsim::host_service::atomic_load(
            header->tma_barrier_wait_ns, std::memory_order_acquire),
        .group_wait_ns = hbfsim::host_service::atomic_load(
            header->tma_group_wait_ns, std::memory_order_acquire),
        .stale_generations = hbfsim::host_service::atomic_load(
            header->tma_stale_generations, std::memory_order_acquire),
        .faults = hbfsim::host_service::atomic_load(
            header->tma_faults, std::memory_order_acquire),
        .leaked = hbfsim::host_service::atomic_load(
            header->tma_leaked, std::memory_order_acquire),
    };
    return HBFSIM_OK;
}

extern "C" int hbfsim_unregister(hbfsim_context* context, void* range_base)
{
    if (context == nullptr || range_base == nullptr) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    hbfsim::runtime::ContextOperation operation(context);
    if (!operation) {
        return HBFSIM_IO_ERROR;
    }
    const auto process = hbfsim::runtime::retirement_liveness(context);
    if (process != HBFSIM_OK) {
        return process;
    }
    std::lock_guard lifecycle(context->capacity_mutex);
#if defined(HBFSIM_ENABLE_CUDA_RUNTIME)
    if (!hbfsim::runtime::cuda_domain_is_current(context->cuda_context,
                                                 context->device_ordinal) ||
        !hbfsim::runtime::launch_gate_available(context) ||
        context->control_generation == 0) {
        return HBFSIM_CUDA_ERROR;
    }
    const auto base = reinterpret_cast<std::uintptr_t>(range_base);
    const auto existing = context->ranges == nullptr
                              ? hbfsim::runtime::RangeLookup{}
                              : context->ranges->lookup(base, 1);
    if (existing.kind != hbfsim::runtime::RangeLookupKind::Matched ||
        existing.record == nullptr || existing.record->base != base) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    if (existing.record->mode == HBFSIM_RANGE_MODE_TIMING) {
        struct TimingRemoval {
            hbfsim_context* context;
            int failure{HBFSIM_IO_ERROR};
        } transaction{context};
        const auto remove = +[](
                                const hbfsim::host_service::SharedRangeRecord&
                                    record,
                                hbfsim::runtime::PublishRange publish,
                                void* publish_state,
                                void* opaque) noexcept -> int {
            auto& item = *static_cast<TimingRemoval*>(opaque);
            if (record.mode != HBFSIM_RANGE_MODE_TIMING) {
                return HBFSIM_INVALID_ARGUMENT;
            }
            struct GateRemoval {
                hbfsim::runtime::PublishRange publish;
                void* publish_state;
            } removal{publish, publish_state};
            const auto acknowledge = +[](void* state) noexcept -> int {
                auto& pending = *static_cast<GateRemoval*>(state);
                pending.publish(pending.publish_state);
                return 0;
            };
            const auto status = hbfsim::runtime::launch_gate_unregister(
                item.context, record.base, record.base + record.length,
                acknowledge, &removal);
            item.failure = hbfsim::runtime::normalize_launch_gate_status(
                status);
            return item.failure;
        };
        return context->ranges->remove(base, remove, &transaction);
    }
    if (!context->capacity) {
        return HBFSIM_UNSUPPORTED;
    }
    auto found = context->capacity_mappings.end();
    for (auto item = context->capacity_mappings.begin();
         item != context->capacity_mappings.end(); ++item) {
        if (*item && (*item)->logical_range.base() == base) {
            found = item;
            break;
        }
    }
    if (found == context->capacity_mappings.end() || !context->capacity ||
        !context->ranges) {
        return HBFSIM_INVALID_ARGUMENT;
    }
    struct UnregisterTransaction {
        hbfsim_context* context;
        hbfsim::runtime::CapacityMapping* mapping;
        int failure{HBFSIM_IO_ERROR};
    } transaction{context, found->get()};
    const auto remove = +[](
                            const hbfsim::host_service::SharedRangeRecord&
                                record,
                            hbfsim::runtime::PublishRange publish_range,
                            void* publish_state,
                            void* opaque) noexcept -> int {
        auto& item = *static_cast<UnregisterTransaction*>(opaque);
        struct GateRemoval {
            UnregisterTransaction* transaction;
            hbfsim::runtime::PublishRange publish;
            void* publish_state;
        } removal{&item, publish_range, publish_state};
        const auto acknowledge = +[](void* state) noexcept -> int {
            auto& pending = *static_cast<GateRemoval*>(state);
            auto& transaction = *pending.transaction;
            auto* context = transaction.context;
            const auto flushed = context->capacity->flush(
                [context](std::uint32_t range_id,
                          std::uint64_t global_page) {
                    return hbfsim::runtime::submit_capacity_program(
                        context, range_id, global_page);
                },
                transaction.mapping->range_id);
            if (flushed != hbfsim::RequestStatus::Ready) {
                transaction.failure =
                    hbfsim::runtime::public_capacity_status(flushed);
                return -1;
            }
            if (!transaction.mapping->logical_range.release()) {
                transaction.failure = HBFSIM_CUDA_ERROR;
                return -1;
            }
            if (context->capacity->router().deactivate(
                    transaction.mapping->range_id) !=
                hbfsim::RequestStatus::Ready) {
                transaction.failure = HBFSIM_IO_ERROR;
                return -1;
            }
            transaction.mapping->active = false;
            pending.publish(pending.publish_state);
            transaction.failure = HBFSIM_OK;
            return 0;
        };
        const auto gate_status = hbfsim::runtime::launch_gate_unregister(
            item.context, record.base, record.base + record.length,
            acknowledge, &removal);
        return gate_status == 0 ? HBFSIM_OK : item.failure;
    };
    const auto status = context->ranges->remove(base, remove, &transaction);
    if (status != HBFSIM_OK) {
        return status;
    }
    found->reset();
    return HBFSIM_OK;
#else
    return HBFSIM_CUDA_ERROR;
#endif
}

extern "C" void hbfsim_context_destroy(hbfsim_context* context)
{
    if (context == nullptr) {
        return;
    }
    auto admission = hbfsim::runtime::close_context_admission(context);
    if (admission == nullptr) {
        return;
    }
    const auto result = hbfsim::runtime::release_context(context, true);
    if (result == hbfsim::runtime::ReleaseResult::retryable) {
        hbfsim::runtime::reopen_context_admission(admission);
    } else if (result == hbfsim::runtime::ReleaseResult::quarantined) {
        hbfsim::runtime::quarantine_context_admission(admission);
    } else {
        hbfsim::runtime::erase_context_admission(context, admission);
    }
}

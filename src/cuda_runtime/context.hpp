#pragma once

#include <hbfsim/api.h>
#include <hbfsim/protocol.hpp>

#include <chrono>
#include <cstdint>
#include <sys/types.h>

namespace hbfsim::runtime {

constexpr std::uint64_t effective_heartbeat_timeout_ns(
    std::uint64_t request_timeout_ns) noexcept
{
    constexpr std::uint64_t kMinimumHeartbeatTimeoutNs = 50'000'000;
    constexpr std::uint64_t kMaximumHeartbeatTimeoutNs = 1'000'000'000;
    return request_timeout_ns < kMinimumHeartbeatTimeoutNs
               ? kMinimumHeartbeatTimeoutNs
           : request_timeout_ns > kMaximumHeartbeatTimeoutNs
               ? kMaximumHeartbeatTimeoutNs
               : request_timeout_ns;
}

using BeforeForkHook = void (*)(int control_fd, void* state) noexcept;
using AfterBeginRetireHook = void (*)(void* state) noexcept;
using PublishRange = void (*)(void* state) noexcept;
using TimingRangeGateHook = int (*)(void* owner,
                                    std::uintptr_t control_alias,
                                    std::uintptr_t begin,
                                    std::uintptr_t end,
                                    PublishRange publish_range,
                                    void* publish_state,
                                    void* state) noexcept;

// Test-only CPU transport seam. It exercises mmap/memfd/fork/exec lifecycle
// but deliberately skips CUDA registration. It is never called by the public
// production constructor and is not live cudaHostRegisterMapped proof.
int create_cpu_test_context(const hbfsim_options* options,
                            const char* daemon_path,
                            hbfsim_context** out);
int create_cpu_test_context_with_spawn_hook(
    const hbfsim_options* options, const char* daemon_path,
    BeforeForkHook before_fork, void* hook_state, hbfsim_context** out);
pid_t daemon_pid_for_test(hbfsim_context* context) noexcept;
int wait_for_heartbeat_for_test(hbfsim_context* context,
                                std::chrono::milliseconds timeout);
int wait_for_fault_for_test(hbfsim_context* context,
                            std::chrono::milliseconds timeout);
std::uint64_t heartbeat_ns_for_test(hbfsim_context* context) noexcept;
int submit_for_test(hbfsim_context* context, const HbfRequest& request,
                    HbfCompletion* completion);
int submit_without_wait_for_test(hbfsim_context* context,
                                 const HbfRequest& request,
                                 std::uint64_t* ticket);
int wait_for_completion_for_test(hbfsim_context* context,
                                 std::uint64_t ticket,
                                 std::uint64_t request_id,
                                 HbfCompletion* completion);
int control_fd_for_test(hbfsim_context* context) noexcept;
int register_device_with_gate_for_test(
    hbfsim_context* context, void* device_ptr, std::size_t length,
    const hbfsim_range_options* options, std::uintptr_t control_alias,
    TimingRangeGateHook gate, void* gate_state) noexcept;
std::uint32_t range_count_for_test(hbfsim_context* context) noexcept;
void close_inherited_fds_for_test(int control_fd,
                                  bool force_fallback) noexcept;
void pause_daemon_for_test(hbfsim_context* context, bool paused) noexcept;
int validate_device_range_with_cuda_for_test(
    std::uintptr_t expected_context, int expected_device, void* device_ptr,
    std::size_t length) noexcept;
int begin_retire_with_cuda_for_test(
    std::uintptr_t expected_context, int expected_device,
    std::uintptr_t owner, std::uint64_t generation,
    int (*begin_retire)(std::uintptr_t, std::uint64_t,
                        std::uintptr_t*) noexcept,
    std::uintptr_t* token_out) noexcept;
int normalize_launch_gate_status_for_test(int gate_status) noexcept;
int retirement_liveness_for_test(hbfsim_context* context) noexcept;
bool wait_for_context_closing_for_test(
    hbfsim_context* context, std::chrono::milliseconds timeout) noexcept;
void set_after_begin_retire_hook_for_test(
    hbfsim_context* context, AfterBeginRetireHook hook, void* state) noexcept;

}  // namespace hbfsim::runtime

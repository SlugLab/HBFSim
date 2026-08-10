#pragma once

#include <hbfsim/api.h>
#include <hbfsim/protocol.hpp>

#include <chrono>
#include <cstdint>
#include <sys/types.h>

namespace hbfsim::runtime {

using BeforeForkHook = void (*)(int control_fd, void* state) noexcept;

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
void close_inherited_fds_for_test(int control_fd,
                                  bool force_fallback) noexcept;
void pause_daemon_for_test(hbfsim_context* context, bool paused) noexcept;

}  // namespace hbfsim::runtime

#include <hbfsim/api.h>

#include "../../src/cuda_runtime/context.hpp"

#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <iterator>
#include <string>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "CPU seam CHECK failed at line %d: %s\n", line,
                 expression);
    std::exit(1);
}

#define CHECK(expression)                                                       \
    do {                                                                        \
        if (!(expression)) {                                                    \
            fail(#expression, __LINE__);                                        \
        }                                                                       \
    } while (false)

hbfsim_options test_options(const char* profile_path,
                            const std::filesystem::path& report_dir,
                            std::uint32_t capacity = 8,
                            std::uint64_t timeout_ns = 50'000'000)
{
    return hbfsim_options{
        .profile_path = profile_path,
        .report_dir = report_dir.c_str(),
        .mode = 0,
        .ring_capacity = capacity,
        .request_timeout_ns = timeout_ns,
    };
}

void observe_cloexec(int control_fd, void* opaque) noexcept
{
    auto* observed = static_cast<bool*>(opaque);
    const auto flags = ::fcntl(control_fd, F_GETFD);
    *observed = flags >= 0 && (flags & FD_CLOEXEC) != 0;
}

struct TimingGateState {
    bool accept{true};
    std::size_t calls{0};
    void* owner{nullptr};
    std::uintptr_t control_alias{0};
    std::uintptr_t begin{0};
    std::uintptr_t end{0};
};

int register_timing_range(void* owner, std::uintptr_t control_alias,
                          std::uintptr_t begin, std::uintptr_t end,
                          hbfsim::runtime::PublishRange publish_range,
                          void* publish_state,
                          void* opaque) noexcept
{
    auto& state = *static_cast<TimingGateState*>(opaque);
    ++state.calls;
    state.owner = owner;
    state.control_alias = control_alias;
    state.begin = begin;
    state.end = end;
    if (!state.accept) {
        return HBFSIM_IO_ERROR;
    }
    publish_range(publish_state);
    return HBFSIM_OK;
}

void verify_daemon_dies_with_context_process(
    const hbfsim_options& options, const char* daemon_path)
{
    CHECK(::syscall(SYS_prctl, PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0);
    int pid_pipe[2]{};
    CHECK(::pipe2(pid_pipe, O_CLOEXEC) == 0);
    const auto creator = ::fork();
    CHECK(creator >= 0);
    if (creator == 0) {
        (void)::close(pid_pipe[0]);
        hbfsim_context* abandoned = nullptr;
        const auto create_status = hbfsim::runtime::create_cpu_test_context(
            &options, daemon_path, &abandoned);
        if (create_status != HBFSIM_OK || abandoned == nullptr) {
            ::_exit(2);
        }
        const auto daemon = hbfsim::runtime::daemon_pid_for_test(abandoned);
        const auto written = ::write(pid_pipe[1], &daemon, sizeof(daemon));
        ::_exit(written == static_cast<ssize_t>(sizeof(daemon)) ? 0 : 3);
    }

    CHECK(::close(pid_pipe[1]) == 0);
    pid_t daemon = -1;
    ssize_t bytes_read = -1;
    do {
        bytes_read = ::read(pid_pipe[0], &daemon, sizeof(daemon));
    } while (bytes_read < 0 && errno == EINTR);
    CHECK(::close(pid_pipe[0]) == 0);
    int creator_status = 0;
    CHECK(::waitpid(creator, &creator_status, 0) == creator);
    CHECK(WIFEXITED(creator_status));
    CHECK(WEXITSTATUS(creator_status) == 0);
    CHECK(bytes_read == static_cast<ssize_t>(sizeof(daemon)));
    CHECK(daemon > 0);

    int daemon_status = 0;
    bool reaped = false;
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::seconds(2);
    while (std::chrono::steady_clock::now() < deadline) {
        const auto result = ::waitpid(daemon, &daemon_status, WNOHANG);
        if (result == daemon) {
            reaped = true;
            break;
        }
        CHECK(result == 0 || (result < 0 && errno == ECHILD));
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    if (!reaped) {
        (void)::kill(daemon, SIGKILL);
        while (::waitpid(daemon, &daemon_status, 0) < 0 && errno == EINTR) {
        }
    }
    CHECK(reaped);
    CHECK(WIFSIGNALED(daemon_status));
    CHECK(WTERMSIG(daemon_status) == SIGKILL);
}

}  // namespace

int main(int argc, char** argv)
{
    CHECK(argc == 3);
    const auto report_dir =
        std::filesystem::temp_directory_path() /
        ("hbfsim-context-cpu-seam-" + std::to_string(::getpid()));
    std::filesystem::create_directories(report_dir);

    hbfsim_context* invalid = nullptr;
    auto invalid_capacity = test_options(
        "configs/profiles/nominal.json", report_dir, 3);
    CHECK(hbfsim::runtime::create_cpu_test_context(
              &invalid_capacity, argv[1], &invalid) == HBFSIM_INVALID_ARGUMENT);
    CHECK(invalid == nullptr);

    auto options = test_options("configs/profiles/nominal.json", report_dir);
    const auto preload_marker = report_dir / "daemon-preload.marker";
    CHECK(::setenv("HBFSIM_PRELOAD_MARKER_PATH",
                   preload_marker.c_str(), 1) == 0);
    CHECK(::setenv("LD_PRELOAD", argv[2], 1) == 0);
    CHECK(::setenv("LD_AUDIT", argv[2], 1) == 0);
    CHECK(::setenv("BPFTIME_PTXPASS_LIBRARIES", "forbidden", 1) == 0);
    CHECK(::setenv("HBFSIM_COVERAGE_PATH", "forbidden", 1) == 0);
    CHECK(::setenv("HBFSIM_PASS_MANIFEST_PATH", "forbidden", 1) == 0);
    bool cloexec_at_fork = false;
    hbfsim_context* context = nullptr;
    CHECK(hbfsim::runtime::create_cpu_test_context_with_spawn_hook(
              &options, argv[1], observe_cloexec, &cloexec_at_fork,
              &context) == HBFSIM_OK);
    CHECK(context != nullptr);
    CHECK(cloexec_at_fork);
    CHECK(!std::filesystem::exists(preload_marker));
    std::ifstream daemon_environment(
        "/proc/" +
            std::to_string(hbfsim::runtime::daemon_pid_for_test(context)) +
            "/environ",
        std::ios::binary);
    CHECK(daemon_environment.good());
    const std::string environment(
        std::istreambuf_iterator<char>(daemon_environment), {});
    CHECK(environment.find("LD_PRELOAD=") == std::string::npos);
    CHECK(environment.find("LD_AUDIT=") == std::string::npos);
    CHECK(environment.find("BPFTIME_PTXPASS_LIBRARIES=") ==
          std::string::npos);
    CHECK(environment.find("HBFSIM_COVERAGE_PATH=") == std::string::npos);
    CHECK(environment.find("HBFSIM_PASS_MANIFEST_PATH=") ==
          std::string::npos);
    CHECK(environment.find("HBFSIM_PRELOAD_MARKER_PATH=") !=
          std::string::npos);
    CHECK(::unsetenv("LD_PRELOAD") == 0);
    CHECK(::unsetenv("LD_AUDIT") == 0);
    CHECK(::unsetenv("BPFTIME_PTXPASS_LIBRARIES") == 0);
    CHECK(::unsetenv("HBFSIM_COVERAGE_PATH") == 0);
    CHECK(::unsetenv("HBFSIM_PASS_MANIFEST_PATH") == 0);
    CHECK(::unsetenv("HBFSIM_PRELOAD_MARKER_PATH") == 0);
    const auto seals = ::fcntl(hbfsim::runtime::control_fd_for_test(context),
                               F_GET_SEALS);
    CHECK(seals >= 0);
    CHECK((seals & (F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_SEAL)) ==
          (F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_SEAL));

    hbfsim_range_options range_options{
        .mode = HBFSIM_RANGE_MODE_TIMING,
        .permissions = HBFSIM_RANGE_READ_WRITE,
        .cache_policy = HBFSIM_CACHE_POLICY_NONE,
        .stream_id = 0,
    };
    CHECK(hbfsim_register_device(context, reinterpret_cast<void*>(1), 4096,
                                 &range_options) == HBFSIM_CUDA_ERROR);
    TimingGateState timing_gate;
    CHECK(hbfsim::runtime::register_device_with_gate_for_test(
              context, reinterpret_cast<void*>(0x3000), 0x1000,
              &range_options, 0xfeed'0000, register_timing_range,
              &timing_gate) == HBFSIM_OK);
    CHECK(timing_gate.calls == 1);
    CHECK(timing_gate.owner == context);
    CHECK(timing_gate.control_alias == 0xfeed'0000);
    CHECK(timing_gate.begin == 0x3000);
    CHECK(timing_gate.end == 0x4000);
    CHECK(hbfsim::runtime::range_count_for_test(context) == 1);
    CHECK(hbfsim::runtime::register_device_with_gate_for_test(
              context, reinterpret_cast<void*>(0x3800), 0x1000,
              &range_options, 0xfeed'0000, register_timing_range,
              &timing_gate) == HBFSIM_INVALID_ARGUMENT);
    CHECK(timing_gate.calls == 1);
    timing_gate.accept = false;
    CHECK(hbfsim::runtime::register_device_with_gate_for_test(
              context, reinterpret_cast<void*>(0x5000), 0x1000,
              &range_options, 0xfeed'0000, register_timing_range,
              &timing_gate) == HBFSIM_IO_ERROR);
    CHECK(hbfsim::runtime::range_count_for_test(context) == 1);
    void* logical_pointer = reinterpret_cast<void*>(1);
    range_options.mode = HBFSIM_RANGE_MODE_CAPACITY;
    timing_gate.accept = true;
    CHECK(hbfsim::runtime::register_device_with_gate_for_test(
              context, reinterpret_cast<void*>(0x5000), 0x1000,
              &range_options, 0xfeed'0000, register_timing_range,
              &timing_gate) == HBFSIM_UNSUPPORTED);
    CHECK(timing_gate.calls == 2);
    CHECK(hbfsim::runtime::range_count_for_test(context) == 1);
    CHECK(hbfsim_map_file(context, "/does/not/need/to/exist", 0, 4096,
                          &range_options,
                          &logical_pointer) == HBFSIM_UNSUPPORTED);
    CHECK(logical_pointer == nullptr);
    CHECK(hbfsim_unregister(context, reinterpret_cast<void*>(1)) ==
          HBFSIM_UNSUPPORTED);
    CHECK(hbfsim_flush(context) == HBFSIM_OK);

    const auto daemon = hbfsim::runtime::daemon_pid_for_test(context);
    CHECK(daemon > 0);
    CHECK(hbfsim::runtime::wait_for_heartbeat_for_test(
              context, std::chrono::milliseconds(250)) == HBFSIM_OK);
    const auto first_heartbeat =
        hbfsim::runtime::heartbeat_ns_for_test(context);
    std::uint64_t second_heartbeat = first_heartbeat;
    const auto heartbeat_deadline = std::chrono::steady_clock::now() +
                                    std::chrono::milliseconds(100);
    while (second_heartbeat == first_heartbeat &&
           std::chrono::steady_clock::now() < heartbeat_deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        second_heartbeat = hbfsim::runtime::heartbeat_ns_for_test(context);
    }
    CHECK(second_heartbeat > first_heartbeat);
    CHECK(second_heartbeat - first_heartbeat <= 10'000'000);
    CHECK(hbfsim::runtime::retirement_liveness_for_test(context) ==
          HBFSIM_OK);

    CHECK(::kill(daemon, SIGKILL) == 0);
    CHECK(hbfsim::runtime::wait_for_fault_for_test(
              context, std::chrono::milliseconds(500)) ==
          HBFSIM_DAEMON_LOST);
    CHECK(hbfsim::runtime::retirement_liveness_for_test(context) ==
          HBFSIM_DAEMON_LOST);
    hbfsim_context_destroy(context);

    int status = 0;
    CHECK(::waitpid(daemon, &status, WNOHANG) == -1);

#if defined(HBFSIM_TEST_CUDA_DISABLED)
    CHECK(::setenv("HBFSIM_DAEMON_PATH", argv[1], 1) == 0);
    hbfsim_context* production_context = nullptr;
    CHECK(hbfsim_context_create(&options, &production_context) ==
          HBFSIM_CUDA_ERROR);
    CHECK(production_context == nullptr);
    CHECK(::unsetenv("HBFSIM_DAEMON_PATH") == 0);
#endif

    hbfsim_context* stopped_context = nullptr;
    CHECK(hbfsim::runtime::create_cpu_test_context(
              &options, argv[1], &stopped_context) == HBFSIM_OK);
    const auto stopped_daemon =
        hbfsim::runtime::daemon_pid_for_test(stopped_context);
    CHECK(::kill(stopped_daemon, SIGSTOP) == 0);
    const auto destroy_started = std::chrono::steady_clock::now();
    hbfsim_context_destroy(stopped_context);
    const auto destroy_elapsed = std::chrono::steady_clock::now() -
                                 destroy_started;
    CHECK(destroy_elapsed < std::chrono::seconds(5));
    CHECK(::waitpid(stopped_daemon, &status, WNOHANG) == -1);

    const auto inherited = ::fcntl(STDERR_FILENO, F_DUPFD, 100);
    CHECK(inherited >= 100);
    const auto hygiene_child = ::fork();
    CHECK(hygiene_child >= 0);
    if (hygiene_child == 0) {
        hbfsim::runtime::close_inherited_fds_for_test(-1, true);
        ::_exit(::fcntl(inherited, F_GETFD) == -1 && errno == EBADF ? 0 : 3);
    }
    CHECK(::waitpid(hygiene_child, &status, 0) == hygiene_child);
    CHECK(WIFEXITED(status));
    CHECK(WEXITSTATUS(status) == 0);
    CHECK(::close(inherited) == 0);

    verify_daemon_dies_with_context_process(options, argv[1]);

    std::filesystem::remove_all(report_dir);
    return 0;
}

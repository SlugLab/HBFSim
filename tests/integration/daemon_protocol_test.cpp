#include <hbfsim/api.h>
#include <hbfsim/protocol.hpp>

#include "../../src/cuda_runtime/context.hpp"
#include "../../src/host_service/control_layout.hpp"
#include "../../src/host_service/request_dispatcher.hpp"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fcntl.h>
#include <linux/memfd.h>
#include <optional>
#include <semaphore>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_set>
#include <vector>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "CPU transport seam CHECK failed at line %d: %s\n",
                 line, expression);
    std::exit(1);
}

#define CHECK(expression)                                                       \
    do {                                                                        \
        if (!(expression)) {                                                    \
            fail(#expression, __LINE__);                                        \
        }                                                                       \
    } while (false)

hbfsim::HbfRequest request(std::uint64_t sequence)
{
    return hbfsim::HbfRequest{
        .request_id = sequence + 1,
        .sequence = sequence,
        .arrival_ns = sequence * 1'000'000,
        .logical_address = (sequence % 8) * 16384,
        .deadline_ns = 0,
        .bytes = 16384,
        .range_id = 1,
        .stream_id = 0,
        .operation =
            static_cast<std::uint32_t>(hbfsim::RequestOperation::Read),
        .page_generation = 1,
        .flags = 0,
    };
}

void verify_concurrent_cpu_transport_seam()
{
    constexpr std::uint32_t capacity = 4096;
    constexpr std::uint64_t producers = 8;
    constexpr std::uint64_t requests_per_producer = 256;
    const auto bytes = hbfsim::host_service::control_region_bytes(capacity);
    void* storage = nullptr;
    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    hbfsim::host_service::ControlView control(storage, bytes);
    CHECK(control.initialize(capacity));

    std::atomic<bool> start{false};
    std::atomic<std::uint64_t> pushed{0};
    std::vector<std::thread> workers;
    for (std::uint64_t producer = 0; producer < producers; ++producer) {
        workers.emplace_back([&, producer] {
            while (!start.load(std::memory_order_acquire)) {
            }
            for (std::uint64_t index = 0; index < requests_per_producer;
                 ++index) {
                auto descriptor =
                    request(producer * requests_per_producer + index);
                if (control.try_push_request(descriptor)) {
                    pushed.fetch_add(1, std::memory_order_relaxed);
                }
            }
        });
    }
    start.store(true, std::memory_order_release);
    for (auto& worker : workers) {
        worker.join();
    }
    CHECK(pushed.load(std::memory_order_relaxed) ==
          producers * requests_per_producer);

    std::unordered_set<std::uint64_t> request_ids;
    hbfsim::HbfRequest descriptor{};
    while (control.try_pop_request(descriptor)) {
        request_ids.insert(descriptor.request_id);
    }
    CHECK(request_ids.size() == producers * requests_per_producer);
    std::free(storage);
}

void verify_dispatcher_batch_and_failure()
{
    constexpr std::uint32_t capacity = 8;
    const auto bytes = hbfsim::host_service::control_region_bytes(capacity);
    void* storage = nullptr;
    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    hbfsim::host_service::ControlView control(storage, bytes);
    CHECK(control.initialize(capacity));
    std::vector<std::uint64_t> tickets;
    for (std::uint64_t sequence = 0; sequence < 3; ++sequence) {
        std::uint64_t ticket = 0;
        auto descriptor = request(sequence);
        descriptor.sequence = 9'999;
        CHECK(control.try_push_request(descriptor, ticket));
        tickets.push_back(ticket);
    }

    std::vector<hbfsim::HbfRequest> submitted;
    std::size_t completion_index = 0;
    hbfsim::host_service::RequestDispatcher dispatcher(
        control,
        hbfsim::host_service::RequestDispatcher::Engine{
            .submit = [&](const hbfsim::HbfRequest& descriptor) {
                submitted.push_back(descriptor);
            },
            .run_next_completion = [&]()
                -> std::optional<hbfsim::HbfCompletion> {
                CHECK(submitted.size() == 3);
                if (completion_index == submitted.size()) {
                    return std::nullopt;
                }
                const auto& descriptor = submitted[completion_index++];
                hbfsim::HbfCompletion completion{};
                completion.request_id = descriptor.request_id;
                completion.page_generation = descriptor.page_generation;
                completion.status = static_cast<std::uint32_t>(
                    hbfsim::RequestStatus::Ready);
                return completion;
            },
        });
    CHECK(dispatcher.poll_once());
    CHECK(submitted.size() == 3);
    for (std::size_t index = 0; index < submitted.size(); ++index) {
        CHECK(submitted[index].sequence == tickets[index]);
    }
    for (std::size_t index = 0; index < tickets.size(); ++index) {
        hbfsim::HbfCompletion completion{};
        CHECK(control.try_consume_completion(tickets[index], completion));
        CHECK(completion.request_id == index + 1);
    }
    std::free(storage);

    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    control = hbfsim::host_service::ControlView(storage, bytes);
    CHECK(control.initialize(capacity));
    tickets.clear();
    for (std::uint64_t sequence = 0; sequence < 4; ++sequence) {
        std::uint64_t ticket = 0;
        CHECK(control.try_push_request(request(sequence + 10), ticket));
        tickets.push_back(ticket);
    }
    std::size_t submit_count = 0;
    hbfsim::host_service::RequestDispatcher failing_dispatcher(
        control,
        hbfsim::host_service::RequestDispatcher::Engine{
            .submit = [&](const hbfsim::HbfRequest&) {
                if (++submit_count == 2) {
                    throw std::runtime_error("injected submit failure");
                }
            },
            .run_next_completion = [] {
                return std::optional<hbfsim::HbfCompletion>{};
            },
        });
    CHECK(failing_dispatcher.poll_once());
    CHECK(hbfsim::host_service::atomic_load(
              control.header()->fault, std::memory_order_acquire) ==
          HBFSIM_IO_ERROR);
    for (const auto ticket : tickets) {
        hbfsim::HbfCompletion completion{};
        CHECK(control.try_consume_completion(ticket, completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    }
    std::free(storage);

    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    control = hbfsim::host_service::ControlView(storage, bytes);
    CHECK(control.initialize(capacity));
    tickets.clear();
    for (std::uint64_t sequence = 0; sequence < 3; ++sequence) {
        std::uint64_t ticket = 0;
        CHECK(control.try_push_request(request(sequence + 20), ticket));
        tickets.push_back(ticket);
    }
    hbfsim::host_service::RequestDispatcher completion_failure(
        control,
        hbfsim::host_service::RequestDispatcher::Engine{
            .submit = [](const hbfsim::HbfRequest&) {},
            .run_next_completion = []()
                -> std::optional<hbfsim::HbfCompletion> {
                throw std::runtime_error("injected completion failure");
            },
        });
    CHECK(completion_failure.poll_once());
    CHECK(hbfsim::host_service::atomic_load(
              control.header()->fault, std::memory_order_acquire) ==
          HBFSIM_IO_ERROR);
    for (const auto ticket : tickets) {
        hbfsim::HbfCompletion completion{};
        CHECK(control.try_consume_completion(ticket, completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    }
    std::free(storage);

    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    control = hbfsim::host_service::ControlView(storage, bytes);
    CHECK(control.initialize(capacity));
    tickets.clear();
    for (std::uint64_t sequence = 0; sequence < 3; ++sequence) {
        std::uint64_t ticket = 0;
        CHECK(control.try_push_request(request(sequence + 30), ticket));
        tickets.push_back(ticket);
    }
    bool malformed_returned = false;
    hbfsim::host_service::RequestDispatcher malformed_completion(
        control,
        hbfsim::host_service::RequestDispatcher::Engine{
            .submit = [](const hbfsim::HbfRequest&) {},
            .run_next_completion = [&]()
                -> std::optional<hbfsim::HbfCompletion> {
                if (malformed_returned) {
                    return std::nullopt;
                }
                malformed_returned = true;
                hbfsim::HbfCompletion completion{};
                completion.request_id = 999'999;
                completion.page_generation = 1;
                completion.status = static_cast<std::uint32_t>(
                    hbfsim::RequestStatus::Ready);
                return completion;
            },
        });
    CHECK(malformed_completion.poll_once());
    CHECK(hbfsim::host_service::atomic_load(
              control.header()->fault, std::memory_order_acquire) ==
          HBFSIM_IO_ERROR);
    for (const auto ticket : tickets) {
        hbfsim::HbfCompletion completion{};
        CHECK(control.try_consume_completion(ticket, completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    }
    std::free(storage);

    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    control = hbfsim::host_service::ControlView(storage, bytes);
    CHECK(control.initialize(capacity));
    tickets.clear();
    for (std::uint64_t sequence = 0; sequence < 3; ++sequence) {
        std::uint64_t ticket = 0;
        CHECK(control.try_push_request(request(sequence + 40), ticket));
        tickets.push_back(ticket);
    }
    hbfsim::host_service::RequestDispatcher impossible_completion(
        control,
        hbfsim::host_service::RequestDispatcher::Engine{
            .submit = [](const hbfsim::HbfRequest&) {},
            .run_next_completion = [] {
                return std::optional<hbfsim::HbfCompletion>{};
            },
        });
    CHECK(impossible_completion.poll_once());
    CHECK(hbfsim::host_service::atomic_load(
              control.header()->fault, std::memory_order_acquire) ==
          HBFSIM_IO_ERROR);
    for (const auto ticket : tickets) {
        hbfsim::HbfCompletion completion{};
        CHECK(control.try_consume_completion(ticket, completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    }
    std::free(storage);

    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    control = hbfsim::host_service::ControlView(storage, bytes);
    CHECK(control.initialize(capacity));
    auto duplicate_left = request(70);
    auto duplicate_right = request(71);
    duplicate_right.request_id = duplicate_left.request_id;
    std::uint64_t duplicate_left_ticket = 0;
    std::uint64_t duplicate_right_ticket = 0;
    CHECK(control.try_push_request(duplicate_left, duplicate_left_ticket));
    CHECK(control.try_push_request(duplicate_right, duplicate_right_ticket));
    hbfsim::host_service::RequestDispatcher duplicate_dispatcher(
        control,
        hbfsim::host_service::RequestDispatcher::Engine{
            .submit = [](const hbfsim::HbfRequest&) {},
            .run_next_completion = [] {
                return std::optional<hbfsim::HbfCompletion>{};
            },
        });
    CHECK(duplicate_dispatcher.poll_once());
    CHECK(hbfsim::host_service::atomic_load(
              control.header()->fault, std::memory_order_acquire) ==
          HBFSIM_IO_ERROR);
    for (const auto ticket :
         {duplicate_left_ticket, duplicate_right_ticket}) {
        hbfsim::HbfCompletion completion{};
        CHECK(control.try_consume_completion(ticket, completion));
        CHECK(completion.request_id == duplicate_left.request_id);
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    }
    std::free(storage);
}

struct BlockingAdmissionHook {
    std::binary_semaphore entered{0};
    std::binary_semaphore proceed{0};
};

void block_admission(void* opaque) noexcept
{
    auto* hook = static_cast<BlockingAdmissionHook*>(opaque);
    hook->entered.release();
    hook->proceed.acquire();
}

void verify_fault_admission_races()
{
    constexpr std::uint32_t capacity = 8;
    const auto bytes = hbfsim::host_service::control_region_bytes(capacity);
    void* storage = nullptr;
    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    hbfsim::host_service::ControlView control(storage, bytes);
    CHECK(control.initialize(capacity));
    std::uint64_t initial_ticket = 0;
    CHECK(control.try_push_request(request(50), initial_ticket));

    hbfsim::host_service::RequestDispatcher dispatcher(
        control,
        hbfsim::host_service::RequestDispatcher::Engine{
            .submit = [](const hbfsim::HbfRequest&) {},
            .run_next_completion = [] {
                return std::optional<hbfsim::HbfCompletion>{};
            },
        });
    BlockingAdmissionHook before_admission;
    std::atomic<bool> producer_admitted{true};
    std::uint64_t rejected_ticket = 0;
    std::thread producer([&] {
        producer_admitted.store(control.try_push_request_with_hooks_for_test(
                                    request(51), rejected_ticket,
                                    block_admission, nullptr,
                                    &before_admission),
                                std::memory_order_release);
    });
    before_admission.entered.acquire();
    std::binary_semaphore failure_done{0};
    std::thread failure([&] {
        CHECK(dispatcher.poll_once());
        failure_done.release();
    });
    failure_done.acquire();
    before_admission.proceed.release();
    producer.join();
    failure.join();
    CHECK(!producer_admitted.load(std::memory_order_acquire));
    hbfsim::HbfCompletion completion{};
    CHECK(control.try_consume_completion(initial_ticket, completion));
    CHECK(completion.status == static_cast<std::uint32_t>(
                                   hbfsim::RequestStatus::IoError));
    std::free(storage);

    CHECK(::posix_memalign(&storage, 64, bytes) == 0);
    control = hbfsim::host_service::ControlView(storage, bytes);
    CHECK(control.initialize(capacity));
    CHECK(control.try_push_request(request(60), initial_ticket));
    hbfsim::host_service::RequestDispatcher reserved_dispatcher(
        control,
        hbfsim::host_service::RequestDispatcher::Engine{
            .submit = [](const hbfsim::HbfRequest&) {},
            .run_next_completion = [] {
                return std::optional<hbfsim::HbfCompletion>{};
            },
        });
    BlockingAdmissionHook after_reservation;
    std::uint64_t reserved_ticket = 0;
    producer_admitted.store(false, std::memory_order_relaxed);
    std::thread reserved_producer([&] {
        producer_admitted.store(control.try_push_request_with_hooks_for_test(
                                    request(61), reserved_ticket, nullptr,
                                    block_admission, &after_reservation),
                                std::memory_order_release);
    });
    after_reservation.entered.acquire();
    std::binary_semaphore reserved_failure_done{0};
    std::thread reserved_failure([&] {
        CHECK(reserved_dispatcher.poll_once());
        reserved_failure_done.release();
    });
    while ((hbfsim::host_service::atomic_load(
                control.header()->admission_state,
                std::memory_order_acquire) &
            hbfsim::host_service::kAdmissionClosedBit) == 0) {
    }
    CHECK(!reserved_failure_done.try_acquire());
    after_reservation.proceed.release();
    reserved_producer.join();
    reserved_failure_done.acquire();
    reserved_failure.join();
    CHECK(producer_admitted.load(std::memory_order_acquire));
    CHECK(control.try_consume_completion(initial_ticket, completion));
    CHECK(completion.status == static_cast<std::uint32_t>(
                                   hbfsim::RequestStatus::IoError));
    CHECK(control.try_consume_completion(reserved_ticket, completion));
    CHECK(completion.request_id == request(61).request_id);
    CHECK(completion.status == static_cast<std::uint32_t>(
                                   hbfsim::RequestStatus::IoError));
    std::free(storage);
}

void verify_non_memfd_rejected(const char* daemon,
                               const std::filesystem::path& report_dir)
{
    char path[] = "/tmp/hbfsim-not-memfd-XXXXXX";
    const auto fd = ::mkstemp(path);
    CHECK(fd >= 0);
    CHECK(::unlink(path) == 0);
    constexpr std::uint32_t capacity = 4;
    const auto bytes = hbfsim::host_service::control_region_bytes(capacity);
    CHECK(::ftruncate(fd, static_cast<off_t>(bytes)) == 0);
    auto* mapping = ::mmap(nullptr, bytes, PROT_READ | PROT_WRITE, MAP_SHARED,
                           fd, 0);
    CHECK(mapping != MAP_FAILED);
    hbfsim::host_service::ControlView control(mapping, bytes);
    CHECK(control.initialize(capacity));
    hbfsim::host_service::atomic_store(control.header()->shutdown, 1,
                                       std::memory_order_release);

    const auto flags = ::fcntl(fd, F_GETFD);
    CHECK(flags >= 0);
    CHECK(::fcntl(fd, F_SETFD, flags & ~FD_CLOEXEC) == 0);
    const std::string fd_text = std::to_string(fd);
    const auto child = ::fork();
    CHECK(child >= 0);
    if (child == 0) {
        ::execl(daemon, daemon, "--profile", "configs/profiles/nominal.json",
                "--control-fd", fd_text.c_str(), "--report-dir",
                report_dir.c_str(), static_cast<char*>(nullptr));
        ::_exit(127);
    }
    int status = 0;
    CHECK(::waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status));
    CHECK(WEXITSTATUS(status) == 2);
    CHECK(::munmap(mapping, bytes) == 0);
    CHECK(::close(fd) == 0);
}

void verify_wrong_size_memfd_rejected(
    const char* daemon, const std::filesystem::path& report_dir)
{
    const auto fd = static_cast<int>(
        ::syscall(SYS_memfd_create, "hbfsim-wrong-size",
                  MFD_CLOEXEC | MFD_ALLOW_SEALING));
    CHECK(fd >= 0);
    constexpr std::uint32_t capacity = 4;
    const auto layout_bytes =
        hbfsim::host_service::control_region_bytes(capacity);
    const auto actual_bytes = layout_bytes + 64;
    CHECK(::ftruncate(fd, static_cast<off_t>(actual_bytes)) == 0);
    auto* mapping =
        ::mmap(nullptr, actual_bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    CHECK(mapping != MAP_FAILED);
    hbfsim::host_service::ControlView control(mapping, layout_bytes);
    CHECK(control.initialize(capacity));
    CHECK(::fcntl(fd, F_ADD_SEALS,
                  F_SEAL_SHRINK | F_SEAL_GROW | F_SEAL_SEAL) == 0);
    const auto flags = ::fcntl(fd, F_GETFD);
    CHECK(flags >= 0);
    CHECK(::fcntl(fd, F_SETFD, flags & ~FD_CLOEXEC) == 0);
    const std::string fd_text = std::to_string(fd);
    const auto child = ::fork();
    CHECK(child >= 0);
    if (child == 0) {
        ::execl(daemon, daemon, "--profile", "configs/profiles/nominal.json",
                "--control-fd", fd_text.c_str(), "--report-dir",
                report_dir.c_str(), static_cast<char*>(nullptr));
        ::_exit(127);
    }
    int status = 0;
    CHECK(::waitpid(child, &status, 0) == child);
    CHECK(WIFEXITED(status));
    CHECK(WEXITSTATUS(status) == 2);
    CHECK(::munmap(mapping, actual_bytes) == 0);
    CHECK(::close(fd) == 0);
}

}  // namespace

int main(int argc, char** argv)
{
    CHECK(argc == 2);
    verify_concurrent_cpu_transport_seam();
    verify_dispatcher_batch_and_failure();
    verify_fault_admission_races();
    const auto report_dir =
        std::filesystem::temp_directory_path() /
        ("hbfsim-daemon-cpu-seam-" + std::to_string(::getpid()));
    std::filesystem::create_directories(report_dir);
    verify_non_memfd_rejected(argv[1], report_dir);
    verify_wrong_size_memfd_rejected(argv[1], report_dir);
    const std::string report_dir_string = report_dir.string();
    hbfsim_options options{
        .profile_path = "configs/profiles/nominal.json",
        .report_dir = report_dir_string.c_str(),
        .mode = 0,
        .ring_capacity = 4,
        .request_timeout_ns = 20'000'000,
    };

    const auto inherited = ::fcntl(STDERR_FILENO, F_DUPFD, 100);
    CHECK(inherited >= 100);
    hbfsim_context* context = nullptr;
    CHECK(hbfsim::runtime::create_cpu_test_context(&options, argv[1],
                                                    &context) == HBFSIM_OK);
    CHECK(!std::filesystem::exists(
        "/proc/" +
        std::to_string(hbfsim::runtime::daemon_pid_for_test(context)) +
        "/fd/" + std::to_string(inherited)));
    CHECK(::close(inherited) == 0);

    // The CPU-only seam exercises multiple wraps through the actual exec'd
    // daemon. It is not evidence for live CUDA host registration behavior.
    for (std::uint64_t sequence = 0; sequence < 20; ++sequence) {
        hbfsim::HbfCompletion completion{};
        CHECK(hbfsim::runtime::submit_for_test(
                  context, request(sequence), &completion) == HBFSIM_OK);
        CHECK(completion.request_id == sequence + 1);
#if defined(HBFSIM_TEST_MQSIM_ENABLED)
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::Ready));
#else
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::Unsupported));
#endif
    }

    hbfsim::runtime::pause_daemon_for_test(context, true);
    std::uint64_t ticket20 = 0;
    std::uint64_t ticket21 = 0;
    std::uint64_t ticket22 = 0;
    std::uint64_t ticket23 = 0;
    auto request20 = request(20);
    auto request21 = request(21);
    auto request22 = request(22);
    auto request23 = request(23);
#if defined(HBFSIM_TEST_MQSIM_ENABLED)
    request20.arrival_ns = 100'000'000;
    request21.arrival_ns = 100'000'000;
    request22.arrival_ns = 100'000'000;
    request23.arrival_ns = 100'000'000;
    request20.logical_address = 0;
    request21.logical_address = 0;
    request22.logical_address = 0;
    request23.logical_address = 0;
#endif
    CHECK(hbfsim::runtime::submit_without_wait_for_test(
              context, request20, &ticket20) == HBFSIM_OK);
    CHECK(hbfsim::runtime::submit_without_wait_for_test(
              context, request21, &ticket21) == HBFSIM_OK);
    CHECK(hbfsim::runtime::submit_without_wait_for_test(
              context, request22, &ticket22) == HBFSIM_OK);
    CHECK(hbfsim::runtime::submit_without_wait_for_test(
              context, request23, &ticket23) == HBFSIM_OK);
    std::uint64_t timeout_ticket = 0;
    CHECK(hbfsim::runtime::submit_without_wait_for_test(
              context, request(24), &timeout_ticket) == HBFSIM_TIMEOUT);
    hbfsim::runtime::pause_daemon_for_test(context, false);

    std::vector<hbfsim::HbfCompletion> overlapping_completions;
    for (const auto [sequence, ticket] :
         std::vector<std::pair<std::uint64_t, std::uint64_t>>{
             {20, ticket20}, {21, ticket21}, {22, ticket22}, {23, ticket23}}) {
        hbfsim::HbfCompletion completion{};
        CHECK(hbfsim::runtime::wait_for_completion_for_test(
                  context, ticket, sequence + 1, &completion) == HBFSIM_OK);
        CHECK(completion.request_id == sequence + 1);
        overlapping_completions.push_back(completion);
    }
#if defined(HBFSIM_TEST_MQSIM_ENABLED)
    for (std::size_t index = 1; index < overlapping_completions.size();
         ++index) {
        CHECK(overlapping_completions[index].modeled_completion_ns >=
              overlapping_completions[index - 1].modeled_completion_ns);
    }
    CHECK(overlapping_completions.back().modeled_completion_ns >
          overlapping_completions.front().modeled_completion_ns);
#endif

    const auto daemon = hbfsim::runtime::daemon_pid_for_test(context);
    hbfsim_context_destroy(context);
    int status = 0;
    CHECK(::waitpid(daemon, &status, WNOHANG) == -1);

#if !defined(HBFSIM_TEST_MQSIM_ENABLED)
    hbfsim_options concurrent_options = options;
    concurrent_options.ring_capacity = 64;
    concurrent_options.request_timeout_ns = 2'000'000'000;
    hbfsim_context* concurrent_context = nullptr;
    CHECK(hbfsim::runtime::create_cpu_test_context(
              &concurrent_options, argv[1], &concurrent_context) == HBFSIM_OK);
    constexpr std::uint64_t thread_count = 8;
    constexpr std::uint64_t requests_per_thread = 64;
    std::atomic<std::uint64_t> failures{0};
    std::vector<std::thread> clients;
    for (std::uint64_t thread = 0; thread < thread_count; ++thread) {
        clients.emplace_back([&, thread] {
            for (std::uint64_t index = 0; index < requests_per_thread;
                 ++index) {
                auto descriptor =
                    request(1'000 + thread * requests_per_thread + index);
                hbfsim::HbfCompletion completion{};
                if (hbfsim::runtime::submit_for_test(
                        concurrent_context, descriptor, &completion) !=
                        HBFSIM_OK ||
                    completion.request_id != descriptor.request_id) {
                    failures.fetch_add(1, std::memory_order_relaxed);
                }
            }
        });
    }
    for (auto& client : clients) {
        client.join();
    }
    const auto concurrent_daemon =
        hbfsim::runtime::daemon_pid_for_test(concurrent_context);
    hbfsim_context_destroy(concurrent_context);
    CHECK(failures.load(std::memory_order_relaxed) == 0);
    CHECK(::waitpid(concurrent_daemon, &status, WNOHANG) == -1);
#endif
    std::filesystem::remove_all(report_dir);
    return 0;
}

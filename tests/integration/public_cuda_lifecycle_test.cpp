#include "../../src/cuda_runtime/context.hpp"

#include <hbfsim/api.h>

#include <atomic>
#include <chrono>
#include <csignal>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <dlfcn.h>
#include <vector>
#include <string>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

extern "C" void fakeCudaSetCurrentDomain(std::uintptr_t context, int device);
extern "C" void fakeCudaSetPointerMetadata(
    std::uintptr_t context, int device, unsigned int memory_type,
    unsigned int is_managed, std::uintptr_t base, std::size_t bytes);
extern "C" void fakeCudaSetHostUnregisterFailure(int fail);
extern "C" void fakeCudaResetLifecycleCounts();
extern "C" int fakeCudaSynchronizeCount();
extern "C" int fakeCudaUnregisterCount();
extern "C" int fakeCudaLaunchCount();
extern "C" std::uint64_t fakeCudaControlAlias();
extern "C" std::uint64_t fakeCudaControlGeneration();
extern "C" void fakeCudaPausePointerValidation();
extern "C" void fakeCudaWaitPointerValidationEntered();
extern "C" void fakeCudaReleasePointerValidation();
extern "C" void fakeCudaCapacityReset();
extern "C" std::size_t fakeCudaCapacityLiveReservations();
extern "C" std::size_t fakeCudaCapacityLiveHandles();
extern "C" std::size_t fakeCudaCapacityLivePinnedBuffers();
extern "C" std::size_t fakeCudaCapacityLiveWorkerContexts();
extern "C" std::size_t fakeCudaCapacityHtoDCalls();
extern "C" std::size_t fakeCudaCapacityEventCount();
extern "C" int fakeCudaCapacityEvent(std::size_t index);
extern "C" void fakeCudaCapacityFail(const char* operation, int call);
extern "C" void fakeCudaSetSynchronizeFailure(int fail);
extern "C" int fakeCudaCapacityReadDevice(std::uintptr_t address, void* bytes,
                                            std::size_t size);
extern "C" int fakeCudaCapacityWriteDevice(std::uintptr_t address,
                                             const void* bytes,
                                             std::size_t size);

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "public lifecycle CHECK failed at line %d: %s\n",
                 line, expression);
    std::exit(1);
}

#define CHECK(expression)                                                       \
    do {                                                                        \
        if (!(expression)) {                                                    \
            fail(#expression, __LINE__);                                        \
        }                                                                       \
    } while (false)

hbfsim_options options(const char* profile, const char* report)
{
    return {.profile_path = profile,
            .report_dir = report,
            .mode = 0,
            .ring_capacity = 8,
            .request_timeout_ns = 1'000'000'000};
}

hbfsim_context* create(const hbfsim_options& configuration)
{
    hbfsim_context* context = nullptr;
    CHECK(hbfsim_context_create(&configuration, &context) == HBFSIM_OK);
    CHECK(context != nullptr);
    return context;
}

int register_range_status(hbfsim_context* context)
{
    fakeCudaSetPointerMetadata(0xCA00, 3, 2, 0, 0x1000, 0x1000);
    const hbfsim_range_options range{
        .mode = HBFSIM_RANGE_MODE_TIMING,
        .permissions = HBFSIM_RANGE_READ_WRITE,
        .cache_policy = HBFSIM_CACHE_POLICY_NONE,
        .stream_id = 0,
    };
    return hbfsim_register_device(context, reinterpret_cast<void*>(0x1000),
                                  0x1000, &range);
}

void register_range(hbfsim_context* context)
{
    CHECK(register_range_status(context) == HBFSIM_OK);
}

void install_manifest(const char* report)
{
    const auto manifest = std::filesystem::path(report) / "manifest.jsonl";
    std::ofstream output(manifest);
    CHECK(output.good());
    output << R"({"module_id":"ptx:sha256:42)"
           << std::string(62, '0')
           << R"(","kernel":"kernel","ptx_target":"sm_120","instrumented":true,"cubin_only":false,"parameters":[{"index":0,"offset":0,"width":8,"kind":"pointer"}],"unsupported_parameters":[]})"
           << '\n';
    output.close();
    CHECK(output.good());
    CHECK(::setenv("HBFSIM_PASS_MANIFEST_PATH", manifest.c_str(), 1) == 0);
    const auto coverage = std::filesystem::path(report) / "coverage.jsonl";
    CHECK(::setenv("HBFSIM_COVERAGE_PATH", coverage.c_str(), 1) == 0);
}

std::string install_capacity_profile(const char* report)
{
    const auto profile =
        std::filesystem::path(report) / "capacity-profile.json";
    std::ofstream output(profile);
    CHECK(output.good());
    output << R"({
  "name": "public-capacity-test",
  "capacity_bytes": 1048576,
  "page_bytes": 16384,
  "read_latency_ns": 10000,
  "program_latency_ns": 100000000,
  "channels": 1,
  "dies_per_channel": 1,
  "planes_per_die": 1,
  "pages_per_block": 1,
  "channel_width_bits": 8,
  "channel_transfer_rate_mtps": 1600,
  "queue_depth": 8,
  "aggregate_bandwidth_bytes_per_s": 1000000000,
  "hbm_cache_bytes": 32768,
  "reference_sample_rate": 0.0,
  "reference_warmup_requests": 0,
  "time_scale": 1,
  "timing_tolerance_ns": 10000
})";
    output.close();
    CHECK(output.good());
    return profile.string();
}

std::vector<char> capacity_bytes(std::uint32_t seed)
{
    std::vector<char> bytes(4 * 16'384);
    for (std::size_t index = 0; index < bytes.size(); ++index) {
        bytes[index] = static_cast<char>((index * 17 + seed) & 0xff);
    }
    return bytes;
}

std::string install_capacity_backing(const char* report, const char* name,
                                     std::uint32_t seed)
{
    const auto backing = std::filesystem::path(report) / name;
    std::ofstream output(backing, std::ios::binary);
    CHECK(output.good());
    const auto bytes = capacity_bytes(seed);
    output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    output.close();
    CHECK(output.good());
    return backing.string();
}

std::vector<char> read_capacity_backing(const std::string& path)
{
    std::ifstream input(path, std::ios::binary);
    CHECK(input.good());
    std::vector<char> bytes(4 * 16'384);
    input.read(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    CHECK(input.gcount() == static_cast<std::streamsize>(bytes.size()));
    return bytes;
}

void load_trusted_module()
{
    using begin_type = std::uint64_t (*)(const char*, std::size_t) noexcept;
    using load_type = int (*)(void**, const void*, unsigned int, void*, void**);
    auto begin = reinterpret_cast<begin_type>(
        ::dlsym(RTLD_DEFAULT, "hbfsim_begin_module_load_from_ptx"));
    auto load = reinterpret_cast<load_type>(
        ::dlsym(RTLD_DEFAULT, "cuModuleLoadDataEx"));
    CHECK(begin != nullptr && load != nullptr);
    std::string values = "0x42";
    for (int index = 1; index < 32; ++index) {
        values += ", 0x00";
    }
    const std::string ptx =
        ".version 8.7\n.target sm_120\n.address_size 64\n"
        ".visible .const .align 8 .b8 __hbfsim_module_identity[32] = {" +
        values + "};\n.visible .entry kernel() { ret; }\n";
    CHECK(begin(ptx.data(), ptx.size()) != 0);
    void* module = nullptr;
    CHECK(load(&module, "public-lifecycle", 0, nullptr, nullptr) == 0);
    CHECK(module != nullptr);
    CHECK(fakeCudaControlAlias() != 0);
    CHECK(fakeCudaControlGeneration() != 0);
}

int launch_relevant(std::uintptr_t pointer = 0x1008)
{
    using launch_type = int (*)(void*, unsigned int, unsigned int,
                                unsigned int, unsigned int, unsigned int,
                                unsigned int, unsigned int, void*, void**,
                                void**);
    auto launch = reinterpret_cast<launch_type>(
        ::dlsym(RTLD_DEFAULT, "cuLaunchKernel"));
    CHECK(launch != nullptr);
    void* parameters[] = {&pointer};
    return launch(reinterpret_cast<void*>(0x9000), 1, 1, 1, 1, 1, 1, 0,
                  nullptr, parameters, nullptr);
}

void check_relevant_launch_is_quarantined()
{
    const auto launches = fakeCudaLaunchCount();
    CHECK(launch_relevant() != 0);
    CHECK(fakeCudaLaunchCount() == launches);
}

void kill_daemon_after_begin(void* opaque) noexcept
{
    const auto daemon = *static_cast<pid_t*>(opaque);
    (void)::kill(daemon, SIGKILL);
    int status = 0;
    while (::waitpid(daemon, &status, 0) < 0 && errno == EINTR) {
    }
}

}  // namespace

int main(int argc, char** argv)
{
    CHECK(argc == 3);
    char report_template[] = "/tmp/hbfsim-public-lifecycle-XXXXXX";
    char* report = ::mkdtemp(report_template);
    CHECK(report != nullptr);
    install_manifest(report);
    const auto capacity_mode = std::strcmp(argv[1], "capacity") == 0;
    const auto capacity_destroy_failure =
        std::strcmp(argv[1], "capacity-destroy-failure") == 0;
    const auto capacity_destroy_cleanup_failure =
        std::strcmp(argv[1], "capacity-destroy-cleanup-failure") == 0;
    const auto capacity_disabled =
        std::strcmp(argv[1], "capacity-disabled") == 0;
    const auto profile =
        capacity_mode || capacity_destroy_failure ||
                capacity_destroy_cleanup_failure || capacity_disabled
            ? install_capacity_profile(report)
            : std::string(argv[2]);
    auto configuration = options(profile.c_str(), report);
    if (capacity_mode || capacity_destroy_failure ||
        capacity_destroy_cleanup_failure || capacity_disabled) {
        configuration.request_timeout_ns = 200'000'000;
    }
    fakeCudaSetCurrentDomain(0xCA00, 3);
    fakeCudaResetLifecycleCounts();
    fakeCudaCapacityReset();
    auto* context = create(configuration);
    const hbfsim_range_options unsupported_capacity{
        .mode = HBFSIM_RANGE_MODE_CAPACITY,
        .permissions = HBFSIM_RANGE_READ,
        .cache_policy = HBFSIM_CACHE_POLICY_NONE,
        .stream_id = 0,
    };
    CHECK(hbfsim_register_device(
              context, reinterpret_cast<void*>(0x1000), 0x1000,
              &unsupported_capacity) == HBFSIM_UNSUPPORTED);
    CHECK(hbfsim::runtime::range_count_for_test(context) == 0);

    if (capacity_disabled) {
        const auto backing =
            install_capacity_backing(report, "capacity-disabled.bin", 3);
        const hbfsim_range_options capacity{
            .mode = HBFSIM_RANGE_MODE_CAPACITY,
            .permissions = HBFSIM_RANGE_READ,
            .cache_policy = HBFSIM_CACHE_POLICY_NONE,
            .stream_id = 0,
        };
        void* logical = reinterpret_cast<void*>(1);
        CHECK(hbfsim_map_file(context, backing.c_str(), 0, 16'384,
                              &capacity, &logical) == HBFSIM_UNSUPPORTED);
        CHECK(logical == nullptr);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 0);
        CHECK(fakeCudaCapacityLiveReservations() == 0);
        CHECK(fakeCudaCapacityLiveHandles() == 0);
        CHECK(fakeCudaCapacityLivePinnedBuffers() == 0);
        CHECK(fakeCudaCapacityLiveWorkerContexts() == 0);
        hbfsim_context_destroy(context);
        std::error_code error;
        std::filesystem::remove_all(report, error);
        return 0;
    }

    if (capacity_destroy_failure) {
        const auto backing =
            install_capacity_backing(report, "capacity-dirty.bin", 41);
        const hbfsim_range_options capacity{
            .mode = HBFSIM_RANGE_MODE_CAPACITY,
            .permissions = HBFSIM_RANGE_READ_WRITE,
            .cache_policy = HBFSIM_CACHE_POLICY_NONE,
            .stream_id = 0,
        };
        void* logical = nullptr;
        CHECK(hbfsim_map_file(context, backing.c_str(), 0, 16'384,
                              &capacity, &logical) == HBFSIM_OK);
        CHECK(logical != nullptr);
        const auto arrival_ns = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now().time_since_epoch())
                .count());
        const hbfsim::HbfRequest request{
            .request_id = 501,
            .sequence = 0,
            .arrival_ns = arrival_ns,
            .logical_address = 0,
            .deadline_ns = 0,
            .bytes = 16'384,
            .range_id = 1,
            .stream_id = 0,
            .operation = static_cast<std::uint32_t>(
                hbfsim::RequestOperation::Write),
            .page_generation = 1,
            .flags = 0,
        };
        hbfsim::HbfCompletion completion{};
        CHECK(hbfsim::runtime::submit_for_test(context, request,
                                               &completion) == HBFSIM_OK);
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::Ready));
        std::vector<char> replacement(16'384, static_cast<char>(0xD7));
        CHECK(fakeCudaCapacityWriteDevice(completion.cache_frame_address,
                                          replacement.data(),
                                          replacement.size()) == 0);
        const auto daemon = hbfsim::runtime::daemon_pid_for_test(context);
        fakeCudaCapacityFail("cuMemcpyDtoH_v2", 1);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaSynchronizeCount() == 1);
        CHECK(fakeCudaUnregisterCount() == 0);
        CHECK(fakeCudaCapacityLiveReservations() == 2);
        CHECK(fakeCudaCapacityLiveHandles() == 1);
        CHECK(fakeCudaCapacityLivePinnedBuffers() == 1);
        CHECK(fakeCudaCapacityLiveWorkerContexts() == 1);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 1);
        CHECK(::kill(daemon, 0) == 0);
        CHECK(hbfsim_flush(context) == HBFSIM_IO_ERROR);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaCapacityLiveReservations() == 2);
        return 0;
    }

    if (capacity_destroy_cleanup_failure) {
        const auto backing = install_capacity_backing(
            report, "capacity-cleanup.bin", 73);
        const hbfsim_range_options capacity{
            .mode = HBFSIM_RANGE_MODE_CAPACITY,
            .permissions = HBFSIM_RANGE_READ_WRITE,
            .cache_policy = HBFSIM_CACHE_POLICY_NONE,
            .stream_id = 0,
        };
        void* logical = nullptr;
        CHECK(hbfsim_map_file(context, backing.c_str(), 0, 16'384,
                              &capacity, &logical) == HBFSIM_OK);
        CHECK(logical != nullptr);
        const auto daemon = hbfsim::runtime::daemon_pid_for_test(context);
        fakeCudaCapacityFail("cuMemUnmap", 1);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaSynchronizeCount() == 1);
        CHECK(fakeCudaUnregisterCount() == 0);
        CHECK(fakeCudaCapacityLiveReservations() == 1);
        CHECK(fakeCudaCapacityLiveHandles() == 1);
        CHECK(fakeCudaCapacityLivePinnedBuffers() == 0);
        CHECK(fakeCudaCapacityLiveWorkerContexts() == 0);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 1);
        CHECK(::kill(daemon, 0) == 0);
        CHECK(hbfsim_flush(context) == HBFSIM_IO_ERROR);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaCapacityLiveReservations() == 1);
        CHECK(fakeCudaCapacityLiveHandles() == 1);
        return 0;
    }

    if (capacity_mode) {
        fakeCudaSetCurrentDomain(0xCB00, 3);
        CHECK(hbfsim_flush(context) == HBFSIM_CUDA_ERROR);
        fakeCudaSetCurrentDomain(0xCA00, 3);
        hbfsim::runtime::pause_daemon_for_test(context, true);
        const hbfsim::HbfRequest stalled_request{
            .request_id = 900,
            .sequence = 0,
            .arrival_ns = 1,
            .logical_address = 0,
            .deadline_ns = 0,
            .bytes = 16'384,
            .range_id = 1,
            .stream_id = 0,
            .operation = static_cast<std::uint32_t>(
                hbfsim::RequestOperation::Read),
            .page_generation = 1,
            .flags = 0,
        };
        std::uint64_t stalled_ticket = 0;
        CHECK(hbfsim::runtime::submit_without_wait_for_test(
                  context, stalled_request, &stalled_ticket) == HBFSIM_OK);
        hbfsim::HbfCompletion stalled_completion{};
        CHECK(hbfsim::runtime::wait_for_completion_for_test(
                  context, stalled_ticket, stalled_request.request_id,
                  &stalled_completion) == HBFSIM_DAEMON_LOST);
        CHECK(hbfsim_flush(context) == HBFSIM_DAEMON_LOST);
        hbfsim::runtime::pause_daemon_for_test(context, false);
        const auto heartbeat_recovery_deadline =
            std::chrono::steady_clock::now() + std::chrono::milliseconds(500);
        while (hbfsim::runtime::retirement_liveness_for_test(context) !=
                   HBFSIM_OK &&
               std::chrono::steady_clock::now() <
                   heartbeat_recovery_deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        CHECK(hbfsim::runtime::retirement_liveness_for_test(context) ==
              HBFSIM_OK);

        const auto backing_a =
            install_capacity_backing(report, "capacity-a.bin", 3);
        const auto backing_b =
            install_capacity_backing(report, "capacity-b.bin", 91);
        const auto backing_c =
            install_capacity_backing(report, "capacity-c.bin", 157);
        const hbfsim_range_options capacity_read{
            .mode = HBFSIM_RANGE_MODE_CAPACITY,
            .permissions = HBFSIM_RANGE_READ,
            .cache_policy = HBFSIM_CACHE_POLICY_NONE,
            .stream_id = 0,
        };
        auto capacity_write = capacity_read;
        capacity_write.permissions = HBFSIM_RANGE_READ_WRITE;
        auto wrong_mode = capacity_read;
        wrong_mode.mode = HBFSIM_RANGE_MODE_TIMING;
        auto write_only = capacity_read;
        write_only.permissions = HBFSIM_RANGE_WRITE;
        void* logical_a = nullptr;
        void* logical_b = nullptr;
        void* rejected = reinterpret_cast<void*>(1);
        CHECK(hbfsim_map_file(context, backing_a.c_str(), 0, 16'384,
                              &wrong_mode, &rejected) == HBFSIM_UNSUPPORTED);
        CHECK(rejected == nullptr);
        rejected = reinterpret_cast<void*>(1);
        CHECK(hbfsim_map_file(context, backing_a.c_str(), 0, 16'384,
                              &write_only, &rejected) ==
              HBFSIM_INVALID_ARGUMENT);
        CHECK(rejected == nullptr);
        rejected = reinterpret_cast<void*>(1);
        CHECK(hbfsim_map_file(context, backing_a.c_str(), 4 * 16'384, 1,
                              &capacity_read, &rejected) == HBFSIM_IO_ERROR);
        CHECK(rejected == nullptr);
        fakeCudaSetCurrentDomain(0xCB00, 3);
        rejected = reinterpret_cast<void*>(1);
        CHECK(hbfsim_map_file(context, backing_a.c_str(), 0, 16'384,
                              &capacity_read, &rejected) == HBFSIM_CUDA_ERROR);
        CHECK(rejected == nullptr);
        fakeCudaSetCurrentDomain(0xCA00, 3);
        CHECK(fakeCudaCapacityLiveReservations() == 0);
        CHECK(fakeCudaCapacityLiveHandles() == 0);
        CHECK(fakeCudaCapacityLivePinnedBuffers() == 0);
        CHECK(fakeCudaCapacityLiveWorkerContexts() == 0);

        CHECK(hbfsim_map_file(context, backing_a.c_str(), 0, 4 * 16'384,
                              &capacity_write, &logical_a) == HBFSIM_OK);
        CHECK(hbfsim_map_file(context, backing_b.c_str(), 0, 4 * 16'384,
                              &capacity_write, &logical_b) == HBFSIM_OK);
        CHECK(logical_a != nullptr);
        CHECK(logical_b != nullptr);
        CHECK(logical_a != logical_b);
        CHECK(reinterpret_cast<std::uintptr_t>(logical_a) % 16'384 == 0);
        CHECK(reinterpret_cast<std::uintptr_t>(logical_b) % 16'384 == 0);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 2);
        CHECK(fakeCudaCapacityLiveReservations() == 3);
        CHECK(fakeCudaCapacityLiveHandles() == 1);
        CHECK(fakeCudaCapacityLivePinnedBuffers() == 1);
        CHECK(fakeCudaCapacityLiveWorkerContexts() == 1);
        fakeCudaCapacityFail("cuMemAddressReserve", 1);
        rejected = reinterpret_cast<void*>(1);
        CHECK(hbfsim_map_file(context, backing_c.c_str(), 0, 16'384,
                              &capacity_read, &rejected) == HBFSIM_CUDA_ERROR);
        CHECK(rejected == nullptr);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 2);
        CHECK(fakeCudaCapacityLiveReservations() == 3);
        fakeCudaSetCurrentDomain(0xCB00, 3);
        CHECK(hbfsim_flush(context) == HBFSIM_CUDA_ERROR);
        fakeCudaSetCurrentDomain(0xCA00, 3);

        const auto submit_page = [&](std::uint64_t request_id,
                                     std::uint64_t global_page,
                                     std::uint32_t range_id,
                                     hbfsim::RequestOperation operation) {
            const auto arrival_ns = static_cast<std::uint64_t>(
                std::chrono::duration_cast<std::chrono::nanoseconds>(
                    std::chrono::steady_clock::now().time_since_epoch())
                    .count());
            const hbfsim::HbfRequest request{
                .request_id = request_id,
                .sequence = 0,
                .arrival_ns = arrival_ns,
                .logical_address = global_page * 16'384,
                .deadline_ns = 0,
                .bytes = 16'384,
                .range_id = range_id,
                .stream_id = 0,
                .operation = static_cast<std::uint32_t>(operation),
                .page_generation = 1,
                .flags = 0,
            };
            hbfsim::HbfCompletion completion{};
            const auto submit_status = hbfsim::runtime::submit_for_test(
                context, request, &completion);
            if (submit_status != HBFSIM_OK) {
                std::fprintf(stderr,
                             "capacity submit: status=%d completion=%u "
                             "daemon=%d htod=%zu events=%zu\n",
                             submit_status, completion.status,
                             static_cast<int>(
                                 hbfsim::runtime::daemon_pid_for_test(
                                     context)),
                             fakeCudaCapacityHtoDCalls(),
                             fakeCudaCapacityEventCount());
                for (std::size_t index = 0;
                     index < fakeCudaCapacityEventCount(); ++index) {
                    std::fprintf(stderr, "capacity event[%zu]=%d\n", index,
                                 fakeCudaCapacityEvent(index));
                }
            }
            CHECK(submit_status == HBFSIM_OK);
            if (completion.status != static_cast<std::uint32_t>(
                                         hbfsim::RequestStatus::Ready)) {
                std::fprintf(stderr,
                             "capacity completion: status=%u frame=%llu "
                             "modeled=%llu service=%llu\n",
                             completion.status,
                             static_cast<unsigned long long>(
                                 completion.cache_frame_address),
                             static_cast<unsigned long long>(
                                 completion.modeled_ns),
                             static_cast<unsigned long long>(
                                 completion.service_ns));
            }
            CHECK(completion.status == static_cast<std::uint32_t>(
                                           hbfsim::RequestStatus::Ready));
            CHECK(completion.cache_frame_address != 0);
            return completion.cache_frame_address;
        };

        const auto frame_a = submit_page(101, 0, 1,
                                         hbfsim::RequestOperation::Read);
        const auto frame_b = submit_page(102, 4, 2,
                                         hbfsim::RequestOperation::Write);
        std::vector<char> observed(16'384);
        CHECK(fakeCudaCapacityReadDevice(frame_a, observed.data(),
                                         observed.size()) == 0);
        const auto expected_a = capacity_bytes(3);
        CHECK(std::equal(observed.begin(), observed.end(),
                         expected_a.begin()));
        CHECK(fakeCudaCapacityReadDevice(frame_b, observed.data(),
                                         observed.size()) == 0);
        const auto expected_b = capacity_bytes(91);
        CHECK(std::equal(observed.begin(), observed.end(),
                         expected_b.begin()));

        CHECK(submit_page(103, 0, 1, hbfsim::RequestOperation::Write) ==
              frame_a);

        std::vector<char> replacement(16'384, static_cast<char>(0xA5));
        CHECK(fakeCudaCapacityWriteDevice(frame_b, replacement.data(),
                                          replacement.size()) == 0);
        fakeCudaCapacityFail("cuMemcpyDtoH_v2", 1);
        CHECK(hbfsim_flush(context) == HBFSIM_CUDA_ERROR);
        const auto failed_persist_b = read_capacity_backing(backing_b);
        CHECK(std::equal(expected_b.begin(), expected_b.end(),
                         failed_persist_b.begin()));
        CHECK(hbfsim_flush(context) == HBFSIM_OK);
        const auto persisted_b = read_capacity_backing(backing_b);
        CHECK(std::equal(replacement.begin(), replacement.end(),
                         persisted_b.begin()));

        load_trusted_module();
        CHECK(launch_relevant(reinterpret_cast<std::uintptr_t>(logical_a) + 8) ==
              0);
        fakeCudaCapacityFail("cuMemAddressFree", 1);
        rejected = reinterpret_cast<void*>(1);
        CHECK(hbfsim_map_file(context, backing_c.c_str(), 0, 16'384,
                              &capacity_read, &rejected) == HBFSIM_CUDA_ERROR);
        CHECK(rejected == nullptr);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 2);
        CHECK(fakeCudaCapacityLiveReservations() == 4);

        fakeCudaCapacityFail("cuMemAddressFree", 1);
        CHECK(hbfsim_unregister(context, logical_a) == HBFSIM_CUDA_ERROR);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 2);
        CHECK(fakeCudaCapacityLiveReservations() == 4);
        fakeCudaSetSynchronizeFailure(1);
        CHECK(hbfsim_unregister(context, logical_a) == HBFSIM_IO_ERROR);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 2);
        CHECK(fakeCudaCapacityLiveReservations() == 4);
        fakeCudaSetSynchronizeFailure(0);
        CHECK(hbfsim_unregister(context, logical_a) == HBFSIM_OK);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 1);
        CHECK(fakeCudaCapacityLiveReservations() == 3);
        CHECK(hbfsim_unregister(context, logical_a) == HBFSIM_INVALID_ARGUMENT);

        const auto frame_b_retry = submit_page(
            104, 4, 2, hbfsim::RequestOperation::Write);
        std::vector<char> replacement_retry(16'384,
                                             static_cast<char>(0x3C));
        CHECK(fakeCudaCapacityWriteDevice(frame_b_retry,
                                          replacement_retry.data(),
                                          replacement_retry.size()) == 0);
        fakeCudaCapacityFail("cuMemcpyDtoH_v2", 1);
        CHECK(hbfsim_unregister(context, logical_b) == HBFSIM_CUDA_ERROR);
        CHECK(hbfsim::runtime::range_count_for_test(context) == 1);
        CHECK(fakeCudaCapacityLiveReservations() == 3);
        const auto unregister_failed_b = read_capacity_backing(backing_b);
        CHECK(std::equal(replacement.begin(), replacement.end(),
                         unregister_failed_b.begin()));
        CHECK(hbfsim_unregister(context, logical_b) == HBFSIM_OK);
        const auto unregister_persisted_b = read_capacity_backing(backing_b);
        CHECK(std::equal(replacement_retry.begin(), replacement_retry.end(),
                         unregister_persisted_b.begin()));
        CHECK(hbfsim::runtime::range_count_for_test(context) == 0);
        CHECK(fakeCudaCapacityLiveReservations() == 2);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaCapacityLiveReservations() == 0);
        CHECK(fakeCudaCapacityLiveHandles() == 0);
        CHECK(fakeCudaCapacityLivePinnedBuffers() == 0);
        CHECK(fakeCudaCapacityLiveWorkerContexts() == 0);
        std::error_code error;
        std::filesystem::remove_all(report, error);
        return 0;
    }

    if (std::strcmp(argv[1], "register-destroy-race") == 0) {
        fakeCudaPausePointerValidation();
        int register_status = HBFSIM_IO_ERROR;
        std::thread registration([&] {
            fakeCudaSetCurrentDomain(0xCA00, 3);
            register_status = register_range_status(context);
        });
        fakeCudaWaitPointerValidationEntered();

        std::atomic<bool> destroy_finished{false};
        std::thread destroy([&] {
            fakeCudaSetCurrentDomain(0xCB00, 3);
            hbfsim_context_destroy(context);
            destroy_finished.store(true, std::memory_order_release);
        });
        CHECK(hbfsim::runtime::wait_for_context_closing_for_test(
            context, std::chrono::milliseconds(500)));
        CHECK(!destroy_finished.load(std::memory_order_acquire));
        const auto closing_status = hbfsim_flush(context);
        fakeCudaReleasePointerValidation();
        registration.join();
        destroy.join();

        CHECK(closing_status == HBFSIM_IO_ERROR);
        CHECK(register_status == HBFSIM_OK);
        CHECK(hbfsim_flush(context) == HBFSIM_OK);
        fakeCudaSetCurrentDomain(0xCA00, 3);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaSynchronizeCount() == 1);
        CHECK(fakeCudaUnregisterCount() == 1);

        auto* success_context = create(configuration);
        fakeCudaPausePointerValidation();
        int success_register_status = HBFSIM_IO_ERROR;
        std::thread success_registration([&] {
            fakeCudaSetCurrentDomain(0xCA00, 3);
            success_register_status = register_range_status(success_context);
        });
        fakeCudaWaitPointerValidationEntered();
        std::atomic<bool> success_destroy_finished{false};
        std::thread success_destroy([&] {
            fakeCudaSetCurrentDomain(0xCA00, 3);
            hbfsim_context_destroy(success_context);
            success_destroy_finished.store(true, std::memory_order_release);
        });
        CHECK(hbfsim::runtime::wait_for_context_closing_for_test(
            success_context, std::chrono::milliseconds(500)));
        CHECK(!success_destroy_finished.load(std::memory_order_acquire));
        CHECK(hbfsim_flush(success_context) == HBFSIM_IO_ERROR);
        fakeCudaReleasePointerValidation();
        success_registration.join();
        success_destroy.join();
        CHECK(success_register_status == HBFSIM_OK);
        CHECK(success_destroy_finished.load(std::memory_order_acquire));
        CHECK(fakeCudaSynchronizeCount() == 2);
        CHECK(fakeCudaUnregisterCount() == 2);
        std::error_code error;
        std::filesystem::remove_all(report, error);
        return 0;
    }

    load_trusted_module();
    register_range(context);
    CHECK(launch_relevant() == 0);
    CHECK(fakeCudaLaunchCount() == 1);

    if (std::strcmp(argv[1], "clean") == 0) {
        const auto daemon = hbfsim::runtime::daemon_pid_for_test(context);
        fakeCudaSetCurrentDomain(0xCB00, 3);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaSynchronizeCount() == 0);
        CHECK(fakeCudaUnregisterCount() == 0);
        CHECK(::kill(hbfsim::runtime::daemon_pid_for_test(context), 0) == 0);
        fakeCudaSetCurrentDomain(0xCA00, 3);
        CHECK(launch_relevant() == 0);
        CHECK(fakeCudaLaunchCount() == 2);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaSynchronizeCount() == 1);
        CHECK(fakeCudaUnregisterCount() == 1);
        CHECK(fakeCudaControlAlias() == 0);
        CHECK(fakeCudaControlGeneration() == 0);
        errno = 0;
        CHECK(::kill(daemon, 0) == -1 && errno == ESRCH);
    } else if (std::strcmp(argv[1], "daemon-death") == 0) {
        const auto daemon = hbfsim::runtime::daemon_pid_for_test(context);
        CHECK(::kill(daemon, SIGKILL) == 0);
        CHECK(hbfsim::runtime::wait_for_fault_for_test(
                  context, std::chrono::milliseconds(500)) ==
              HBFSIM_DAEMON_LOST);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaSynchronizeCount() == 0);
        CHECK(fakeCudaUnregisterCount() == 0);
        CHECK(fakeCudaControlAlias() != 0);
        check_relevant_launch_is_quarantined();
        CHECK(hbfsim_flush(context) == HBFSIM_IO_ERROR);
        hbfsim_context* rejected = nullptr;
        CHECK(hbfsim_context_create(&configuration, &rejected) != HBFSIM_OK);
        CHECK(rejected == nullptr);
    } else if (std::strcmp(argv[1], "daemon-after-begin") == 0) {
        auto daemon = hbfsim::runtime::daemon_pid_for_test(context);
        hbfsim::runtime::set_after_begin_retire_hook_for_test(
            context, kill_daemon_after_begin, &daemon);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaSynchronizeCount() == 0);
        CHECK(fakeCudaUnregisterCount() == 0);
        check_relevant_launch_is_quarantined();
        CHECK(hbfsim_flush(context) == HBFSIM_IO_ERROR);
        hbfsim_context* rejected = nullptr;
        CHECK(hbfsim_context_create(&configuration, &rejected) != HBFSIM_OK);
        CHECK(rejected == nullptr);
    } else if (std::strcmp(argv[1], "unregister-failure") == 0) {
        fakeCudaSetHostUnregisterFailure(1);
        hbfsim_context_destroy(context);
        CHECK(fakeCudaSynchronizeCount() == 1);
        CHECK(fakeCudaUnregisterCount() == 1);
        CHECK(fakeCudaControlAlias() == 0);
        check_relevant_launch_is_quarantined();
        CHECK(hbfsim_flush(context) == HBFSIM_IO_ERROR);
        hbfsim_context* rejected = nullptr;
        CHECK(hbfsim_context_create(&configuration, &rejected) != HBFSIM_OK);
        CHECK(rejected == nullptr);
    } else {
        fail("known lifecycle mode", __LINE__);
    }
    std::error_code error;
    std::filesystem::remove_all(report, error);
    return 0;
}

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

int launch_relevant()
{
    using launch_type = int (*)(void*, unsigned int, unsigned int,
                                unsigned int, unsigned int, unsigned int,
                                unsigned int, unsigned int, void*, void**,
                                void**);
    auto launch = reinterpret_cast<launch_type>(
        ::dlsym(RTLD_DEFAULT, "cuLaunchKernel"));
    CHECK(launch != nullptr);
    std::uintptr_t pointer = 0x1008;
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
    const auto configuration = options(argv[2], report);
    fakeCudaSetCurrentDomain(0xCA00, 3);
    fakeCudaResetLifecycleCounts();
    auto* context = create(configuration);

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

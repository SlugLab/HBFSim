#include "../../adapters/vllm/hbfsim_extension.h"

#include <hbfsim/api.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <unistd.h>

extern "C" void fakeCudaSetCurrentDomain(std::uintptr_t context, int device);
extern "C" void fakeCudaSetPointerMetadata(
    std::uintptr_t context, int device, unsigned int memory_type,
    unsigned int is_managed, std::uintptr_t base, std::size_t bytes);

namespace {

void require(bool condition, const char* expression)
{
    if (!condition) {
        throw std::runtime_error(expression);
    }
}

#define CHECK(expression) require(static_cast<bool>(expression), #expression)

}  // namespace

int main(int argc, char** argv)
{
    CHECK(argc == 3);
    CHECK(::setenv("HBFSIM_DAEMON_PATH", argv[1], 1) == 0);
    fakeCudaSetCurrentDomain(0xCA00, 3);
    fakeCudaSetPointerMetadata(0xCA00, 3, 2, 0, 0x1000, 0x2000);

    const auto report = std::filesystem::temp_directory_path() /
                        ("hbfsim-vllm-extension-" +
                         std::to_string(static_cast<long long>(::getpid())));
    std::filesystem::remove_all(report);
    std::filesystem::create_directories(report);

    hbfsim_vllm_options options{
        .struct_bytes = sizeof(hbfsim_vllm_options),
        .profile_path = argv[2],
        .report_dir = report.c_str(),
        .ring_capacity = 8,
        .timing_model = HBFSIM_MODEL_HYBRID,
        .request_timeout_ns = 1'000'000'000,
        .exact_profile_path = nullptr,
        .exact_cache_condition = 0,
        .exact_concurrency_condition = 0,
        .exact_cluster_x = 0,
        .exact_cluster_y = 0,
        .exact_cluster_z = 0,
    };
    hbfsim_vllm_session* session = reinterpret_cast<hbfsim_vllm_session*>(1);
    CHECK(hbfsim_vllm_abi_version() == 3);
    CHECK(hbfsim_vllm_session_create(nullptr, &session) ==
          HBFSIM_INVALID_ARGUMENT);
    CHECK(session == nullptr);
    CHECK(hbfsim_vllm_session_create(&options, &session) == HBFSIM_OK);
    CHECK(session != nullptr);
    CHECK(hbfsim_vllm_register_storage(session, 0, 0x1000) ==
          HBFSIM_INVALID_ARGUMENT);
    CHECK(hbfsim_vllm_register_storage(session, 0x1000, 0x2000) == HBFSIM_OK);
    CHECK(hbfsim_vllm_publish_exact_contract(session) == HBFSIM_UNSUPPORTED);
    CHECK(hbfsim_vllm_finalize_exact(session) == HBFSIM_UNSUPPORTED);
    CHECK(hbfsim_vllm_session_close(&session) == HBFSIM_OK);
    CHECK(session == nullptr);
    CHECK(hbfsim_vllm_session_close(&session) == HBFSIM_OK);
    CHECK(std::string(hbfsim_vllm_status_string(HBFSIM_OK)) == "ok");

    const auto source_root = std::filesystem::path(argv[2]).parent_path()
                                 .parent_path().parent_path();
    const auto exact_profile =
        source_root / "tests/fixtures/exact/sm120-valid.json";
    auto exact_options = options;
    exact_options.exact_profile_path = exact_profile.c_str();
    exact_options.exact_cache_condition = HBFSIM_EXACT_CACHE_WARM_L2;
    exact_options.exact_concurrency_condition =
        HBFSIM_EXACT_CONCURRENCY_EXCLUSIVE_PROCESS;
    exact_options.exact_cluster_x = 2;
    exact_options.exact_cluster_y = 1;
    exact_options.exact_cluster_z = 1;
    CHECK(hbfsim_vllm_session_create(&exact_options, &session) == HBFSIM_OK);
    CHECK(hbfsim_vllm_register_storage(session, 0x1000, 0x2000) == HBFSIM_OK);
    CHECK(hbfsim_vllm_publish_exact_contract(session) == HBFSIM_OK);
    CHECK(hbfsim_vllm_finalize_exact(session) == HBFSIM_UNSUPPORTED);
    CHECK(hbfsim_vllm_session_close(&session) == HBFSIM_UNSUPPORTED);
    CHECK(session == nullptr);

    std::filesystem::remove_all(report);
    return 0;
}

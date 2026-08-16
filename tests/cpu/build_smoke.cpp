#include <hbfsim/api.h>

#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <sys/mman.h>
#include <type_traits>
#include <unistd.h>

namespace {

void require(bool condition)
{
    if (!condition) {
        std::abort();
    }
}

}  // namespace

int main()
{
    static_assert(std::is_standard_layout_v<hbfsim_options>);
    static_assert(std::is_standard_layout_v<hbfsim_options_v2>);
    require(hbfsim_abi_version() == 5u);
    require(hbfsim_get_stats(nullptr, nullptr) == HBFSIM_INVALID_ARGUMENT);
    require(hbfsim_get_tma_stats(nullptr, nullptr) == HBFSIM_INVALID_ARGUMENT);

    hbfsim_context* context = reinterpret_cast<hbfsim_context*>(1);
    hbfsim_options_v2 options{};
    hbfsim_exact_run_contract contract{};
    contract.struct_bytes = sizeof(contract);
    require(hbfsim_publish_exact_run_contract(nullptr, &contract) ==
            HBFSIM_INVALID_ARGUMENT);
    require(hbfsim_finalize_exact(nullptr) == HBFSIM_INVALID_ARGUMENT);
    options.struct_bytes = offsetof(hbfsim_options_v2, fidelity);
    require(hbfsim_context_create_v2(&options, &context) ==
            HBFSIM_INVALID_ARGUMENT);
    require(context == nullptr);
    options.struct_bytes = sizeof(options) + 1;
    context = reinterpret_cast<hbfsim_context*>(1);
    require(hbfsim_context_create_v2(&options, &context) ==
            HBFSIM_INVALID_ARGUMENT);
    require(context == nullptr);
    options.struct_bytes = sizeof(options);
    options.fidelity = 99;
    context = reinterpret_cast<hbfsim_context*>(1);
    require(hbfsim_context_create_v2(&options, &context) ==
            HBFSIM_INVALID_ARGUMENT);
    require(context == nullptr);
    options.fidelity = HBFSIM_FIDELITY_EXACT_SM120;
    require(hbfsim_context_create_v2(&options, &context) ==
            HBFSIM_INVALID_ARGUMENT);
    require(context == nullptr);

    const auto raw_page_bytes = ::sysconf(_SC_PAGESIZE);
    require(raw_page_bytes > 0);
    const auto page_bytes = static_cast<std::size_t>(raw_page_bytes);
    auto* guarded = static_cast<unsigned char*>(::mmap(
        nullptr, page_bytes * 2, PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    require(guarded != MAP_FAILED);
    require(::mprotect(guarded + page_bytes, page_bytes, PROT_NONE) == 0);
    auto* legacy = reinterpret_cast<hbfsim_options*>(
        guarded + page_bytes - sizeof(hbfsim_options));
    std::memset(legacy, 0, sizeof(*legacy));
    context = reinterpret_cast<hbfsim_context*>(1);
    require(hbfsim_context_create(legacy, &context) ==
            HBFSIM_INVALID_ARGUMENT);
    require(context == nullptr);
    require(::munmap(guarded, page_bytes * 2) == 0);
    return 0;
}

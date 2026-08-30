#include "../../src/cuda_runtime/context.hpp"

#include <hbfsim/api.h>

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

extern "C" void fakeCudaSetCurrentDomain(std::uintptr_t context, int device);
extern "C" void fakeCudaSetPointerMetadata(
    std::uintptr_t context, int device, unsigned int memory_type,
    unsigned int is_managed, std::uintptr_t base, std::size_t bytes);

namespace {

int retire_calls = 0;

int begin_retire(std::uintptr_t, std::uint64_t,
                 std::uintptr_t* token) noexcept
{
    ++retire_calls;
    *token = 0x7777;
    return 0;
}

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "device range CHECK failed at line %d: %s\n", line,
                 expression);
    std::exit(1);
}

#define CHECK(expression)                                                       \
    do {                                                                        \
        if (!(expression)) {                                                    \
            fail(#expression, __LINE__);                                        \
        }                                                                       \
    } while (false)

int validate(std::uintptr_t pointer, std::size_t length)
{
    return hbfsim::runtime::validate_device_range_with_cuda_for_test(
        0xCA00, 3, reinterpret_cast<void*>(pointer), length);
}

}  // namespace

int main()
{
    fakeCudaSetCurrentDomain(0xCA00, 3);
    fakeCudaSetPointerMetadata(0xCA00, 3, 2, 0, 0x1000, 0x1000);
    CHECK(validate(0x1000, 0x1000) == HBFSIM_OK);
    CHECK(validate(0x1800, 0x800) == HBFSIM_OK);
    CHECK(validate(0x1800, 0x801) == HBFSIM_INVALID_ARGUMENT);
    CHECK(validate(0x0fff, 1) == HBFSIM_INVALID_ARGUMENT);
    CHECK(validate(UINTPTR_MAX - 7, 16) == HBFSIM_INVALID_ARGUMENT);

    fakeCudaSetCurrentDomain(0xCB00, 3);
    CHECK(validate(0x1000, 1) == HBFSIM_CUDA_ERROR);
    fakeCudaSetCurrentDomain(0xCA00, 4);
    CHECK(validate(0x1000, 1) == HBFSIM_CUDA_ERROR);
    fakeCudaSetCurrentDomain(0xCA00, 3);

    fakeCudaSetPointerMetadata(0xCB00, 3, 2, 0, 0x1000, 0x1000);
    CHECK(validate(0x1000, 1) == HBFSIM_CUDA_ERROR);
    fakeCudaSetPointerMetadata(0xCA00, 4, 2, 0, 0x1000, 0x1000);
    CHECK(validate(0x1000, 1) == HBFSIM_CUDA_ERROR);
    fakeCudaSetPointerMetadata(0xCA00, 3, 1, 0, 0x1000, 0x1000);
    CHECK(validate(0x1000, 1) == HBFSIM_CUDA_ERROR);
    fakeCudaSetPointerMetadata(0xCA00, 3, 2, 1, 0x1000, 0x1000);
    CHECK(validate(0x1000, 1) == HBFSIM_CUDA_ERROR);

    std::uintptr_t token = 0;
    fakeCudaSetCurrentDomain(0xCB00, 3);
    CHECK(hbfsim::runtime::begin_retire_with_cuda_for_test(
              0xCA00, 3, 0xA000, 7, begin_retire, &token) ==
          HBFSIM_CUDA_ERROR);
    CHECK(retire_calls == 0 && token == 0);
    fakeCudaSetCurrentDomain(0xCA00, 3);
    CHECK(hbfsim::runtime::begin_retire_with_cuda_for_test(
              0xCA00, 3, 0xA000, 7, begin_retire, &token) == HBFSIM_OK);
    CHECK(retire_calls == 1 && token == 0x7777);
    CHECK(hbfsim::runtime::normalize_launch_gate_status_for_test(0) ==
          HBFSIM_OK);
    CHECK(hbfsim::runtime::normalize_launch_gate_status_for_test(-1) ==
          HBFSIM_IO_ERROR);
    CHECK(hbfsim::runtime::normalize_launch_gate_status_for_test(99) ==
          HBFSIM_IO_ERROR);
    return 0;
}

#include <cstddef>
#include <cstdint>
extern "C" __global__ void r3_page_read_kernel(
    const std::uint64_t* data, std::uint64_t base_byte_offset,
    std::uint64_t words, std::uint64_t* output)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    const auto first = base_byte_offset / sizeof(std::uint64_t);
    for (std::uint64_t index = 0; index < words; ++index)
        output[index] = data[first + index];
}

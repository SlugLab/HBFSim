#include <cstdint>

namespace {

__device__ std::uint64_t splitmix(std::uint64_t value)
{
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

__device__ std::uint64_t rotate_left(std::uint64_t value, unsigned shift)
{
    return (value << shift) | (value >> (64 - shift));
}

}  // namespace

extern "C" __global__ void hbf_access_kernel(
    std::uint64_t* data, const std::uint64_t* byte_offsets,
    std::uint64_t iterations, std::uint64_t seed, bool mixed_write,
    std::uint64_t* output)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) {
        return;
    }
    std::uint64_t checksum = splitmix(seed ^ 0x48424653494dULL);
    for (std::uint64_t index = 0; index < iterations; ++index) {
        const auto word = byte_offsets[index] / sizeof(std::uint64_t);
        auto value = data[word];
        if (mixed_write && (index & 1U) != 0) {
            value ^= splitmix(seed + index);
            data[word] = value;
        }
        checksum = rotate_left(checksum ^ (value + index), 13);
    }
    *output = checksum;
}

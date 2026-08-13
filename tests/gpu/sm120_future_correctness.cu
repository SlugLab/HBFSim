#include <cstdint>

namespace {

__device__ __forceinline__ std::uint64_t global_timer()
{
    std::uint64_t value;
    asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(value));
    return value;
}

__device__ __forceinline__ std::uint64_t independent_work(
    std::uint64_t value)
{
#pragma unroll 1
    for (int index = 0; index < 512; ++index) {
        value ^= value << 13;
        value ^= value >> 7;
        value ^= value << 17;
        asm volatile("" : "+l"(value));
    }
    return value;
}

__device__ __forceinline__ void publish_stamps(
    std::uint64_t* stamps, std::uint64_t issue,
    std::uint64_t independent_end, std::uint64_t wait_end)
{
    stamps[0] = issue;
    stamps[1] = independent_end;
    stamps[2] = wait_end;
}

}  // namespace

extern "C" __global__ void sm120_future_load(
    const std::uint64_t* data, std::uint64_t seed,
    std::uint64_t* output, std::uint64_t* stamps)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    const auto issue = global_timer();
    const auto loaded = data[0];
    const auto independent = independent_work(seed);
    const auto independent_end = global_timer();
    asm volatile("" ::: "memory");
    output[0] = loaded ^ independent;
    const auto wait_end = global_timer();
    publish_stamps(stamps, issue, independent_end, wait_end);
}

extern "C" __global__ void sm120_future_store(
    std::uint64_t* data, std::uint64_t seed,
    std::uint64_t* output, std::uint64_t* stamps)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    auto source = seed ^ 0x53544f5245ULL;
    const auto issue = global_timer();
    data[1] = source;
    source = independent_work(source ^ 0x9e3779b97f4a7c15ULL);
    const auto independent_end = global_timer();
    __threadfence_system();
    const auto wait_end = global_timer();
    output[0] = data[1];
    output[1] = source;
    publish_stamps(stamps, issue, independent_end, wait_end);
}

extern "C" __global__ void sm120_future_atomic(
    unsigned long long* data, std::uint64_t seed,
    std::uint64_t* output, std::uint64_t* stamps)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    const auto issue = global_timer();
    const auto old = atomicAdd(data + 2, 7ULL);
    const auto independent = independent_work(seed ^ 0x41544f4d4943ULL);
    const auto independent_end = global_timer();
    output[0] = old ^ independent;
    output[1] = data[2];
    const auto wait_end = global_timer();
    publish_stamps(stamps, issue, independent_end, wait_end);
}

extern "C" __global__ void sm120_future_vector_branch(
    const ulonglong2* data, int enabled, std::uint64_t seed,
    std::uint64_t* output, std::uint64_t* stamps)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    const auto issue = global_timer();
    if (enabled == 0) {
        const auto independent =
            independent_work(seed ^ 0x564543544f52ULL);
        const auto independent_end = global_timer();
        output[0] = independent;
        publish_stamps(stamps, issue, independent_end, global_timer());
        return;
    }
    const auto loaded = data[2];
    const auto independent = independent_work(seed ^ 0x564543544f52ULL);
    const auto independent_end = global_timer();
    output[0] = loaded.x ^ loaded.y ^ independent;
    const auto wait_end = global_timer();
    publish_stamps(stamps, issue, independent_end, wait_end);
}

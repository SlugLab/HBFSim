#include <cuda.h>
#include <cuda/ptx>

#include <cstdint>

namespace {

__device__ __forceinline__ std::uint32_t shared_address(const void* value)
{
    return static_cast<std::uint32_t>(__cvta_generic_to_shared(value));
}

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
    for (int index = 0; index < 1024; ++index) {
        value ^= value << 13;
        value ^= value >> 7;
        value ^= value << 17;
        asm volatile("" : "+l"(value));
    }
    return value;
}

}  // namespace

extern "C" __global__ void sm120_tma_load_2d(
    const __grid_constant__ CUtensorMap tensor_map, std::uint32_t* output,
    std::uint64_t* stamps, std::uint64_t seed)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    __shared__ alignas(8) std::uint64_t barrier;
    if (blockIdx.x != 0 || threadIdx.x != 0) return;

    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(&barrier);
    const auto descriptor_address =
        reinterpret_cast<std::uint64_t>(&tensor_map);
    asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                 : : "r"(barrier_address) : "memory");
    std::uint64_t state;
    asm volatile(
        "mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], 256;"
        : "=l"(state) : "r"(barrier_address) : "memory");
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");

    const auto issue = global_timer();
    asm volatile(
        "cp.async.bulk.tensor.2d.shared::cta.global.tile."
        "mbarrier::complete_tx::bytes [%0], [%1, {%2, %3}], [%4];"
        : : "r"(tile_address), "l"(descriptor_address), "r"(0),
            "r"(0), "r"(barrier_address) : "memory");
    const auto independent = independent_work(seed);
    const auto independent_end = global_timer();
    while (!cuda::ptx::mbarrier_test_wait(&barrier, state)) {
    }
    const auto wait_end = global_timer();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
#pragma unroll 1
    for (std::uint32_t index = 0; index < 64; ++index) {
        output[index] = tile[index];
    }
    __threadfence_system();
    stamps[0] = issue;
    stamps[1] = independent_end;
    stamps[2] = wait_end;
    stamps[3] = independent;
}

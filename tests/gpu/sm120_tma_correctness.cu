#include <cuda.h>
#include <cuda/ptx>
#include <cooperative_groups.h>

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

template <int Dimensions>
__device__ __forceinline__ void issue_tiled_load(
    std::uint32_t tile, std::uint64_t descriptor, std::uint32_t barrier,
    std::int32_t x = 0)
{
    if constexpr (Dimensions == 1) {
        asm volatile(
            "cp.async.bulk.tensor.1d.shared::cta.global.tile."
            "mbarrier::complete_tx::bytes [%0], [%1, {%2}], [%3];"
            : : "r"(tile), "l"(descriptor), "r"(x), "r"(barrier)
            : "memory");
    } else if constexpr (Dimensions == 2) {
        asm volatile(
            "cp.async.bulk.tensor.2d.shared::cta.global.tile."
            "mbarrier::complete_tx::bytes [%0], [%1, {%2, %3}], [%4];"
            : : "r"(tile), "l"(descriptor), "r"(x), "r"(0),
                "r"(barrier) : "memory");
    } else if constexpr (Dimensions == 3) {
        asm volatile(
            "cp.async.bulk.tensor.3d.shared::cta.global.tile."
            "mbarrier::complete_tx::bytes "
            "[%0], [%1, {%2, %3, %4}], [%5];"
            : : "r"(tile), "l"(descriptor), "r"(x), "r"(0), "r"(0),
                "r"(barrier) : "memory");
    } else if constexpr (Dimensions == 4) {
        asm volatile(
            "cp.async.bulk.tensor.4d.shared::cta.global.tile."
            "mbarrier::complete_tx::bytes "
            "[%0], [%1, {%2, %3, %4, %5}], [%6];"
            : : "r"(tile), "l"(descriptor), "r"(x), "r"(0), "r"(0),
                "r"(0), "r"(barrier) : "memory");
    } else {
        asm volatile(
            "cp.async.bulk.tensor.5d.shared::cta.global.tile."
            "mbarrier::complete_tx::bytes "
            "[%0], [%1, {%2, %3, %4, %5, %6}], [%7];"
            : : "r"(tile), "l"(descriptor), "r"(x), "r"(0), "r"(0),
                "r"(0), "r"(0), "r"(barrier) : "memory");
    }
}

template <int Dimensions>
__device__ __forceinline__ void issue_tiled_store(
    std::uint64_t descriptor, std::uint32_t tile)
{
    if constexpr (Dimensions == 1) {
        asm volatile(
            "cp.async.bulk.tensor.1d.global.shared::cta.tile.bulk_group "
            "[%0, {%1}], [%2];"
            : : "l"(descriptor), "r"(0), "r"(tile) : "memory");
    } else if constexpr (Dimensions == 2) {
        asm volatile(
            "cp.async.bulk.tensor.2d.global.shared::cta.tile.bulk_group "
            "[%0, {%1, %2}], [%3];"
            : : "l"(descriptor), "r"(0), "r"(0), "r"(tile) : "memory");
    } else if constexpr (Dimensions == 3) {
        asm volatile(
            "cp.async.bulk.tensor.3d.global.shared::cta.tile.bulk_group "
            "[%0, {%1, %2, %3}], [%4];"
            : : "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(tile)
            : "memory");
    } else if constexpr (Dimensions == 4) {
        asm volatile(
            "cp.async.bulk.tensor.4d.global.shared::cta.tile.bulk_group "
            "[%0, {%1, %2, %3, %4}], [%5];"
            : : "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(0),
                "r"(tile) : "memory");
    } else {
        asm volatile(
            "cp.async.bulk.tensor.5d.global.shared::cta.tile.bulk_group "
            "[%0, {%1, %2, %3, %4, %5}], [%6];"
            : : "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(0),
                "r"(0), "r"(tile) : "memory");
    }
}

template <int Dimensions>
__device__ __forceinline__ void tiled_load_body(
    const CUtensorMap& tensor_map, std::uint32_t* output,
    std::uint64_t* stamps, std::uint64_t seed, std::uint32_t* tile,
    std::uint64_t* barrier, std::int32_t x, std::uint32_t phases)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(barrier);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                 : : "r"(barrier_address) : "memory");
    std::uint64_t independent = seed;
    std::uint64_t issue = 0;
    std::uint64_t independent_end = 0;
    for (std::uint32_t phase = 0; phase < phases; ++phase) {
        std::uint64_t state;
        asm volatile(
            "mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], 256;"
            : "=l"(state) : "r"(barrier_address) : "memory");
        asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
        if (phase == 0) issue = global_timer();
        issue_tiled_load<Dimensions>(tile_address, descriptor,
                                      barrier_address, x);
        independent = independent_work(independent);
        independent_end = global_timer();
        while (!cuda::ptx::mbarrier_test_wait(barrier, state)) {
        }
        asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    }
    const auto wait_end = global_timer();
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

template <int Dimensions>
__device__ __forceinline__ void tiled_store_body(
    const CUtensorMap& tensor_map, const std::uint32_t* input,
    std::uint64_t* stamps, std::uint64_t seed, std::uint32_t* tile,
    bool reuse_source)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    for (std::uint32_t index = 0; index < 64; ++index) tile[index] = input[index];
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    const auto tile_address = shared_address(tile);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    const auto issue = global_timer();
    issue_tiled_store<Dimensions>(descriptor, tile_address);
    const auto independent = independent_work(seed);
    const auto independent_end = global_timer();
    asm volatile("cp.async.bulk.commit_group;" : : : "memory");
    asm volatile("cp.async.bulk.wait_group.read 0;" : : : "memory");
    if (reuse_source) {
        for (std::uint32_t index = 0; index < 64; ++index) {
            tile[index] ^= 0xa5a5a5a5U;
        }
    }
    asm volatile("cp.async.bulk.wait_group 0;" : : : "memory");
    const auto wait_end = global_timer();
    __threadfence_system();
    stamps[0] = issue;
    stamps[1] = independent_end;
    stamps[2] = wait_end;
    stamps[3] = independent;
}

}  // namespace

#define HBFSIM_DEFINE_TILED_LOAD(DIMENSIONS)                                 \
    extern "C" __global__ void sm120_tma_load_##DIMENSIONS##d(              \
        const __grid_constant__ CUtensorMap tensor_map,                      \
        std::uint32_t* output, std::uint64_t* stamps, std::uint64_t seed)    \
    {                                                                         \
        __shared__ alignas(128) std::uint32_t tile[64];                       \
        __shared__ alignas(8) std::uint64_t barrier;                          \
        tiled_load_body<DIMENSIONS>(tensor_map, output, stamps, seed, tile,   \
                                     &barrier, 0, 1);                         \
    }

HBFSIM_DEFINE_TILED_LOAD(1)
HBFSIM_DEFINE_TILED_LOAD(2)
HBFSIM_DEFINE_TILED_LOAD(3)
HBFSIM_DEFINE_TILED_LOAD(4)
HBFSIM_DEFINE_TILED_LOAD(5)

#define HBFSIM_DEFINE_TILED_STORE(DIMENSIONS)                                \
    extern "C" __global__ void sm120_tma_store_##DIMENSIONS##d(             \
        const __grid_constant__ CUtensorMap tensor_map,                      \
        const std::uint32_t* input, std::uint64_t* stamps,                   \
        std::uint64_t seed)                                                   \
    {                                                                         \
        __shared__ alignas(128) std::uint32_t tile[64];                       \
        tiled_store_body<DIMENSIONS>(tensor_map, input, stamps, seed, tile,   \
                                      false);                                 \
    }

HBFSIM_DEFINE_TILED_STORE(1)
HBFSIM_DEFINE_TILED_STORE(2)
HBFSIM_DEFINE_TILED_STORE(3)
HBFSIM_DEFINE_TILED_STORE(4)
HBFSIM_DEFINE_TILED_STORE(5)

extern "C" __global__ void sm120_tma_im2col_load_3d(
    const __grid_constant__ CUtensorMap tensor_map, std::uint32_t* output,
    std::uint64_t* stamps, std::uint64_t seed)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    __shared__ alignas(8) std::uint64_t barrier;
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(&barrier);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                 : : "r"(barrier_address) : "memory");
    std::uint64_t state;
    asm volatile(
        "mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], 256;"
        : "=l"(state) : "r"(barrier_address) : "memory");
    const std::uint16_t filter_offset = 0;
    const auto issue = global_timer();
    asm volatile(
        "cp.async.bulk.tensor.3d.shared::cta.global.im2col."
        "mbarrier::complete_tx::bytes "
        "[%0], [%1, {%2, %3, %4}], [%5], {%6};"
        : : "r"(tile_address), "l"(descriptor), "r"(0), "r"(0),
            "r"(0), "r"(barrier_address), "h"(filter_offset) : "memory");
    const auto independent = independent_work(seed);
    const auto independent_end = global_timer();
    while (!cuda::ptx::mbarrier_test_wait(&barrier, state)) {
    }
    const auto wait_end = global_timer();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    for (std::uint32_t index = 0; index < 64; ++index) output[index] = tile[index];
    __threadfence_system();
    stamps[0] = issue;
    stamps[1] = independent_end;
    stamps[2] = wait_end;
    stamps[3] = independent;
}

extern "C" __global__ void sm120_tma_im2col_store_3d(
    const __grid_constant__ CUtensorMap tensor_map,
    const std::uint32_t* input, std::uint64_t* stamps, std::uint64_t seed)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    for (std::uint32_t index = 0; index < 64; ++index) tile[index] = input[index];
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    const auto tile_address = shared_address(tile);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    const auto issue = global_timer();
    asm volatile(
        "cp.async.bulk.tensor.3d.global.shared::cta.im2col_no_offs."
        "bulk_group [%0, {%1, %2, %3}], [%4];"
        : : "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(tile_address)
        : "memory");
    const auto independent = independent_work(seed);
    const auto independent_end = global_timer();
    asm volatile("cp.async.bulk.commit_group;" : : : "memory");
    asm volatile("cp.async.bulk.wait_group.read 0;" : : : "memory");
    asm volatile("cp.async.bulk.wait_group 0;" : : : "memory");
    const auto wait_end = global_timer();
    __threadfence_system();
    stamps[0] = issue;
    stamps[1] = independent_end;
    stamps[2] = wait_end;
    stamps[3] = independent;
}

extern "C" __global__ void sm120_tma_im2col_wide_load_3d(
    const __grid_constant__ CUtensorMap tensor_map, std::uint32_t* output,
    std::uint64_t* stamps, std::uint64_t seed)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    __shared__ alignas(8) std::uint64_t barrier;
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(&barrier);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                 : : "r"(barrier_address) : "memory");
    std::uint64_t state;
    asm volatile(
        "mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], 256;"
        : "=l"(state) : "r"(barrier_address) : "memory");
    const std::uint16_t halo = 0;
    const std::uint16_t width_offset = 0;
    const auto issue = global_timer();
    asm volatile(
        "cp.async.bulk.tensor.3d.im2col::w.shared::cta.global."
        "mbarrier::complete_tx::bytes "
        "[%0], [%1, {%2, %3, %4}], [%5], {%6, %7};"
        : : "r"(tile_address), "l"(descriptor), "r"(0), "r"(0),
            "r"(0), "r"(barrier_address), "h"(halo), "h"(width_offset)
        : "memory");
    const auto independent = independent_work(seed);
    const auto independent_end = global_timer();
    while (!cuda::ptx::mbarrier_test_wait(&barrier, state)) {
    }
    const auto wait_end = global_timer();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    for (std::uint32_t index = 0; index < 64; ++index) output[index] = tile[index];
    __threadfence_system();
    stamps[0] = issue;
    stamps[1] = independent_end;
    stamps[2] = wait_end;
    stamps[3] = independent;
}

extern "C" __global__ void sm120_tma_oob_zero_1d(
    const __grid_constant__ CUtensorMap tensor_map, std::uint32_t* output,
    std::uint64_t* stamps, std::uint64_t seed)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    __shared__ alignas(8) std::uint64_t barrier;
    tiled_load_body<1>(tensor_map, output, stamps, seed, tile, &barrier, 48, 1);
}

template <std::uint32_t TransferBytes>
__device__ __forceinline__ void tiled_oob_nan_body(
    const CUtensorMap& tensor_map, std::uint8_t* output,
    std::uint64_t* stamps, std::uint64_t seed, std::uint8_t* tile,
    std::uint64_t* barrier)
{
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(barrier);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                 : : "r"(barrier_address) : "memory");
    std::uint64_t state;
    asm volatile(
        "mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], %2;"
        : "=l"(state)
        : "r"(barrier_address), "r"(TransferBytes)
        : "memory");
    const auto issue = global_timer();
    issue_tiled_load<1>(tile_address, descriptor, barrier_address, 48);
    const auto independent = independent_work(seed);
    const auto independent_end = global_timer();
    while (!cuda::ptx::mbarrier_test_wait(barrier, state)) {
    }
    const auto wait_end = global_timer();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
#pragma unroll 1
    for (std::uint32_t index = 0; index < TransferBytes; ++index) {
        output[index] = tile[index];
    }
    __threadfence_system();
    stamps[0] = issue;
    stamps[1] = independent_end;
    stamps[2] = wait_end;
    stamps[3] = independent;
}

#define HBFSIM_DEFINE_OOB_NAN(NAME, TRANSFER_BYTES)                           \
    extern "C" __global__ void sm120_tma_oob_nan_##NAME##_1d(               \
        const __grid_constant__ CUtensorMap tensor_map,                      \
        std::uint8_t* output, std::uint64_t* stamps, std::uint64_t seed)      \
    {                                                                         \
        __shared__ alignas(128) std::uint8_t tile[TRANSFER_BYTES];            \
        __shared__ alignas(8) std::uint64_t barrier;                          \
        tiled_oob_nan_body<TRANSFER_BYTES>(tensor_map, output, stamps, seed,  \
                                            tile, &barrier);                  \
    }

HBFSIM_DEFINE_OOB_NAN(f16, 128)
HBFSIM_DEFINE_OOB_NAN(f32, 256)
HBFSIM_DEFINE_OOB_NAN(f64, 512)
HBFSIM_DEFINE_OOB_NAN(bf16, 128)
HBFSIM_DEFINE_OOB_NAN(f32_ftz, 256)
HBFSIM_DEFINE_OOB_NAN(tf32, 256)
HBFSIM_DEFINE_OOB_NAN(tf32_ftz, 256)

extern "C" __global__ void sm120_tma_phase_reuse_1d(
    const __grid_constant__ CUtensorMap tensor_map, std::uint32_t* output,
    std::uint64_t* stamps, std::uint64_t seed)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    __shared__ alignas(8) std::uint64_t barrier;
    tiled_load_body<1>(tensor_map, output, stamps, seed, tile, &barrier, 0, 2);
}

extern "C" __global__ void sm120_tma_source_reuse_1d(
    const __grid_constant__ CUtensorMap tensor_map,
    const std::uint32_t* input, std::uint64_t* stamps, std::uint64_t seed)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    tiled_store_body<1>(tensor_map, input, stamps, seed, tile, true);
}

extern "C" __global__ void sm120_tma_device_replace_1d(
    CUtensorMap* tensor_map, const void* replacement,
    std::uint32_t* output, std::uint64_t* stamps, std::uint64_t seed)
{
    __shared__ alignas(128) std::uint32_t tile[64];
    __shared__ alignas(8) std::uint64_t barrier;
    if (blockIdx.x != 0 || threadIdx.x != 0) return;
    const auto descriptor = reinterpret_cast<std::uint64_t>(tensor_map);
    asm volatile(
        "fence.proxy.tensormap::generic.acquire.gpu [%0], 128;"
        : : "l"(descriptor) : "memory");
    asm volatile(
        "tensormap.replace.tile.global_address.global.b1024.b64 [%0], %1;"
        : : "l"(descriptor),
            "l"(reinterpret_cast<std::uint64_t>(replacement)) : "memory");
    asm volatile("fence.proxy.tensormap::generic.release.gpu;" : : : "memory");
    asm volatile(
        "fence.proxy.tensormap::generic.acquire.gpu [%0], 128;"
        : : "l"(descriptor) : "memory");
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(&barrier);
    asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                 : : "r"(barrier_address) : "memory");
    std::uint64_t state;
    asm volatile(
        "mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], 256;"
        : "=l"(state) : "r"(barrier_address) : "memory");
    const auto issue = global_timer();
    issue_tiled_load<1>(tile_address, descriptor, barrier_address, 0);
    const auto independent = independent_work(seed);
    const auto independent_end = global_timer();
    while (!cuda::ptx::mbarrier_test_wait(&barrier, state)) {
    }
    const auto wait_end = global_timer();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    for (std::uint32_t index = 0; index < 64; ++index) output[index] = tile[index];
    __threadfence_system();
    stamps[0] = issue;
    stamps[1] = independent_end;
    stamps[2] = wait_end;
    stamps[3] = independent;
}

extern "C" __global__ void sm120_tma_descriptor_copy_1d(
    const CUtensorMap* source_map, CUtensorMap* destination_map,
    const void* replacement, std::uint32_t* output,
    std::uint64_t* stamps, std::uint64_t seed)
{
    __shared__ alignas(128) unsigned char shared_map[128];
    __shared__ alignas(128) std::uint32_t tile[64];
    __shared__ alignas(8) std::uint64_t barrier;
    if (blockIdx.x != 0) return;
    const auto* source = reinterpret_cast<const unsigned char*>(source_map);
    for (std::uint32_t byte = threadIdx.x; byte < 128;
         byte += blockDim.x) {
        shared_map[byte] = source[byte];
    }
    __syncthreads();
    const auto shared_map_address = shared_address(shared_map);
    const auto destination = reinterpret_cast<std::uint64_t>(destination_map);
    asm volatile(
        "tensormap.cp_fenceproxy.global.shared::cta."
        "tensormap::generic.release.gpu.sync.aligned [%0], [%1], 128;"
        : : "l"(destination), "r"(shared_map_address) : "memory");
    __syncthreads();
    if (threadIdx.x != 0) return;
    asm volatile(
        "fence.proxy.tensormap::generic.acquire.gpu [%0], 128;"
        : : "l"(destination) : "memory");
    asm volatile(
        "tensormap.replace.tile.global_address.global.b1024.b64 [%0], %1;"
        : : "l"(destination),
            "l"(reinterpret_cast<std::uint64_t>(replacement)) : "memory");
    asm volatile("fence.proxy.tensormap::generic.release.gpu;" : : : "memory");
    asm volatile(
        "fence.proxy.tensormap::generic.acquire.gpu [%0], 128;"
        : : "l"(destination) : "memory");
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(&barrier);
    asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                 : : "r"(barrier_address) : "memory");
    std::uint64_t state;
    asm volatile(
        "mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], 256;"
        : "=l"(state) : "r"(barrier_address) : "memory");
    const auto issue = global_timer();
    issue_tiled_load<1>(tile_address, destination, barrier_address, 0);
    const auto independent = independent_work(seed);
    const auto independent_end = global_timer();
    while (!cuda::ptx::mbarrier_test_wait(&barrier, state)) {
    }
    const auto wait_end = global_timer();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    for (std::uint32_t index = 0; index < 64; ++index) output[index] = tile[index];
    __threadfence_system();
    stamps[0] = issue;
    stamps[1] = independent_end;
    stamps[2] = wait_end;
    stamps[3] = independent;
}

extern "C" __global__ __cluster_dims__(2, 1, 1)
void sm120_tma_multicast_5d(
    const __grid_constant__ CUtensorMap tensor_map, std::uint32_t* output,
    std::uint64_t* stamps, std::uint64_t seed)
{
    namespace cg = cooperative_groups;
    __shared__ alignas(128) std::uint32_t tile[64];
    __shared__ alignas(8) std::uint64_t barrier;
    const auto cluster = cg::this_cluster();
    const auto rank = cluster.block_rank();
    if (threadIdx.x != 0) return;
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(&barrier);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                 : : "r"(barrier_address) : "memory");
    cluster.sync();
    std::uint64_t state;
    asm volatile(
        "mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], 256;"
        : "=l"(state) : "r"(barrier_address) : "memory");
    cluster.sync();
    std::uint64_t issue = 0;
    if (rank == 1) {
        issue = global_timer();
        const std::uint16_t mask = 3;
        asm volatile(
            "cp.async.bulk.tensor.5d.shared::cluster.global.tile."
            "mbarrier::complete_tx::bytes.multicast::cluster "
            "[%0], [%1, {%2, %3, %4, %5, %6}], [%7], %8;"
            : : "r"(tile_address), "l"(descriptor), "r"(0), "r"(0),
                "r"(0), "r"(0), "r"(0), "r"(barrier_address), "h"(mask)
            : "memory");
    }
    const auto independent = independent_work(seed ^ rank);
    const auto independent_end = global_timer();
    while (!cuda::ptx::mbarrier_test_wait(&barrier, state)) {
    }
    const auto wait_end = global_timer();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    for (std::uint32_t index = 0; index < 64; ++index) {
        output[rank * 64 + index] = tile[index];
    }
    __threadfence_system();
    if (rank == 1) {
        stamps[0] = issue;
        stamps[1] = independent_end;
        stamps[2] = wait_end;
        stamps[3] = independent;
    }
    cluster.sync();
}

#include <cuda.h>
#include <cuda/ptx>
#include <cuda_runtime_api.h>
#include <cooperative_groups.h>
#include <json.hpp>
#include <openssl/evp.h>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <iterator>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

using json = nlohmann::json;

[[noreturn]] void fail(const std::string& message)
{
    throw std::runtime_error(message);
}

void cuda_check(cudaError_t status, const char* operation)
{
    if (status != cudaSuccess)
        fail(std::string(operation) + ": " + cudaGetErrorString(status));
}

void driver_check(CUresult status, const char* operation)
{
    if (status == CUDA_SUCCESS) return;
    const char* text = nullptr;
    (void)cuGetErrorString(status, &text);
    fail(std::string(operation) + ": " +
         (text == nullptr ? "unknown CUDA error" : text));
}

std::string sha256(const void* data, std::size_t bytes)
{
    std::array<unsigned char, 32> digest{};
    unsigned int size = 0;
    auto* context = EVP_MD_CTX_new();
    if (context == nullptr || EVP_DigestInit_ex(context, EVP_sha256(), nullptr) != 1 ||
        EVP_DigestUpdate(context, data, bytes) != 1 ||
        EVP_DigestFinal_ex(context, digest.data(), &size) != 1 || size != 32) {
        if (context != nullptr) EVP_MD_CTX_free(context);
        fail("SHA-256 failed");
    }
    EVP_MD_CTX_free(context);
    std::ostringstream result;
    result << std::hex << std::setfill('0');
    for (const auto value : digest) result << std::setw(2) << unsigned(value);
    return result.str();
}

__host__ __device__ std::uint64_t step(std::uint64_t value)
{
    value ^= value << 13;
    value ^= value >> 7;
    value ^= value << 17;
    return value;
}

__device__ std::uint64_t timer()
{
    std::uint64_t value;
    asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(value));
    return value;
}

template <std::uint32_t Depth>
__global__ void ordinary_calibration_kernel(
    const std::uint64_t* input, std::uint64_t* output, std::uint64_t* stamps,
    std::uint32_t words, std::uint32_t iterations,
    std::uint32_t distance, std::uint32_t operation, std::uint64_t seed)
{
    const auto tid = threadIdx.x + blockIdx.x * blockDim.x;
    if (tid == 0) {
        std::uint32_t smid = 0, warpid = 0, rank = 0;
        asm volatile("mov.u32 %0, %%smid;" : "=r"(smid));
        asm volatile("mov.u32 %0, %%warpid;" : "=r"(warpid));
        asm volatile("mov.u32 %0, %%cluster_ctarank;" : "=r"(rank));
        stamps[0] = timer();
        stamps[2] = smid;
        stamps[3] = warpid;
        stamps[4] = rank;
    }
    std::uint64_t values[Depth];
    std::uint64_t loaded[Depth];
    const auto threads = blockDim.x * gridDim.x;
    #pragma unroll
    for (std::uint32_t slot = 0; slot < Depth; ++slot)
        values[slot] = seed ^ (std::uint64_t{tid} << 32) ^ tid ^
                       (std::uint64_t{slot} * 0xd1b54a32d192ed03ULL);
    for (std::uint32_t iteration = 0; iteration < iterations; ++iteration) {
        if (operation != 1) {
            #pragma unroll
            for (std::uint32_t slot = 0; slot < Depth; ++slot) {
                const auto index =
                    (tid * 131U + iteration + distance + slot * 17U) % words;
                asm volatile("ld.global.u64 %0, [%1];"
                             : "=l"(loaded[slot])
                             : "l"(input + index) : "memory");
            }
        }
        for (std::uint32_t separation = 0; separation < distance; ++separation)
            asm volatile("mov.u64 %0, %0;" : "+l"(values[0]));
        #pragma unroll
        for (std::uint32_t slot = 0; slot < Depth; ++slot) {
            if (operation != 1) values[slot] ^= loaded[slot];
            values[slot] = step(values[slot]);
            if (operation != 0) {
                const auto output_index = (tid + slot * threads) % words;
                asm volatile("st.global.u64 [%0], %1;"
                             : : "l"(output + output_index),
                                 "l"(values[slot]) : "memory");
            }
        }
    }
    if (operation == 0) {
        #pragma unroll
        for (std::uint32_t slot = 0; slot < Depth; ++slot)
            output[(tid + slot * threads) % words] = values[slot];
    }
    __syncthreads();
    if (tid == 0) stamps[1] = timer();
}

template <std::uint32_t Depth>
void launch_ordinary(
    const std::uint64_t* input, std::uint64_t* output, std::uint64_t* stamps,
    std::uint32_t words, std::uint32_t iterations, std::uint32_t distance,
    std::uint32_t operation, std::uint64_t seed, std::uint32_t threads)
{
    ordinary_calibration_kernel<Depth><<<1, threads>>>(
        input, output, stamps, words, iterations, distance, operation, seed);
}

void launch_ordinary_depth(
    std::uint32_t depth, const std::uint64_t* input, std::uint64_t* output,
    std::uint64_t* stamps, std::uint32_t words, std::uint32_t iterations,
    std::uint32_t distance, std::uint32_t operation, std::uint64_t seed,
    std::uint32_t threads)
{
    switch (depth) {
    case 1: launch_ordinary<1>(input, output, stamps, words, iterations,
                              distance, operation, seed, threads); break;
    case 2: launch_ordinary<2>(input, output, stamps, words, iterations,
                              distance, operation, seed, threads); break;
    case 4: launch_ordinary<4>(input, output, stamps, words, iterations,
                              distance, operation, seed, threads); break;
    case 8: launch_ordinary<8>(input, output, stamps, words, iterations,
                              distance, operation, seed, threads); break;
    case 16: launch_ordinary<16>(input, output, stamps, words, iterations,
                                distance, operation, seed, threads); break;
    default: fail("unsupported ordinary queue depth");
    }
}

__device__ std::uint32_t shared_address(const void* value)
{
    return static_cast<std::uint32_t>(__cvta_generic_to_shared(value));
}

__device__ void issue_tma_load(
    std::uint32_t dimensions, std::uint32_t tile, std::uint64_t descriptor,
    std::uint32_t barrier)
{
    switch (dimensions) {
    case 1:
        asm volatile("cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes [%0], [%1, {%2}], [%3];"
                     : : "r"(tile), "l"(descriptor), "r"(0), "r"(barrier) : "memory");
        break;
    case 2:
        asm volatile("cp.async.bulk.tensor.2d.shared::cta.global.tile.mbarrier::complete_tx::bytes [%0], [%1, {%2, %3}], [%4];"
                     : : "r"(tile), "l"(descriptor), "r"(0), "r"(0), "r"(barrier) : "memory");
        break;
    case 3:
        asm volatile("cp.async.bulk.tensor.3d.shared::cta.global.tile.mbarrier::complete_tx::bytes [%0], [%1, {%2, %3, %4}], [%5];"
                     : : "r"(tile), "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(barrier) : "memory");
        break;
    case 4:
        asm volatile("cp.async.bulk.tensor.4d.shared::cta.global.tile.mbarrier::complete_tx::bytes [%0], [%1, {%2, %3, %4, %5}], [%6];"
                     : : "r"(tile), "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(0), "r"(barrier) : "memory");
        break;
    case 5:
        asm volatile("cp.async.bulk.tensor.5d.shared::cta.global.tile.mbarrier::complete_tx::bytes [%0], [%1, {%2, %3, %4, %5, %6}], [%7];"
                     : : "r"(tile), "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(0), "r"(0), "r"(barrier) : "memory");
        break;
    }
}

__device__ void issue_tma_store(
    std::uint32_t dimensions, std::uint64_t descriptor, std::uint32_t tile)
{
    switch (dimensions) {
    case 1:
        asm volatile("cp.async.bulk.tensor.1d.global.shared::cta.tile.bulk_group [%0, {%1}], [%2];"
                     : : "l"(descriptor), "r"(0), "r"(tile) : "memory");
        break;
    case 2:
        asm volatile("cp.async.bulk.tensor.2d.global.shared::cta.tile.bulk_group [%0, {%1, %2}], [%3];"
                     : : "l"(descriptor), "r"(0), "r"(0), "r"(tile) : "memory");
        break;
    case 3:
        asm volatile("cp.async.bulk.tensor.3d.global.shared::cta.tile.bulk_group [%0, {%1, %2, %3}], [%4];"
                     : : "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(tile) : "memory");
        break;
    case 4:
        asm volatile("cp.async.bulk.tensor.4d.global.shared::cta.tile.bulk_group [%0, {%1, %2, %3, %4}], [%5];"
                     : : "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(0), "r"(tile) : "memory");
        break;
    case 5:
        asm volatile("cp.async.bulk.tensor.5d.global.shared::cta.tile.bulk_group [%0, {%1, %2, %3, %4, %5}], [%6];"
                     : : "l"(descriptor), "r"(0), "r"(0), "r"(0), "r"(0), "r"(0), "r"(tile) : "memory");
        break;
    }
}

__device__ void issue_tma_multicast_5d(
    std::uint32_t tile, std::uint64_t descriptor, std::uint32_t barrier,
    std::uint16_t mask)
{
    asm volatile("cp.async.bulk.tensor.5d.shared::cluster.global.tile.mbarrier::complete_tx::bytes.multicast::cluster [%0], [%1, {%2, %3, %4, %5, %6}], [%7], %8;"
                 : : "r"(tile), "l"(descriptor), "r"(0), "r"(0),
                     "r"(0), "r"(0), "r"(0), "r"(barrier), "h"(mask)
                 : "memory");
}

__device__ void record_route(std::uint64_t* stamps)
{
    std::uint32_t smid = 0, warpid = 0, rank = 0;
    asm volatile("mov.u32 %0, %%smid;" : "=r"(smid));
    asm volatile("mov.u32 %0, %%warpid;" : "=r"(warpid));
    asm volatile("mov.u32 %0, %%cluster_ctarank;" : "=r"(rank));
    stamps[2] = smid;
    stamps[3] = warpid;
    stamps[4] = rank;
}

extern "C" __global__ void tma_load_calibration_kernel(
    const __grid_constant__ CUtensorMap tensor_map, std::uint32_t* output,
    std::uint64_t* stamps, std::uint32_t dimensions, std::uint32_t tile_bytes,
    std::uint32_t iterations, std::uint32_t queue_depth)
{
    extern __shared__ __align__(128) unsigned char storage[];
    const auto lane = threadIdx.x & 31U;
    const auto warp = threadIdx.x >> 5U;
    const auto slot_stride = (tile_bytes + 127U) & ~127U;
    auto* tile = storage + warp * slot_stride;
    auto* barrier = reinterpret_cast<std::uint64_t*>(
        storage + blockDim.x / 32U * slot_stride + warp * 8U);
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(barrier);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    if (lane == 0)
        asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                     : : "r"(barrier_address) : "memory");
    __syncthreads();
    if (threadIdx.x == 0) {
        record_route(stamps);
        stamps[0] = timer();
    }
    __syncthreads();
    if (lane == 0) {
        for (std::uint32_t issued = 0; issued < iterations;) {
            const auto remaining = iterations - issued;
            const auto batch = queue_depth < remaining ? queue_depth : remaining;
            std::uint64_t state;
            const auto transaction_bytes = tile_bytes * batch;
            asm volatile("mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], %2;"
                         : "=l"(state)
                         : "r"(barrier_address), "r"(transaction_bytes) : "memory");
            asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
            for (std::uint32_t item = 0; item < batch; ++item)
                issue_tma_load(dimensions, tile_address, descriptor, barrier_address);
            while (!cuda::ptx::mbarrier_test_wait(barrier, state)) {}
            issued += batch;
        }
    }
    __syncthreads();
    asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    if (threadIdx.x == 0) {
        for (std::uint32_t index = 0; index < tile_bytes / 4U; ++index)
            output[index] = reinterpret_cast<std::uint32_t*>(tile)[index];
        __threadfence_system();
        stamps[1] = timer();
    }
}

extern "C" __global__ void tma_store_calibration_kernel(
    const __grid_constant__ CUtensorMap tensor_map,
    const std::uint32_t* input, std::uint64_t* stamps,
    std::uint32_t dimensions, std::uint32_t tile_bytes,
    std::uint32_t iterations, std::uint32_t queue_depth)
{
    extern __shared__ __align__(128) unsigned char storage[];
    const auto lane = threadIdx.x & 31U;
    const auto warp = threadIdx.x >> 5U;
    const auto slot_stride = (tile_bytes + 127U) & ~127U;
    auto* tile = storage + warp * slot_stride;
    if (lane == 0) {
        for (std::uint32_t index = 0; index < tile_bytes / 4U; ++index)
            reinterpret_cast<std::uint32_t*>(tile)[index] = input[index];
        asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
    }
    const auto tile_address = shared_address(tile);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    __syncthreads();
    if (threadIdx.x == 0) {
        record_route(stamps);
        stamps[0] = timer();
    }
    __syncthreads();
    if (lane == 0) {
        for (std::uint32_t issued = 0; issued < iterations;) {
            const auto remaining = iterations - issued;
            const auto batch = queue_depth < remaining ? queue_depth : remaining;
            for (std::uint32_t item = 0; item < batch; ++item)
                issue_tma_store(dimensions, descriptor, tile_address);
            asm volatile("cp.async.bulk.commit_group;" : : : "memory");
            asm volatile("cp.async.bulk.wait_group.read 0;" : : : "memory");
            asm volatile("cp.async.bulk.wait_group 0;" : : : "memory");
            issued += batch;
        }
    }
    __syncthreads();
    if (threadIdx.x == 0) {
        __threadfence_system();
        stamps[1] = timer();
    }
}

extern "C" __global__ __cluster_dims__(2, 1, 1)
void tma_multicast_calibration_kernel(
    const __grid_constant__ CUtensorMap tensor_map, std::uint32_t* output,
    std::uint64_t* stamps, std::uint32_t output_stride_words,
    std::uint32_t tile_bytes, std::uint32_t iterations,
    std::uint32_t queue_depth, std::uint32_t issuer_rank,
    std::uint16_t multicast_mask)
{
    namespace cg = cooperative_groups;
    extern __shared__ __align__(128) unsigned char storage[];
    const auto cluster = cg::this_cluster();
    const auto rank = cluster.block_rank();
    const auto lane = threadIdx.x & 31U;
    const auto warp = threadIdx.x >> 5U;
    const auto slot_stride = (tile_bytes + 127U) & ~127U;
    auto* tile = storage + warp * slot_stride;
    auto* barrier = reinterpret_cast<std::uint64_t*>(
        storage + blockDim.x / 32U * slot_stride + warp * 8U);
    const auto tile_address = shared_address(tile);
    const auto barrier_address = shared_address(barrier);
    const auto descriptor = reinterpret_cast<std::uint64_t>(&tensor_map);
    if (lane == 0)
        asm volatile("mbarrier.init.shared::cta.b64 [%0], 1;"
                     : : "r"(barrier_address) : "memory");
    cluster.sync();
    if (lane == 0 && warp == 0 && rank == issuer_rank) {
        record_route(stamps);
        stamps[0] = timer();
    }
    for (std::uint32_t issued = 0; issued < iterations;) {
        const auto remaining = iterations - issued;
        const auto batch = queue_depth < remaining ? queue_depth : remaining;
        std::uint64_t state = 0;
        if (lane == 0) {
            const auto transaction_bytes = tile_bytes * batch;
            asm volatile("mbarrier.arrive.expect_tx.shared::cta.b64 %0, [%1], %2;"
                         : "=l"(state)
                         : "r"(barrier_address), "r"(transaction_bytes)
                         : "memory");
            asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
        }
        cluster.sync();
        if (lane == 0 && rank == issuer_rank)
            for (std::uint32_t item = 0; item < batch; ++item)
                issue_tma_multicast_5d(
                    tile_address, descriptor, barrier_address, multicast_mask);
        if (lane == 0)
            while (!cuda::ptx::mbarrier_test_wait(barrier, state)) {}
        cluster.sync();
        issued += batch;
    }
    if (lane == 0 && warp == 0) {
        asm volatile("fence.proxy.async.shared::cta;" : : : "memory");
        for (std::uint32_t index = 0; index < tile_bytes / 4U; ++index)
            output[rank * output_stride_words + index] =
                reinterpret_cast<std::uint32_t*>(tile)[index];
        __threadfence_system();
    }
    cluster.sync();
    if (threadIdx.x == 0 && rank == issuer_rank) stamps[1] = timer();
}

extern "C" __global__ void warm_cache_kernel(
    const std::uint64_t* input, const std::uint64_t* output,
    std::uint64_t* sink, std::uint32_t words)
{
    std::uint64_t value = 0;
    for (std::uint32_t index = blockIdx.x * blockDim.x + threadIdx.x;
         index < words; index += blockDim.x * gridDim.x)
        value ^= input[index] ^ output[index];
    if (value != 0) atomicXor(reinterpret_cast<unsigned long long*>(sink), value);
}

extern "C" __global__ void cold_cache_kernel(
    std::uint64_t* data, std::size_t words)
{
    for (std::size_t index = blockIdx.x * blockDim.x + threadIdx.x;
         index < words; index += blockDim.x * gridDim.x)
        data[index] = step(data[index] ^ index);
}

json find_case(const json& manifest, const std::string& id)
{
    for (const auto& item : manifest.at("cases"))
        if (item.at("id").get<std::string>() == id) return item;
    fail("case id not found");
}

std::vector<std::byte> read_file(const std::string& path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input) fail("cannot open case manifest");
    const std::string text{std::istreambuf_iterator<char>(input), {}};
    return {reinterpret_cast<const std::byte*>(text.data()),
            reinterpret_cast<const std::byte*>(text.data() + text.size())};
}

}  // namespace

int main(int argc, char** argv)
{
    try {
        std::string cases_path, case_id;
        for (int index = 1; index < argc; index += 2) {
            if (index + 1 >= argc) fail("missing option value");
            const std::string key = argv[index];
            if (key == "--cases") cases_path = argv[index + 1];
            else if (key == "--case-id") case_id = argv[index + 1];
            else fail("unknown option " + key);
        }
        if (cases_path.empty() || case_id.empty()) fail("missing cases or case id");
        const auto raw = read_file(cases_path);
        const std::string manifest_text(
            reinterpret_cast<const char*>(raw.data()), raw.size());
        const auto manifest = json::parse(manifest_text);
        const auto selected = find_case(manifest, case_id);
        const auto operation = selected.at("operation_class").get<std::string>();
        const auto bytes = selected.at("bytes").get<std::uint32_t>();
        if (bytes < 256 || bytes % sizeof(std::uint64_t) != 0)
            fail("case bytes must be aligned and at least 256");
        const auto words = bytes / sizeof(std::uint64_t);
        const auto warps = selected.at("warps").get<std::uint32_t>();
        const auto queue_depth = selected.at("queue_depth").get<std::uint32_t>();
        const auto iterations = selected.at("iterations").get<std::uint32_t>();
        const auto distance = selected.at("load_use_distance").get<std::uint32_t>();
        const auto seed = selected.at("seed").get<std::uint64_t>();
        const auto cache_condition =
            selected.at("cache_condition").get<std::string>();
        if (warps == 0 || warps > 8 || iterations < queue_depth)
            fail("case launch shape or queue depth is unsupported");
        const bool tma = operation == "tma_load" || operation == "tma_store" ||
                         operation == "unicast" || operation == "multicast";
        const auto output_copies = operation == "multicast" ? 2U : 1U;
        const auto output_bytes = std::size_t{bytes} * output_copies;
        std::vector<std::uint64_t> input(words),
            expected(output_bytes / sizeof(std::uint64_t)),
            output(output_bytes / sizeof(std::uint64_t));
        for (std::uint32_t index = 0; index < words; ++index)
            input[index] = step(seed ^ (std::uint64_t{index} * 0x9e3779b97f4a7c15ULL));
        std::uint64_t *device_input = nullptr, *device_output = nullptr,
                      *device_stamps = nullptr, *device_condition_sink = nullptr,
                      *device_conditioning = nullptr;
        cuda_check(cudaMalloc(&device_input, bytes), "allocate input");
        cuda_check(cudaMalloc(&device_output, output_bytes), "allocate output");
        cuda_check(cudaMalloc(&device_stamps, 5 * sizeof(std::uint64_t)),
                   "allocate stamps");
        cuda_check(cudaMalloc(&device_condition_sink, sizeof(std::uint64_t)),
                   "allocate cache-condition sink");
        cuda_check(cudaMemcpy(device_input, input.data(), bytes,
                              cudaMemcpyHostToDevice), "copy input");
        cuda_check(cudaMemset(device_output, 0, output_bytes), "clear output");
        cuda_check(cudaMemset(device_stamps, 0, 5 * sizeof(std::uint64_t)),
                   "clear stamps");
        cuda_check(cudaMemset(device_condition_sink, 0, sizeof(std::uint64_t)),
                   "clear cache-condition sink");

        alignas(64) CUtensorMap tensor_map{};
        std::uint32_t dimension_count = 0;
        std::uint32_t tile_bytes = 0;
        std::size_t tma_shared_bytes = 0;
        std::array<cuuint64_t, 5> global_dim{1, 1, 1, 1, 1};
        std::array<cuuint64_t, 4> global_stride{0, 0, 0, 0};
        std::array<cuuint32_t, 5> box_dim{1, 1, 1, 1, 1};
        std::array<cuuint32_t, 5> element_stride{1, 1, 1, 1, 1};
        if (tma) {
            dimension_count = selected.at("dimension_count").get<std::uint32_t>();
            const auto dimensions =
                selected.at("dimensions").get<std::vector<std::uint32_t>>();
            if (dimension_count < 1 || dimension_count > 5 ||
                    dimensions.size() != dimension_count)
                fail("invalid TMA dimensions");
            std::uint64_t tile_elements = 1;
            for (std::uint32_t index = 0; index < dimension_count; ++index) {
                global_dim[index] = dimensions[index];
                box_dim[index] = dimensions[index];
                tile_elements *= dimensions[index];
            }
            if (tile_elements > bytes / sizeof(std::uint32_t) ||
                    tile_elements * sizeof(std::uint32_t) % 16 != 0)
                fail("TMA tile exceeds case bytes or is not 16-byte aligned");
            tile_bytes = static_cast<std::uint32_t>(
                tile_elements * sizeof(std::uint32_t));
            if (dimension_count > 1) {
                auto stride = (global_dim[0] * sizeof(std::uint32_t) + 15U) & ~15ULL;
                for (std::uint32_t index = 0; index + 1 < dimension_count;
                     ++index) {
                    global_stride[index] = stride;
                    stride *= global_dim[index + 1];
                }
            }
            std::uint64_t last_byte = (global_dim[0] - 1U) * 4U + 4U;
            for (std::uint32_t index = 1; index < dimension_count; ++index)
                last_byte += (global_dim[index] - 1U) * global_stride[index - 1U];
            if (last_byte > bytes) fail("TensorMap storage span exceeds case bytes");
            void* map_base = operation == "tma_store"
                ? static_cast<void*>(device_output)
                : static_cast<void*>(device_input);
            driver_check(cuTensorMapEncodeTiled(
                &tensor_map, CU_TENSOR_MAP_DATA_TYPE_UINT32, dimension_count,
                map_base, global_dim.data(), global_stride.data(),
                box_dim.data(), element_stride.data(),
                CU_TENSOR_MAP_INTERLEAVE_NONE, CU_TENSOR_MAP_SWIZZLE_NONE,
                CU_TENSOR_MAP_L2_PROMOTION_NONE,
                CU_TENSOR_MAP_FLOAT_OOB_FILL_NONE), "encode TensorMap");
            const auto slot_stride = (tile_bytes + 127U) & ~127U;
            tma_shared_bytes = std::size_t{warps} * slot_stride +
                               std::size_t{warps} * sizeof(std::uint64_t);

            const auto* input32 =
                reinterpret_cast<const std::uint32_t*>(input.data());
            auto* expected32 = reinterpret_cast<std::uint32_t*>(expected.data());
            for (std::uint32_t linear = 0; linear < tile_elements; ++linear) {
                auto remainder = linear;
                std::uint64_t byte_offset = 0;
                for (std::uint32_t dimension = 0;
                     dimension < dimension_count; ++dimension) {
                    const auto coordinate = remainder % global_dim[dimension];
                    remainder /= global_dim[dimension];
                    byte_offset += coordinate *
                        (dimension == 0 ? 4U : global_stride[dimension - 1U]);
                }
                if (operation == "tma_store")
                    expected32[byte_offset / 4U] = input32[linear];
                else
                    for (std::uint32_t copy = 0; copy < output_copies; ++copy)
                        expected32[copy * bytes / 4U + linear] =
                            input32[byte_offset / 4U];
            }
        }

        std::uint64_t conditioning_bytes = 0;
        std::string conditioning_method;
        if (cache_condition == "warm") {
            warm_cache_kernel<<<std::min<std::uint32_t>(256U, (words + 255U) / 256U), 256>>>(
                device_input, device_output, device_condition_sink, words);
            conditioning_bytes = std::uint64_t{bytes} * 2U;
            conditioning_method = "explicit_target_read";
        } else if (cache_condition == "cold") {
            int l2_bytes = 0;
            cuda_check(cudaDeviceGetAttribute(
                &l2_bytes, cudaDevAttrL2CacheSize, 0), "query L2 size");
            conditioning_bytes = std::max<std::uint64_t>(
                64ULL << 20,
                static_cast<std::uint64_t>(std::max(l2_bytes, 1)) * 2U);
            cuda_check(cudaMalloc(&device_conditioning, conditioning_bytes),
                       "allocate L2 eviction buffer");
            cuda_check(cudaMemset(device_conditioning, 0xa5, conditioning_bytes),
                       "initialize L2 eviction buffer");
            cold_cache_kernel<<<1024, 256>>>(
                device_conditioning,
                conditioning_bytes / sizeof(std::uint64_t));
            conditioning_method = "two_l2_or_64mib_streaming_evict";
        } else {
            fail("unsupported cache condition");
        }
        cuda_check(cudaGetLastError(), "launch cache conditioning kernel");
        cuda_check(cudaDeviceSynchronize(), "synchronize cache conditioning");

        std::string hardware_opcode_class = "ordinary";
        std::uint64_t issued_operations = 0;
        if (!tma) {
            const std::uint32_t code = operation == "ordinary_store" ? 1U :
                                       operation == "mixed_hbm_hbf" ? 2U : 0U;
            const auto threads = warps * 32U;
            launch_ordinary_depth(
                queue_depth, device_input, device_output, device_stamps,
                words, iterations, distance, code, seed, threads);
            for (std::uint32_t tid = 0; tid < threads; ++tid) {
                for (std::uint32_t slot = 0; slot < queue_depth; ++slot) {
                    auto value = seed ^ (std::uint64_t{tid} << 32) ^ tid ^
                        (std::uint64_t{slot} * 0xd1b54a32d192ed03ULL);
                    for (std::uint32_t iteration = 0; iteration < iterations;
                         ++iteration) {
                        if (code != 1)
                            value ^= input[(tid * 131U + iteration + distance +
                                            slot * 17U) % words];
                        value = step(value);
                    }
                    expected[(tid + slot * threads) % words] = value;
                }
            }
            hardware_opcode_class = code == 0 ? "ld.global.u64" :
                code == 1 ? "st.global.u64" :
                            "ld.global.u64+st.global.u64";
            issued_operations = std::uint64_t{threads} * iterations *
                queue_depth * (code == 2 ? 2U : 1U);
        } else {
            const auto threads = warps * 32U;
            if (operation == "tma_store") {
                tma_store_calibration_kernel<<<1, threads, tma_shared_bytes>>>(
                    tensor_map, reinterpret_cast<const std::uint32_t*>(device_input),
                    device_stamps, dimension_count, tile_bytes, iterations,
                    queue_depth);
                hardware_opcode_class =
                    "tma_store_" + std::to_string(dimension_count) + "d";
            } else if (operation == "multicast") {
                const auto mask =
                    selected.at("multicast_mask").get<std::uint16_t>();
                const auto issuer = selected.at("cta_rank").get<std::uint32_t>();
                const auto cluster_shape =
                    selected.at("cluster_shape").get<std::vector<std::uint32_t>>();
                if (dimension_count != 5 || mask != 3 || issuer >= 2 ||
                        cluster_shape != std::vector<std::uint32_t>{2, 1, 1})
                    fail("multicast case must use real 2-CTA 5D contract");
                tma_multicast_calibration_kernel<<<2, threads, tma_shared_bytes>>>(
                    tensor_map, reinterpret_cast<std::uint32_t*>(device_output),
                    device_stamps, bytes / sizeof(std::uint32_t), tile_bytes,
                    iterations, queue_depth, issuer, mask);
                hardware_opcode_class = "tma_load_5d_multicast";
            } else {
                tma_load_calibration_kernel<<<1, threads, tma_shared_bytes>>>(
                    tensor_map, reinterpret_cast<std::uint32_t*>(device_output),
                    device_stamps, dimension_count, tile_bytes, iterations,
                    queue_depth);
                hardware_opcode_class = "tma_load_" +
                    std::to_string(dimension_count) + "d" +
                    (operation == "unicast" ? "_unicast" : "");
            }
            issued_operations = std::uint64_t{warps} * iterations;
        }
        cuda_check(cudaGetLastError(), "launch calibration kernel");
        cuda_check(cudaDeviceSynchronize(), "synchronize calibration kernel");
        std::array<std::uint64_t, 5> stamps{};
        cuda_check(cudaMemcpy(output.data(), device_output, output_bytes,
                              cudaMemcpyDeviceToHost), "copy output");
        cuda_check(cudaMemcpy(stamps.data(), device_stamps, sizeof(stamps),
                              cudaMemcpyDeviceToHost), "copy stamps");
        if (device_conditioning != nullptr)
            cuda_check(cudaFree(device_conditioning), "free L2 eviction buffer");
        cuda_check(cudaFree(device_condition_sink), "free cache-condition sink");
        cuda_check(cudaFree(device_stamps), "free stamps");
        cuda_check(cudaFree(device_output), "free output");
        cuda_check(cudaFree(device_input), "free input");
        const auto output_hash = sha256(output.data(), output_bytes);
        const auto expected_hash = sha256(expected.data(), output_bytes);
        const auto case_metadata = selected.dump();
        const json report{
            {"schema_version", 1}, {"case_id", case_id},
            {"suite", manifest.at("suite")},
            {"operation_class", operation},
            {"hardware_opcode_class", hardware_opcode_class},
            {"case_manifest_sha256", sha256(raw.data(), raw.size())},
            {"case_metadata_sha256",
             sha256(case_metadata.data(), case_metadata.size())},
            {"output_sha256", output_hash}, {"expected_sha256", expected_hash},
            {"bit_exact", output_hash == expected_hash},
            {"smid", stamps[2]}, {"warpid", stamps[3]},
            {"cluster_ctarank", stamps[4]},
            {"timestamps", {{"issue", stamps[0]}, {"end", stamps[1]}}},
            {"requested_dimension_count", selected.at("dimension_count")},
            {"executed_dimension_count", dimension_count},
            {"multicast_mask", selected.at("multicast_mask")},
            {"executed_multicast_mask", selected.at("multicast_mask")},
            {"executed_queue_depth", queue_depth},
            {"executed_cluster_shape", selected.at("cluster_shape")},
            {"issued_operations", issued_operations},
            {"cache_condition_executed", cache_condition},
            {"cache_condition_method", conditioning_method},
            {"cache_conditioning_bytes", conditioning_bytes},
            {"validation_bytes", output_bytes},
        };
        std::printf("%s\n", report.dump().c_str());
        return output_hash == expected_hash ? 0 : 2;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "sm120_calibration: %s\n", error.what());
        return 70;
    }
}

#include <cstdint>
#include <cuda.h>
#include <cuda_runtime_api.h>

namespace {

constexpr std::uint32_t kThreads = 128;
constexpr std::uint32_t kDepth = 8;
constexpr std::uint32_t kIterations = 384;
constexpr std::uint32_t kDistance = 32;
constexpr std::uint32_t kWords = 12288 / sizeof(std::uint64_t);
constexpr std::uint64_t kSeed = 0x0123456789ABCDEFULL;
constexpr std::uint64_t kSlotMix = 0xD1B54A32D192ED03ULL;

__device__ __forceinline__ std::uint64_t step(std::uint64_t value)
{
    value ^= value << 13;
    value ^= value >> 7;
    value ^= value << 17;
    return value;
}

}  // namespace

extern "C" __global__ void hbfsim_llama_probe_kernel(
    std::uint64_t* output, const std::uint64_t* registered_weight,
    std::uint64_t delay_ns)
{
    if (blockIdx.x != 0 || threadIdx.x >= kThreads ||
        registered_weight == nullptr) {
        return;
    }
    std::uint64_t start;
    std::uint64_t now;
    asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(start));
    do {
        asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(now));
    } while (delay_ns != 0 && now - start < delay_ns);
    std::uint64_t values[kDepth];
    std::uint64_t loaded[kDepth];
#pragma unroll
    for (std::uint32_t slot = 0; slot < kDepth; ++slot) {
        values[slot] = kSeed ^ (std::uint64_t{threadIdx.x} << 32) ^
                       threadIdx.x ^ (std::uint64_t{slot} * kSlotMix);
    }
    for (std::uint32_t iteration = 0; iteration < kIterations; ++iteration) {
#pragma unroll
        for (std::uint32_t slot = 0; slot < kDepth; ++slot) {
            const auto index = (threadIdx.x * 131U + iteration + kDistance +
                                slot * 17U) % kWords;
            asm volatile("ld.global.u64 %0, [%1];"
                         : "=l"(loaded[slot])
                         : "l"(registered_weight + index) : "memory");
        }
        for (std::uint32_t separation = 0; separation < kDistance;
             ++separation) {
            asm volatile("mov.u64 %0, %0;" : "+l"(values[0]));
        }
#pragma unroll
        for (std::uint32_t slot = 0; slot < kDepth; ++slot) {
            values[slot] = step(values[slot] ^ loaded[slot]);
        }
    }
    std::uint64_t checksum = 0;
#pragma unroll
    for (std::uint32_t slot = 0; slot < kDepth; ++slot) {
        checksum ^= values[slot];
    }
    output[threadIdx.x] = checksum;
}

extern "C" int hbfsim_llama_launch_probe(
    std::uint64_t* output, const std::uint64_t* registered_weight,
    std::uint64_t delay_ns, cudaStream_t stream)
{
    cudaFunction_t function = nullptr;
    auto status = cudaGetFuncBySymbol(
        &function, reinterpret_cast<const void*>(hbfsim_llama_probe_kernel));
    if (status != cudaSuccess) return static_cast<int>(status);
    void* arguments[] = {&output, &registered_weight, &delay_ns};
    const auto launched = cuLaunchKernel(
        reinterpret_cast<CUfunction>(function), 1, 1, 1, kThreads, 1, 1, 0,
        reinterpret_cast<CUstream>(stream), arguments, nullptr);
    return launched == CUDA_SUCCESS ? static_cast<int>(cudaSuccess)
                                    : static_cast<int>(cudaErrorLaunchFailure);
}

extern "C" void* hbfsim_llama_probe_function()
{
    cudaFunction_t function = nullptr;
    if (cudaGetFuncBySymbol(
            &function,
            reinterpret_cast<const void*>(hbfsim_llama_probe_kernel)) !=
        cudaSuccess) {
        return nullptr;
    }
    return reinterpret_cast<void*>(function);
}

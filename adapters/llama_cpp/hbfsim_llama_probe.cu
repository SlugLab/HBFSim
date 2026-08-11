#include <cstdint>
#include <cuda_runtime_api.h>

extern "C" __global__ void hbfsim_llama_probe_kernel(
    std::uint64_t* output, std::uint64_t delay_ns)
{
    std::uint64_t start;
    std::uint64_t now;
    asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(start));
    do {
        asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(now));
    } while (now - start < delay_ns);
    *output = now - start;
}

extern "C" int hbfsim_llama_launch_probe(
    std::uint64_t* output, std::uint64_t delay_ns, cudaStream_t stream)
{
    hbfsim_llama_probe_kernel<<<1, 1, 0, stream>>>(output, delay_ns);
    return static_cast<int>(cudaGetLastError());
}

#pragma once

#include <cstdint>

struct alignas(8) HbfAccessObservation {
    std::uint64_t sequence;
    std::uint64_t byte_offset;
    std::uint64_t gpu_begin_ns;
    std::uint64_t gpu_end_ns;
    std::uint32_t bytes;
    std::uint32_t operation;
    std::uint64_t reserved;
};

static_assert(sizeof(HbfAccessObservation) == 48);

#if defined(__CUDACC__)
__device__ inline std::uint64_t hbf_observation_clock_ns()
{
    std::uint64_t now = 0;
    asm volatile("mov.u64 %0, %%globaltimer;" : "=l"(now) : : "memory");
    return now;
}

__device__ inline void hbf_record_access(
    HbfAccessObservation* observations, std::uint64_t capacity,
    std::uint64_t& count, std::uint64_t byte_offset,
    std::uint32_t operation, std::uint64_t begin_ns, std::uint64_t end_ns)
{
    if (observations == nullptr) {
        return;
    }
    const auto sequence = count++;
    if (sequence >= capacity) {
        return;
    }
    observations[sequence] = {
        .sequence = sequence,
        .byte_offset = byte_offset,
        .gpu_begin_ns = begin_ns,
        .gpu_end_ns = end_ns,
        .bytes = sizeof(std::uint64_t),
        .operation = operation,
        .reserved = 0,
    };
}
#endif

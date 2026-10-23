#pragma once
#include <cstdint>
#if defined(__CUDACC__)
#define HBFSIM_UNIFORM_HD __host__ __device__
#else
#define HBFSIM_UNIFORM_HD
#endif
namespace hbfsim::accounting_uniform {
inline constexpr std::uint64_t max_counter = UINT64_MAX;
enum class Outcome : unsigned { fallback = 0, committed = 1 };

// Shared verbatim by CUDA candidate and CPU tests; no CUDA/runtime calls.
HBFSIM_UNIFORM_HD inline bool checked_sum(std::uint64_t value, unsigned count,
                                          std::uint64_t& sum) {
    if (count == 0 || count > 32 || value > max_counter / count) return false;
    sum = value * static_cast<std::uint64_t>(count);
    return true;
}

template<class Atomic>
HBFSIM_UNIFORM_HD inline Outcome try_commit(const Atomic& atomic, std::uint64_t sum) {
    if (sum == 0) return Outcome::committed; // zero means no atomic operation
    auto observed = atomic.load();
    for (;;) {
        if (sum > max_counter - observed) return Outcome::fallback;
        auto expected = observed;
        if (atomic.cas(expected, observed + sum)) return Outcome::committed;
        observed = expected;
    }
}
} // namespace hbfsim::accounting_uniform
#undef HBFSIM_UNIFORM_HD

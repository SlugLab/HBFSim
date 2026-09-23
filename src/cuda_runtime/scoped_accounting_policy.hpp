#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

inline int hbfsim_scoped_accounting_policy(
    const std::vector<std::string>& actual,
    const std::vector<std::string>& expected,
    std::uint64_t capacity, std::size_t trace_bytes,
    std::uint64_t max_total_bytes) noexcept
{
    if (actual != expected) return -13;
    if (actual.empty() || trace_bytes == 0 || max_total_bytes == 0 ||
        capacity > SIZE_MAX / trace_bytes ||
        capacity > max_total_bytes / trace_bytes / actual.size()) return -14;
    return 0;
}

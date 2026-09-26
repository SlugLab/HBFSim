#pragma once

#include <cstdint>
#include <limits>
#include <span>

namespace hbfsim::scoped_aux {

struct RegisteredSpan {
    std::uint64_t base;
    std::uint64_t bytes;
};

struct AccessSpan {
    std::uint64_t base;
    std::uint64_t bytes;
    std::uint64_t backing_base;
    std::uint64_t backing_bytes;
};

inline bool checked_end(std::uint64_t base, std::uint64_t bytes,
                        std::uint64_t& end) noexcept
{
    if (base == 0 || bytes == 0 ||
        bytes > std::numeric_limits<std::uint64_t>::max() - base)
        return false;
    end = base + bytes;
    return true;
}

// Conservative proof: the *entire* backing allocation, not merely the
// requested view or its first address, must miss every registered range.
inline bool complete_disjoint(
    std::span<const RegisteredSpan> registered,
    std::span<const AccessSpan> accesses, bool complete) noexcept
{
    if (!complete || registered.empty() || accesses.empty()) return false;
    for (const auto& reg : registered) {
        std::uint64_t reg_end = 0;
        if (!checked_end(reg.base, reg.bytes, reg_end)) return false;
    }
    for (const auto& access : accesses) {
        std::uint64_t end = 0, backing_end = 0;
        if (!checked_end(access.base, access.bytes, end) ||
            !checked_end(access.backing_base, access.backing_bytes, backing_end) ||
            access.base < access.backing_base || end > backing_end)
            return false;
        for (const auto& reg : registered) {
            const auto reg_end = reg.base + reg.bytes;
            if (access.backing_base < reg_end && reg.base < backing_end)
                return false;
        }
    }
    return true;
}

} // namespace hbfsim::scoped_aux

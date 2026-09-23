#pragma once
#include <cstdint>

namespace hbfsim {

inline constexpr std::uint64_t kStrictBridgeTriState = 1ull << 0;
inline constexpr std::uint64_t kStrictBridgeRuntimeExactDriver = 1ull << 1;
inline constexpr std::uint64_t kStrictBridgeRequiredCapabilities =
    kStrictBridgeTriState | kStrictBridgeRuntimeExactDriver;

enum class StrictDirectAction { Reject, Original, Patched };

constexpr StrictDirectAction strict_direct_action(std::uint64_t capabilities,
                                                   int gate_decision) noexcept
{
    if ((capabilities & kStrictBridgeRequiredCapabilities) !=
        kStrictBridgeRequiredCapabilities)
        return StrictDirectAction::Reject;
    if (gate_decision == 1)
        return StrictDirectAction::Original;
    if (gate_decision == 2)
        return StrictDirectAction::Patched;
    return StrictDirectAction::Reject;
}

}  // namespace hbfsim

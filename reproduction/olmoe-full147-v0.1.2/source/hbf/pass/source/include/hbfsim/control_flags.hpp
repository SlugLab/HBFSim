#pragma once

#include <cstdint>

// SharedControlHeader::reserved0 is the existing capability word. These
// named bits do not change the control header's size or field offsets.
namespace hbfsim::control_flags {
inline constexpr std::uint32_t kCapacityMedia = 1U << 0;
inline constexpr std::uint32_t kUcieBackend = 1U << 1;
inline constexpr std::uint32_t kZeroInjectedWait = 1U << 2;
inline constexpr std::uint32_t kKnown =
    kCapacityMedia | kUcieBackend | kZeroInjectedWait;
} // namespace hbfsim::control_flags

#pragma once

#include <cstddef>
#include <cstdint>
#include <hbfsim/timing_future_abi.hpp>

namespace hbfsim {

inline constexpr std::uint32_t kLaunchGateAbiVersionV2 = 2;
inline constexpr std::uint32_t kLaunchGateAbiVersion = 3;
inline constexpr std::uint32_t kLaunchGateAbiVersionV4 = 4;

enum class LaunchGateRangePolicy : std::uint32_t {
    LegacyStrict = 0,
    TimingBacked = 1,
    CapacityUnbacked = 2,
};

using LaunchGatePublishRange = int (*)(void* state) noexcept;

struct LaunchGateApiV2 {
    std::uint32_t abi_version;
    std::uint32_t struct_bytes;
    int (*activate)(std::uintptr_t owner, std::uintptr_t control_alias,
                    std::uintptr_t cuda_context, int device_ordinal,
                    std::uint64_t* generation_out) noexcept;
    int (*register_range)(std::uintptr_t owner, std::uint64_t generation,
                          std::uintptr_t begin, std::uintptr_t end,
                          LaunchGatePublishRange publish,
                          void* publish_state) noexcept;
    int (*unregister_range)(std::uintptr_t owner, std::uint64_t generation,
                            std::uintptr_t begin, std::uintptr_t end,
                            LaunchGatePublishRange publish,
                            void* publish_state) noexcept;
    int (*begin_retire)(std::uintptr_t owner, std::uint64_t generation,
                        std::uintptr_t* token_out) noexcept;
    int (*invalidate_retire)(std::uintptr_t token) noexcept;
    int (*finish_retire)(std::uintptr_t token) noexcept;
    int (*quarantine_retire)(std::uintptr_t token) noexcept;
};

struct LaunchGateApiV3 {
    // Keep the complete v2 layout as a prefix so common lifecycle operations
    // can be consumed through LaunchGateApiV2 after version validation.
    std::uint32_t abi_version;
    std::uint32_t struct_bytes;
    int (*activate)(std::uintptr_t owner, std::uintptr_t control_alias,
                    std::uintptr_t cuda_context, int device_ordinal,
                    std::uint64_t* generation_out) noexcept;
    int (*register_range)(std::uintptr_t owner, std::uint64_t generation,
                          std::uintptr_t begin, std::uintptr_t end,
                          LaunchGatePublishRange publish,
                          void* publish_state) noexcept;
    int (*unregister_range)(std::uintptr_t owner, std::uint64_t generation,
                            std::uintptr_t begin, std::uintptr_t end,
                            LaunchGatePublishRange publish,
                            void* publish_state) noexcept;
    int (*begin_retire)(std::uintptr_t owner, std::uint64_t generation,
                        std::uintptr_t* token_out) noexcept;
    int (*invalidate_retire)(std::uintptr_t token) noexcept;
    int (*finish_retire)(std::uintptr_t token) noexcept;
    int (*quarantine_retire)(std::uintptr_t token) noexcept;
    int (*register_range_with_policy)(
        std::uintptr_t owner, std::uint64_t generation, std::uintptr_t begin,
        std::uintptr_t end, LaunchGateRangePolicy policy,
        LaunchGatePublishRange publish, void* publish_state) noexcept;
};

struct LaunchGateApiV4 {
    std::uint32_t abi_version;
    std::uint32_t struct_bytes;
    int (*activate)(std::uintptr_t, std::uintptr_t, std::uintptr_t, int,
                    std::uint64_t*) noexcept;
    int (*register_range)(std::uintptr_t, std::uint64_t, std::uintptr_t,
                          std::uintptr_t, LaunchGatePublishRange, void*) noexcept;
    int (*unregister_range)(std::uintptr_t, std::uint64_t, std::uintptr_t,
                            std::uintptr_t, LaunchGatePublishRange, void*) noexcept;
    int (*begin_retire)(std::uintptr_t, std::uint64_t, std::uintptr_t*) noexcept;
    int (*invalidate_retire)(std::uintptr_t) noexcept;
    int (*finish_retire)(std::uintptr_t) noexcept;
    int (*quarantine_retire)(std::uintptr_t) noexcept;
    int (*register_range_with_policy)(std::uintptr_t, std::uint64_t,
        std::uintptr_t, std::uintptr_t, LaunchGateRangePolicy,
        LaunchGatePublishRange, void*) noexcept;
    int (*activate_with_capabilities)(std::uintptr_t, std::uintptr_t,
        std::uintptr_t, int, const timing_future::Capabilities*, std::uint64_t*) noexcept;
};
static_assert(offsetof(LaunchGateApiV4, activate_with_capabilities)==sizeof(LaunchGateApiV3));
static_assert(offsetof(LaunchGateApiV4, register_range_with_policy)==
              offsetof(LaunchGateApiV3, register_range_with_policy));

using LaunchGateGetApi = const void* (*)(
    std::uint32_t requested_version) noexcept;

}  // namespace hbfsim

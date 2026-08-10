#pragma once

#include <cstddef>
#include <cstdint>

namespace hbfsim {

inline constexpr std::uint32_t kLaunchGateAbiVersion = 2;

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

using LaunchGateGetApi = const LaunchGateApiV2* (*)(
    std::uint32_t requested_version) noexcept;

}  // namespace hbfsim

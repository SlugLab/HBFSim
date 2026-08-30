#pragma once

#include <cstddef>
#include <cstdint>

namespace hbfsim {

inline constexpr std::uint32_t kLaunchGateAbiVersionV2 = 2;
inline constexpr std::uint32_t kLaunchGateAbiVersionV3 = 3;
inline constexpr std::uint32_t kLaunchGateAbiVersion = 4;

enum class LaunchGateRangePolicy : std::uint32_t {
    LegacyStrict = 0,
    TimingBacked = 1,
    CapacityUnbacked = 2,
};

using LaunchGatePublishRange = int (*)(void* state) noexcept;
using LaunchGateHostTiming = int (*)(void *state,
                                     std::uintptr_t address) noexcept;

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
  int (*register_range_with_policy)(std::uintptr_t owner,
                                    std::uint64_t generation,
                                    std::uintptr_t begin, std::uintptr_t end,
                                    LaunchGateRangePolicy policy,
                                    LaunchGatePublishRange publish,
                                    void *publish_state) noexcept;
};

struct LaunchGateApiV4 {
  // Keep the complete v3 layout as a prefix. Version 4 adds an activation
  // entry point that installs a lifecycle-owned host timing callback for
  // platforms without device-to-host native atomics.
  std::uint32_t abi_version;
  std::uint32_t struct_bytes;
  int (*activate)(std::uintptr_t owner, std::uintptr_t control_alias,
                  std::uintptr_t cuda_context, int device_ordinal,
                  std::uint64_t *generation_out) noexcept;
  int (*register_range)(std::uintptr_t owner, std::uint64_t generation,
                        std::uintptr_t begin, std::uintptr_t end,
                        LaunchGatePublishRange publish,
                        void *publish_state) noexcept;
  int (*unregister_range)(std::uintptr_t owner, std::uint64_t generation,
                          std::uintptr_t begin, std::uintptr_t end,
                          LaunchGatePublishRange publish,
                          void *publish_state) noexcept;
  int (*begin_retire)(std::uintptr_t owner, std::uint64_t generation,
                      std::uintptr_t *token_out) noexcept;
  int (*invalidate_retire)(std::uintptr_t token) noexcept;
  int (*finish_retire)(std::uintptr_t token) noexcept;
  int (*quarantine_retire)(std::uintptr_t token) noexcept;
  int (*register_range_with_policy)(std::uintptr_t owner,
                                    std::uint64_t generation,
                                    std::uintptr_t begin, std::uintptr_t end,
                                    LaunchGateRangePolicy policy,
                                    LaunchGatePublishRange publish,
                                    void *publish_state) noexcept;
  int (*activate_with_host_timing)(std::uintptr_t owner,
                                   std::uintptr_t control_alias,
                                   std::uintptr_t cuda_context,
                                   int device_ordinal,
                                   LaunchGateHostTiming host_timing,
                                   void *host_timing_state,
                                   std::uint64_t *generation_out) noexcept;
};

using LaunchGateGetApi =
    const void *(*)(std::uint32_t requested_version) noexcept;

}  // namespace hbfsim

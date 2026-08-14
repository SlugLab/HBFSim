#pragma once

#include <cstddef>
#include <cstdint>
#include <type_traits>

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

inline constexpr std::uint32_t kExactRunContractAbiVersion = 1;

enum class ExactCacheConditionAbi : std::uint32_t {
    Unspecified = 0,
    WarmL2 = 1,
    Cold = 2,
};

enum class ExactConcurrencyConditionAbi : std::uint32_t {
    Unspecified = 0,
    ExclusiveProcess = 1,
};

struct ExactRunContractAbi {
    std::uint32_t abi_version;
    std::uint32_t struct_bytes;
    ExactCacheConditionAbi cache_condition;
    ExactConcurrencyConditionAbi concurrency_condition;
    std::uint32_t cluster_x;
    std::uint32_t cluster_y;
    std::uint32_t cluster_z;
    std::uint64_t cache_condition_epoch;
};

inline constexpr std::uint32_t kExactPostRunEvidenceAbiVersion = 1;

struct ExactPostRunEvidenceAbi {
    std::uint32_t abi_version;
    std::uint32_t struct_bytes;
    std::uint64_t future_issued;
    std::uint64_t future_issue_throttle_ns;
    std::uint64_t future_dependency_wait_ns;
    std::uint64_t future_ordering_wait_ns;
    std::uint64_t future_drained;
    std::uint64_t future_leaked;
    std::uint64_t future_faults;
    std::uint64_t tma_issued;
    std::uint64_t tma_hbm_bytes;
    std::uint64_t tma_hbf_bytes;
    std::uint64_t tma_oob_bytes;
    std::uint64_t tma_fanout_targets;
    std::uint64_t tma_barrier_wait_ns;
    std::uint64_t tma_group_wait_ns;
    std::uint64_t tma_stale_generations;
    std::uint64_t tma_faults;
    std::uint64_t tma_leaked;
    std::uint32_t channel_routing_version;
    std::uint32_t channel_gnic_count;
    std::uint32_t channel_gpc_count;
    std::uint32_t maximum_gnic_outstanding;
    std::uint32_t maximum_gpc_outstanding;
    std::uint32_t migration_visible_sm_mismatch;
    std::uint32_t counter_residual_failed;
    std::uint32_t reserved0;
    std::uint64_t channel_saturated_requests;
    std::uint64_t channel_gnic_requests;
    std::uint64_t channel_gpc_requests;
    char routing_program_sha256[65];
    std::byte reserved1[7];
};

using LaunchGateFinalizeExactV1 = int (*)(
    std::uintptr_t owner, std::uint64_t generation,
    const ExactPostRunEvidenceAbi* evidence) noexcept;

struct LaunchGateApiV4 {
    // Keep the complete v3 layout as a literal prefix. Consumers must still
    // validate abi_version and struct_bytes before using any callback.
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
    int (*configure_exact)(std::uintptr_t owner, std::uint64_t generation,
                           const char* profile_json,
                           std::size_t profile_bytes) noexcept;
    int (*publish_run_contract)(std::uintptr_t owner,
                                std::uint64_t generation,
                                const ExactRunContractAbi* contract) noexcept;
};

static_assert(std::is_standard_layout_v<ExactRunContractAbi>);
static_assert(std::is_standard_layout_v<ExactPostRunEvidenceAbi>);
static_assert(std::is_standard_layout_v<LaunchGateApiV4>);
static_assert(offsetof(LaunchGateApiV3, register_range_with_policy) ==
              offsetof(LaunchGateApiV4, register_range_with_policy));
static_assert(offsetof(LaunchGateApiV4, configure_exact) ==
              sizeof(LaunchGateApiV3));

using LaunchGateGetApi = const void* (*)(
    std::uint32_t requested_version) noexcept;

}  // namespace hbfsim

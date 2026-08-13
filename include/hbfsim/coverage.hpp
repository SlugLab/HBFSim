#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <mutex>
#include <shared_mutex>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace hbfsim {

class LaunchRangeSynchronizer {
  public:
    [[nodiscard]] std::shared_lock<std::shared_mutex> launch_guard()
    {
        return std::shared_lock(mutex_);
    }
    [[nodiscard]] std::unique_lock<std::shared_mutex> registration_guard()
    {
        std::unique_lock lock(mutex_);
        if (launch_seen_.load(std::memory_order_acquire)) {
            throw std::logic_error(
                "HBF ranges must be registered before the first launch");
        }
        return lock;
    }
    [[nodiscard]] std::unique_lock<std::shared_mutex> retirement_guard()
    {
        return std::unique_lock(mutex_);
    }
    [[nodiscard]] std::unique_lock<std::shared_mutex> mutation_guard()
    {
        return std::unique_lock(mutex_);
    }
    // The caller must hold launch_guard() until the launch has been enqueued.
    void mark_launch_seen() noexcept
    {
        launch_seen_.store(true, std::memory_order_release);
    }
    // The caller must hold retirement_guard().
    void reset_launch_seen() noexcept
    {
        launch_seen_.store(false, std::memory_order_release);
    }

  private:
    std::shared_mutex mutex_;
    std::atomic_bool launch_seen_{false};
};

enum class ParameterKind { Scalar, Pointer, OpaqueAggregate };

enum class RangePolicy : std::uint32_t {
    None = 0,
    LegacyStrict = 1,
    TimingBacked = 2,
    CapacityUnbacked = 3,
};

[[nodiscard]] const char* range_policy_name(RangePolicy policy) noexcept;

struct ParameterMetadata {
    std::size_t index{0};
    std::size_t offset{0};
    std::size_t width{0};
    ParameterKind kind{ParameterKind::Scalar};
};

struct UnsupportedParameter {
    std::size_t index{0};
    std::string operation;
};

struct FutureBudgetEvidence {
    std::uint32_t thread_futures{0};
    std::uint32_t warp_futures{0};
    std::uint32_t cta_futures{0};
    std::uint32_t cluster_futures{0};
};

struct FutureInstructionEvidence {
    std::uint32_t instruction_id{0};
    std::uint32_t source_line{0};
    std::uint32_t bytes{0};
    std::string opcode;
    std::string memory_kind;
};

struct FutureManifestEvidence {
    std::uint32_t manifest_schema_version{0};
    std::string async_transform_version;
    std::string ir_sha256;
    std::vector<FutureInstructionEvidence> instructions;
    FutureBudgetEvidence maximum_live;
    std::vector<std::string> ambiguities;
};

struct FutureRuntimeEvidence {
    std::uint64_t issued{0};
    std::uint64_t issue_throttle_ns{0};
    std::uint64_t dependency_wait_ns{0};
    std::uint64_t ordering_wait_ns{0};
    std::uint64_t drained{0};
    std::uint64_t leaked{0};
    bool observed{false};
};

struct ModuleManifest {
    std::string module_id;
    std::string kernel;
    std::string ptx_target;
    bool instrumented{false};
    bool cubin_only{false};
    std::vector<ParameterMetadata> parameters;
    std::vector<UnsupportedParameter> unsupported_parameters;
    std::uint32_t manifest_schema_version{1};
    std::string original_ptx_sha256;
    std::string transformed_ptx_sha256;
    bool aot_required_for_exact{false};
    FutureManifestEvidence future_manifest;
};

struct ArgumentSlot {
    std::size_t offset{0};
    std::uintptr_t value{0};
};

struct LaunchParameter {
    std::size_t index{0};
    std::size_t offset{0};
    std::size_t width{0};
    bool opaque_aggregate{false};
    std::vector<ArgumentSlot> slots;
};

struct KernelLaunch {
    std::string module_id;
    std::string kernel;
    std::vector<LaunchParameter> parameters;
};

struct GateDecision {
    bool allowed{true};
    std::string module_id;
    std::string kernel;
    std::string ptx_target;
    bool cubin_only{false};
    std::string reason{"allowed"};
    std::string operation;
    std::size_t inspected_parameters{0};
    std::size_t parameter_index{0};
    std::size_t parameter_offset{0};
    std::uintptr_t address{0};
    RangePolicy range_policy{RangePolicy::None};
    bool modeled{false};
    bool opaque_unmodeled{false};
    std::uint32_t manifest_schema_version{1};
    std::string original_ptx_sha256;
    std::string transformed_ptx_sha256;
    bool aot_required_for_exact{false};
    std::string requested_fidelity{"emulation"};
    std::string admitted_fidelity{"emulation"};
    std::string exact_profile_id;
    std::string cubin_sha256;
    std::string sass_sha256;
    std::vector<std::string> exact_rejection_reasons;
    bool aot_verified{false};
    bool validation_passed{false};
    FutureManifestEvidence future_manifest;
    FutureRuntimeEvidence future_runtime;
};

[[nodiscard]] ModuleManifest module_manifest_from_json(const std::string& json);
[[nodiscard]] std::string
module_id_from_identity(const std::array<std::uint8_t, 32>& identity);
[[nodiscard]] GateDecision uninspectable_launch_decision(bool has_hbf_ranges,
                                                         std::string kind);
[[nodiscard]] GateDecision
uninspectable_launch_decision(bool has_hbf_ranges, bool has_capacity_ranges,
                              std::string kind);

class CoverageGate {
  public:
    void add_module(ModuleManifest manifest);
    void add_range(std::uintptr_t begin, std::uintptr_t end);
    void add_range(std::uintptr_t begin, std::uintptr_t end,
                   RangePolicy policy);
    void remove_range(std::uintptr_t begin, std::uintptr_t end) noexcept;
    void clear_ranges();
    [[nodiscard]] bool has_ranges() const;
    [[nodiscard]] bool has_capacity_ranges() const;
    [[nodiscard]] bool has_strict_ranges() const;
    [[nodiscard]] GateDecision check_launch(const KernelLaunch& launch) const;

  private:
    struct AddressRange {
        std::uintptr_t begin;
        std::uintptr_t end;
        RangePolicy policy;
    };

    [[nodiscard]] RangePolicy policy_for(std::uintptr_t address) const;

    mutable std::shared_mutex mutex_;
    std::unordered_map<std::string, ModuleManifest> modules_;
    std::vector<AddressRange> ranges_;
};

class CoverageWriter {
  public:
    explicit CoverageWriter(std::filesystem::path path = "coverage.json");
    void append(const GateDecision& decision);

  private:
    std::filesystem::path path_;
    mutable std::mutex mutex_;
};

[[nodiscard]] bool try_append_coverage(CoverageWriter& writer,
                                       const GateDecision& decision) noexcept;
[[nodiscard]] bool
coverage_decision_permits_launch(CoverageWriter& writer,
                                 const GateDecision& decision) noexcept;

}  // namespace hbfsim

#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace hbfsim {

enum class ParameterKind { Scalar, Pointer, OpaqueAggregate };

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

struct ModuleManifest {
    std::string module_id;
    std::string kernel;
    std::string ptx_target;
    bool instrumented{false};
    bool cubin_only{false};
    std::vector<ParameterMetadata> parameters;
    std::vector<UnsupportedParameter> unsupported_parameters;
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
};

[[nodiscard]] ModuleManifest module_manifest_from_json(const std::string& json);

class CoverageGate {
  public:
    void add_module(ModuleManifest manifest);
    void add_range(std::uintptr_t begin, std::uintptr_t end);
    [[nodiscard]] bool has_ranges() const;
    [[nodiscard]] GateDecision check_launch(const KernelLaunch& launch) const;

  private:
    struct AddressRange {
        std::uintptr_t begin;
        std::uintptr_t end;
    };

    [[nodiscard]] bool contains(std::uintptr_t address) const;

    mutable std::shared_mutex mutex_;
    std::unordered_map<std::string, ModuleManifest> modules_;
    std::unordered_map<std::string, std::vector<std::string>> modules_by_kernel_;
    std::vector<AddressRange> ranges_;
};

class CoverageWriter {
  public:
    explicit CoverageWriter(std::filesystem::path path = "coverage.json");
    void append(const GateDecision& decision);

  private:
    void flush() const;

    std::filesystem::path path_;
    mutable std::mutex mutex_;
    std::vector<GateDecision> decisions_;
};

[[nodiscard]] bool try_append_coverage(
    CoverageWriter& writer, const GateDecision& decision) noexcept;
[[nodiscard]] bool coverage_decision_permits_launch(
    CoverageWriter& writer, const GateDecision& decision) noexcept;

}  // namespace hbfsim

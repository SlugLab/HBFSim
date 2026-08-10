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

struct UnsupportedParameter {
    std::size_t index{0};
    std::string operation;
};

struct ModuleManifest {
    std::string name;
    bool instrumented{false};
    std::vector<std::size_t> pointer_parameters;
    std::vector<UnsupportedParameter> unsupported_parameters;
};

struct KernelLaunch {
    std::string module;
    std::string kernel;
    std::vector<std::uintptr_t> arguments;
};

struct GateDecision {
    bool allowed{true};
    std::string module;
    std::string kernel;
    std::string reason{"allowed"};
    std::string operation;
    std::size_t inspected_parameters{0};
    std::size_t parameter_index{0};
    std::uintptr_t address{0};
};

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

}  // namespace hbfsim

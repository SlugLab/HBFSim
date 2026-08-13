#pragma once

#include <hbfsim/exact_profile.hpp>

#include <array>
#include <atomic>
#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <string_view>

namespace hbfsim {

using ModuleIdentity = std::array<std::uint8_t, 32>;
using ModuleHandle = std::uintptr_t;
using ModuleLoadToken = std::uint64_t;

struct ExactArtifactToolchain {
    std::string cuda_release;
    std::string ptxas_version;
    std::string nvdisasm_version;
    std::string cuobjdump_version;
};

struct LoadedModuleEvidence {
    ModuleIdentity identity{};
    std::string module_id;
    std::string ptx_target;
    std::string original_ptx_sha256;
    std::string transformed_ptx_sha256;
    std::string cubin_sha256;
    std::string sass_sha256;
    ExactArtifactToolchain toolchain;
    std::vector<ExactKernelArtifact> kernels;
    bool aot_verified{false};
};

class ModuleLoadTransactionStore {
  public:
    [[nodiscard]] ModuleLoadToken begin(std::string_view ptx) noexcept;
    [[nodiscard]] std::optional<ModuleIdentity> take() noexcept;
    void end(ModuleLoadToken token) noexcept;

  private:
    struct Entry {
        ModuleLoadToken token;
        ModuleIdentity identity;
    };

    static thread_local std::optional<Entry> current_;
    std::atomic<ModuleLoadToken> next_token_{1};
};

class ModuleIdentityRegistry {
  public:
    [[nodiscard]] bool associate(ModuleHandle module,
                                 const ModuleIdentity& identity);
    [[nodiscard]] bool associate(ModuleHandle module,
                                 const LoadedModuleEvidence& evidence);
    [[nodiscard]] std::optional<ModuleIdentity>
    lookup(ModuleHandle module) const;
    [[nodiscard]] std::optional<LoadedModuleEvidence>
    lookup_evidence(ModuleHandle module) const;
    void erase(ModuleHandle module);
    void clear();

  private:
    mutable std::mutex mutex_;
    std::map<ModuleHandle, LoadedModuleEvidence> associations_;
};

}  // namespace hbfsim

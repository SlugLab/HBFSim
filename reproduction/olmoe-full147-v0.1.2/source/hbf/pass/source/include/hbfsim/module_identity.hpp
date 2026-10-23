#pragma once

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
    [[nodiscard]] std::optional<ModuleIdentity>
    lookup(ModuleHandle module) const;
    void erase(ModuleHandle module);
    void clear();

  private:
    mutable std::mutex mutex_;
    std::map<ModuleHandle, ModuleIdentity> associations_;
};

}  // namespace hbfsim

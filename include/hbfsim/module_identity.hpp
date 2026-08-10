#pragma once

#include <array>
#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <set>

namespace hbfsim {

using ModuleIdentity = std::array<std::uint8_t, 32>;
using ModuleHandle = std::uintptr_t;

class ModuleIdentityRegistry {
  public:
    void expect(const ModuleIdentity& identity);
    [[nodiscard]] bool associate(ModuleHandle module,
                                 const ModuleIdentity& identity);
    [[nodiscard]] std::optional<ModuleIdentity>
    lookup(ModuleHandle module) const;
    void erase(ModuleHandle module);
    void discard_expectations();

  private:
    mutable std::mutex mutex_;
    std::set<ModuleIdentity> expected_;
    std::map<ModuleHandle, ModuleIdentity> associations_;
};

}  // namespace hbfsim

#include "hbfsim/module_identity.hpp"

namespace hbfsim {

void ModuleIdentityRegistry::expect(const ModuleIdentity& identity)
{
    std::scoped_lock lock(mutex_);
    for (const auto& association : associations_) {
        if (association.second == identity) {
            return;
        }
    }
    expected_.insert(identity);
}

bool ModuleIdentityRegistry::associate(ModuleHandle module,
                                       const ModuleIdentity& identity)
{
    std::scoped_lock lock(mutex_);
    if (associations_.contains(module)) {
        return false;
    }
    const auto expected = expected_.find(identity);
    if (expected == expected_.end()) {
        return false;
    }
    expected_.erase(expected);
    associations_.emplace(module, identity);
    return true;
}

std::optional<ModuleIdentity>
ModuleIdentityRegistry::lookup(ModuleHandle module) const
{
    std::scoped_lock lock(mutex_);
    const auto found = associations_.find(module);
    return found == associations_.end()
               ? std::nullopt
               : std::optional<ModuleIdentity>{found->second};
}

void ModuleIdentityRegistry::erase(ModuleHandle module)
{
    std::scoped_lock lock(mutex_);
    associations_.erase(module);
}

void ModuleIdentityRegistry::discard_expectations()
{
    std::scoped_lock lock(mutex_);
    expected_.clear();
}

}  // namespace hbfsim

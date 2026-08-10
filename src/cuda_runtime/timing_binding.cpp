#include <hbfsim/timing_binding.hpp>

#include <vector>

namespace hbfsim {

bool TimingBindingRegistry::add_module(ModuleHandle module,
                                       std::uintptr_t cuda_context,
                                       int device_ordinal,
                                       ModuleControlInitializer initialize,
                                       void* state) noexcept
{
    if (module == 0 || cuda_context == 0 || device_ordinal < 0 ||
        initialize == nullptr) {
        return false;
    }
    std::scoped_lock lock(mutex_);
    if (quarantined_ || retiring_ || modules_.contains(module)) {
        return false;
    }
    const bool matches_active = owner_ != 0 && cuda_context_ == cuda_context &&
                                device_ordinal_ == device_ordinal;
    const auto alias = matches_active ? control_alias_ : 0;
    const auto generation = matches_active ? generation_ : 0;
    const bool initialized = initialize(module, alias, generation, state);
    modules_.emplace(module, ModuleState{.cuda_context = cuda_context,
                                         .device_ordinal = device_ordinal,
                                         .generation = initialized
                                                           ? generation
                                                           : 0});
    return initialized;
}

void TimingBindingRegistry::erase(ModuleHandle module) noexcept
{
    std::scoped_lock lock(mutex_);
    modules_.erase(module);
}

void TimingBindingRegistry::erase_context(std::uintptr_t cuda_context,
                                          ModuleEraseCallback erased,
                                          void* state) noexcept
{
    if (cuda_context == 0) {
        return;
    }
    std::scoped_lock lock(mutex_);
    if (owner_ != 0 && cuda_context_ == cuda_context) {
        quarantined_ = true;
        retiring_ = true;
    }
    for (auto item = modules_.begin(); item != modules_.end();) {
        if (item->second.cuda_context != cuda_context) {
            ++item;
            continue;
        }
        const auto module = item->first;
        item = modules_.erase(item);
        if (erased != nullptr) {
            erased(module, state);
        }
    }
}

void TimingBindingRegistry::erase_unbound_device(
    int device_ordinal, ModuleEraseCallback erased, void* state) noexcept
{
    if (device_ordinal < 0) {
        return;
    }
    std::scoped_lock lock(mutex_);
    for (auto item = modules_.begin(); item != modules_.end();) {
        if (item->second.device_ordinal != device_ordinal ||
            item->second.generation != 0) {
            ++item;
            continue;
        }
        const auto module = item->first;
        item = modules_.erase(item);
        if (erased != nullptr) {
            erased(module, state);
        }
    }
}

void TimingBindingRegistry::clear() noexcept
{
    std::scoped_lock lock(mutex_);
    modules_.clear();
}

bool TimingBindingRegistry::activate(
    std::uintptr_t owner, std::uintptr_t control_alias,
    std::uintptr_t cuda_context, int device_ordinal,
    ModuleControlInitializer initialize, void* state,
    std::uint64_t& generation_out) noexcept
{
    generation_out = 0;
    if (owner == 0 || control_alias == 0 || cuda_context == 0 ||
        device_ordinal < 0 || initialize == nullptr) {
        return false;
    }
    std::scoped_lock lock(mutex_);
    if (quarantined_ || retiring_ || owner_ != 0) {
        return false;
    }
    if (next_generation_ == 0) {
        return false;
    }
    const auto candidate = next_generation_;
    next_generation_ = candidate == UINT64_MAX ? 0 : candidate + 1;
    for (auto& [module, module_state] : modules_) {
        if (module_state.cuda_context != cuda_context ||
            module_state.device_ordinal != device_ordinal) {
            continue;
        }
        if (!initialize(module, control_alias, candidate, state)) {
            module_state.generation = 0;
            continue;
        }
        module_state.generation = candidate;
    }
    owner_ = owner;
    control_alias_ = control_alias;
    cuda_context_ = cuda_context;
    device_ordinal_ = device_ordinal;
    generation_ = candidate;
    generation_out = candidate;
    return true;
}

bool TimingBindingRegistry::quiesce(std::uintptr_t owner,
                                    std::uint64_t generation) noexcept
{
    std::scoped_lock lock(mutex_);
    if (quarantined_ || retiring_ || owner_ != owner ||
        generation_ != generation || owner == 0 || generation == 0) {
        return false;
    }
    retiring_ = true;
    return true;
}

bool TimingBindingRegistry::invalidate(std::uintptr_t owner,
                                       std::uint64_t generation,
                                       ModuleControlInitializer initialize,
                                       void* state) noexcept
{
    if (initialize == nullptr) {
        return false;
    }
    std::scoped_lock lock(mutex_);
    if (quarantined_ || !retiring_ || owner_ != owner ||
        generation_ != generation || owner == 0 || generation == 0) {
        return false;
    }
    bool cleared = true;
    for (auto& [module, module_state] : modules_) {
        if (module_state.cuda_context != cuda_context_ ||
            module_state.device_ordinal != device_ordinal_ ||
            module_state.generation != generation_) {
            continue;
        }
        if (!initialize(module, 0, 0, state)) {
            cleared = false;
        } else {
            module_state.generation = 0;
        }
    }
    if (!cleared) {
        quarantined_ = true;
        return false;
    }
    return true;
}

bool TimingBindingRegistry::finish_retire(std::uintptr_t owner,
                                          std::uint64_t generation) noexcept
{
    std::scoped_lock lock(mutex_);
    if (quarantined_ || !retiring_ || owner_ != owner ||
        generation_ != generation || owner == 0 || generation == 0) {
        return false;
    }
    owner_ = 0;
    control_alias_ = 0;
    cuda_context_ = 0;
    device_ordinal_ = -1;
    generation_ = 0;
    retiring_ = false;
    return true;
}

bool TimingBindingRegistry::ready(ModuleHandle module,
                                  std::uintptr_t cuda_context,
                                  int device_ordinal,
                                  std::uint64_t generation) const noexcept
{
    std::scoped_lock lock(mutex_);
    if (quarantined_ || retiring_ || owner_ == 0 ||
        cuda_context_ != cuda_context || device_ordinal_ != device_ordinal ||
        generation_ != generation || generation == 0) {
        return false;
    }
    const auto found = modules_.find(module);
    return found != modules_.end() &&
           found->second.cuda_context == cuda_context &&
           found->second.device_ordinal == device_ordinal &&
           found->second.generation == generation;
}

bool TimingBindingRegistry::ready_for_active(
    ModuleHandle module, std::uintptr_t cuda_context,
    int device_ordinal) const noexcept
{
    std::scoped_lock lock(mutex_);
    if (quarantined_ || retiring_ || owner_ == 0 ||
        cuda_context_ != cuda_context || device_ordinal_ != device_ordinal ||
        generation_ == 0) {
        return false;
    }
    const auto found = modules_.find(module);
    return found != modules_.end() &&
           found->second.cuda_context == cuda_context &&
           found->second.device_ordinal == device_ordinal &&
           found->second.generation == generation_;
}

bool TimingBindingRegistry::owns(std::uintptr_t owner,
                                 std::uint64_t generation) const noexcept
{
    std::scoped_lock lock(mutex_);
    return !quarantined_ && !retiring_ && owner != 0 && generation != 0 &&
           owner_ == owner && generation_ == generation;
}

bool TimingBindingRegistry::active_domain(std::uintptr_t cuda_context,
                                          int device_ordinal) const noexcept
{
    std::scoped_lock lock(mutex_);
    return owner_ != 0 && cuda_context_ == cuda_context &&
           device_ordinal_ == device_ordinal;
}

bool TimingBindingRegistry::active_context(
    std::uintptr_t cuda_context) const noexcept
{
    std::scoped_lock lock(mutex_);
    return owner_ != 0 && cuda_context_ == cuda_context;
}

bool TimingBindingRegistry::active_device(int device_ordinal) const noexcept
{
    std::scoped_lock lock(mutex_);
    return owner_ != 0 && device_ordinal_ == device_ordinal;
}

void TimingBindingRegistry::set_next_generation_for_test(
    std::uint64_t generation) noexcept
{
    std::scoped_lock lock(mutex_);
    next_generation_ = generation;
}

}  // namespace hbfsim

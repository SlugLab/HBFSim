#pragma once

#include <hbfsim/module_identity.hpp>

#include <cstdint>
#include <map>
#include <mutex>

namespace hbfsim {

using ModuleControlInitializer = bool (*)(ModuleHandle module,
                                          std::uintptr_t control_alias,
                                          std::uint64_t generation,
                                          void* state) noexcept;
using ModuleEraseCallback = void (*)(ModuleHandle module,
                                     void* state) noexcept;

class TimingBindingRegistry {
  public:
    [[nodiscard]] bool add_module(ModuleHandle module,
                                  std::uintptr_t cuda_context,
                                  int device_ordinal,
                                  ModuleControlInitializer initialize,
                                  void* state) noexcept;
    void erase(ModuleHandle module) noexcept;
    void erase_context(std::uintptr_t cuda_context,
                       ModuleEraseCallback erased, void* state) noexcept;
    void erase_unbound_device(int device_ordinal,
                              ModuleEraseCallback erased,
                              void* state) noexcept;
    void clear() noexcept;

    [[nodiscard]] bool activate(std::uintptr_t owner,
                                std::uintptr_t control_alias,
                                std::uintptr_t cuda_context,
                                int device_ordinal,
                                ModuleControlInitializer initialize,
                                void* state,
                                std::uint64_t& generation_out) noexcept;
    [[nodiscard]] bool can_activate() const noexcept;
    [[nodiscard]] bool quiesce(std::uintptr_t owner,
                               std::uint64_t generation) noexcept;
    [[nodiscard]] bool invalidate(std::uintptr_t owner,
                                  std::uint64_t generation,
                                  ModuleControlInitializer initialize,
                                  void* state) noexcept;
    [[nodiscard]] bool finish_retire(std::uintptr_t owner,
                                     std::uint64_t generation) noexcept;
    [[nodiscard]] bool ready(ModuleHandle module,
                             std::uintptr_t cuda_context,
                             int device_ordinal,
                             std::uint64_t generation) const noexcept;
    [[nodiscard]] bool ready_for_active(ModuleHandle module,
                                        std::uintptr_t cuda_context,
                                        int device_ordinal) const noexcept;
    [[nodiscard]] bool owns(std::uintptr_t owner,
                            std::uint64_t generation) const noexcept;
    [[nodiscard]] bool active_domain(std::uintptr_t cuda_context,
                                     int device_ordinal) const noexcept;
    [[nodiscard]] bool active_context(std::uintptr_t cuda_context) const noexcept;
    [[nodiscard]] bool active_device(int device_ordinal) const noexcept;
    void set_next_generation_for_test(std::uint64_t generation) noexcept;

  private:
    struct ModuleState {
        std::uintptr_t cuda_context{0};
        int device_ordinal{-1};
        std::uint64_t generation{0};
    };

    mutable std::mutex mutex_;
    std::map<ModuleHandle, ModuleState> modules_;
    std::uintptr_t owner_{0};
    std::uintptr_t control_alias_{0};
    std::uintptr_t cuda_context_{0};
    int device_ordinal_{-1};
    std::uint64_t generation_{0};
    std::uint64_t next_generation_{1};
    bool retiring_{false};
    bool quarantined_{false};
};

}  // namespace hbfsim

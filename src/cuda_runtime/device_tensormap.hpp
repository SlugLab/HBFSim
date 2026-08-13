#pragma once

#include "../host_service/control_layout.hpp"

#include <hbfsim/tensormap.hpp>

#include <cstdint>
#include <optional>

namespace hbfsim::runtime {

class DeviceTensorMapTable {
  public:
    explicit DeviceTensorMapTable(host_service::ControlView control) noexcept;
    bool publish(const TensorMapRecord& record) noexcept;
    [[nodiscard]] std::optional<host_service::SharedTensorMapSlot> lookup(
        const std::array<std::byte, 32>& sha,
        std::uint64_t descriptor_generation) const noexcept;
    void invalidate() noexcept;

  private:
    host_service::ControlView control_;
};

bool bind_device_tensormap_domain(std::uintptr_t context, int device,
                                  host_service::ControlView control) noexcept;
void unbind_device_tensormap_domain(std::uintptr_t context, int device) noexcept;

}  // namespace hbfsim::runtime

extern "C" int hbfsim_publish_tensormap_device_v1(
    std::uintptr_t context, int device,
    const hbfsim::TensorMapRecord* record) noexcept;

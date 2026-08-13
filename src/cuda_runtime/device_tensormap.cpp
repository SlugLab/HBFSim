#include "device_tensormap.hpp"

#include <algorithm>
#include <cstring>
#include <map>
#include <mutex>

namespace hbfsim::runtime {
namespace {

struct Domain {
    std::uintptr_t context{0};
    int device{-1};
    bool operator<(const Domain& other) const noexcept
    {
        return context < other.context ||
               (context == other.context && device < other.device);
    }
};

std::mutex& domains_mutex()
{
    static std::mutex mutex;
    return mutex;
}

std::map<Domain, host_service::ControlView>& domains()
{
    static std::map<Domain, host_service::ControlView> value;
    return value;
}

}  // namespace

DeviceTensorMapTable::DeviceTensorMapTable(
    host_service::ControlView control) noexcept
    : control_(control)
{
}

bool DeviceTensorMapTable::publish(const TensorMapRecord& record) noexcept
{
    if (!control_.valid() || record.generation == 0 ||
        record.base_address == 0 || record.shape.rank == 0 ||
        record.shape.rank > 5) {
        return false;
    }
    auto* header = control_.header();
    const auto count = host_service::atomic_load(
        header->tensormap_count, std::memory_order_acquire);
    if (count >= host_service::kTensorMapCapacity) return false;
    auto generation = host_service::atomic_load(
        header->tensormap_publication_generation,
        std::memory_order_relaxed);
    if (generation == UINT64_MAX) return false;
    ++generation;
    auto& slot = control_.tensormap_slots()[count];
    slot.publication_generation = 0;
    slot.descriptor_generation = record.generation;
    std::copy(record.descriptor_sha256.begin(),
              record.descriptor_sha256.end(), slot.descriptor_sha256);
    std::copy(record.descriptor.begin(), record.descriptor.end(),
              slot.descriptor);
    slot.base_address = record.base_address;
    std::copy(record.shape.global_dim.begin(), record.shape.global_dim.end(),
              slot.global_dim);
    std::copy(record.shape.global_stride.begin(),
              record.shape.global_stride.end(), slot.global_stride);
    std::copy(record.shape.box_dim.begin(), record.shape.box_dim.end(),
              slot.box_dim);
    std::copy(record.shape.element_stride.begin(),
              record.shape.element_stride.end(), slot.element_stride);
    slot.rank = record.shape.rank;
    slot.mode = static_cast<std::uint32_t>(record.mode);
    slot.element_type = record.element_type;
    slot.interleave = record.interleave;
    slot.swizzle = record.swizzle;
    slot.l2_promotion = record.l2_promotion;
    slot.oob_fill = record.oob_fill;
    slot.fenced = record.fenced ? 1U : 0U;
    host_service::atomic_store(slot.publication_generation, generation,
                               std::memory_order_release);
    host_service::atomic_store(header->tensormap_publication_generation,
                               generation, std::memory_order_release);
    host_service::atomic_store(header->tensormap_count, count + 1,
                               std::memory_order_release);
    return true;
}

std::optional<host_service::SharedTensorMapSlot> DeviceTensorMapTable::lookup(
    const std::array<std::byte, 32>& sha,
    std::uint64_t descriptor_generation) const noexcept
{
    if (!control_.valid() || descriptor_generation == 0) return std::nullopt;
    const auto count = host_service::atomic_load(
        control_.header()->tensormap_count, std::memory_order_acquire);
    if (count > host_service::kTensorMapCapacity) return std::nullopt;
    for (std::uint32_t index = count; index != 0; --index) {
        const auto& slot = control_.tensormap_slots()[index - 1];
        if (host_service::atomic_load(slot.publication_generation,
                                      std::memory_order_acquire) == 0 ||
            slot.descriptor_generation != descriptor_generation ||
            !std::equal(sha.begin(), sha.end(), slot.descriptor_sha256)) {
            continue;
        }
        return slot;
    }
    return std::nullopt;
}

void DeviceTensorMapTable::invalidate() noexcept
{
    if (!control_.valid()) return;
    auto* header = control_.header();
    const auto count = std::min(
        host_service::atomic_load(header->tensormap_count,
                                  std::memory_order_acquire),
        host_service::kTensorMapCapacity);
    for (std::uint32_t index = 0; index < count; ++index) {
        host_service::atomic_store(
            control_.tensormap_slots()[index].publication_generation, 0,
            std::memory_order_release);
    }
    host_service::atomic_store(header->tensormap_count, 0,
                               std::memory_order_release);
}

bool bind_device_tensormap_domain(std::uintptr_t context, int device,
                                  host_service::ControlView control) noexcept
{
    if (context == 0 || device < 0 || !control.valid()) return false;
    std::lock_guard lock(domains_mutex());
    return domains().emplace(Domain{context, device}, control).second;
}

void unbind_device_tensormap_domain(std::uintptr_t context, int device) noexcept
{
    std::lock_guard lock(domains_mutex());
    const auto found = domains().find({context, device});
    if (found != domains().end()) {
        DeviceTensorMapTable(found->second).invalidate();
        domains().erase(found);
    }
}

}  // namespace hbfsim::runtime

extern "C" int hbfsim_publish_tensormap_device_v1(
    std::uintptr_t context, int device,
    const hbfsim::TensorMapRecord* record) noexcept
{
    if (record == nullptr) return -1;
    std::lock_guard lock(hbfsim::runtime::domains_mutex());
    const auto found = hbfsim::runtime::domains().find({context, device});
    if (found == hbfsim::runtime::domains().end()) return -1;
    return hbfsim::runtime::DeviceTensorMapTable(found->second).publish(*record)
               ? 0
               : -1;
}

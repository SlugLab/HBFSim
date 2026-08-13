#include "../../src/cuda_runtime/device_tensormap.hpp"
#include "../../src/cuda_runtime/device/hbf_device.cuh"

#include <hbfsim/tensormap.hpp>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <stdexcept>
#include <thread>
#include <vector>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

hbfsim::TensorMapRecord record(std::uint64_t generation, std::byte tag)
{
    hbfsim::TensorMapRecord result;
    result.descriptor.fill(tag);
    result.descriptor_sha256 = hbfsim::tensormap_sha256(result.descriptor);
    result.generation = generation;
    result.base_address = 0x100000 + generation * 0x1000;
    result.shape.rank = 2;
    result.shape.global_dim = {64, 32, 0, 0, 0};
    result.shape.global_stride = {0, 256, 0, 0, 0};
    result.shape.box_dim = {16, 8, 0, 0, 0};
    result.shape.element_stride = {1, 1, 0, 0, 0};
    result.element_type = 2;
    result.fenced = true;
    return result;
}

}  // namespace

int main()
{
    constexpr std::uint32_t ring_capacity = 8;
    const auto bytes =
        hbfsim::host_service::control_region_bytes(ring_capacity);
    void* storage = nullptr;
    require(::posix_memalign(&storage, 64, bytes) == 0,
            "control allocation failed");
    hbfsim::host_service::ControlView control(storage, bytes);
    require(control.initialize(ring_capacity), "control initialization failed");
    hbfsim::runtime::DeviceTensorMapTable table(control);
    require(hbfsim::runtime::bind_device_tensormap_domain(
                0xCA00, 3, control),
            "device TensorMap domain bind failed");

    auto first = record(1, std::byte{0x11});
    require(hbfsim_publish_tensormap_device_v1(0xCA00, 3, &first) == 0,
            "device TensorMap callback publish failed");
    auto found = table.lookup(first.descriptor_sha256, 1);
    require(found && found->publication_generation == 1 &&
                found->descriptor_generation == 1 &&
                found->base_address == first.base_address &&
                found->fenced == 1,
            "device TensorMap slot differs");
    require(hbfsim::device::find_tensormap_slot(
                reinterpret_cast<const hbfsim::device::SharedTensorMapSlot*>(
                    control.tensormap_slots()),
                control.header()->tensormap_count,
                first.descriptor_sha256.data(), 1) == 0,
            "device reference lookup differs");

    auto replacement = record(2, std::byte{0x22});
    require(table.publish(replacement) &&
                table.lookup(replacement.descriptor_sha256, 2) &&
                !table.lookup(replacement.descriptor_sha256, 1),
            "generation-safe device replacement differs");

    std::atomic_bool failed{false};
    std::vector<std::thread> readers;
    for (int thread = 0; thread < 8; ++thread) {
        readers.emplace_back([&] {
            for (int iteration = 0; iteration < 10000; ++iteration) {
                const auto value = table.lookup(first.descriptor_sha256, 1);
                if (!value || value->base_address != first.base_address) {
                    failed = true;
                }
            }
        });
    }
    for (std::uint64_t generation = 3;
         generation <= hbfsim::host_service::kTensorMapCapacity;
         ++generation) {
        require(table.publish(record(
                    generation,
                    static_cast<std::byte>(generation & 0xffU))),
                "bounded slot fixture publish failed");
    }
    for (auto& reader : readers) reader.join();
    require(!failed, "concurrent immutable slot reader was inconsistent");
    require(!table.publish(record(999, std::byte{0x77})),
            "TensorMap slot capacity was not enforced");

    hbfsim::runtime::unbind_device_tensormap_domain(0xCA00, 3);
    require(control.header()->tensormap_count == 0 &&
                !table.lookup(first.descriptor_sha256, 1),
            "stale slot invalidation failed");
    std::free(storage);
    return 0;
}

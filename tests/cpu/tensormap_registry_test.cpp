#include <hbfsim/tensormap.hpp>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <thread>
#include <vector>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

hbfsim::TensorMapRecord record(std::byte tag, std::uintptr_t address,
                               hbfsim::TensorMapMode mode)
{
    hbfsim::TensorMapRecord result;
    result.descriptor.fill(tag);
    result.base_address = address;
    result.mode = mode;
    result.shape.rank = mode == hbfsim::TensorMapMode::Tiled ? 2 : 5;
    result.shape.global_dim = {64, 32, 8, 4, 2};
    result.shape.global_stride = {0, 64, 2048, 16384, 65536};
    result.shape.box_dim = {16, 8, 1, 1, 1};
    result.shape.element_stride = {1, 1, 1, 1, 1};
    result.element_type = 7;
    return result;
}

}  // namespace

int main()
{
    hbfsim::TensorMapRegistry registry;
    auto tiled = record(std::byte{0x11}, 0x100000,
                        hbfsim::TensorMapMode::Tiled);
    require(registry.publish(0xabc, 0, tiled), "tiled publish failed");
    auto found = registry.lookup(0xabc, 0, tiled.descriptor);
    require(found && found->generation == 1 &&
                found->base_address == 0x100000 && !found->fenced,
            "published tiled record differs");
    require(!registry.lookup(0xdef, 0, tiled.descriptor) &&
                !registry.lookup(0xabc, 1, tiled.descriptor),
            "TensorMap crossed context/device isolation");
    require(registry.fence(0xabc, 0, tiled.descriptor) &&
                registry.lookup_fenced(0xabc, 0, tiled.descriptor),
            "descriptor fence was not recorded");

    auto after = tiled.descriptor;
    after[0] = std::byte{0x22};
    require(registry.replace_address(0xabc, 0, tiled.descriptor, after,
                                     0x200000),
            "replace-address failed");
    found = registry.lookup(0xabc, 0, after);
    require(found && found->generation == 2 &&
                found->base_address == 0x200000 && !found->fenced,
            "replace-address generation/provenance differs");
    require(!registry.lookup_fenced(0xabc, 0, after),
            "updated descriptor inherited an old fence");

    auto copied = after;
    copied[1] = std::byte{0x33};
    require(registry.copy_descriptor(0xabc, 0, after, copied),
            "descriptor copy failed");
    found = registry.lookup(0xabc, 0, copied);
    require(found && found->generation == 3 && !found->fenced,
            "descriptor copy generation differs");

    auto duplicate = record(std::byte{0x11}, 0x300000,
                            hbfsim::TensorMapMode::Im2colWide);
    require(registry.publish(0xabc, 0, duplicate),
            "duplicate-byte publish failed");
    found = registry.lookup(0xabc, 0, duplicate.descriptor);
    require(found && found->generation == 4 &&
                found->base_address == 0x300000 &&
                found->mode == hbfsim::TensorMapMode::Im2colWide,
            "latest duplicate-byte generation was not selected");

    std::atomic_bool failed{false};
    std::vector<std::thread> readers;
    for (int thread = 0; thread < 8; ++thread) {
        readers.emplace_back([&] {
            for (int iteration = 0; iteration < 1000; ++iteration) {
                const auto value =
                    registry.lookup(0xabc, 0, duplicate.descriptor);
                if (!value || value->generation != 4) failed = true;
            }
        });
    }
    for (auto& reader : readers) reader.join();
    require(!failed, "concurrent registry lookup was inconsistent");

    registry.erase_context(0xabc);
    require(!registry.lookup(0xabc, 0, duplicate.descriptor),
            "context invalidation retained a descriptor");
    require(!registry.publish(0, 0, tiled) &&
                !registry.publish(1, -1, tiled),
            "invalid domain was accepted");
    registry.clear();
    return 0;
}

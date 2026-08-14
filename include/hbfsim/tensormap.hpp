#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <shared_mutex>
#include <span>
#include <unordered_map>
#include <vector>

namespace hbfsim {

enum class TensorMapMode : std::uint32_t { Tiled, Im2col, Im2colWide };

struct TensorMapShape {
    std::uint32_t rank{0};
    std::array<std::uint64_t, 5> global_dim{};
    std::array<std::uint64_t, 5> global_stride{};
    std::array<std::uint32_t, 5> box_dim{};
    std::array<std::uint32_t, 5> element_stride{};
    std::array<std::int32_t, 5> lower_corner{};
    std::array<std::int32_t, 5> upper_corner{};
    std::uint32_t channels_per_pixel{0};
    std::uint32_t pixels_per_column{0};
    std::uint32_t wide_mode{0};
};

struct TensorMapRecord {
    std::array<std::byte, 128> descriptor{};
    std::array<std::byte, 32> descriptor_sha256{};
    std::uintptr_t base_address{0};
    std::uint64_t generation{0};
    TensorMapMode mode{TensorMapMode::Tiled};
    TensorMapShape shape;
    std::uint32_t element_type{0};
    std::uint32_t interleave{0};
    std::uint32_t swizzle{0};
    std::uint32_t swizzle_atomicity{0};
    std::uint32_t l2_promotion{0};
    std::uint32_t oob_fill{0};
    bool fenced{false};
};

[[nodiscard]] std::array<std::byte, 32> tensormap_sha256(
    std::span<const std::byte, 128> descriptor);

class TensorMapRegistry {
  public:
    bool publish(std::uintptr_t context, int device, TensorMapRecord record);
    [[nodiscard]] std::optional<TensorMapRecord> lookup(
        std::uintptr_t context, int device,
        std::span<const std::byte, 128> descriptor) const;
    [[nodiscard]] std::optional<TensorMapRecord> lookup_fenced(
        std::uintptr_t context, int device,
        std::span<const std::byte, 128> descriptor) const;
    bool replace_address(std::uintptr_t context, int device,
                         std::span<const std::byte, 128> before,
                         std::span<const std::byte, 128> after,
                         std::uintptr_t new_address);
    bool copy_descriptor(std::uintptr_t context, int device,
                         std::span<const std::byte, 128> source,
                         std::span<const std::byte, 128> destination);
    bool fence(std::uintptr_t context, int device,
               std::span<const std::byte, 128> descriptor);
    void erase_context(std::uintptr_t context);
    void erase_device(int device);
    void clear();

  private:
    struct Domain {
        std::uintptr_t context{0};
        int device{-1};
        bool operator==(const Domain&) const = default;
    };
    struct DomainHash {
        std::size_t operator()(const Domain& value) const noexcept;
    };
    struct DomainRecords {
        std::uint64_t next_generation{1};
        std::vector<TensorMapRecord> records;
    };

    mutable std::shared_mutex mutex_;
    std::unordered_map<Domain, DomainRecords, DomainHash> domains_;
};

TensorMapRegistry& global_tensormap_registry();

}  // namespace hbfsim

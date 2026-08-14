#pragma once

#include <hbfsim/tensormap.hpp>

#include <cstdint>
#include <span>
#include <vector>

namespace hbfsim {

enum class SegmentSpace : std::uint32_t { Hbm, Hbf, OobFill };
enum class TmaTransferDirection : std::uint32_t { Load, Store };
enum class TmaAccessMode : std::uint32_t {
    Tile,
    Gather4,
    Scatter4,
    Im2col,
    Im2colWide,
};

struct TileSegment {
    SegmentSpace space{SegmentSpace::Hbm};
    std::uintptr_t global_address{0};
    std::uint64_t logical_offset{0};
    std::uint64_t destination_offset{0};
    std::uint64_t bytes{0};
    std::uint32_t range_id{0};
};

struct ImmutableRange {
    std::uintptr_t base{0};
    std::uint64_t bytes{0};
    std::uint32_t range_id{0};
};

class ImmutableRangeSnapshot {
  public:
    ImmutableRangeSnapshot() = default;
    explicit ImmutableRangeSnapshot(std::vector<ImmutableRange> ranges);
    [[nodiscard]] const std::vector<ImmutableRange>& ranges() const noexcept;

  private:
    std::vector<ImmutableRange> ranges_;
};

[[nodiscard]] std::vector<TileSegment> expand_and_split(
    const TensorMapRecord& map,
    std::span<const std::int32_t> coordinates,
    const ImmutableRangeSnapshot& ranges,
    TmaTransferDirection direction,
    TmaAccessMode mode = TmaAccessMode::Tile,
    std::span<const std::int32_t> im2col_offsets = {});

}  // namespace hbfsim

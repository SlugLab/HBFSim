#include <hbfsim/tma_tile.hpp>

#include "device/hbf_device.cuh"

#include <algorithm>
#include <array>
#include <limits>
#include <stdexcept>

namespace hbfsim {
namespace {

std::uint64_t checked_add(std::uint64_t left, std::uint64_t right)
{
    if (right > std::numeric_limits<std::uint64_t>::max() - left) {
        throw std::overflow_error("TMA address/offset addition overflow");
    }
    return left + right;
}

std::uint64_t checked_mul(std::uint64_t left, std::uint64_t right)
{
    if (left != 0 && right > std::numeric_limits<std::uint64_t>::max() / left) {
        throw std::overflow_error("TMA address/offset multiplication overflow");
    }
    return left * right;
}

std::uint32_t element_bytes(std::uint32_t type)
{
    const auto bytes = device::tma_global_unit_bytes(type);
    if (bytes == 0) throw std::invalid_argument("unknown TensorMap element type");
    return bytes;
}

struct Classification {
    SegmentSpace space{SegmentSpace::Hbm};
    std::uint32_t range_id{0};
    std::uint64_t until{1};
};

Classification classify(std::uintptr_t address, std::uint64_t remaining,
                        const ImmutableRangeSnapshot& snapshot)
{
    std::uint64_t until = remaining;
    for (const auto& range : snapshot.ranges()) {
        const auto end = checked_add(range.base, range.bytes);
        if (address >= range.base && address < end) {
            return {.space = SegmentSpace::Hbf,
                    .range_id = range.range_id,
                    .until = std::min<std::uint64_t>(remaining, end - address)};
        }
        if (range.base > address) {
            until = std::min<std::uint64_t>(until, range.base - address);
            break;
        }
    }
    return {.space = SegmentSpace::Hbm, .range_id = 0, .until = until};
}

void append_segment(std::vector<TileSegment>& output, TileSegment segment)
{
    if (segment.bytes == 0) return;
    if (!output.empty()) {
        auto& previous = output.back();
        const bool global_contiguous =
            previous.space == SegmentSpace::OobFill ||
            checked_add(previous.global_address, previous.bytes) ==
                segment.global_address;
        if (previous.space == segment.space &&
            previous.range_id == segment.range_id && global_contiguous &&
            checked_add(previous.logical_offset, previous.bytes) ==
                segment.logical_offset &&
            checked_add(previous.destination_offset, previous.bytes) ==
                segment.destination_offset) {
            previous.bytes = checked_add(previous.bytes, segment.bytes);
            return;
        }
    }
    output.push_back(segment);
}

}  // namespace

ImmutableRangeSnapshot::ImmutableRangeSnapshot(
    std::vector<ImmutableRange> ranges)
    : ranges_(std::move(ranges))
{
    std::sort(ranges_.begin(), ranges_.end(),
              [](const auto& left, const auto& right) {
                  return left.base < right.base;
              });
    std::uintptr_t previous_end = 0;
    for (const auto& range : ranges_) {
        if (range.base == 0 || range.bytes == 0 || range.range_id == 0 ||
            range.bytes > std::numeric_limits<std::uintptr_t>::max() -
                              range.base) {
            throw std::invalid_argument("invalid immutable HBF range");
        }
        if (previous_end != 0 && range.base < previous_end) {
            throw std::invalid_argument("overlapping immutable HBF ranges");
        }
        previous_end = range.base + range.bytes;
    }
}

const std::vector<ImmutableRange>& ImmutableRangeSnapshot::ranges() const noexcept
{
    return ranges_;
}

std::vector<TileSegment> expand_and_split(
    const TensorMapRecord& map,
    std::span<const std::int32_t> coordinates,
    const ImmutableRangeSnapshot& ranges,
    TmaTransferDirection direction, TmaAccessMode mode,
    std::span<const std::int32_t> im2col_offsets)
{
    const auto rank = map.shape.rank;
    if (rank == 0 || rank > 5 || coordinates.size() < rank ||
        map.base_address == 0) {
        throw std::invalid_argument("invalid TensorMap tile expansion input");
    }
    if ((mode == TmaAccessMode::Gather4 ||
         mode == TmaAccessMode::Scatter4) &&
        (rank != 2 || coordinates.size() != 5)) {
        throw std::invalid_argument("gather4/scatter4 requires five 2d coordinates");
    }
    if (mode == TmaAccessMode::Gather4 &&
        direction != TmaTransferDirection::Load) {
        throw std::invalid_argument("gather4 is load-only");
    }
    if (mode == TmaAccessMode::Scatter4 &&
        direction != TmaTransferDirection::Store) {
        throw std::invalid_argument("scatter4 is store-only");
    }
    if ((mode == TmaAccessMode::Im2col ||
         mode == TmaAccessMode::Im2colWide) &&
        direction != TmaTransferDirection::Load) {
        throw std::invalid_argument("im2col is load-only");
    }
    const auto expected_offsets = mode == TmaAccessMode::Im2col
                                      ? rank - 2
                                      : mode == TmaAccessMode::Im2colWide ? 2U
                                                                          : 0U;
    if (!im2col_offsets.empty() && im2col_offsets.size() != expected_offsets) {
        throw std::invalid_argument("TMA im2col offset count differs");
    }
    device::SharedTensorMapSlot slot{};
    slot.base_address = map.base_address;
    slot.rank = rank;
    slot.mode = static_cast<std::uint32_t>(map.mode);
    slot.element_type = map.element_type;
    slot.interleave = map.interleave;
    slot.swizzle = map.swizzle;
    slot.swizzle_atomicity = map.swizzle_atomicity;
    slot.l2_promotion = map.l2_promotion;
    slot.oob_fill = map.oob_fill;
    slot.channels_per_pixel = map.shape.channels_per_pixel;
    slot.pixels_per_column = map.shape.pixels_per_column;
    slot.wide_mode = map.shape.wide_mode;
    std::copy(map.shape.global_dim.begin(), map.shape.global_dim.end(),
              slot.global_dim);
    std::copy(map.shape.global_stride.begin(), map.shape.global_stride.end(),
              slot.global_stride);
    std::copy(map.shape.box_dim.begin(), map.shape.box_dim.end(), slot.box_dim);
    std::copy(map.shape.element_stride.begin(),
              map.shape.element_stride.end(), slot.element_stride);
    std::copy(map.shape.lower_corner.begin(), map.shape.lower_corner.end(),
              slot.lower_corner);
    std::copy(map.shape.upper_corner.begin(), map.shape.upper_corner.end(),
              slot.upper_corner);
    std::array<std::int32_t, 3> offsets{};
    std::copy(im2col_offsets.begin(), im2col_offsets.end(), offsets.begin());
    const auto access_mode = static_cast<std::uint32_t>(mode);
    const auto elements = device::tma_access_element_count(
        slot, access_mode, offsets.data());
    if (elements == 0) {
        throw std::invalid_argument("TensorMap/access mode is inconsistent");
    }
    const auto bytes = element_bytes(map.element_type);
    const auto total_bytes = checked_mul(elements, bytes);
    if (total_bytes > (1ULL << 32)) {
        throw std::invalid_argument("TMA tile exceeds bounded materialization");
    }
    std::vector<TileSegment> result;
    for (std::uint64_t linear_element = 0; linear_element < elements;
         ++linear_element) {
        const auto element = device::tma_element_address(
            slot, coordinates.data(), access_mode, linear_element,
            offsets.data());
        if (!element.valid || element.bytes != bytes) {
            throw std::invalid_argument("invalid TensorMap element address");
        }
        const auto logical = checked_mul(linear_element, bytes);
        if (element.oob) {
            append_segment(result, {.space = SegmentSpace::OobFill,
                                    .global_address = 0,
                                    .logical_offset = logical,
                                    .destination_offset =
                                        element.destination_offset,
                                    .bytes = bytes,
                                    .range_id = 0});
            continue;
        }
        auto address = element.global_address;
        if (bytes > std::numeric_limits<std::uintptr_t>::max() - address) {
            throw std::overflow_error("TMA element end address overflow");
        }
        std::uint64_t emitted = 0;
        while (emitted < bytes) {
            const auto classification = classify(address + emitted,
                                                 bytes - emitted, ranges);
            append_segment(result,
                           {.space = classification.space,
                            .global_address = address + emitted,
                            .logical_offset = logical + emitted,
                            .destination_offset =
                                element.destination_offset + emitted,
                            .bytes = classification.until,
                            .range_id = classification.range_id});
            emitted += classification.until;
        }
    }
    std::uint64_t conserved = 0;
    for (const auto& segment : result) {
        conserved = checked_add(conserved, segment.bytes);
    }
    if (conserved != total_bytes) {
        throw std::logic_error("TMA tile byte conservation failed");
    }
    return result;
}

}  // namespace hbfsim

#include <hbfsim/tma_tile.hpp>

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
    static constexpr std::array<std::uint32_t, 13> bytes{
        1, 2, 4, 4, 8, 8, 2, 4, 8, 2, 4, 4, 4};
    if (type >= bytes.size()) {
        throw std::invalid_argument(
            "packed sub-byte TensorMap elements require bit segments");
    }
    return bytes[type];
}

std::uint64_t swizzled_destination(const TensorMapRecord& map,
                                   std::uint64_t linear,
                                   std::uint64_t row_bytes)
{
    if (map.swizzle == 0 || row_bytes == 0) return linear;
    const std::uint64_t configured_span = map.swizzle == 1 ? 32 :
                                          map.swizzle == 2 ? 64 : 128;
    const std::uint64_t span = std::min(configured_span, row_bytes);
    const std::uint64_t row = linear / row_bytes;
    const std::uint64_t in_row = linear % row_bytes;
    const std::uint64_t span_base = (in_row / span) * span;
    const std::uint64_t in_span = in_row % span;
    const std::uint64_t atom = map.swizzle >= 4 ? 32 : 16;
    const std::uint64_t atoms = std::max<std::uint64_t>(1, span / atom);
    const std::uint64_t atom_index = in_span / atom;
    const std::uint64_t within_atom = in_span % atom;
    const std::uint64_t swizzled_atom = atom_index ^ (row % atoms);
    std::uint64_t within = within_atom;
    if (map.swizzle == 5 && (row & 1U) != 0) within ^= 8;
    return checked_add(checked_mul(row, row_bytes),
                       checked_add(span_base,
                                   checked_add(swizzled_atom * atom, within)));
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
    TmaTransferDirection direction, TmaAccessMode mode)
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
    const auto bytes = element_bytes(map.element_type);
    std::array<std::uint64_t, 5> box{};
    std::uint64_t elements = 1;
    for (std::uint32_t index = 0; index < rank; ++index) {
        box[index] = map.shape.box_dim[index] == 0
                         ? 1
                         : map.shape.box_dim[index];
        elements = checked_mul(elements, box[index]);
    }
    if (mode == TmaAccessMode::Gather4 || mode == TmaAccessMode::Scatter4) {
        box[1] = 4;
        elements = checked_mul(box[0], 4);
    }
    const auto total_bytes = checked_mul(elements, bytes);
    if (total_bytes > (1ULL << 32)) {
        throw std::invalid_argument("TMA tile exceeds bounded materialization");
    }
    const auto row_bytes = checked_mul(box[0], bytes);

    std::vector<TileSegment> result;
    for (std::uint64_t linear_element = 0; linear_element < elements;
         ++linear_element) {
        auto remainder = linear_element;
        std::array<std::uint64_t, 5> local{};
        for (std::uint32_t dimension = 0; dimension < rank; ++dimension) {
            local[dimension] = remainder % box[dimension];
            remainder /= box[dimension];
        }
        std::array<std::int64_t, 5> global{};
        bool in_bounds = true;
        for (std::uint32_t dimension = 0; dimension < rank; ++dimension) {
            std::int64_t origin = coordinates[dimension];
            if ((mode == TmaAccessMode::Im2col ||
                 mode == TmaAccessMode::Im2colWide ||
                 map.mode != TensorMapMode::Tiled) && dimension != 0) {
                origin += map.shape.lower_corner[dimension - 1];
            }
            if ((mode == TmaAccessMode::Gather4 ||
                 mode == TmaAccessMode::Scatter4) && dimension == 1) {
                origin = coordinates[local[1] + 1];
                local[1] = 0;
            }
            const auto step = map.shape.element_stride[dimension] == 0
                                  ? 1
                                  : map.shape.element_stride[dimension];
            if (local[dimension] >
                static_cast<std::uint64_t>(
                    std::numeric_limits<std::int64_t>::max()) /
                    step) {
                throw std::overflow_error("TMA coordinate overflow");
            }
            global[dimension] =
                origin + static_cast<std::int64_t>(local[dimension] * step);
            if (global[dimension] < 0 ||
                static_cast<std::uint64_t>(global[dimension]) >=
                    map.shape.global_dim[dimension]) {
                in_bounds = false;
            }
        }
        const auto logical = checked_mul(linear_element, bytes);
        const auto destination =
            swizzled_destination(map, logical, row_bytes);
        if (!in_bounds) {
            append_segment(result, {.space = SegmentSpace::OobFill,
                                    .global_address = 0,
                                    .logical_offset = logical,
                                    .destination_offset = destination,
                                    .bytes = bytes,
                                    .range_id = 0});
            continue;
        }
        std::uint64_t global_offset =
            checked_mul(static_cast<std::uint64_t>(global[0]), bytes);
        for (std::uint32_t dimension = 1; dimension < rank; ++dimension) {
            global_offset = checked_add(
                global_offset,
                checked_mul(static_cast<std::uint64_t>(global[dimension]),
                            map.shape.global_stride[dimension]));
        }
        if (global_offset > std::numeric_limits<std::uintptr_t>::max() -
                                map.base_address) {
            throw std::overflow_error("TMA global address overflow");
        }
        auto address = map.base_address + global_offset;
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
                            .destination_offset = destination + emitted,
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

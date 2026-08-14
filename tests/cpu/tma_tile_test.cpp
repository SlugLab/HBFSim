#include <hbfsim/tma_tile.hpp>

#include <algorithm>
#include <cstdint>
#include <stdexcept>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

hbfsim::TensorMapRecord tiled(std::uint32_t rank)
{
    hbfsim::TensorMapRecord map;
    map.base_address = 0x100000;
    map.shape.rank = rank;
    map.shape.global_dim = {32, 16, 8, 4, 2};
    map.shape.global_stride = {0, 128, 2048, 16384, 65536};
    map.shape.box_dim = {4, 2, 2, 2, 2};
    map.shape.element_stride = {1, 1, 1, 1, 1};
    map.element_type = 2;
    return map;
}

std::uint64_t bytes(const std::vector<hbfsim::TileSegment>& segments)
{
    std::uint64_t total = 0;
    for (const auto& segment : segments) total += segment.bytes;
    return total;
}

}  // namespace

int main()
{
    for (std::uint32_t rank = 1; rank <= 5; ++rank) {
        const auto map = tiled(rank);
        const std::int32_t coordinates[5]{0, 0, 0, 0, 0};
        const auto segments = hbfsim::expand_and_split(
            map, std::span(coordinates, rank), {},
            hbfsim::TmaTransferDirection::Load);
        std::uint64_t expected = 4;
        for (std::uint32_t dimension = 0; dimension < rank; ++dimension) {
            expected *= map.shape.box_dim[dimension];
        }
        require(bytes(segments) == expected,
                "ranked tile byte count differs");
        require(std::all_of(segments.begin(), segments.end(), [](const auto& s) {
                    return s.space == hbfsim::SegmentSpace::Hbm;
                }),
                "plain tile was not classified as HBM");
    }

    auto map = tiled(1);
    map.shape.box_dim[0] = 8;
    hbfsim::ImmutableRangeSnapshot mixed({
        {.base = 0x100008, .bytes = 8, .range_id = 7},
        {.base = 0x100018, .bytes = 4, .range_id = 9},
    });
    const std::int32_t zero[1]{0};
    const auto split = hbfsim::expand_and_split(
        map, zero, mixed, hbfsim::TmaTransferDirection::Load);
    require(bytes(split) == 32 && split.size() == 5 &&
                split[0].space == hbfsim::SegmentSpace::Hbm &&
                split[1].space == hbfsim::SegmentSpace::Hbf &&
                split[1].range_id == 7 &&
                split[3].space == hbfsim::SegmentSpace::Hbf &&
                split[3].range_id == 9,
            "mixed HBM/two-HBF split differs");

    const std::int32_t oob_coordinate[1]{30};
    const auto oob = hbfsim::expand_and_split(
        map, oob_coordinate, {}, hbfsim::TmaTransferDirection::Load);
    require(bytes(oob) == 32 &&
                std::any_of(oob.begin(), oob.end(), [](const auto& segment) {
                    return segment.space == hbfsim::SegmentSpace::OobFill;
                }),
            "OOB fill segments are missing");

    map.swizzle = 3;
    const auto swizzled = hbfsim::expand_and_split(
        map, zero, {}, hbfsim::TmaTransferDirection::Load);
    require(bytes(swizzled) == 32,
            "swizzled tile lost bytes");

    auto traversed = tiled(2);
    traversed.shape.box_dim = {4, 5, 0, 0, 0};
    traversed.shape.element_stride = {8, 2, 0, 0, 0};
    const std::int32_t zero2[2]{};
    require(bytes(hbfsim::expand_and_split(
                traversed, zero2,
                {}, hbfsim::TmaTransferDirection::Load)) == 48,
            "CPU tiled traversal-stride byte count differs");

    auto packed = tiled(1);
    packed.base_address = 0x200000;
    packed.element_type = 14;
    packed.shape.global_dim[0] = 128;
    packed.shape.box_dim[0] = 128;
    const auto packed_segments = hbfsim::expand_and_split(
        packed, zero, {}, hbfsim::TmaTransferDirection::Load);
    require(bytes(packed_segments) == 64 &&
                packed_segments.back().destination_offset == 112,
            "CPU packed b4x16_p64 expansion differs");

    auto gather = tiled(2);
    gather.shape.box_dim = {4, 1, 0, 0, 0};
    const std::int32_t gather_coordinates[5]{0, 1, 3, 5, 7};
    require(bytes(hbfsim::expand_and_split(
                gather, gather_coordinates, {},
                hbfsim::TmaTransferDirection::Load,
                hbfsim::TmaAccessMode::Gather4)) == 64,
            "gather4 byte expansion differs");

    auto im2col = tiled(3);
    im2col.mode = hbfsim::TensorMapMode::Im2col;
    im2col.shape.lower_corner = {-1, -1, 0, 0, 0};
    im2col.shape.upper_corner = {0, 0, 0, 0, 0};
    im2col.shape.channels_per_pixel = 2;
    im2col.shape.pixels_per_column = 4;
    const std::int32_t im2col_coordinates[3]{0, 0, 0};
    const auto im2col_segments = hbfsim::expand_and_split(
        im2col, im2col_coordinates, {},
        hbfsim::TmaTransferDirection::Load,
        hbfsim::TmaAccessMode::Im2col);
    require(std::any_of(im2col_segments.begin(), im2col_segments.end(),
                        [](const auto& segment) {
                            return segment.space ==
                                   hbfsim::SegmentSpace::OobFill;
                        }),
            "im2col signed halo did not produce OOB segments");
    const std::int32_t im2col_offset[1]{1};
    const auto shifted_im2col = hbfsim::expand_and_split(
        im2col, im2col_coordinates, {},
        hbfsim::TmaTransferDirection::Load,
        hbfsim::TmaAccessMode::Im2col, im2col_offset);
    require(bytes(shifted_im2col) == 32 &&
                shifted_im2col.front().space == hbfsim::SegmentSpace::Hbm,
            "runtime im2col offset did not shift the filter base");

    bool rejected = false;
    try {
        (void)hbfsim::ImmutableRangeSnapshot({
            {.base = 0x1000, .bytes = 0x100, .range_id = 1},
            {.base = 0x1080, .bytes = 0x100, .range_id = 2},
        });
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    require(rejected, "overlapping range snapshot was accepted");
    return 0;
}

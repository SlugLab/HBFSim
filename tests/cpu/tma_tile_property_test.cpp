#include <hbfsim/tma_tile.hpp>

#include <algorithm>
#include <cstdint>
#include <random>
#include <stdexcept>
#include <vector>

int main()
{
    std::mt19937_64 random(0x48424653494dULL);
    for (std::uint32_t iteration = 0; iteration < 2000; ++iteration) {
        hbfsim::TensorMapRecord map;
        map.base_address = 0x100000;
        map.shape.rank = 1 + random() % 5;
        map.element_type = random() % 13;
        const std::uint32_t element_bytes[13]{
            1, 2, 4, 4, 8, 8, 2, 4, 4, 8, 2, 4, 4};
        std::uint64_t expected = 1;
        for (std::uint32_t dimension = 0; dimension < map.shape.rank;
             ++dimension) {
            map.shape.box_dim[dimension] = dimension == 0
                ? (16U / element_bytes[map.element_type]) *
                      (1U + random() % 2U)
                : 1U + random() % 4U;
            map.shape.global_dim[dimension] =
                map.shape.box_dim[dimension] + 8U + random() % 32U;
            map.shape.element_stride[dimension] = 1;
            expected *= map.shape.box_dim[dimension];
        }
        expected *= element_bytes[map.element_type];
        map.shape.global_stride[1] = 256;
        map.shape.global_stride[2] = 8192;
        map.shape.global_stride[3] = 262144;
        map.shape.global_stride[4] = 8388608;
        std::int32_t coordinates[5]{};
        for (std::uint32_t dimension = 0; dimension < map.shape.rank;
             ++dimension) {
            coordinates[dimension] =
                static_cast<std::int32_t>(random() % 12) - 2;
        }
        hbfsim::ImmutableRangeSnapshot ranges({
            {.base = 0x100040, .bytes = 0x80, .range_id = 1},
            {.base = 0x100200, .bytes = 0x100, .range_id = 2},
        });
        const auto segments = hbfsim::expand_and_split(
            map, std::span(coordinates, map.shape.rank), ranges,
            hbfsim::TmaTransferDirection::Load);
        std::uint64_t actual = 0;
        std::vector<bool> logical(expected, false);
        for (const auto& segment : segments) {
            if (segment.bytes == 0 ||
                segment.logical_offset + segment.bytes > expected) {
                throw std::runtime_error("property: invalid segment bounds");
            }
            actual += segment.bytes;
            for (std::uint64_t byte = 0; byte < segment.bytes; ++byte) {
                const auto index = segment.logical_offset + byte;
                if (logical[index]) {
                    throw std::runtime_error("property: logical overlap");
                }
                logical[index] = true;
            }
        }
        if (actual != expected ||
            !std::all_of(logical.begin(), logical.end(), [](bool value) {
                return value;
            })) {
            throw std::runtime_error("property: byte conservation failed");
        }
    }
    return 0;
}

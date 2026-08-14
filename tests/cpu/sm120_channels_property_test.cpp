#include <hbfsim/sm120_channels.hpp>

#include <algorithm>
#include <array>
#include <cstdint>
#include <random>

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (false)

namespace {

hbfsim::Sm120ChannelConfig config()
{
    return {
        .gnic = {.count = 4, .depth = 64,
                 .arbitration = hbfsim::QueueArbitration::Fifo,
                 .service_ns_by_class = {3, 5, 7, 11, 13, 17, 19}},
        .gpc = {.count = 2, .depth = 64,
                .arbitration = hbfsim::QueueArbitration::Fifo,
                .service_ns_by_class = {2, 4, 6, 8, 10, 12, 14}},
        .routing = {.version = 1,
                    .smsp_proxy_lut = {0, 1, 2, 3, 2, 0, 3, 1},
                    .gnic_lut = {0, 1, 2, 3, 3, 2, 1, 0},
                    .gpc_lut = {0, 1, 1, 0}},
    };
}

}  // namespace

int main()
{
    using namespace hbfsim;
    std::mt19937_64 random(0x534d313230ULL);
    for (int trial = 0; trial < 500; ++trial) {
        auto cfg = config();
        Sm120ChannelModel model(cfg, 8);
        std::array<std::uint64_t, 4> expected_gnic{};
        std::array<std::uint64_t, 2> expected_gpc{};
        for (int request = 0; request < 32; ++request) {
            RoutingInput input{
                .smid = static_cast<std::uint32_t>(random() % 8),
                .warpid = static_cast<std::uint32_t>(random() % 64),
                .cta_x = static_cast<std::uint32_t>(1 + random() % 1024),
                .cta_y = static_cast<std::uint32_t>(1 + random() % 8),
                .cta_z = static_cast<std::uint32_t>(1 + random() % 4),
                .resident_warps = static_cast<std::uint32_t>(1 + random() % 64),
                .cluster_ctarank = static_cast<std::uint32_t>(random() % 16),
                .operation = static_cast<Sm120Operation>(random() % 7),
            };
            ReservationConditions conditions{
                .arrival_ns = static_cast<std::uint64_t>(trial) * 10'000 +
                              request * 1'000,
                .base_ready_ns = 0,
                .media_ready_ns = random() % 100,
                .capacity_ready_ns = random() % 100,
                .native_ready_ns = random() % 100,
                .bytes = static_cast<std::uint32_t>(1 + random() % 4096),
            };
            const auto result = model.reserve(input, conditions);
            CHECK(result.accepted);
            CHECK(result.ready_ns >= conditions.arrival_ns);
            CHECK(result.ready_ns >= conditions.media_ready_ns);
            CHECK(result.ready_ns >= conditions.capacity_ready_ns);
            CHECK(result.ready_ns >= conditions.native_ready_ns);
            if (result.uses_gnic) expected_gnic[result.gnic] += conditions.bytes;
            if (result.uses_gpc) expected_gpc[result.gpc] += conditions.bytes;
        }
        std::array<std::uint64_t, 4> actual_gnic{};
        std::array<std::uint64_t, 2> actual_gpc{};
        for (std::uint32_t sm = 0; sm < 8; ++sm) {
            const auto counts = model.counters(sm);
            CHECK(counts.has_value());
            for (std::size_t q = 0; q < 4; ++q) actual_gnic[q] += counts->gnic_bytes[q];
            for (std::size_t q = 0; q < 2; ++q) actual_gpc[q] += counts->gpc_bytes[q];
        }
        CHECK(actual_gnic == expected_gnic);
        CHECK(actual_gpc == expected_gpc);

        auto renamed = cfg;
        for (auto& value : renamed.routing.gnic_lut) value = 3 - value;
        for (auto& value : renamed.routing.gpc_lut) value = 1 - value;
        Sm120ChannelModel original(cfg, 8);
        Sm120ChannelModel permuted(renamed, 8);
        RoutingInput input{.smid = 1, .warpid = 2, .cta_x = 128,
                           .cta_y = 1, .cta_z = 1, .resident_warps = 4,
                           .cluster_ctarank = 0,
                           .operation = Sm120Operation::MixedHbmHbf};
        ReservationConditions condition{.arrival_ns = 10, .bytes = 256};
        const auto left = original.reserve(input, condition);
        const auto right = permuted.reserve(input, condition);
        CHECK(left.ready_ns == right.ready_ns);
        CHECK(left.gnic == 3 - right.gnic);
        CHECK(left.gpc == 1 - right.gpc);
    }
    return 0;
}

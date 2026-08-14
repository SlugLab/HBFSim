#include <hbfsim/sm120_channels.hpp>

#include "../../src/cuda_runtime/device/hbf_device.cuh"
#include "../../src/host_service/control_layout.hpp"

#include <array>
#include <cstdint>
#include <limits>
#include <vector>

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (false)

namespace {

hbfsim::Sm120ChannelConfig fixture()
{
    return {
        .gnic = {.count = 4, .depth = 2,
                 .arbitration = hbfsim::QueueArbitration::Fifo,
                 .service_ns_by_class = {10, 11, 12, 13, 14, 15, 16}},
        .gpc = {.count = 2, .depth = 2,
                .arbitration = hbfsim::QueueArbitration::RoundRobin,
                .service_ns_by_class = {20, 21, 22, 23, 24, 25, 26}},
        .routing = {.version = 1,
                    .smsp_proxy_lut = {0, 1, 2, 3},
                    .gnic_lut = {0, 1, 2, 3, 1, 2, 3, 0},
                    .gpc_lut = {0, 1, 1, 0}},
    };
}

}  // namespace

int main()
{
    using namespace hbfsim;
    auto config = fixture();
    CHECK(validate_sm120_channel_config(config).empty());
    auto bad = config;
    bad.gnic.count = 3;
    CHECK(validate_sm120_channel_config(bad) == "gnic_count_not_four");
    bad = config;
    bad.gpc.count = 4;
    CHECK(validate_sm120_channel_config(bad) == "gpc_count_not_two");
    bad = config;
    bad.routing.gnic_lut[3] = 4;
    CHECK(validate_sm120_channel_config(bad) == "gnic_lut_out_of_range");

    const RoutingInput input{.smid = 7, .warpid = 3,
                             .cta_x = 128, .cta_y = 2, .cta_z = 1,
                             .resident_warps = 8, .cluster_ctarank = 1,
                             .operation = Sm120Operation::TmaMulticast};
    const auto selected = route_sm120(config, input);
    CHECK(selected.valid);
    CHECK(selected.smsp_proxy < 4);
    CHECK(selected.gnic < 4);
    CHECK(selected.gpc < 2);
    CHECK(route_sm120(config, input).gnic == selected.gnic);

    Sm120ChannelModel model(config, 8);
    ReservationConditions conditions{.arrival_ns = 100,
                                     .base_ready_ns = 105,
                                     .media_ready_ns = 107,
                                     .capacity_ready_ns = 0,
                                     .native_ready_ns = 109,
                                     .bytes = 64};
    auto first = model.reserve(input, conditions);
    CHECK(first.accepted && !first.saturated);
    CHECK(first.uses_gnic && !first.uses_gpc);
    CHECK(first.ready_ns >= 114);
    CHECK(first.ready_ns >= conditions.media_ready_ns);
    CHECK(first.ready_ns >= conditions.native_ready_ns);

    auto second = model.reserve(input, conditions);
    CHECK(second.accepted);
    auto third = model.reserve(input, conditions);
    CHECK(!third.accepted && third.saturated);
    CHECK(third.ready_ns == std::numeric_limits<std::uint64_t>::max());

    conditions.arrival_ns = second.ready_ns;
    auto drained = model.reserve(input, conditions);
    CHECK(drained.accepted);

    auto store_input = input;
    store_input.operation = Sm120Operation::OrdinaryStore;
    conditions.arrival_ns = 1'000;
    auto store0 = model.reserve(store_input, conditions);
    auto store1 = model.reserve(store_input, conditions);
    CHECK(store0.accepted && store1.accepted);
    CHECK(!store0.uses_gnic && store0.uses_gpc);
    CHECK(store0.gpc != store1.gpc);

    auto mixed_input = input;
    mixed_input.operation = Sm120Operation::MixedHbmHbf;
    conditions.arrival_ns = 2'000;
    auto mixed = model.reserve(mixed_input, conditions);
    CHECK(mixed.accepted && mixed.uses_gnic && mixed.uses_gpc);
    const auto counters = model.counters(7);
    CHECK(counters.has_value());
    std::uint64_t gnic_bytes = 0;
    std::uint64_t gpc_bytes = 0;
    for (auto value : counters->gnic_bytes) gnic_bytes += value;
    for (auto value : counters->gpc_bytes) gpc_bytes += value;
    CHECK(gnic_bytes == 4 * 64);
    CHECK(gpc_bytes == 3 * 64);
    CHECK(model.counters(2).has_value());
    CHECK(!model.counters(8).has_value());

    const auto profile = load_exact_profile(
        "tests/fixtures/exact/sm120-stage4-valid.json");
    const auto bytes = host_service::control_region_bytes(8);
    std::vector<std::byte> storage(bytes);
    host_service::ControlView control(storage.data(), storage.size());
    CHECK(control.initialize(8));
    CHECK(publish_sm120_channel_config(storage.data(), storage.size(),
                                       profile.calibration, 120));
    CHECK(control.header()->sm120_channel_state_count == 120);
    CHECK(control.header()->sm120_channel_profile_generation == 1);
    const auto* shared = control.sm120_channel_config();
    CHECK(device::sm120_channel_config_valid(*reinterpret_cast<
          const device::SharedSm120ChannelConfig*>(shared)));
    const device::Sm120DeviceRoutingInput device_input{
        .smid = input.smid, .warpid = input.warpid,
        .cta_x = input.cta_x, .cta_y = input.cta_y, .cta_z = input.cta_z,
        .resident_warps = input.resident_warps,
        .cluster_ctarank = input.cluster_ctarank,
        .operation = static_cast<std::uint32_t>(input.operation)};
    const auto device_selected = device::route_sm120_channel(
        *reinterpret_cast<const device::SharedSm120ChannelConfig*>(shared),
        device_input);
    const auto published_config = make_sm120_channel_config(profile.calibration);
    const auto host_selected = route_sm120(published_config, input);
    CHECK(device_selected.valid);
    CHECK(device_selected.smsp_proxy == host_selected.smsp_proxy);
    CHECK(device_selected.gnic == host_selected.gnic);
    CHECK(device_selected.gpc == host_selected.gpc);
    CHECK(device::sm120_ready_max(10, 20, 15, 30, 25, 29) == 30);
    return 0;
}

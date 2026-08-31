#include <hbfsim/retention_refresh.hpp>

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>

using hbfsim::reliability::RetentionError;
using hbfsim::reliability::RetentionRefreshConfig;
using hbfsim::reliability::RetentionRefreshModel;

namespace {

constexpr std::uint64_t kHourNs = 3'600'000'000'000ULL;

bool close(double left, double right, double relative = 1.0e-12)
{
    return std::abs(left - right) <=
           relative * std::max({1.0, std::abs(left), std::abs(right)});
}

RetentionRefreshConfig fixture()
{
    return RetentionRefreshConfig{
        .reference_temperature_c = 85.0,
        .reference_interval_ns = 24 * kHourNs,
        .activation_energy_ev = 1.1,
        .protected_bytes = 8192,
        .page_bytes = 4096,
        .independent_channels = 2,
        .read_latency_ns = 4,
        .program_latency_ns = 6,
        .read_command_j = 1.0,
        .read_joules_per_byte = 0.0,
        .program_command_j = 2.0,
        .program_joules_per_byte = 0.0,
    };
}

}  // namespace

int main()
{
    assert(close(RetentionRefreshModel::acceleration_factor(85.0, 85.0,
                                                             1.1),
                 1.0));
    assert(RetentionRefreshModel::acceleration_factor(85.0, 100.0, 1.1) >
           1.0);
    assert(RetentionRefreshModel::acceleration_factor(85.0, 70.0, 1.1) <
           1.0);

    RetentionRefreshModel model(fixture());
    const auto first = model.advance(0, 12 * kHourNs, 85.0);
    assert(first.refresh_cycles == 0);
    assert(close(first.equivalent_age_remainder_s, 12.0 * 3600.0));
    const auto second = model.advance(12 * kHourNs, 24 * kHourNs, 85.0);
    assert(second.refresh_cycles == 1);
    assert(second.refresh_pages == 2);
    assert(second.extra_read_bytes == 8192);
    assert(second.extra_program_bytes == 8192);
    assert(close(second.extra_media_energy_j, 6.0));
    assert(second.serialized_media_busy_ns == 20);
    assert(second.channel_critical_path_ns == 10);
    assert(second.same_die_collision_upper_bound_ns == 10);
    assert(close(second.equivalent_age_remainder_s, 0.0));
    assert(model.stats().refresh_cycles == 1);
    assert(model.stats().refresh_pages == 2);

    const auto factor = RetentionRefreshModel::acceleration_factor(
        85.0, 100.0, 1.1);
    RetentionRefreshModel hot(fixture());
    const auto week_ns = 7 * 24 * kHourNs;
    const auto hot_step = hot.advance(0, week_ns, 100.0);
    assert(hot_step.acceleration_factor == factor);
    const auto expected = static_cast<std::uint64_t>(std::floor(7.0 * factor));
    assert(hot.stats().refresh_cycles == expected);

    bool rejected = false;
    try {
        const auto impossible =
            model.advance(23 * kHourNs, 25 * kHourNs, 85.0);
        (void)impossible;
    } catch (const RetentionError&) {
        rejected = true;
    }
    assert(rejected);

    rejected = false;
    try {
        auto invalid = fixture();
        invalid.protected_bytes = 0;
        RetentionRefreshModel unused(invalid);
    } catch (const RetentionError&) {
        rejected = true;
    }
    assert(rejected);
    std::cout << "retention-refresh: PASS\n";
}

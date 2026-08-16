#include "../../src/cuda_runtime/device/hbf_device.cuh"
#include "../../src/host_service/refresh_scheduler.hpp"

#include <hbfsim/profile.hpp>

#include <chrono>
#include <vector>

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (false)

int main()
{
    using namespace std::chrono_literals;
    auto profile = hbfsim::load_profile(
        "configs/profiles/thermal-validation.json");
    auto thermal = *profile.thermal_reliability;
    thermal.refresh_damage_threshold = 0.0001L;

    hbfsim::host_service::RefreshScheduler reference(profile, thermal);
    hbfsim::host_service::RefreshScheduler fast(profile, thermal);
    const auto bytes = static_cast<std::uint64_t>(profile.page_bytes) *
                       profile.pages_per_block;
    reference.register_range(0, bytes, true);
    fast.register_range(0, bytes, true);
    reference.age(85'000, 1s, 1);
    fast.age(85'000, 1s, 1);
    const auto reference_plan = reference.plan(1, {});
    const auto fast_plan = fast.plan(1, {});
    CHECK(reference_plan.size() == fast_plan.size());
    for (std::size_t index = 0; index < reference_plan.size(); ++index) {
        CHECK(reference_plan[index].kind == fast_plan[index].kind);
        CHECK(reference_plan[index].address == fast_plan[index].address);
        CHECK(reference.complete(reference_plan[index], true));
    }

    auto debt = fast.planned_bytes();
    std::size_t action = 0;
    while (debt != 0) {
        const auto claim = hbfsim::device::claim_refresh_debt(
            debt, thermal.refresh_quantum_bytes);
        CHECK(claim.claimed == fast_plan[action].bytes);
        debt = claim.remaining;
        CHECK(fast.complete(fast_plan[action], true));
        ++action;
    }
    CHECK(action == fast_plan.size());
    CHECK(reference.completed_blocks() == fast.completed_blocks());
    CHECK(reference.maximum_pec() == fast.maximum_pec());
    CHECK(reference.planned_bytes() == fast.planned_bytes());

    const auto decay = hbfsim::host_service::decay_refresh_debt(
        8ULL << 10, 1ms, 4ULL << 20, 900'000);
    CHECK(decay.remaining <= (8ULL << 10));
    CHECK(decay.drained + decay.remaining == (8ULL << 10));
    return 0;
}

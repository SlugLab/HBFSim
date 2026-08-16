#include "../../src/host_service/refresh_scheduler.hpp"

#include <hbfsim/profile.hpp>

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string_view>

namespace {
void check(bool condition, std::string_view message)
{
    if (!condition) {
        std::cerr << "check failed: " << message << '\n';
        std::abort();
    }
}
}

int main()
{
    using namespace std::chrono_literals;
    using hbfsim::host_service::ApplicationMediaUse;
    using hbfsim::host_service::RefreshActionKind;
    using hbfsim::host_service::RefreshScheduler;

    auto profile = hbfsim::load_profile(
        "configs/profiles/thermal-validation.json");
    auto thermal = *profile.thermal_reliability;
    thermal.refresh_damage_threshold = 0.0001L;
    RefreshScheduler scheduler(profile, thermal);
    scheduler.register_range(0, profile.page_bytes * profile.pages_per_block * 4,
                             true);
    scheduler.age(85'000, 1s, 1);

    const ApplicationMediaUse active_read{.channel = 0, .die = 0};
    const auto plan = scheduler.plan(10, std::span(&active_read, 1));
    check(!plan.empty(), "eligible zones produce refresh work");
    check(plan.front().kind == RefreshActionKind::Read,
          "refresh reads before rewrite");
    check(plan[1].kind == RefreshActionKind::Write &&
              plan[1].block == plan[0].block &&
              plan[1].page == plan[0].page,
          "matching rewrite follows read");
    check(plan.front().channel != 0 || plan.front().die != 0,
          "refresh does not overlap an application read on the same die");

    RefreshScheduler completion(profile, thermal);
    completion.register_range(0, profile.page_bytes * profile.pages_per_block,
                              true);
    completion.age(85'000, 1s, 1);
    const auto block_plan = completion.plan(10, {});
    check(block_plan.size() == profile.pages_per_block * 2,
          "one block emits a read/write pair for every page");
    for (const auto& action : block_plan) {
        check(completion.complete(action, true),
              "ordered refresh action completes");
    }
    check(completion.completed_blocks() == 1 &&
              completion.maximum_pec() == 1,
          "a complete block commits one PEC");

    RefreshScheduler failed(profile, thermal);
    failed.register_range(0, profile.page_bytes * profile.pages_per_block,
                          true);
    failed.age(85'000, 1s, 1);
    const auto failed_plan = failed.plan(10, {});
    check(failed.complete(failed_plan.front(), false),
          "failed action is consumed");
    check(failed.completed_blocks() == 0 && failed.maximum_pec() == 0,
          "failure does not commit PEC");
    check(!failed.plan(11, {}).empty(), "failed block remains eligible");
    return 0;
}

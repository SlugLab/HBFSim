#include "../../src/host_service/refresh_scheduler.hpp"
#include "../../src/host_service/request_dispatcher.hpp"

#include <hbfsim/profile.hpp>

#include <chrono>
#include <cstdlib>
#include <deque>
#include <vector>

#define CHECK(condition) do { if (!(condition)) return __LINE__; } while (false)

int main()
{
    using namespace std::chrono_literals;
    constexpr std::uint32_t capacity = 8;
    std::vector<std::byte> storage(
        hbfsim::host_service::control_region_bytes(capacity));
    hbfsim::host_service::ControlView control(storage.data(), storage.size());
    CHECK(control.initialize(capacity));

    auto profile = hbfsim::load_profile(
        "configs/profiles/thermal-validation.json");
    auto thermal = *profile.thermal_reliability;
    thermal.refresh_damage_threshold = 0.0001L;
    hbfsim::host_service::RefreshScheduler scheduler(profile, thermal);
    scheduler.register_range(0,
        static_cast<std::uint64_t>(profile.page_bytes) * profile.pages_per_block,
        true);
    scheduler.age(85'000, 1s, 1);
    const auto refresh = scheduler.plan(1, {});

    std::deque<hbfsim::HbfCompletion> completions;
    hbfsim::host_service::RequestDispatcher dispatcher(
        control, {
            .submit = [&](const hbfsim::HbfRequest& request) {
                completions.push_back({
                    .request_id = request.request_id,
                    .modeled_completion_ns = 100,
                    .modeled_ns = 100,
                    .service_ns = 100,
                    .page_generation = request.page_generation,
                    .status = static_cast<std::uint32_t>(
                        hbfsim::RequestStatus::Ready),
                });
            },
            .run_next_completion = [&]()
                -> std::optional<hbfsim::HbfCompletion> {
                if (completions.empty()) return std::nullopt;
                auto result = completions.front();
                completions.pop_front();
                return result;
            },
        });
    dispatcher.attach_refresh_scheduler(&scheduler);
    for (const auto& action : refresh) {
        CHECK(dispatcher.enqueue_background(action));
    }

    hbfsim::HbfRequest foreground{
        .request_id = 9,
        .arrival_ns = 0,
        .logical_address = profile.page_bytes * profile.pages_per_block,
        .bytes = profile.page_bytes,
        .operation = static_cast<std::uint32_t>(
            hbfsim::RequestOperation::Read),
        .page_generation = 1,
    };
    std::uint64_t ticket = 0;
    CHECK(control.try_push_request(foreground, ticket));
    CHECK(dispatcher.poll_once());
    hbfsim::HbfCompletion completion{};
    CHECK(control.try_consume_completion(ticket, completion));
    CHECK(completion.request_id == foreground.request_id);
    CHECK(scheduler.completed_blocks() == 1);
    CHECK(control.header()->thermal_refresh_read_bytes ==
          static_cast<std::uint64_t>(profile.page_bytes) *
              profile.pages_per_block);
    CHECK(control.header()->thermal_refresh_write_bytes ==
          static_cast<std::uint64_t>(profile.page_bytes) *
              profile.pages_per_block);
    return 0;
}

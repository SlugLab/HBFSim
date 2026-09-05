#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <map>
#include <stdexcept>
#include <vector>

namespace {
void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

struct Run {
    std::vector<hbfsim::HbfCompletion> completions;
    std::vector<hbfsim::MqsimObservation> events;
};

Run run(const hbfsim::Profile& profile, bool observe)
{
    hbfsim::MqsimOnlineEngine engine(profile);
    if (observe) engine.enable_observations();
    for (std::uint64_t id = 1; id <= 6; ++id) {
        engine.submit({.request_id = id, .sequence = id,
                       .arrival_ns = id < 5 ? 0ULL : 15000ULL,
                       .logical_address = (id - 1) * 16384,
                       .bytes = 16384, .operation = 0});
    }
    bool rejected = false;
    try { engine.enable_observations(); }
    catch (const std::logic_error&) { rejected = true; }
    require(rejected, "observation mode changed after submission");
    require(engine.pending() == 6, "not all requests remained outstanding");
    Run result;
    while (engine.pending()) {
        const auto completion = engine.run_next_completion();
        require(completion.has_value(), "lost completion");
        result.completions.push_back(*completion);
        auto events = engine.take_observations();
        result.events.insert(result.events.end(), events.begin(), events.end());
        require(engine.take_observations().empty(), "event drain returned duplicates");
    }
    return result;
}
}  // namespace

int main()
{
    auto profile = hbfsim::load_profile("configs/profiles/nominal.json");
    profile.capacity_bytes = 16ULL << 30;
    profile.hbm_cache_bytes = 64ULL << 20;
    profile.queue_depth = 2;
    const auto baseline = run(profile, false);
    const auto observed = run(profile, true);
    require(baseline.events.empty(), "default runtime emitted observation data");
    require(observed.completions.size() == 6 && observed.events.size() == 18,
            "request/event conservation failed");
    for (std::size_t i = 0; i < baseline.completions.size(); ++i) {
        const auto& left = baseline.completions[i];
        const auto& right = observed.completions[i];
        require(left.request_id == right.request_id &&
                left.modeled_completion_ns == right.modeled_completion_ns &&
                left.modeled_ns == right.modeled_ns &&
                left.service_ns == right.service_ns && left.status == right.status,
                "observation changed legacy completion semantics/order");
    }
    std::map<std::uint64_t, std::vector<hbfsim::MqsimObservation>> by_id;
    std::uint64_t previous = 0, bytes = 0;
    std::size_t peak = 0;
    for (const auto& event : observed.events) {
        require(event.time_ns >= previous, "event clock moved backward");
        previous = event.time_ns;
        require(event.device_outstanding <= profile.queue_depth, "QD bound exceeded");
        if (event.kind != hbfsim::MqsimEventKind::Completion) {
            require(event.modeled_completion_ns == 0, "non-completion invented a completion time");
        }
        peak = std::max(peak, event.device_outstanding);
        by_id[event.request_id].push_back(event);
    }
    require(peak == 2, "concurrent admission was serialized");
    bool saw_queue_wait = false;
    for (std::uint64_t id = 1; id <= 6; ++id) {
        const auto& events = by_id.at(id);
        require(events.size() == 3 && events[0].kind == hbfsim::MqsimEventKind::Arrival &&
                events[1].kind == hbfsim::MqsimEventKind::Admission &&
                events[2].kind == hbfsim::MqsimEventKind::Completion,
                "request lifecycle order/identity failed");
        const auto arrival = id < 5 ? 0ULL : 15000ULL;
        require(events[0].time_ns == arrival, "arrival timestamp changed");
        for (const auto& event : events) {
            require(event.arrival_ns == arrival && event.bytes == 16384,
                    "original request metadata changed");
        }
        require(events[2].modeled_completion_ns >= events[2].time_ns,
                "reported bandwidth bound precedes media completion");
        saw_queue_wait = saw_queue_wait || events[1].time_ns > arrival;
        bytes += events[2].bytes;
    }
    require(saw_queue_wait && bytes == 6 * 16384, "queue/byte conservation failed");
    profile.aggregate_bandwidth_bytes_per_s = 100000;
    const auto bandwidth_bound = run(profile, true);
    std::map<std::uint64_t, std::uint64_t> returned_times;
    for (const auto& completion : bandwidth_bound.completions) {
        returned_times.emplace(completion.request_id, completion.modeled_completion_ns);
    }
    bool saw_active_bound = false;
    for (const auto& event : bandwidth_bound.events) {
        if (event.kind != hbfsim::MqsimEventKind::Completion) continue;
        require(event.modeled_completion_ns == returned_times.at(event.request_id),
                "observation disagrees with returned completion");
        saw_active_bound = saw_active_bound || event.modeled_completion_ns > event.time_ns;
    }
    require(saw_active_bound, "control did not exercise distinct media and bandwidth times");
    std::cout << "PASS observed/default parity; 6 requests; 18 events; QD=2\n";
}

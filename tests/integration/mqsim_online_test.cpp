#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <cassert>
#include <cstdint>
#include <stdexcept>

namespace {

hbfsim::HbfRequest read_request(std::uint64_t id,
                                std::uint64_t sequence,
                                std::uint64_t arrival_ns,
                                std::uint64_t address,
                                std::uint32_t bytes)
{
    return hbfsim::HbfRequest{
        .request_id = id,
        .sequence = sequence,
        .arrival_ns = arrival_ns,
        .logical_address = address,
        .deadline_ns = 0,
        .bytes = bytes,
        .range_id = 1,
        .stream_id = 0,
        .operation =
            static_cast<std::uint32_t>(hbfsim::RequestOperation::Read),
        .page_generation = 1,
        .flags = 0,
    };
}

}  // namespace

int main()
{
    auto profile = hbfsim::load_profile("configs/profiles/nominal.json");
    profile.capacity_bytes = 4ULL * 1024 * 1024 * 1024;
    profile.hbm_cache_bytes = 64ULL * 1024 * 1024;
    hbfsim::validate_profile(profile);

    bool geometry_rejected = false;
    try {
        hbfsim::MqsimOnlineEngine too_small(profile);
    } catch (const std::invalid_argument&) {
        geometry_rejected = true;
    }
    assert(geometry_rejected);

    profile.capacity_bytes = 16ULL * 1024 * 1024 * 1024;
    hbfsim::validate_profile(profile);

    hbfsim::MqsimOnlineEngine engine(profile);
    engine.submit(read_request(2, 2, 100, 0x4000, 16384));
    engine.submit(read_request(1, 1, 0, 0x0000, 16384));
    assert(engine.pending() == 2);

    const auto first = engine.run_next_completion();
    const auto second = engine.run_next_completion();
    assert(first.has_value());
    assert(second.has_value());
    assert(first->request_id == 1);
    assert(second->modeled_completion_ns >= first->modeled_completion_ns);
    assert(first->modeled_ns >= profile.read_latency_ns);
    assert(engine.pending() == 0);
    assert(!engine.run_next_completion().has_value());

    const auto next_arrival = second->modeled_completion_ns + 100;
    engine.submit(read_request(4, 4, next_arrival, 0x8000, 16384));
    engine.submit(read_request(3, 3, next_arrival, 0x8000, 16384));
    const auto third = engine.run_next_completion();
    const auto fourth = engine.run_next_completion();
    assert(third.has_value());
    assert(fourth.has_value());
    assert(third->request_id == 3);
    assert(fourth->request_id == 4);

    bool rejected = false;
    try {
        engine.submit(read_request(3, 3, 200, 1, 511));
    } catch (const std::invalid_argument&) {
        rejected = true;
    }
    assert(rejected);

    return 0;
}

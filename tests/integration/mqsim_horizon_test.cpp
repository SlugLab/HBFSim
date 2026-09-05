#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/profile.hpp>
#include <hbfsim/protocol.hpp>

#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

namespace {
void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

hbfsim::HbfRequest request(std::uint64_t id, std::uint64_t arrival)
{
    return {.request_id=id, .sequence=id, .arrival_ns=arrival,
            .logical_address=(id-1)*16384, .bytes=16384, .operation=0};
}

std::vector<hbfsim::HbfCompletion> run(const hbfsim::Profile& profile, bool bounded)
{
    hbfsim::MqsimOnlineEngine engine(profile);
    engine.enable_observations();
    for (std::uint64_t id=1; id<=6; ++id) engine.submit(request(id, id<5 ? 0 : 15000));
    std::vector<hbfsim::HbfCompletion> completed;
    std::size_t observations=0;
    std::uint64_t completed_bytes=0, horizon=1000;
    while (engine.pending()) {
        const auto completion = bounded ? engine.run_next_completion_until(horizon)
                                        : engine.run_next_completion();
        if (bounded) {
            require(engine.current_time_ns() <= horizon, "clock exceeded horizon");
            if (!completion) {
                require(engine.current_time_ns() == horizon, "did not reach requested horizon");
                horizon += 7000;
            } else require(completion->modeled_completion_ns <= engine.current_time_ns(),
                           "reported completion returned before ready time");
        }
        if (completion) completed.push_back(*completion);
        for (const auto& event : engine.take_observations()) {
            ++observations;
            require(event.device_outstanding<=profile.queue_depth, "QD exceeded");
            if (event.kind==hbfsim::MqsimEventKind::Completion) completed_bytes+=event.bytes;
        }
    }
    require(completed.size()==6 && observations==18 && completed_bytes==6*16384,
            "horizon markers changed request/observation/byte conservation");
    return completed;
}
}

int main()
{
    auto profile=hbfsim::load_profile("configs/profiles/nominal.json");
    profile.capacity_bytes=16ULL<<30; profile.hbm_cache_bytes=64ULL<<20;
    profile.queue_depth=2;
    const auto legacy=run(profile, false), bounded=run(profile, true);
    for (std::size_t i=0; i<legacy.size(); ++i) {
        require(legacy[i].request_id==bounded[i].request_id &&
                legacy[i].modeled_completion_ns==bounded[i].modeled_completion_ns &&
                legacy[i].modeled_ns==bounded[i].modeled_ns &&
                legacy[i].status==bounded[i].status, "clock markers changed service/order");
    }
    {
        hbfsim::MqsimOnlineEngine engine(profile);
        engine.submit(request(1, 0));
        require(!engine.run_next_completion_until(1000), "media completed before control horizon");
        require(engine.current_time_ns()==1000 && engine.pending()==1, "lost pending request at horizon");
        engine.submit(request(2, 1000));
        bool rejected=false;
        try {(void)engine.run_next_completion_until(999);}
        catch (const std::invalid_argument&) {rejected=true;}
        require(rejected, "backward horizon admitted");
        require(engine.run_next_completion_until(100000).has_value(), "missing first completion");
        require(engine.run_next_completion_until(100000).has_value(), "missing second completion");
        require(!engine.pending(), "duplicate/lost completion");
        require(!engine.run_next_completion_until(200000) && engine.current_time_ns()==200000,
                "idle compute clock did not advance");
    }
    {
        hbfsim::MqsimOnlineEngine engine(profile);
        engine.submit(request(1, 0));
        require(engine.run_next_completion_until(1000000).has_value(), "missing mixed-API completion");
        const auto before = engine.current_time_ns();
        require(!engine.run_next_completion(), "empty legacy poll produced a completion");
        require(engine.current_time_ns() == before, "cancelled clock marker advanced empty legacy poll");
        engine.submit(request(2, 100000));
        require(engine.run_next_completion().has_value(), "mixed-API request failed");
    }
    {
        profile.aggregate_bandwidth_bytes_per_s=100000;
        hbfsim::MqsimOnlineEngine engine(profile);
        engine.submit(request(1, 0));
        require(!engine.run_next_completion_until(1000000), "bandwidth deadline released early");
        require(engine.pending()==1, "early polling consumed completion record");
        const auto completed=engine.run_next_completion_until(200000000);
        require(completed && completed->modeled_completion_ns==163840000 &&
                engine.current_time_ns()==completed->modeled_completion_ns,
                "reported completion clock ignored bandwidth deadline");
    }
    std::cout << "PASS legacy parity, compute horizon, bandwidth readiness and conservation\n";
}

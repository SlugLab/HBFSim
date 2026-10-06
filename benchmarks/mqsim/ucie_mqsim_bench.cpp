#include <hbfsim/profile.hpp>
#include <hbfsim/ucie/mqsim_frontend.hpp>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char** argv)
{
    try {
        if (argc != 3)
            throw std::invalid_argument("usage: ucie_mqsim_bench LINK_PROFILE COUNT(1..1000)");
        const auto text=std::string(argv[2]);
        if (text.empty() || text.find_first_not_of("0123456789")!=std::string::npos)
            throw std::invalid_argument("COUNT must be an integer");
        const auto count=std::stoul(text);
        if (count<1 || count>1000)
            throw std::invalid_argument("COUNT must be in 1..1000");
        auto link_profile=hbfsim::ucie::load_link_profile(argv[1]);
        auto media_profile=hbfsim::load_profile("configs/profiles/nominal.json");
        media_profile.capacity_bytes=link_profile.local_capacity_bytes;
        media_profile.page_bytes=4096;
        media_profile.hbm_cache_bytes=std::min<std::uint64_t>(media_profile.hbm_cache_bytes,
            media_profile.capacity_bytes);
        hbfsim::validate_profile(media_profile);
        hbfsim::ucie::UcieMqsimFrontend front(link_profile,media_profile);
        std::uint64_t consumed=0;
        const auto poll=[&]() {
            const auto horizon=front.current_time_ns()+1000000000ULL;
            const auto done=front.run_next_completion_until(horizon);
            if (!done) throw std::runtime_error("MQSim/UCIe request did not finish within benchmark horizon");
            if (done->media.status!=static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready))
                throw std::runtime_error("native MQSim returned non-ready completion");
            std::cout << "{\"request_id\":" << done->request.request_id
                << ",\"axi_id\":" << done->request.axi_id
                << ",\"request_delivered_ns\":" << done->request_delivered_ns
                << ",\"media_ready_ns\":" << done->media_ready_ns
                << ",\"response_delivered_ns\":" << done->response_delivered_ns
                << ",\"application_bytes\":64,\"media_submit_bytes\":4096}\n";
            ++consumed;
        };
        for (std::uint64_t id=1; id<=count; ++id) {
            hbfsim::ucie::AxiRead read{.request_id=id,
                .arrival_ns=front.current_time_ns(),
                .local_address=((id-1)%(link_profile.local_capacity_bytes/4096))*4096,
                .axi_id=static_cast<std::uint32_t>(id%4)};
            while (!front.try_submit(read)) {
                poll();
                read.arrival_ns=front.current_time_ns();
            }
        }
        while (consumed<count) poll();
        // The last R poll releases credit; its reverse-direction grant flits
        // remain real wire events until the link is drained.
        for (unsigned steps=0; front.link().next_event_ns() && steps<100000; ++steps) {
            const auto event_time=*front.link().next_event_ns();
            if (front.run_next_completion_until(event_time))
                throw std::logic_error("unexpected completion during final credit drain");
        }
        if (front.link().next_event_ns() || front.outstanding()!=0 ||
            front.link().credits(hbfsim::ucie::Direction::Request)!=
                link_profile.initial_ar_granules ||
            front.link().credits(hbfsim::ucie::Direction::Return)!=
                link_profile.initial_r_granules)
            throw std::logic_error("UCIe accepted/credit resources did not close");
        const auto& counters=front.counters();
        const auto& ar=front.link().counters(hbfsim::ucie::Direction::Request);
        const auto& r=front.link().counters(hbfsim::ucie::Direction::Return);
        std::uint64_t mqsim_arrivals=0, mqsim_admissions=0, mqsim_callbacks=0;
        std::uint64_t callbacks_before_cap_ready=0;
        for (const auto& obs: front.take_media_observations()) {
            if (obs.kind==hbfsim::MqsimEventKind::Arrival) ++mqsim_arrivals;
            if (obs.kind==hbfsim::MqsimEventKind::Admission) ++mqsim_admissions;
            if (obs.kind==hbfsim::MqsimEventKind::Completion) {
                ++mqsim_callbacks;
                if (obs.modeled_completion_ns>obs.time_ns) ++callbacks_before_cap_ready;
            }
        }
        if (counters.accepted!=count || counters.consumed!=count ||
            counters.media_completions!=count ||
            counters.application_bytes!=count*64 ||
            counters.media_submit_bytes!=count*4096 ||
            mqsim_arrivals!=count || mqsim_admissions!=count ||
            mqsim_callbacks!=count || counters.observations_dropped!=0)
            throw std::logic_error("UCIe/MQSim request or byte accounting did not close");
        std::cout << "{\"summary\":true,\"accepted\":" << counters.accepted
            << ",\"consumed\":" << counters.consumed
            << ",\"application_bytes\":" << counters.application_bytes
            << ",\"media_submit_bytes\":" << counters.media_submit_bytes
            << ",\"mqsim_arrivals\":" << mqsim_arrivals
            << ",\"mqsim_admissions\":" << mqsim_admissions
            << ",\"mqsim_callbacks\":" << mqsim_callbacks
            << ",\"callbacks_before_cap_ready\":" << callbacks_before_cap_ready
            << ",\"ar_wire_bytes\":" << ar.wire_bytes
            << ",\"r_wire_bytes\":" << r.wire_bytes
            << ",\"ar_flits\":" << ar.flits
            << ",\"r_flits\":" << r.flits
            << ",\"flit_trace_dropped\":" << front.link().flit_records_dropped()
            << ",\"event_trace_dropped\":" << counters.trace_dropped
            << ",\"observation_trace_dropped\":" << counters.observations_dropped
            << ",\"credit_profile_evidence\":\"SCENARIO_ASSUMPTION\"}\n";
    } catch (const std::exception& error) {
        std::cerr << "ucie_mqsim_bench: " << error.what() << '\n';
        return 1;
    }
}

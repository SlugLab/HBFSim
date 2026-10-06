#include <hbfsim/ucie/device_frontend.hpp>
#include <hbfsim/ucie/device_profile.hpp>

#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc,char** argv)
{
    try {
        if (argc!=3)
            throw std::invalid_argument("usage: ucie_device_bench DEVICE_PROFILE COUNT(1..1000)");
        const auto count_text=std::string(argv[2]);
        if (count_text.empty() ||
            count_text.find_first_not_of("0123456789")!=std::string::npos)
            throw std::invalid_argument("COUNT must be a positive integer");
        const auto count=std::stoull(count_text);
        if (count<1 || count>1000)
            throw std::invalid_argument("COUNT must be in 1..1000");
        const auto profile=hbfsim::ucie::load_device_profile(argv[1]);
        hbfsim::ucie::UcieDeviceFrontend front(profile.link,profile.media,
            profile.layout,profile.stack_id,profile.module_id,
            profile.coalescing,profile.buffer_cache);
        front.add_backing({1,0,profile.link.local_capacity_bytes,1,0,1,
                           profile.stack_id,profile.module_id,7,true});
        std::uint64_t polled=0;
        const auto poll=[&]() {
            const auto completion=front.run_next_completion_until(
                front.current_time_ns()+1'000'000'000ULL);
            if (!completion || completion->result!=hbfsim::ucie::DeviceResult::Ready)
                throw std::runtime_error("strict native MQSim UCIe read failed");
            std::cout << "{\"request_id\":" << completion->request.request_id
                      << ",\"ar_delivered_ns\":" << completion->ar_delivered_ns
                      << ",\"media_ready_ns\":" << completion->media_ready_ns
                      << ",\"r_delivered_ns\":" << completion->response_delivered_ns
                      << ",\"consumed_ns\":" << completion->consumed_ns
                      << ",\"application_bytes\":64,\"axi_payload_bytes\":64}\n";
            ++polled;
        };
        for (std::uint64_t id=1;id<=count;++id) {
            hbfsim::ucie::DeviceRead read{
                .request_id=id,.arrival_ns=front.current_time_ns(),
                .local_address=((id-1)%64)*64,
                .axi_id=static_cast<std::uint32_t>(id%(1U<<14)),
                .stack_id=profile.stack_id,.module_id=profile.module_id,
                .endpoint_id=7};
            while (!front.try_submit(read)) {
                poll();
                read.arrival_ns=front.current_time_ns();
            }
        }
        while (polled<count) poll();
        front.drain_until_idle(front.current_time_ns()+1'000'000'000ULL);
        const auto& c=front.counters();
        const auto& groups=front.coalescer().counters();
        const auto& ar=front.link().counters(hbfsim::ucie::Direction::Request);
        const auto& r=front.link().counters(hbfsim::ucie::Direction::Return);
        if (c.accepted!=count || c.succeeded!=count || c.failed || c.cancelled ||
            c.caller_outstanding || front.outstanding() ||
            c.native_commands!=c.media_submits ||
            c.application_bytes!=count*64 || c.axi_payload_bytes!=count*64 ||
            c.media_submit_bytes!=c.media_submits*4096)
            throw std::logic_error("Stage2 byte, request, native command closure failed");
        for (const auto& proof:front.native_proofs())
            std::cout << "{\"native_group\":" << proof.group_token
                      << ",\"stack_id\":" << proof.stack_id
                      << ",\"module_id\":" << proof.module_id
                      << ",\"hbf_bank\":" << proof.bank.bank
                      << ",\"hbf_core_die\":" << proof.bank.core_die
                      << ",\"native_channel\":" << proof.bank.native_channel
                      << ",\"native_chip\":" << proof.bank.native_chip
                      << ",\"logical_page\":" << proof.proof.observed_logical_page
                      << ",\"native_media_bytes\":" << proof.proof.observed_media_bytes
                      << ",\"command_issued_ns\":" << proof.proof.first_issued_ns
                      << ",\"media_begin_ns\":" << proof.proof.first_media_begin_ns
                      << ",\"media_end_ns\":" << proof.proof.last_media_end_ns
                      << ",\"issued_count\":" << proof.proof.phase_events[0]
                      << ",\"media_begin_count\":" << proof.proof.phase_events[1]
                      << ",\"media_end_count\":" << proof.proof.phase_events[2]
                      << ",\"data_out_begin_count\":" << proof.proof.phase_events[3]
                      << ",\"data_out_end_count\":" << proof.proof.phase_events[4]
                      << "}\n";
        std::cout << "{\"summary\":true,\"accepted\":" << c.accepted
                  << ",\"succeeded\":" << c.succeeded
                  << ",\"native_commands\":" << c.native_commands
                  << ",\"media_submits\":" << c.media_submits
                  << ",\"application_bytes\":" << c.application_bytes
                  << ",\"axi_payload_bytes\":" << c.axi_payload_bytes
                  << ",\"media_submit_bytes\":" << c.media_submit_bytes
                  << ",\"ar_wire_bytes\":" << ar.wire_bytes
                  << ",\"r_wire_bytes\":" << r.wire_bytes
                  << ",\"ar_flits\":" << ar.flits
                  << ",\"r_flits\":" << r.flits
                  << ",\"media_misses\":" << groups.media_misses
                  << ",\"inflight_joins\":" << groups.inflight_joins
                  << ",\"buffer_hits\":" << groups.buffer_hits
                  << ",\"peak_bank_slots\":" << groups.peak_slots_in_one_bank
                  << ",\"peak_transport_outstanding\":"
                  << c.peak_transport_outstanding
                  << ",\"peak_media_groups\":" << c.peak_media_groups
                  << ",\"coalescing\":" << (profile.coalescing?"true":"false")
                  << ",\"buffer_cache\":" << (profile.buffer_cache?"true":"false")
                  << ",\"scenario_assumption\":"
                  << (profile.layout.scenario_assumption?"true":"false")
                  << ",\"request_records_dropped\":" << c.request_records_dropped
                  << ",\"native_proofs_dropped\":" << c.native_proofs_dropped
                  << ",\"trace_dropped\":" << c.trace_dropped
                  << ",\"legacy_hbm_cache\":\"NOT_USED\"}\n";
    } catch (const std::exception& error) {
        std::cerr << "ucie_device_bench: " << error.what() << '\n';
        return 1;
    }
}

#include <hbfsim/ucie/stack_frontend.hpp>

#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void require(bool condition,const char* message)
{ if (!condition) throw std::runtime_error(message); }

hbfsim::Profile media_profile()
{
    auto p=hbfsim::load_profile("configs/profiles/nominal.json");
    p.capacity_bytes=8ULL<<20;
    p.channels=2;
    p.page_bytes=4096;
    if (p.hbm_cache_bytes>p.capacity_bytes) p.hbm_cache_bytes=p.capacity_bytes;
    return p;
}
std::vector<hbfsim::ucie::LinkProfile> links(std::uint32_t max_accepted=1)
{
    std::vector<hbfsim::ucie::LinkProfile> out;
    for (int m=0;m<2;++m) {
        hbfsim::ucie::LinkProfile p;
        p.name="stack-module"+std::to_string(m);
        p.local_capacity_bytes=4ULL<<20;
        p.max_accepted=max_accepted;
        p.initial_ar_granules=12;
        p.initial_r_granules=56;
        p.propagation_ns=100;
        out.push_back(p);
    }
    return out;
}
hbfsim::ucie::DeviceRead read(std::uint64_t id,std::uint32_t module,
                              std::uint64_t arrival=0)
{
    return {.request_id=id,.arrival_ns=arrival,.local_address=0,
            .axi_id=1,.stack_id=0,.module_id=module,.endpoint_id=7};
}
void add_backings(hbfsim::ucie::StackFrontend& front)
{
    for (std::uint32_t m=0;m<2;++m)
        front.add_backing({m+1,0,4ULL<<20,m+1,0,1,0,m,7,true});
}
void real_two_modules()
{
    using namespace hbfsim::ucie;
    auto profiles=links();
    profiles[1].propagation_ns=1; // later module has the earliest AR event
    StackFrontend front(profiles,media_profile(),{2,1,1,true},0);
    add_backings(front);
    require(front.try_submit(read(1,0)) && front.try_submit(read(2,1)),
            "two module AR requests were not accepted");
    front.advance_until(1'000'000);
    require(front.ready_ids().size()==2 &&
            front.peek_completion(1) && front.peek_completion(2),
            "central stack loop failed to deliver both module R packets");
    require(front.coalescer().group_count()==2 &&
            front.coalescer().occupied_slots(0,0)==1 &&
            front.coalescer().occupied_slots(1,0)==1,
            "non-consuming peek released bank slots");
    require(front.link(0).counters(Direction::Request).wire_bytes>0 &&
            front.link(1).counters(Direction::Request).wire_bytes>0,
            "module AR links were not independent active states");
    for (const auto id:front.ready_ids()) {
        const auto result=front.consume_completion(id);
        require(result.result==DeviceResult::Ready &&
                result.axi_payload_bytes==64 && result.consumed_ns==1'000'000,
                "real worker response did not consume at caller time");
    }
    front.drain_until_idle(1'001'000);
    require(front.counters().native_commands==2 &&
            front.counters().media_submit_bytes==8192 &&
            front.counters().accepted==front.counters().succeeded &&
            front.native_proofs().size()==2,
            "two-module native media proof/accounting did not close");
    const auto& requests=front.request_records();
    require(requests.size()==2 &&
            requests[1].module_id==1 && requests[0].module_id==0 &&
            requests[1].ar_delivered_ns<requests[0].ar_delivered_ns &&
            requests[1].ar_delivered_ns<requests[0].media_ready_ns &&
            requests[1].media_ready_ns>=requests[1].ar_delivered_ns &&
            requests[0].media_ready_ns>=requests[0].ar_delivered_ns &&
            requests[1].r_delivered_ns>=requests[1].media_ready_ns &&
            requests[0].r_delivered_ns>=requests[0].media_ready_ns,
            "central loop advanced a later module by vector order");
    for (const auto& proof:front.native_proofs())
        require(proof.proof.issued_commands==1 &&
                proof.proof.logical_page_match && proof.proof.bank_match &&
                proof.proof.phase_events[1]==1 && proof.proof.phase_events[2]==1,
                "module native command/event mismatch");
    const auto& proof0=front.native_proofs().at(0);
    const auto& proof1=front.native_proofs().at(1);
    require(proof0.bank.native_channel!=proof1.bank.native_channel &&
            proof0.proof.first_media_begin_ns<proof1.proof.last_media_end_ns &&
            proof1.proof.first_media_begin_ns<proof0.proof.last_media_end_ns,
            "independent native channels did not overlap sense intervals");
    std::cout << "stack_real modules=2 native_commands=2"
              << " ar_m0=" << requests[0].ar_delivered_ns
              << " ar_m1=" << requests[1].ar_delivered_ns << ' ';
    for (const auto& proof:front.native_proofs())
        std::cout << "module=" << proof.module_id
                  << ":channel=" << proof.bank.native_channel
                  << ":sense=" << proof.proof.first_media_begin_ns
                  << ".." << proof.proof.last_media_end_ns << " ";
    std::cout << '\n';
}
void cancelled_physical_admission()
{
    using namespace hbfsim::ucie;
    StackFrontend front(links(),media_profile(),{2,1,1,true},0);
    add_backings(front);
    require(front.try_submit(read(11,0)),"first request failed admission");
    front.cancel(11);
    require(front.peek_completion(11) &&
            front.consume_completion(11).result==DeviceResult::Cancelled,
            "cancelled caller did not receive exactly one result");
    require(!front.try_submit(read(12,0,front.current_time_ns())),
            "cancel+consume prematurely released physical admission slot");
    front.advance_until(1000);
    require(front.try_submit(read(12,0,front.current_time_ns())),
            "physical drain failed to restore module admission slot");
    front.cancel(12);
    (void)front.consume_completion(12);
    front.drain_until_idle(10'000);
}
void direct_alias_uses_canonical_page()
{
    using namespace hbfsim::ucie;
    StackFrontend front(links(),media_profile(),{2,1,1,true},0);
    front.add_backing({1,0,64,301,4096,1,0,0,7,true});
    require(front.try_submit(read(41,0)),
            "direct stack alias failed ordinary registration");
    front.advance_until(100'000);
    require(front.peek_completion(41) &&
            front.native_proofs().size()==1 &&
            front.native_proofs()[0].proof.expected_logical_page==2 &&
            front.native_proofs()[0].proof.observed_logical_page==2 &&
            front.request_records()[0].canonical_page==1,
            "ordinary alias read used virtual page instead of canonical page");
    (void)front.consume_completion(41);
}
}
int main()
{
    real_two_modules();
    cancelled_physical_admission();
    direct_alias_uses_canonical_page();
}

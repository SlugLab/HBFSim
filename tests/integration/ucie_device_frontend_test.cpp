#include <hbfsim/ucie/device_frontend.hpp>
#include <hbfsim/ucie/mqsim_frontend.hpp>

#include <cstdint>
#include <iostream>
#include <stdexcept>

namespace {
void require(bool value,const char* why)
{ if (!value) throw std::runtime_error(why); }

hbfsim::Profile profile()
{
    auto p=hbfsim::load_profile("configs/profiles/nominal.json");
    p.capacity_bytes=8ULL<<20; // 2 research banks x >=4 blocks each
    p.channels=1;
    p.page_bytes=4096;
    if (p.hbm_cache_bytes>p.capacity_bytes) p.hbm_cache_bytes=p.capacity_bytes;
    return p;
}
hbfsim::ucie::LinkProfile link()
{
    hbfsim::ucie::LinkProfile p;
    p.name="stage2-cpu-two-bank";
    p.local_capacity_bytes=8ULL<<20;
    p.max_accepted=8;
    p.initial_ar_granules=24;
    p.initial_r_granules=112;
    p.propagation_ns=1;
    return p;
}
void run(bool coalesce,std::uint64_t expected_commands)
{
    using namespace hbfsim::ucie;
    UcieDeviceFrontend front(link(),profile(),{1,1,2,true},0,0,
                             coalesce,false);
    front.add_backing({1,0,8ULL<<20,1,0,1,0,0,7,true});
    for (std::uint64_t id=1;id<=8;++id)
        require(front.try_submit(DeviceRead{.request_id=id,
            .arrival_ns=0,.local_address=(id-1)*64,
            .axi_id=static_cast<std::uint32_t>(id),
            .stack_id=0,.module_id=0,.endpoint_id=7}),
            "eight AR were not accepted");
    std::uint64_t delivered=0;
    while (delivered<8) {
        const auto result=front.run_next_completion_until(1'000'000'000ULL);
        require(result && result->result==DeviceResult::Ready &&
                result->axi_payload_bytes==64,
                "real NAND-backed R did not arrive ready");
        ++delivered;
    }
    front.drain_until_idle(1'000'000'000ULL);
    const auto& counts=front.counters();
    require(counts.accepted==8 && counts.succeeded==8 &&
            counts.caller_outstanding==0 && counts.native_commands==expected_commands &&
            counts.media_submits==expected_commands &&
            counts.application_bytes==8*64 &&
            counts.axi_payload_bytes==8*64 &&
            counts.media_submit_bytes==expected_commands*4096,
            "native/inflight/application byte accounting did not close");
    require(front.link().counters(Direction::Request).wire_bytes>0 &&
            front.link().counters(Direction::Return).wire_bytes>0,
            "AR/R link wire traffic was not modeled");
    require(front.native_proofs().size()==expected_commands &&
            front.request_records().size()==8,
            "bounded native/request proof mapping is incomplete");
    for (const auto& record:front.request_records())
        require(record.group_token!=0 && record.canonical_page==0 &&
                record.media_lpa==0 && record.result==DeviceResult::Ready &&
                record.ar_delivered_ns>0 && record.r_delivered_ns>0,
                "request-to-group/native mapping lost identity or timing");
    std::cout << "coalesce=" << coalesce << " ar=8 r=" << delivered
              << " native_commands=" << counts.native_commands
              << " media_bytes=" << counts.media_submit_bytes << '\n';
}

void run_two_banks()
{
    using namespace hbfsim::ucie;
    UcieDeviceFrontend front(link(),profile(),{1,1,2,true},0,0,true,false);
    front.add_backing({1,0,8ULL<<20,1,0,1,0,0,7,true});
    for (std::uint64_t id=1;id<=4;++id)
        require(front.try_submit(DeviceRead{.request_id=id,.arrival_ns=0,
            .local_address=(id-1)*4096,
            .axi_id=static_cast<std::uint32_t>(id),.endpoint_id=7}),
            "four bank reads were not accepted");
    front.drain_until_idle(1'000'000'000ULL);
    require(front.native_proofs().size()==4 &&
            front.counters().native_commands==4,
            "multi-bank native command count missing");
    const auto& proofs=front.native_proofs();
    bool overlap=false;
    for (std::size_t a=0;a<proofs.size();++a) {
        const auto& left=proofs[a];
        require(left.proof.phase_events[1]==1 && left.proof.phase_events[2]==1 &&
                left.proof.first_media_begin_ns<=left.proof.last_media_end_ns,
                "native sense phase observation incomplete");
        for (std::size_t b=a+1;b<proofs.size();++b) {
            const auto& right=proofs[b];
            const bool intersect=left.proof.first_media_begin_ns<right.proof.last_media_end_ns &&
                right.proof.first_media_begin_ns<left.proof.last_media_end_ns;
            if (left.bank.native_chip==right.bank.native_chip)
                require(!intersect,"same native bank sensed overlapping pages");
            else if (intersect) overlap=true;
        }
        std::cout << "page=" << left.proof.observed_logical_page
                  << " hbf_bank=" << left.bank.bank
                  << " native_chip=" << left.bank.native_chip
                  << " sense_begin=" << left.proof.first_media_begin_ns
                  << " sense_end=" << left.proof.last_media_end_ns << '\n';
    }
    require(overlap,"distinct native banks showed no actual sense overlap");
}

void test_mixed_frontend_guard()
{
    using namespace hbfsim::ucie;
    auto legacy_profile=profile();
    legacy_profile.dies_per_channel=1;
    legacy_profile.planes_per_die=1;
    {
        UcieDeviceFrontend hbf(link(),profile(),{1,1,2,true},0,0);
        bool rejected=false;
        try { UcieMqsimFrontend old(link(),legacy_profile); }
        catch (const std::logic_error&) { rejected=true; }
        require(rejected,"legacy frontend reset active HBF engine");
    }
    {
        UcieMqsimFrontend old(link(),legacy_profile);
        bool rejected=false;
        try { UcieDeviceFrontend hbf(link(),profile(),{1,1,2,true},0,0); }
        catch (const std::logic_error&) { rejected=true; }
        require(rejected,"HBF frontend reset active legacy engine");
    }
}
}
int main()
{
    run(true,1);
    run(false,8);
    run_two_banks();
    test_mixed_frontend_guard();
}

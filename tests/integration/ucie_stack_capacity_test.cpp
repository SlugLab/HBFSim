#include <hbfsim/ucie/multistack_frontend.hpp>

#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {
using namespace hbfsim::ucie;
constexpr std::uint64_t gib=1ULL<<30;
constexpr std::uint64_t stack_bytes=512*gib;
constexpr std::uint64_t module_bytes=32*gib;
constexpr std::uint64_t last_lpa=stack_bytes/4096-1;
constexpr const char* profile=
    "configs/profiles/ucie/hbf-stage3-4x512gib-top.json";

void require(bool value,const char* message)
{ if (!value) throw std::runtime_error(message); }

void one_worker(const char* worker)
{
    StackWorkerClient front(worker,profile,0,true);
    front.add_backing({1,0,64,1,0,1,0,0,7,true});
    front.add_backing({2,module_bytes-64,64,2,module_bytes-64,
                       1,0,15,7,true});
    require(front.try_submit({.request_id=1,.arrival_ns=0,.local_address=0,
                .axi_id=1,.stack_id=0,.module_id=0,.endpoint_id=7}) &&
            front.try_submit({.request_id=2,.arrival_ns=0,
                .local_address=module_bytes-64,.axi_id=2,
                .stack_id=0,.module_id=15,.endpoint_id=7}),
            "one 512GiB stack rejected its first or last module read");
    std::uint32_t consumed=0;
    std::uint64_t final_h=0;
    for (std::uint64_t h=0;h<100'000 && consumed<2;++h) {
        for (const auto& ready:front.advance_until(h)) {
            const auto completion=front.consume_completion(ready.request_id,h);
            require(completion.result==DeviceResult::Ready &&
                    completion.axi_payload_bytes==64,
                    "512GiB one-worker response failed");
            ++consumed;
        }
        (void)front.advance_until(h);
        if (consumed<2) front.close_horizon(h);
        final_h=h;
    }
    const auto proof=front.native_evidence(0);
    const auto stats=front.stats();
    require(consumed==2 && proof.size()==2 &&
            stats.native_commands==2 &&
            proof[0].proof.observed_logical_page==0 &&
            proof[1].proof.observed_logical_page==last_lpa &&
            proof[0].proof.logical_page_match &&
            proof[1].proof.logical_page_match &&
            proof[1].module_id==15 &&
            stats.media_submit_bytes==8192,
            "512GiB true high-address native LPA/command proof failed");
    std::cout << "capacity_one stack_bytes=" << stack_bytes
              << " pid=" << stats.process_id
              << " rss_kib=" << stats.max_rss_kib
              << " native=" << stats.native_commands
              << " first_lpa=0 last_lpa="
              << proof[1].proof.observed_logical_page
              << " final_h=" << final_h << '\n';
}

void four_workers(const char* worker)
{
    MultistackFrontend front(profile,worker);
    front.add_backing({1,0,64,1,0,1,7,true});
    front.add_backing({2,4*stack_bytes-64,64,2,
                       4*stack_bytes-64,1,7,true});
    front.add_backing({3,module_bytes-32,64,3,
                       module_bytes-32,1,7,true});
    front.add_backing({4,stack_bytes-32,64,4,
                       stack_bytes-32,1,7,true});
    const GlobalRead reads[]={
        {.request_id=1,.arrival_ns=0,.address=0,.bytes=64,
            .endpoint_id=7,.axi_id=1},
        {.request_id=2,.arrival_ns=0,.address=4*stack_bytes-64,
            .bytes=64,.endpoint_id=7,.axi_id=2},
        {.request_id=3,.arrival_ns=0,.address=module_bytes-32,
            .bytes=64,.endpoint_id=7,.axi_id=3},
        {.request_id=4,.arrival_ns=0,.address=stack_bytes-32,
            .bytes=64,.endpoint_id=7,.axi_id=4}};
    for (const auto& read:reads)
        require(front.try_submit(read),"2TiB address fixture rejected a parent");
    std::uint64_t final_h=0;
    bool all=false;
    for (std::uint64_t h=0;h<100'000;++h) {
        front.advance_until(h);
        if (front.ready_ids().size()==4) {
            for (const auto& read:reads) {
                const auto completion=front.consume_completion(read.request_id);
                require(completion.result==DeviceResult::Ready &&
                        completion.original_bytes==64 &&
                        completion.axi_payload_bytes==
                            (read.request_id<3?64U:128U),
                        "2TiB parent read or cross-boundary split failed");
            }
            final_h=h;
            all=true;
            break;
        }
    }
    require(all && front.counters().accepted==4 &&
            front.counters().succeeded==4 &&
            front.counters().original_bytes==256 &&
            front.counters().axi_payload_bytes==384,
            "4-stack parent accounting failed");
    const auto high=front.worker(3).native_evidence(0);
    require(high.size()==1 && high[0].module_id==15 &&
            high[0].proof.observed_logical_page==last_lpa &&
            high[0].proof.logical_page_match &&
            high[0].proof.issued_commands==1,
            "final 2TiB physical byte did not reach final native LPA");
    for (std::uint32_t stack=0;stack<4;++stack) {
        const auto stats=front.worker(stack).stats();
        std::cout << "capacity_four stack=" << stack
                  << " pid=" << stats.process_id
                  << " rss_kib=" << stats.max_rss_kib
                  << " native=" << stats.native_commands << '\n';
    }
    std::cout << "capacity_four total_bytes=" << 4*stack_bytes
              << " final_h=" << final_h
              << " last_lpa=" << high[0].proof.observed_logical_page
              << " original=256 axi=384\n";
}
}

int main(int argc,char** argv)
{
    if (argc!=3) throw std::invalid_argument("usage: capacity_test WORKER one|four");
    if (std::string(argv[2])=="one") one_worker(argv[1]);
    else if (std::string(argv[2])=="four") four_workers(argv[1]);
    else throw std::invalid_argument("capacity fixture mode must be one or four");
}

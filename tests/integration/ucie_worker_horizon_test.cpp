#include <hbfsim/ucie/worker_protocol.hpp>

#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <vector>

namespace {
void require(bool condition,const char* message)
{ if (!condition) throw std::runtime_error(message); }
}

int main(int argc,char** argv)
{
    using namespace hbfsim::ucie;
    if (argc!=2) throw std::invalid_argument("worker executable path required");
    const char* profile="configs/profiles/ucie/hbf-stage3-small-stack.json";
    std::vector<std::unique_ptr<StackWorkerClient>> workers;
    for (std::uint32_t s=0;s<2;++s) {
        workers.push_back(std::make_unique<StackWorkerClient>(argv[1],profile,s));
        workers.back()->add_backing({1,0,4ULL<<20,1,0,1,s,s,7,true});
    }
    require(workers[0]->try_submit({.request_id=1,.arrival_ns=0,
                .local_address=0,.axi_id=1,.stack_id=0,.module_id=0,
                .endpoint_id=7}) &&
            workers[1]->try_submit({.request_id=2,.arrival_ns=0,
                .local_address=0,.axi_id=1,.stack_id=1,.module_id=1,
                .endpoint_id=7}),
            "two distinct stack worker reads were not accepted");
    std::uint64_t consumed=0;
    std::uint64_t last_h=0;
    for (std::uint64_t h=0;h<100'000 && consumed<2;++h) {
        // Reverse IPC receive order every other horizon. Model tie-breaking
        // is by stack identity, not OS scheduling or reply order.
        std::vector<WorkerReady> ready[2];
        if (h%2==0) {
            ready[1]=workers[1]->advance_until(h);
            ready[0]=workers[0]->advance_until(h);
        } else {
            ready[0]=workers[0]->advance_until(h);
            ready[1]=workers[1]->advance_until(h);
        }
        for (std::uint32_t s=0;s<2;++s)
            for (const auto& item:ready[s]) {
                require(item.result==DeviceResult::Ready &&
                        item.response_delivered_ns<=h &&
                        item.media_ready_ns>=item.ar_delivered_ns,
                        "worker reported premature or invalid media/R completion");
                const auto completed=workers[s]->consume_completion(
                    item.request_id,h);
                require(completed.result==DeviceResult::Ready &&
                        completed.axi_payload_bytes==64 &&
                        completed.consumed_ns==h,
                        "parent/worker consume acknowledgement mismatch");
                ++consumed;
            }
        // CONSUME can create same-h credit events. Re-advance both workers
        // before CLOSE, then proceed only when both quiesce.
        (void)workers[0]->advance_until(h);
        (void)workers[1]->advance_until(h);
        workers[0]->close_horizon(h);
        workers[1]->close_horizon(h);
        last_h=h;
    }
    require(consumed==2,"two real workers failed to complete by horizon");
    const auto a=workers[0]->stats();
    const auto b=workers[1]->stats();
    require(a.process_id && b.process_id && a.process_id!=b.process_id &&
            a.native_commands==1 && b.native_commands==1 &&
            a.media_submit_bytes==4096 && b.media_submit_bytes==4096 &&
            a.accepted==1 && b.accepted==1 &&
            a.succeeded==1 && b.succeeded==1,
            "separate process/native NAND accounting did not close");
    const auto proof0=workers[0]->native_evidence(0);
    const auto proof1=workers[1]->native_evidence(0);
    const auto req0=workers[0]->request_evidence(0);
    const auto req1=workers[1]->request_evidence(0);
    require(proof0.size()==1 && proof1.size()==1 &&
            req0.size()==1 && req1.size()==1 &&
            proof0[0].stack_id==0 && proof0[0].module_id==0 &&
            proof1[0].stack_id==1 && proof1[0].module_id==1 &&
            proof0[0].proof.observed_logical_page==0 &&
            proof1[0].proof.observed_logical_page==1 &&
            proof0[0].proof.logical_page_match &&
            proof1[0].proof.logical_page_match &&
            req0[0].group_token==proof0[0].group_token &&
            req1[0].group_token==proof1[0].group_token &&
            a.module_links.size()==2 && b.module_links.size()==2 &&
            a.module_links[0].ar_wire_bytes>0 &&
            b.module_links[1].r_wire_bytes>0,
            "worker request/native/link evidence mapping incomplete");
    std::cout << "worker_horizon stacks=2 consumed=2"
              << " final_h=" << last_h
              << " pid0=" << a.process_id << " pid1=" << b.process_id
              << " native0=" << a.native_commands
              << " native1=" << b.native_commands
              << " rss0_kib=" << a.max_rss_kib
              << " rss1_kib=" << b.max_rss_kib
              << " lpa0=" << proof0[0].proof.observed_logical_page
              << " lpa1=" << proof1[0].proof.observed_logical_page
              << " sense0=" << proof0[0].proof.first_media_begin_ns
              << ".." << proof0[0].proof.last_media_end_ns
              << " sense1=" << proof1[0].proof.first_media_begin_ns
              << ".." << proof1[0].proof.last_media_end_ns << '\n';
}

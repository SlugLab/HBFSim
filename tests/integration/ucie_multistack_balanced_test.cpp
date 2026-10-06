#include <hbfsim/ucie/multistack_frontend.hpp>

#include <iostream>
#include <set>
#include <stdexcept>

namespace {
void require(bool value,const char* message)
{ if (!value) throw std::runtime_error(message); }
}

int main(int argc,char** argv)
{
    if (argc!=2) return 2;
    using namespace hbfsim::ucie;
    try {
        constexpr std::uint64_t stack_bytes=4ULL<<20;
        MultistackFrontend front(
            "configs/profiles/ucie/hbf-stage3-balanced-top.json",argv[1]);
        for (std::uint64_t stack=0;stack<4;++stack) {
            const auto address=stack*stack_bytes;
            front.add_backing({stack+1,address,64,stack+101,address,
                               1,7,true});
            require(front.try_submit({.request_id=stack+1,.arrival_ns=0,
                .address=address,.bytes=64,.endpoint_id=7,
                .axi_id=static_cast<std::uint32_t>(stack+1)}),
                "balanced stack read was rejected");
        }
        std::uint64_t final_h=0;
        bool all=false;
        for (std::uint64_t h=0;h<100000;++h) {
            front.advance_until(h);
            if (front.ready_ids().size()==4) {
                for (std::uint64_t id=1;id<=4;++id) {
                    const auto completion=front.consume_completion(id);
                    require(completion.result==DeviceResult::Ready &&
                            completion.child_count==1 &&
                            completion.axi_payload_bytes==64,
                            "balanced stack completion failed");
                }
                final_h=h;
                all=true;
                break;
            }
        }
        require(all,"four balanced stacks did not complete");
        const auto accounting=front.accounting();
        require(accounting.stacks.size()==4 &&
                accounting.host.original_bytes==256 &&
                accounting.host.unique_canonical_bytes==256 &&
                accounting.host.unique_canonical_exact &&
                accounting.host.accepted_axi_payload_bytes==256 &&
                accounting.host.axi_payload_bytes==256 &&
                accounting.media_submit_bytes==16384 &&
                accounting.native_commands==4 &&
                accounting.upstream_ar_wire_bytes>0 &&
                accounting.upstream_r_wire_bytes>0 &&
                accounting.worker_ar_wire_bytes>0 &&
                accounting.worker_r_wire_bytes>0,
                "balanced stack accounting or real shared link failed");
        std::set<std::uint64_t> pids;
        for (std::uint32_t stack=0;stack<4;++stack) {
            const auto& stats=accounting.stacks.at(stack);
            const auto proof=front.worker(stack).native_evidence(0);
            require(stats.native_commands==1 &&
                    stats.media_submit_bytes==4096 && proof.size()==1 &&
                    proof[0].stack_id==stack &&
                    proof[0].proof.logical_page_match &&
                    proof[0].proof.observed_logical_page==0,
                    "stack lacked its own native NAND command/proof");
            pids.insert(stats.process_id);
            std::cout << "balanced stack=" << stack
                      << " pid=" << stats.process_id
                      << " native=" << stats.native_commands
                      << " lpa=" << proof[0].proof.observed_logical_page
                      << " rss_kib=" << stats.max_rss_kib << '\n';
        }
        require(pids.size()==4,"workers did not have distinct process IDs");
        std::cout << "balanced final_h=" << final_h
                  << " original=256 unique=256 axi=256 media=16384"
                  << " native=4 upstream_ar_wire="
                  << accounting.upstream_ar_wire_bytes
                  << " upstream_r_wire=" << accounting.upstream_r_wire_bytes
                  << " worker_ar_wire=" << accounting.worker_ar_wire_bytes
                  << " worker_r_wire=" << accounting.worker_r_wire_bytes
                  << " unique_exact=1\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

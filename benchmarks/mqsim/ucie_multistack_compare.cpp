#include <hbfsim/ucie/multistack_frontend.hpp>

#include <algorithm>
#include <array>
#include <iostream>
#include <stdexcept>

namespace {
using namespace hbfsim::ucie;

void require(bool value,const char* message)
{ if (!value) throw std::runtime_error(message); }

void run(const char* worker,bool shared,bool hotspot)
{
    const auto config=shared?
        "configs/profiles/ucie/hbf-stage3-balanced-top.json":
        "configs/profiles/ucie/hbf-stage3-balanced-independent-top.json";
    constexpr std::uint64_t stack_bytes=4ULL<<20;
    MultistackFrontend front(config,worker);
    for (std::uint64_t index=0;index<4;++index) {
        const auto address=hotspot?index*4096:index*stack_bytes;
        front.add_backing({index+1,address,64,index+101,address,1,7,true});
        require(front.try_submit({.request_id=index+1,.arrival_ns=0,
            .address=address,.bytes=64,.endpoint_id=7,
            .axi_id=static_cast<std::uint32_t>(index+1)}),
            "comparison input rejected");
    }
    std::size_t consumed=0;
    std::uint64_t final_h=0;
    for (std::uint64_t h=0;h<100000 && consumed<4;++h) {
        front.advance_until(h);
        const auto ready=front.ready_ids();
        for (const auto id:ready) {
            const auto result=front.consume_completion(id);
            require(result.result==DeviceResult::Ready,
                    "comparison read did not complete Ready");
            ++consumed;
        }
        final_h=h;
    }
    require(consumed==4,"comparison did not finish all reads");
    const auto stats=front.accounting();
    require(stats.host.original_bytes==256 &&
            stats.host.unique_canonical_bytes==256 &&
            stats.host.unique_canonical_exact &&
            stats.host.accepted_axi_payload_bytes==256 &&
            stats.host.axi_payload_bytes==256 &&
            stats.native_commands==4 &&
            stats.media_submit_bytes==16384,
            "comparison did not conserve original/AXI/media work");
    std::array<std::uint64_t,4> commands{};
    std::array<std::uint64_t,4> first_media{};
    std::array<std::uint64_t,4> last_media{};
    for (std::uint32_t stack=0;stack<4;++stack) {
        commands[stack]=stats.stacks.at(stack).native_commands;
        const auto records=front.worker(stack).native_evidence(0);
        if (hotspot && stack==0) {
            require(records.size()==4,"hotspot lacked four actual senses");
            for (std::size_t i=1;i<records.size();++i)
                require(records[i-1].proof.last_media_end_ns<=
                            records[i].proof.first_media_begin_ns,
                        "same-bank senses overlapped");
        }
        if (!records.empty()) {
            first_media[stack]=records.front().proof.first_media_begin_ns;
            last_media[stack]=records.back().proof.last_media_end_ns;
        }
    }
    if (hotspot)
        require(commands==std::array<std::uint64_t,4>{4,0,0,0},
                "hotspot work escaped its stack");
    else {
        require(commands==std::array<std::uint64_t,4>{1,1,1,1},
                "balanced work failed to use four native stacks");
        const auto latest_begin=*std::max_element(first_media.begin(),
                                                  first_media.end());
        const auto earliest_end=*std::min_element(last_media.begin(),
                                                  last_media.end());
        require(latest_begin<earliest_end,
                "balanced stack media intervals did not overlap");
    }
    if (shared)
        require(stats.upstream_ar_wire_bytes>0 &&
                stats.upstream_r_wire_bytes>0,
                "shared mode did not use real upstream link");
    else require(stats.upstream_ar_wire_bytes==0 &&
                 stats.upstream_r_wire_bytes==0,
                 "independent mode counted a nonexistent shared link");
    std::cout << "compare topology=" << (shared?"shared":"independent")
              << " placement=" << (hotspot?"hotspot":"balanced")
              << " final_h=" << final_h
              << " native=" << commands[0] << ',' << commands[1] << ','
              << commands[2] << ',' << commands[3]
              << " original=256 unique=256 axi=256 media=16384"
              << " upstream_ar_wire=" << stats.upstream_ar_wire_bytes
              << " upstream_r_wire=" << stats.upstream_r_wire_bytes
              << " worker_ar_wire=" << stats.worker_ar_wire_bytes
              << " worker_r_wire=" << stats.worker_r_wire_bytes
              << " B_total=512000000000\n";
}
}

int main(int argc,char** argv)
{
    if (argc!=2) return 2;
    try {
        run(argv[1],false,false);
        run(argv[1],true,false);
        run(argv[1],false,true);
        run(argv[1],true,true);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

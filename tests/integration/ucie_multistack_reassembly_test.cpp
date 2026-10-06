#include <hbfsim/ucie/multistack_frontend.hpp>

#include <cstdint>
#include <iostream>
#include <stdexcept>

namespace {
using namespace hbfsim::ucie;
constexpr std::uint64_t mib=1ULL<<20;
constexpr const char* profile=
    "configs/profiles/ucie/hbf-stage3-cache-stack.json";

void require(bool condition,const char* message)
{ if (!condition) throw std::runtime_error(message); }

std::uint64_t wait_ready(MultistackFrontend& front,std::uint64_t id,
                         std::uint64_t maximum)
{
    for (std::uint64_t h=0;h<=maximum;++h) {
        front.advance_until(h);
        if (front.peek_completion(id)) return h;
    }
    throw std::runtime_error("bounded multistack reassembly did not complete");
}

void four_kib_with_two_worker_slots(const char* worker)
{
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile);
    front.add_backing({1,0,4096,101,0,1,7,true});
    require(front.try_submit({.request_id=1,.arrival_ns=0,.address=0,
                .bytes=4096,.endpoint_id=7,.axi_id=1}),
            "4 KiB parent was rejected by two worker admission slots");
    const auto h=wait_ready(front,1,100'000);
    const auto ready=front.peek_completion(1);
    require(ready && ready->result==DeviceResult::Ready &&
            ready->child_count==64 && ready->original_bytes==4096 &&
            ready->axi_payload_bytes==4096 &&
            front.worker(0).stats().accepted==64,
            "4 KiB parent did not incrementally dispatch and assemble 64 R");
    (void)front.consume_completion(1);
    std::cout << "reassembly_4k h=" << h
              << " native=" << front.worker(0).stats().native_commands
              << " child=64 original=4096 axi=4096\n";
}

void three_pages_one_bank(const char* worker)
{
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile,
                             true);
    constexpr std::uint32_t bytes=2*4096+1;
    front.add_backing({1,0,bytes,201,0,1,7,true});
    require(front.try_submit({.request_id=2,.arrival_ns=0,.address=0,
                .bytes=bytes,.endpoint_id=7,.axi_id=2}),
            "three-page parent was not atomically admitted");
    const auto h=wait_ready(front,2,120'000);
    const auto ready=front.peek_completion(2);
    require(ready && ready->result==DeviceResult::Ready &&
            ready->child_count==129 && ready->original_bytes==bytes &&
            ready->axi_payload_bytes==129*64 &&
            front.worker(0).stats().accepted==129,
            "three-page parent deadlocked behind two real bank slots");
    const auto native=front.worker(0).native_evidence(0,16);
    require(native.size()>=3 && front.worker(0).stats().native_commands>=3,
            "three distinct same-bank pages lacked native commands");
    for (std::size_t i=1;i<native.size();++i)
        require(native[i-1].proof.last_media_end_ns<=
                native[i].proof.first_media_begin_ns,
                "same-bank native media sense intervals overlapped");
    const auto tail=front.worker(0).request_evidence(128,16);
    require(tail.size()==1 && tail[0].r_delivered_ns<ready->ready_ns &&
            front.shared_upstream_link().counters(
                Direction::Request).wire_bytes>0 &&
            front.shared_upstream_link().counters(
                Direction::Return).wire_bytes>0,
            "shared upstream did not actually serialize a later R delivery");
    (void)front.consume_completion(2);
    std::cout << "reassembly_three_pages h=" << h
              << " native=" << front.worker(0).stats().native_commands
              << " child=129 original=" << bytes
              << " axi=" << 129*64 << '\n';
}
}

int main(int argc,char** argv)
{
    if (argc!=2) throw std::invalid_argument("worker executable required");
    four_kib_with_two_worker_slots(argv[1]);
    three_pages_one_bank(argv[1]);
}

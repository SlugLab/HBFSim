#include <hbfsim/ucie/multistack_frontend.hpp>

#include <iostream>
#include <stdexcept>

namespace {
void require(bool value,const char* message)
{ if (!value) throw std::runtime_error(message); }

void exact_short_aliases(const char* worker)
{
    using namespace hbfsim::ucie;
    constexpr std::uint64_t mib=1ULL<<20;
    constexpr std::uint64_t virtual_base=1ULL<<60;
    const char* profile="configs/profiles/ucie/hbf-stage3-small-stack.json";
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile);
    front.add_backing({1,virtual_base+3,8,101,3,1,7,true});
    front.add_backing({2,virtual_base+20,8,102,20,1,7,true});
    bool rejected=false;
    try {
        (void)front.try_submit({.request_id=9,.arrival_ns=0,
            .address=virtual_base+3,.bytes=9,.endpoint_id=7,.axi_id=9});
    } catch (const std::invalid_argument&) { rejected=true; }
    require(rejected && front.counters().accepted==0,
            "AXI padding granted caller one unregistered original byte");
    require(front.try_submit({.request_id=10,.arrival_ns=0,
                .address=virtual_base+3,.bytes=8,.endpoint_id=7,.axi_id=10}) &&
            front.try_submit({.request_id=11,.arrival_ns=0,
                .address=virtual_base+20,.bytes=8,.endpoint_id=7,.axi_id=11}),
            "two independent short objects sharing one 64B packet failed");
    bool both=false;
    for (std::uint64_t h=0;h<100'000;++h) {
        front.advance_until(h);
        if (front.ready_ids().size()==2) {
            both=true;
            require(front.peek_completion(10)->original_bytes==8 &&
                    front.peek_completion(11)->original_bytes==8 &&
                    front.peek_completion(10)->axi_payload_bytes==64 &&
                    front.peek_completion(11)->axi_payload_bytes==64,
                    "short original bytes were confused with packet bytes");
            (void)front.consume_completion(10);
            (void)front.consume_completion(11);
            break;
        }
    }
    const auto requests=front.worker(0).request_evidence(0);
    require(both && requests.size()==2 &&
            requests[0].canonical_id!=requests[1].canonical_id &&
            front.worker(0).stats().native_commands==2,
            "short objects were silently merged under one canonical identity");
    const auto accounting=front.accounting();
    require(accounting.host.original_bytes==16 &&
            accounting.host.unique_canonical_bytes==16 &&
            accounting.host.unique_canonical_exact &&
            accounting.host.accepted_axi_payload_bytes==128 &&
            accounting.host.axi_payload_bytes==128 &&
            accounting.media_submit_bytes==8192 &&
            accounting.native_commands==2 &&
            accounting.worker_ar_wire_bytes>0 &&
            accounting.worker_r_wire_bytes>0,
            "original/unique/AXI/wire/media accounting was conflated");
    require(front.try_submit({.request_id=12,
                .arrival_ns=front.current_time_ns(),
                .address=virtual_base+3,.bytes=8,.endpoint_id=7,.axi_id=12}),
            "repeat original span was rejected");
    bool repeated=false;
    for (std::uint64_t h=front.current_time_ns();h<100'000;++h) {
        front.advance_until(h);
        if (front.peek_completion(12)) {
            (void)front.consume_completion(12);
            repeated=true;
            break;
        }
    }
    require(repeated && front.counters().original_bytes==24 &&
            front.counters().unique_canonical_bytes==16 &&
            front.counters().accepted_axi_payload_bytes==192,
            "repeated original bytes were double-counted as unique");
}

void short_cross_stack_alias(const char* worker)
{
    using namespace hbfsim::ucie;
    constexpr std::uint64_t mib=1ULL<<20;
    constexpr std::uint64_t virtual_base=1ULL<<60;
    const char* profile="configs/profiles/ucie/hbf-stage3-small-stack.json";
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile);
    front.add_backing({1,virtual_base+4096,8,201,8*mib-4,1,7,true});
    require(front.try_submit({.request_id=21,.arrival_ns=0,
        .address=virtual_base+4096,.bytes=8,.endpoint_id=7,.axi_id=21}),
        "short canonical span crossing stacks was not admitted");
    bool ready=false;
    for (std::uint64_t h=0;h<100'000;++h) {
        front.advance_until(h);
        if (const auto result=front.peek_completion(21)) {
            require(result->result==DeviceResult::Ready &&
                    result->original_bytes==8 &&
                    result->axi_payload_bytes==128 &&
                    result->child_count==2,
                    "cross-stack short alias byte accounting failed");
            (void)front.consume_completion(21);
            ready=true;
            break;
        }
    }
    require(ready && front.worker(0).stats().native_commands==1 &&
            front.worker(1).stats().native_commands==1,
            "canonical cross-stack alias lacked two real media reads");
    require(front.counters().original_bytes==8 &&
            front.counters().unique_canonical_bytes==8 &&
            front.counters().accepted_axi_payload_bytes==128,
            "cross-stack original/unique/AXI accounting diverged");
}

void generation_retire_inflight(const char* worker)
{
    using namespace hbfsim::ucie;
    constexpr std::uint64_t mib=1ULL<<20;
    const char* profile="configs/profiles/ucie/hbf-stage3-small-stack.json";
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile);
    front.add_backing({1,0,64,301,0,1,7,true});
    require(front.try_submit({.request_id=31,.arrival_ns=0,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=31}),
            "old generation was not admitted");
    front.retire_backing_generation(301,1,7);
    bool rejected=false;
    try {
        (void)front.try_submit({.request_id=32,.arrival_ns=0,
            .address=0,.bytes=64,.endpoint_id=7,.axi_id=32});
    } catch (const std::invalid_argument&) { rejected=true; }
    require(rejected,"retired generation remained available to new callers");
    front.add_backing({2,0,64,301,0,2,7,true});
    require(front.try_submit({.request_id=33,.arrival_ns=0,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=33}),
            "new generation could not coexist with old in-flight completion");
    bool both=false;
    for (std::uint64_t h=0;h<100'000;++h) {
        front.advance_until(h);
        if (front.ready_ids().size()==2) {
            require(front.peek_completion(31)->result==DeviceResult::Ready &&
                    front.peek_completion(33)->result==DeviceResult::Ready,
                    "generation handoff misreported one completion");
            (void)front.consume_completion(31);
            (void)front.consume_completion(33);
            both=true;
            break;
        }
    }
    const auto requests=front.worker(0).request_evidence(0);
    require(both && requests.size()==2 &&
            requests[0].generation==1 && requests[1].generation==2 &&
            front.worker(0).stats().native_commands==2,
            "old callback joined/notified new generation");
}
}

int main(int argc,char** argv)
{
    using namespace hbfsim::ucie;
    if (argc!=2) throw std::invalid_argument("worker executable required");
    const char* profile="configs/profiles/ucie/hbf-stage3-small-stack.json";
    constexpr std::uint64_t mib=1ULL<<20;
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),argv[1],
                             profile,false,128);
    front.add_backing({1,0,16*mib,1,0,1,7,true});
    require(front.try_submit({.request_id=1,.arrival_ns=0,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=1}) &&
            front.try_submit({.request_id=2,.arrival_ns=0,
                .address=12*mib,.bytes=64,.endpoint_id=7,.axi_id=1}),
            "two stack reads were not accepted");
    std::uint64_t first_ready=0;
    for (std::uint64_t h=0;h<100'000;++h) {
        front.advance_until(h);
        if (front.ready_ids().size()==2) {
            first_ready=h;
            break;
        }
    }
    require(first_ready!=0 && front.current_time_ns()==first_ready,
            "parent did not reach two real worker completions");
    const auto before=front.ready_ids();
    require(before.size()==2 && before[0]==1 && before[1]==2,
            "parent completion order changed despite common input time");
    require(front.counters().reassembly_reserved_bytes==128 &&
            front.counters().peak_reassembly_reserved_bytes==128,
            "slow caller did not retain its finite host reassembly capacity");
    require(!front.try_submit({.request_id=3,.arrival_ns=first_ready,
                .address=4*mib,.bytes=64,.endpoint_id=7,.axi_id=3}) &&
            front.counters().rejected_reassembly==1,
            "third parent bypassed full host reassembly reservation");
    const auto a=front.consume_completion(1);
    require(front.counters().reassembly_reserved_bytes==64 &&
            front.try_submit({.request_id=3,.arrival_ns=first_ready,
                .address=4*mib,.bytes=64,.endpoint_id=7,.axi_id=3}),
            "same-horizon caller consume failed to release host capacity");
    const auto b=front.consume_completion(2);
    require(a.result==DeviceResult::Ready &&
            b.result==DeviceResult::Ready &&
            a.original_bytes==64 && b.original_bytes==64 &&
            a.axi_payload_bytes==64 && b.axi_payload_bytes==64 &&
            a.consumed_ns==first_ready &&
            b.consumed_ns==first_ready &&
            front.counters().caller_outstanding==1,
            "parent consumed worker result before the public open horizon");
    front.advance_until(first_ready);
    const auto s0=front.worker(0).stats();
    const auto s1=front.worker(1).stats();
    require(s0.process_id && s1.process_id &&
            s0.process_id!=s1.process_id &&
            s0.native_commands==1 && s1.native_commands==1 &&
            s0.media_submit_bytes==4096 &&
            s1.media_submit_bytes==4096,
            "parent did not own two independent real MQSim workers");
    bool third_ready=false;
    for (std::uint64_t h=first_ready+1;h<100'000;++h) {
        front.advance_until(h);
        if (front.peek_completion(3)) {
            const auto third=front.consume_completion(3);
            require(third.result==DeviceResult::Ready &&
                    third.consumed_ns==h,
                    "re-admitted parent failed exact same-horizon consumption");
            third_ready=true;
            break;
        }
    }
    require(third_ready && front.counters().caller_outstanding==0 &&
            front.counters().reassembly_reserved_bytes==0,
            "slow-caller backpressure case did not fully drain");
    std::cout << "multistack_frontend stacks=2 consumed=2 h=" << first_ready
              << " pid0=" << s0.process_id << " pid1=" << s1.process_id
              << " native0=" << s0.native_commands
              << " native1=" << s1.native_commands << '\n';
    exact_short_aliases(argv[1]);
    short_cross_stack_alias(argv[1]);
    generation_retire_inflight(argv[1]);
}

#include <cstdio>
#if !__has_include(<hbfsim/timing_future_abi.hpp>)
int main() { std::fputs("FAIL: shared host/device future transitions are absent\n",stderr); return 1; }
#else
#include <hbfsim/timing_future_abi.hpp>
#include <cstdlib>
#include <initializer_list>
using namespace hbfsim::timing_future;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while(false)
DeviceTimingFutureV1 token() { return {0x1000,7,100,300,1000,0x4000,11,State::Issued,0}; }
TimingFutureLaneMetadataV1 metadata() { return {1,32,9,4,0x5,0,11}; }
int main()
{
    {
        auto forged=token();auto meta=metadata();forged.state=State::Native;
        const auto native=consume_state(forged,meta,0,9,4,0x1000,7,100,0,WaitKind::Dependency);
        forged=token();forged.state=State::ModelReady;
        const auto early=consume_state(forged,meta,0,9,4,0x1000,7,100,0,WaitKind::Dependency);
        if(native.status!=kUnsupported) std::fputs("FAIL: Native accepted nonzero modeled reservation before deadline\n",stderr);
        if(early.status!=kUnsupported) std::fputs("FAIL: forged ModelReady consumed before readiness\n",stderr);
        CHECK(native.status==kUnsupported && early.status==kUnsupported);
    }
    for(unsigned bad : {kPending,kReady,999U}) {
        auto failed=token();failed.state=State::TerminalError;failed.status=bad;
        CHECK(poll_state(failed,metadata(),0,9,4,0x1000,7,350,0).status==kUnsupported);
    }
    {
        auto invalid=token();invalid.status=kReady;
        CHECK(poll_state(invalid,metadata(),0,9,4,0x1000,7,200,0).status==kUnsupported);
        invalid=token();
        CHECK(poll_state(invalid,metadata(),0,9,4,0x1000,7,200,kReady).status==kUnsupported);
    }
    CHECK(derive_capabilities(1,0,1,10,20,100,1000).bits==kFastScalarTiming);
    for (unsigned mode: {0U,2U,3U}) CHECK(derive_capabilities(mode,0,1,10,20,100,1000).bits==0);
    CHECK(derive_capabilities(1,1,1,10,20,100,1000).bits==0);
    CHECK(derive_capabilities(1,0,2,10,20,100,1000).bits==0);
    CHECK(derive_capabilities(1,0,1,0,20,100,1000).bits==0);
    CHECK(derive_capabilities(1,0,1,10,20,100,0).bits==0);
    auto f=token(); auto m=metadata();
    CHECK(poll_state(f,m,0,9,4,0x1000,7,299,0).state==State::Issued);
    CHECK(poll_state(f,m,0,9,4,0x1000,7,300,0).events==kBecameReady);
    CHECK(poll_state(f,m,0,9,4,0x1000,7,350,0).events==0);
    CHECK(consume_state(f,m,0,9,4,0x1000,7,350,0,WaitKind::Dependency).events==kConsumed);
    CHECK(consume_state(f,m,0,9,4,0x1000,7,350,0,WaitKind::Ordering).events==0);
    CHECK(f.state==State::Consumed);
    f=token(); f.ready_ns=900; f.deadline_ns=400;
    CHECK(poll_state(f,m,0,9,4,0x1000,7,950,0).status==kTimeout);
    CHECK(poll_state(f,m,0,9,4,0x1000,7,1000,0).events==0);
    f=token(); f.deadline_ns=400;
    CHECK(poll_state(f,m,0,9,4,0x1000,7,950,0).state==State::ModelReady);
    f=token(); CHECK(poll_state(f,m,0,9,4,0x1000,8,300,0).status==kUnsupported);
    f=token(); CHECK(poll_state(f,m,0,9,4,0x2000,7,300,0).status==kUnsupported);
    f=token(); CHECK(poll_state(f,m,0,9,4,0x1000,7,300,kDaemonLost).status==kDaemonLost);
    for (unsigned change=0;change<8;++change) {
        f=token(); m=metadata();
        switch(change) { case 0:m.abi_version=2;break;case 1:m.struct_bytes=24;break;
        case 2:m.reservation_id=12;break;case 3:m.instruction_id=10;break;case 4:m.bytes=8;break;
        case 5:m.group_leader=2;break;case 6:m.group_mask=4;break;case 7:m.group_mask=0;break; }
        const auto invalid=poll_state(f,m,0,9,4,0x1000,7,300,0);
        CHECK(invalid.status==kUnsupported);
        CHECK(invalid.events==0); // An unvalidated binding cannot release shared pending work.
    }
    f=token(); m=metadata(); CHECK(poll_state(f,m,1,9,4,0x1000,7,300,0).status==kUnsupported);
    CHECK(can_issue(State::Unissued) && can_issue(State::Consumed));
    CHECK(!can_issue(State::Issued) && !can_issue(State::ModelReady) && !can_issue(State::TerminalError));
    CHECK(scalar_reservation(100,50,20,10,1000).ready_ns==120);
    CHECK(scalar_reservation(100,500,20,10,1000).ready_ns==510);
    CHECK(!scalar_reservation(UINT64_MAX-5,0,20,10,1000).valid);
    CHECK(!scalar_reservation(100,0,20,10,0).valid);
    // Same range/page group, including both halves of page identity.
    std::uint32_t ranges[32]{}; std::uint64_t pages[32]{};
    for(unsigned i=0;i<32;++i) { ranges[i]=4;pages[i]=5; }
    CHECK(page_group_mask(0xffffffffU,ranges,pages,3)==0xffffffffU);
    ranges[4]=5;pages[5]=(1ULL<<32)|5;
    CHECK(page_group_mask(0x3f,ranges,pages,0)==0xf);
    CHECK(page_group_mask(0x3f,ranges,pages,4)==0x10);
    CHECK(page_group_mask(0x3f,ranges,pages,5)==0x20);
    CHECK(page_group_mask(0x2,ranges,pages,0)==0);
    unsigned issued=0,consumed=0,drained=0,pending=0,errors=0;
    for(unsigned predicates=0;predicates<8;++predicates) {
        f={};m={};bool valid=false;
        if(predicates&1) {f=token();m=metadata();valid=true;++issued;++pending;}
        if((predicates&2)&&valid) {auto r=consume_state(f,m,0,9,4,0x1000,7,350,0,WaitKind::Dependency);CHECK(r.events&kConsumed);++consumed;--pending;valid=false;}
        if((predicates&4)&&valid) {auto r=consume_state(f,m,0,9,4,0x1000,7,350,0,WaitKind::Ordering);CHECK(r.events&kDrained);++drained;--pending;valid=false;}
        if(valid) {auto r=consume_state(f,m,0,9,4,0x1000,7,350,0,WaitKind::Dependency);CHECK(r.events&kConsumed);++consumed;--pending;}
        CHECK(issued==consumed+drained+errors+pending);
    }
    CHECK(pending==0 && issued==4);
    std::puts("PASS: modeled readiness, terminal errors, metadata, grouping and executed-path conservation");
}
#endif

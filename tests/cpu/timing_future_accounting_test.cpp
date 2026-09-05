#include <hbfsim/timing_future_abi.hpp>
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstdlib>
using namespace hbfsim::timing_future;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while(false)

// Run every completion order for a sparse issue-time group. Wait-time lanes
// may have diverged; this exercises the same lane-local accounting as CUDA.
int main()
{
    // Exercise the actual lane transition functions behind every traced path.
    // One successful modeled issue record, at most one ready record and one
    // terminal record. Pending polls and repeated terminal operations are free.
    for(bool separate_ready:{false,true})for(auto error:{0U,kTimeout,kUnsupported,kDaemonLost})
    for(auto kind:{WaitKind::Dependency,WaitKind::Ordering}) {
        DeviceTimingFutureV1 f{0x1000,7,100,300,1000,0x4000,11,State::Issued,kPending};
        TimingFutureLaneMetadataV1 m{1,32,9,4,1,0,11};
        unsigned records=1;
        for(unsigned now=100;now<300;++now) {
            const auto t=poll_state(f,m,0,9,4,0x1000,7,now,0);
            CHECK(t.status==kPending && t.events==0);
        }
        if(separate_ready)records+=bool(poll_state(f,m,0,9,4,0x1000,7,300,0).events);
        records+=bool(consume_state(f,m,0,9,4,0x1000,7,350,error,kind).events);
        for(unsigned repeat=0;repeat<100;++repeat)
            records+=bool(consume_state(f,m,0,9,4,0x1000,7,350,0,kind).events);
        CHECK(records<=kMaximumRecordsPerProducer);
        if(!error && separate_ready)CHECK(records==kMaximumRecordsPerProducer);
    }
    for(auto kind : {WaitKind::Dependency,WaitKind::Ordering}) {
        DeviceTimingFutureV1 original{0x1000,7,100,300,1000,0x4000,11,State::Issued,kPending};
        TimingFutureLaneMetadataV1 metadata{1,32,9,4,0x5,0,11};
        auto candidate=original;
        auto tentative=consume_state(candidate,metadata,0,9,4,0x1000,7,350,0,kind);
        const auto rejected=commit_transition(original,candidate,tentative,false);
        const auto delta=accounting_delta(rejected.events,0,0);
        CHECK(original.state==State::TerminalError && original.status==kUnsupported);
        CHECK(delta.terminal_error==1 && delta.terminal==1 && delta.consumed==0 &&
            delta.drained==0 && delta.groups_completed==0 && delta.model_ready==0);
    }
    std::array<unsigned,3> order{0,2,7};
    do {
        for (unsigned errors=0;errors<8;++errors) {
            Counters c{};c.issued=3;c.pending=3;c.groups_issued=1;
            auto account=[&](const Transition& t,unsigned lane) {
                const auto d=accounting_delta(t.events,lane,0);
                c.model_ready+=d.model_ready;c.consumed+=d.consumed;
                c.drained+=d.drained;c.terminal_error+=d.terminal_error;
                c.groups_completed+=d.groups_completed;
                CHECK(c.pending>=d.terminal);c.pending-=d.terminal;
                CHECK(c.issued==c.consumed+c.drained+c.terminal_error+c.pending);
            };
            for (auto lane:order) {
                const unsigned index=lane==0?0:lane==2?1:2;
                DeviceTimingFutureV1 f{0x1000,7,100,300,1000,0x4000,11,State::Issued,0};
                TimingFutureLaneMetadataV1 m{1,32,9,4,0x85,0,11};
                const auto error=errors&(1U<<index)?kDaemonLost:0;
                auto t=poll_state(f,m,lane,9,4,0x1000,7,350,error);account(t,lane);
                t=consume_state(f,m,lane,9,4,0x1000,7,350,0,
                    lane==2?WaitKind::Ordering:WaitKind::Dependency);account(t,lane);
                // Repeated polls/drains do not repeat terminal/group accounting.
                account(consume_state(f,m,lane,9,4,0x1000,7,350,0,WaitKind::Ordering),lane);
            }
            CHECK(c.pending==0);
            CHECK(c.groups_completed==((errors&1U)?0:1));
        }
    } while(std::next_permutation(order.begin(),order.end()));
    DeviceTimingFutureV1 native{0x1000,7,100,100,1000,0x8000,0,State::Native,kReady};
    TimingFutureLaneMetadataV1 metadata{1,32,9,4,0,32,0};
    auto t=consume_state(native,metadata,13,9,4,0x1000,7,100,0,WaitKind::Dependency);
    CHECK(t.state==State::Consumed && t.status==kReady && t.events==0);
    CHECK(accounting_delta(t.events,13,32).terminal==0);
    auto config=ModuleConfig{};config.enabled=1;config.control_alias=1;config.control_generation=1;
    config.trace_address=0x1000;config.trace_capacity=10;
    CHECK(valid_trace_span(config));
    config.trace_address=UINT64_MAX-63;CHECK(!valid_trace_span(config));
    config.trace_address=0x1000;config.trace_capacity=UINT64_MAX;CHECK(!valid_trace_span(config));
    config.trace_capacity=10;config.enabled=0;CHECK(!valid_trace_span(config));
    std::puts("PASS: divergent group completion, disjoint terminal conservation and trace bounds");
}

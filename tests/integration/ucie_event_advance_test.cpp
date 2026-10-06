#include <hbfsim/ucie/multistack_frontend.hpp>

#include <chrono>
#include <cerrno>
#include <cstdint>
#include <iostream>
#include <signal.h>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

namespace {
using namespace hbfsim::ucie;
using Clock=std::chrono::steady_clock;
constexpr std::uint64_t mib=1ULL<<20;
constexpr const char* profile="configs/profiles/ucie/hbf-stage3-small-stack.json";

void require(bool condition,const char* message)
{ if (!condition) throw std::runtime_error(message); }

struct Record {
    std::uint64_t id{};
    std::uint64_t time{};
    DeviceResult result{};
    std::uint32_t original{};
    std::uint32_t axi{};
    std::uint32_t children{};
    bool operator==(const Record&) const=default;
};
struct Run {
    std::vector<Record> delivered;
    MultistackAccounting accounting;
    std::uint64_t end_ns{};
    std::uint64_t wall_ms{};
    std::uint64_t startup_ms{};
    std::uint64_t service_ms{};
    std::uint64_t advances{};
    std::uint64_t peeks{};
    std::uint64_t closes{};
    std::uint64_t combined{};
    std::uint64_t ipc{};
    std::uint64_t reserves{};
    std::uint64_t submits{};
    std::uint64_t consumes{};
    std::uint64_t is_active{};
    std::uint32_t upstream_ar_credit{};
    std::uint32_t upstream_r_credit{};
};

Run finish(MultistackFrontend& front,std::vector<Record> delivered,
           Clock::time_point start,Clock::time_point created,bool shared)
{
    Run run;
    run.delivered=std::move(delivered);
    run.end_ns=front.current_time_ns();
    run.accounting=front.accounting();
    if (shared) {
        run.upstream_ar_credit=front.shared_upstream_link().credits(
            Direction::Request);
        run.upstream_r_credit=front.shared_upstream_link().credits(
            Direction::Return);
    }
    for (const auto& stack:run.accounting.stacks) {
        run.advances+=stack.advance_commands;
        run.peeks+=stack.peek_commands;
        run.closes+=stack.close_commands;
        run.combined+=stack.close_advance_commands;
        run.ipc+=stack.ipc_commands;
        run.reserves+=stack.reserve_commands;
        run.submits+=stack.submit_commands;
        run.consumes+=stack.consume_commands;
        run.is_active+=stack.is_active_commands;
    }
    const auto done=Clock::now();
    run.wall_ms=std::chrono::duration_cast<std::chrono::milliseconds>(
        done-start).count();
    run.startup_ms=std::chrono::duration_cast<std::chrono::milliseconds>(
        created-start).count();
    run.service_ms=std::chrono::duration_cast<std::chrono::milliseconds>(
        done-created).count();
    return run;
}

Run idle(const char* worker,MultistackAdvanceMode mode)
{
    const auto start=Clock::now();
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,
                             profile,false,256,mode);
    const auto created=Clock::now();
    front.advance_until(20'000);
    require(front.current_time_ns()==20'000 && front.ready_ids().empty(),
            "idle horizon did not advance exactly");
    return finish(front,{},start,created,false);
}

Run dense(const char* worker,MultistackAdvanceMode mode)
{
    const auto start=Clock::now();
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,
                             profile,true,256,mode);
    const auto created=Clock::now();
    front.add_backing({1,0,16*mib,101,0,1,7,true});
    require(front.try_submit({.request_id=1,.arrival_ns=0,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=5}),
            "first hot stack read rejected");
    require(front.try_submit({.request_id=2,.arrival_ns=0,
                .address=64,.bytes=64,.endpoint_id=7,.axi_id=5}),
            "second same-ID read rejected");
    require(front.try_submit({.request_id=3,.arrival_ns=0,
                .address=8*mib,.bytes=64,.endpoint_id=7,.axi_id=5}),
            "balanced stack read rejected");
    std::vector<Record> delivered;
    bool same_time_reissue=false;
    while (delivered.size()<4) {
        const auto ids=front.ready_ids();
        for (const auto id:ids) {
            const auto peek=front.peek_completion(id);
            require(peek.has_value(),"ready ID lacks completion");
            const auto completed=front.consume_completion(id);
            delivered.push_back({id,peek->ready_ns,completed.result,
                completed.original_bytes,completed.axi_payload_bytes,
                completed.child_count});
            if (id==1) {
                const auto now=front.current_time_ns();
                require(front.try_submit({.request_id=4,.arrival_ns=now,
                            .address=128,.bytes=64,.endpoint_id=7,.axi_id=5}),
                        "same-horizon credit reissue was rejected");
                same_time_reissue=true;
            }
        }
        if (delivered.size()==4) break;
        const auto next=front.next_event_ns();
        require(next && *next>front.current_time_ns() && *next<=100'000,
                "dense fixture lacks bounded next event");
        front.advance_until(*next);
    }
    const auto end=front.current_time_ns();
    require(same_time_reissue && end>0 && end<=100'000,
            "dense completion outside fixture bound");
    return finish(front,std::move(delivered),start,created,true);
}

Run cancel_backpressure(const char* worker,MultistackAdvanceMode mode)
{
    const auto start=Clock::now();
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,
                             profile,true,128,mode);
    const auto created=Clock::now();
    front.add_backing({1,0,16*mib,201,0,1,7,true});
    require(front.try_submit({.request_id=9,.arrival_ns=0,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=9}),
            "native command prerequisite read rejected");
    while (!front.peek_completion(9)) {
        const auto event=front.next_event_ns();
        require(event && *event>front.current_time_ns() && *event<100'000,
                "native command prerequisite did not finish");
        front.advance_until(*event);
    }
    std::vector<Record> delivered;
    const auto first=front.consume_completion(9);
    delivered.push_back({9,first.ready_ns,first.result,first.original_bytes,
                         first.axi_payload_bytes,first.child_count});
    require(first.result==DeviceResult::Ready &&
            front.accounting().native_commands>0,
            "cancellation fixture lacks a completed native NAND command");
    const auto arrival=front.current_time_ns();
    require(front.try_submit({.request_id=10,.arrival_ns=arrival,
                .address=8*mib-32,.bytes=64,.endpoint_id=7,.axi_id=9}),
            "cross-stack cancellation read rejected");
    require(!front.try_submit({.request_id=11,.arrival_ns=arrival,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=9}),
            "reassembly backpressure failed");
    front.advance_until(arrival+100);
    const auto in_flight=front.accounting();
    require(in_flight.worker_ar_wire_bytes>0 &&
            in_flight.stacks[0].physical_outstanding>0 &&
            in_flight.stacks[1].physical_outstanding>0,
            "cancelled read was not in flight on both stacks");
    front.cancel(10);
    for (const auto id:front.ready_ids()) {
        const auto completion=front.consume_completion(id);
        delivered.push_back({id,completion.ready_ns,completion.result,
            completion.original_bytes,completion.axi_payload_bytes,
            completion.child_count});
    }
    require(delivered.size()==2 && delivered[1].result==DeviceResult::Cancelled,
            "same-horizon cancellation was not presented");
    front.advance_until(arrival+30'000);
    const auto settled=front.accounting();
    require(front.ready_ids().empty() &&
            settled.host.caller_outstanding==0 &&
            settled.host.succeeded==1 && settled.host.cancelled==1 &&
            settled.stacks[0].physical_outstanding==0 &&
            settled.stacks[1].physical_outstanding==0,
            "cancelled in-flight read did not drain exactly once");
    return finish(front,std::move(delivered),start,created,true);
}

Run host_page_16k(const char* worker,MultistackAdvanceMode mode)
{
    const auto start=Clock::now();
    MultistackFrontend front(
        "configs/profiles/ucie/hbf-stage3-16k-small-top.json",worker,mode);
    const auto created=Clock::now();
    front.add_backing({1,0,32*mib,301,0,1,7,true});
    require(front.try_submit({.request_id=100,.arrival_ns=0,
                .address=16*mib-8192,.bytes=16384,.endpoint_id=7,
                .axi_id=100}),
            "16KiB input crossing stacks was rejected");
    while (!front.peek_completion(100)) {
        const auto event=front.next_event_ns();
        require(event && *event>front.current_time_ns() && *event<100'000,
                "16KiB input lacks a bounded next event");
        front.advance_until(*event);
    }
    const auto result=front.consume_completion(100);
    require(result.result==DeviceResult::Ready &&
            result.original_bytes==16384 &&
            result.axi_payload_bytes==16384 &&
            result.child_count==256,
            "16KiB host input did not close across 256 AXI children");
    auto run=finish(front,{{100,result.ready_ns,result.result,
        result.original_bytes,result.axi_payload_bytes,result.child_count}},
        start,created,false);
    require(run.accounting.media_submit_bytes>=16384 &&
            run.accounting.media_submit_bytes%4096==0 &&
            run.accounting.native_commands>=4,
            "16KiB host input lacked real 4KiB media commands");
    return run;
}

Run deadline(const char* worker,MultistackAdvanceMode mode)
{
    const auto start=Clock::now();
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,
                             profile,true,256,mode);
    const auto created=Clock::now();
    front.add_backing({1,0,16*mib,601,0,1,7,true});
    require(front.try_submit({.request_id=60,.arrival_ns=0,
                .deadline_ns=100,.address=0,.bytes=64,.endpoint_id=7,
                .axi_id=60}),"deadline read rejected");
    front.advance_until(100);
    require(front.peek_completion(60) &&
            front.peek_completion(60)->ready_ns==100 &&
            front.peek_completion(60)->result==DeviceResult::TimedOut,
            "parent deadline did not fire at the exact horizon");
    const auto result=front.consume_completion(60);
    front.advance_until(30'000);
    const auto state=front.accounting();
    require(state.host.caller_outstanding==0 &&
            state.stacks[0].physical_outstanding==0 &&
            state.stacks[1].physical_outstanding==0 &&
            front.ready_ids().empty(),
            "deadline cleanup did not drain exactly once");
    return finish(front,{{60,result.ready_ns,result.result,
        result.original_bytes,result.axi_payload_bytes,result.child_count}},
        start,created,true);
}

void partial_worker_failure(const char* worker)
{
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,
        profile,false,256,MultistackAdvanceMode::EventDriven);
    front.add_backing({1,0,16*mib,701,0,1,7,true});
    require(front.try_submit({.request_id=70,.arrival_ns=0,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=70}) &&
            front.try_submit({.request_id=71,.arrival_ns=0,
                .address=8*mib,.bytes=64,.endpoint_id=7,.axi_id=71}),
            "partial worker failure fixture rejected parents");
    const auto next=front.next_event_ns();
    require(next && *next>0,"partial worker failure has no next event");
    const auto workers=front.accounting().stacks;
    const auto dead=static_cast<pid_t>(workers.at(1).process_id);
    const auto peer=static_cast<pid_t>(workers.at(0).process_id);
    require(dead>0 && peer>0 && dead!=peer,
            "partial failure fixture has invalid owned worker IDs");
    require(::kill(dead,SIGKILL)==0,"could not stop exact owned second worker");
    bool strict=false;
    try { front.advance_until(*next); }
    catch (const std::exception&) { strict=true; }
    errno=0;
    const auto alive=::kill(peer,0);
    require(strict && alive<0 && errno==ESRCH,
            "partial combined failure did not poison and reap healthy worker");
    require(front.peek_completion(70) && front.peek_completion(71) &&
            front.peek_completion(70)->result==DeviceResult::BackendError &&
            front.peek_completion(71)->result==DeviceResult::BackendError,
            "partial combined failure leaked an uncommitted ready result");
    require(front.consume_completion(70).result==DeviceResult::BackendError &&
            front.consume_completion(71).result==DeviceResult::BackendError &&
            front.counters().caller_outstanding==0 &&
            front.counters().failed==2,
            "partial combined failure did not close both parents once");
    std::cout << "partial-combined-worker-failure PASS" << std::endl;
}

Run consecutive_host_pages(const char* worker,MultistackAdvanceMode mode)
{
    const auto start=Clock::now();
    MultistackFrontend front(
        "configs/profiles/ucie/hbf-stage3-16k-small-top.json",worker,mode);
    const auto created=Clock::now();
    front.add_backing({1,0,32*mib,401,0,1,7,true});
    std::vector<Record> delivered;
    for (std::uint64_t id=200;id<203;++id) {
        const auto arrival=front.current_time_ns();
        require(front.try_submit({.request_id=id,.arrival_ns=arrival,
                    .address=16*mib-8192,.bytes=16384,.endpoint_id=7,
                    .axi_id=100}),"consecutive 16KiB read was rejected");
        while (!front.peek_completion(id)) {
            const auto event=front.next_event_ns();
            require(event && *event>front.current_time_ns() &&
                    *event<100'000,"consecutive 16KiB read stalled");
            front.advance_until(*event);
        }
        const auto result=front.consume_completion(id);
        require(result.result==DeviceResult::Ready &&
                result.original_bytes==16384 && result.child_count==256,
                "consecutive 16KiB result was incomplete");
        delivered.push_back({id,result.ready_ns,result.result,
            result.original_bytes,result.axi_payload_bytes,result.child_count});
    }
    return finish(front,std::move(delivered),start,created,false);
}

Run future_host_page(const char* worker,MultistackAdvanceMode mode)
{
    const auto start=Clock::now();
    MultistackFrontend front(
        "configs/profiles/ucie/hbf-stage3-16k-small-top.json",worker,mode);
    const auto created=Clock::now();
    front.add_backing({1,0,32*mib,601,0,1,7,true});
    require(front.try_submit({.request_id=300,.arrival_ns=100,
                .deadline_ns=0,.address=0,.bytes=16384,
                .endpoint_id=7,.axi_id=1}),
            "future 16KiB parent was not accepted");
    if (mode!=MultistackAdvanceMode::NanosecondReference)
        require(front.next_event_ns()==100,
                "future parent arrival missing from global next event");
    front.advance_until(99);
    std::uint64_t worker_accepted=0;
    for (const auto& stack:front.accounting().stacks)
        worker_accepted+=stack.accepted;
    require(worker_accepted==0 && front.ready_ids().empty(),
            "future parent dispatched before arrival");
    if (mode!=MultistackAdvanceMode::NanosecondReference)
        require(front.next_event_ns()==100,
                "future arrival changed before its horizon");
    front.advance_until(100);
    worker_accepted=0;
    for (const auto& stack:front.accounting().stacks)
        worker_accepted+=stack.accepted;
    require(worker_accepted>0,"future parent did not dispatch at t=100");
    while (!front.peek_completion(300)) {
        const auto next=front.next_event_ns();
        require(next && *next>front.current_time_ns() && *next<100'000,
                "future 16KiB parent stalled");
        front.advance_until(*next);
    }
    const auto result=front.consume_completion(300);
    require(result.result==DeviceResult::Ready &&
            result.original_bytes==16384 && result.child_count==256,
            "future 16KiB parent did not complete all children");
    return finish(front,{{300,result.ready_ns,result.result,
        result.original_bytes,result.axi_payload_bytes,result.child_count}},
        start,created,false);
}

Run multiple_future_parents(const char* worker,MultistackAdvanceMode mode)
{
    const auto start=Clock::now();
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,
                             profile,false,256,mode);
    const auto created=Clock::now();
    front.add_backing({1,0,16*mib,602,0,1,7,true});
    for (const auto [id,arrival,address]:
         {std::tuple<std::uint64_t,std::uint64_t,std::uint64_t>{401,100,0},
          std::tuple<std::uint64_t,std::uint64_t,std::uint64_t>{402,250,64},
          std::tuple<std::uint64_t,std::uint64_t,std::uint64_t>{403,20'000,8*mib}})
        require(front.try_submit({.request_id=id,.arrival_ns=arrival,
                    .deadline_ns=0,.address=address,.bytes=64,
                    .endpoint_id=7,.axi_id=static_cast<std::uint32_t>(id)}),
                "future multi-parent read was not accepted");
    std::vector<Record> delivered;
    while (delivered.size()<3) {
        for (const auto id:front.ready_ids()) {
            const auto result=front.consume_completion(id);
            delivered.push_back({id,result.ready_ns,result.result,
                result.original_bytes,result.axi_payload_bytes,
                result.child_count});
        }
        if (delivered.size()==3) break;
        const auto next=front.next_event_ns();
        require(next && *next>front.current_time_ns() && *next<50'000,
                "multiple future parents lost their event horizon");
        front.advance_until(*next);
    }
    require(delivered.size()==3,"future parents did not complete once each");
    return finish(front,std::move(delivered),start,created,false);
}

void invalidation(const char* worker)
{
    const auto peeks=[](MultistackFrontend& front) {
        std::uint64_t total=0;
        for (const auto& stack:front.accounting().stacks)
            total+=stack.peek_commands;
        return total;
    };
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,
        profile,true,256,MultistackAdvanceMode::EventDriven);
    front.add_backing({1,0,16*mib,501,0,1,7,true});
    require(front.try_submit({.request_id=1,.arrival_ns=0,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=1}),
            "cache invalidation seed rejected");
    auto event=front.next_event_ns();
    require(event && *event>front.current_time_ns(),"seed has no event");
    const auto first_peeks=peeks(front);
    require(front.next_event_ns()==event && peeks(front)==first_peeks,
            "unchanged horizon did not reuse the cached peek");
    while (*event<=front.current_time_ns()+1) {
        front.advance_until(*event);
        event=front.next_event_ns();
        require(event && *event>front.current_time_ns() && *event<1000,
                "fixture did not reach a future media gap");
    }
    const auto before_early=peeks(front);
    front.advance_until(front.current_time_ns()+1);
    event=front.next_event_ns();
    require(event && peeks(front)>before_early,
            "earlier caller horizon reused stale event lookahead");
    const auto before_submit=peeks(front);
    require(front.try_submit({.request_id=2,
                .arrival_ns=front.current_time_ns(),.address=64,
                .bytes=64,.endpoint_id=7,.axi_id=2}),
            "same-horizon mutation read rejected");
    (void)front.next_event_ns();
    require(peeks(front)>before_submit,"submit did not invalidate event peek");
    const auto before_retire=peeks(front);
    front.retire_backing_generation(501,1,7);
    (void)front.next_event_ns();
    require(peeks(front)>before_retire,"retire did not invalidate event peek");
    const auto before_cancel=peeks(front);
    front.cancel(2);
    (void)front.next_event_ns();
    require(peeks(front)>before_cancel,"cancel did not invalidate event peek");
    const auto before_consume=peeks(front);
    require(front.consume_completion(2).result==DeviceResult::Cancelled,
            "cancelled completion could not be consumed");
    (void)front.next_event_ns();
    require(peeks(front)>before_consume,"consume did not invalidate event peek");
    (void)front.worker(0); // intentionally exposes a mutable worker reference
    (void)front.next_event_ns();
    const auto before_exposed=peeks(front);
    (void)front.next_event_ns();
    require(peeks(front)>before_exposed,
            "mutable worker exposure did not permanently disable cache");
    front.advance_until(100'000);
    require(front.consume_completion(1).result==DeviceResult::Ready,
            "invalidation fixture changed surviving native completion");
    std::cout << "cache-invalidation PASS" << std::endl;
}

void compare(const char* name,const Run& reference,const Run& events)
{
    require(reference.delivered==events.delivered,
            "completion result/time/order/bytes differs from 1ns oracle");
    require(reference.upstream_ar_credit==events.upstream_ar_credit &&
            reference.upstream_r_credit==events.upstream_r_credit,
            "shared upstream credit balance differs from 1ns oracle");
    require(reference.end_ns==events.end_ns,
            "final shared modeled clock differs from 1ns oracle");
    const auto& a=reference.accounting;
    const auto& b=events.accounting;
    require(a.host.accepted==b.host.accepted &&
            a.host.succeeded==b.host.succeeded &&
            a.host.failed==b.host.failed &&
            a.host.cancelled==b.host.cancelled &&
            a.host.rejected_backpressure==b.host.rejected_backpressure &&
            a.host.rejected_reassembly==b.host.rejected_reassembly &&
            a.host.original_bytes==b.host.original_bytes &&
            a.host.axi_payload_bytes==b.host.axi_payload_bytes &&
            a.worker_ar_wire_bytes==b.worker_ar_wire_bytes &&
            a.worker_r_wire_bytes==b.worker_r_wire_bytes &&
            a.upstream_ar_wire_bytes==b.upstream_ar_wire_bytes &&
            a.upstream_r_wire_bytes==b.upstream_r_wire_bytes &&
            a.media_submit_bytes==b.media_submit_bytes &&
            a.native_commands==b.native_commands,
            "service, byte, or link-credit accounting differs from 1ns oracle");
    require(a.stacks.size()==b.stacks.size(),"stack count differs");
    for (std::size_t i=0;i<a.stacks.size();++i) {
        require(a.stacks[i].physical_outstanding==b.stacks[i].physical_outstanding &&
                a.stacks[i].module_links.size()==b.stacks[i].module_links.size(),
                "worker outstanding/credit topology differs");
        for (std::size_t m=0;m<a.stacks[i].module_links.size();++m)
            require(a.stacks[i].module_links[m].ar_wire_bytes==
                        b.stacks[i].module_links[m].ar_wire_bytes &&
                    a.stacks[i].module_links[m].r_wire_bytes==
                        b.stacks[i].module_links[m].r_wire_bytes &&
                    a.stacks[i].module_links[m].ar_credit_granules==
                        b.stacks[i].module_links[m].ar_credit_granules &&
                    a.stacks[i].module_links[m].r_credit_granules==
                        b.stacks[i].module_links[m].r_credit_granules,
                    "per-module credit/wire accounting differs");
    }
    std::cout << name << " modeled_end_ns=" << events.end_ns
              << " ref_advances=" << reference.advances
              << " event_advances=" << events.advances
              << " event_peeks=" << events.peeks
              << " ref_closes=" << reference.closes
              << " event_closes=" << events.closes
              << " event_combined=" << events.combined
              << " ref_total_ipc=" << reference.ipc
              << " event_total_ipc=" << events.ipc
              << " event_reserve=" << events.reserves
              << " event_submit=" << events.submits
              << " event_consume=" << events.consumes
              << " event_is_active=" << events.is_active
              << " ref_wall_ms=" << reference.wall_ms
              << " event_wall_ms=" << events.wall_ms
              << " event_startup_ms=" << events.startup_ms
              << " event_service_ms=" << events.service_ms << std::endl;
    require(events.advances<reference.advances,
            "event path did not reduce worker ADVANCE IPC calls");
}
}

int main(int argc,char** argv)
{
    if (argc!=2) return 2;
    try {
        const auto run_case=[&](const char* name,auto scenario) {
            const auto reference=scenario(argv[1],
                MultistackAdvanceMode::NanosecondReference);
            const auto uncached=scenario(argv[1],
                MultistackAdvanceMode::EventDrivenUncached);
            const auto cached=scenario(argv[1],
                MultistackAdvanceMode::EventDriven);
            const auto separate=scenario(argv[1],
                MultistackAdvanceMode::EventDrivenCachedSeparate);
            compare((std::string(name)+"-uncached").c_str(),reference,uncached);
            compare((std::string(name)+"-cached").c_str(),reference,cached);
            compare((std::string(name)+"-separate").c_str(),reference,separate);
            require(cached.ipc<=uncached.ipc,
                    "lookahead cache increased IPC count");
            require(cached.ipc<separate.ipc && cached.combined>0 &&
                    separate.combined==0,
                    "combined CLOSE_ADVANCE did not save real IPC");
            std::cout << name << " cache_delta_ipc="
                      << static_cast<std::int64_t>(uncached.ipc)-
                             static_cast<std::int64_t>(cached.ipc)
                      << " uncached_service_ms=" << uncached.service_ms
                      << " cached_service_ms=" << cached.service_ms
                      << " separate_total_ipc=" << separate.ipc
                      << " combined_total_ipc=" << cached.ipc
                      << " separate_service_ms=" << separate.service_ms
                      << std::endl;
        };
        run_case("idle-gap",idle);
        run_case("dense-balanced-hotspot",dense);
        run_case("cancel-backpressure",cancel_backpressure);
        run_case("host-16k-media-4k",host_page_16k);
        run_case("deadline",deadline);
        compare("future-host-16k",
                future_host_page(argv[1],MultistackAdvanceMode::NanosecondReference),
                future_host_page(argv[1],MultistackAdvanceMode::EventDriven));
        compare("multiple-future-parents-idle-gap",
                multiple_future_parents(argv[1],MultistackAdvanceMode::NanosecondReference),
                multiple_future_parents(argv[1],MultistackAdvanceMode::EventDriven));
        const auto consecutive_uncached=consecutive_host_pages(argv[1],
            MultistackAdvanceMode::EventDrivenUncached);
        const auto consecutive_cached=consecutive_host_pages(argv[1],
            MultistackAdvanceMode::EventDriven);
        const auto consecutive_separate=consecutive_host_pages(argv[1],
            MultistackAdvanceMode::EventDrivenCachedSeparate);
        require(consecutive_uncached.delivered==consecutive_cached.delivered &&
                consecutive_uncached.end_ns==consecutive_cached.end_ns &&
                consecutive_uncached.accounting.media_submit_bytes==
                    consecutive_cached.accounting.media_submit_bytes &&
                consecutive_cached.ipc<consecutive_uncached.ipc,
                "consecutive 16KiB cache comparison changed service semantics");
        require(consecutive_cached.delivered==consecutive_separate.delivered &&
                consecutive_cached.end_ns==consecutive_separate.end_ns &&
                consecutive_cached.ipc<consecutive_separate.ipc,
                "consecutive combined comparison changed semantics or IPC");
        std::cout << "consecutive-host-pages modeled_end_ns="
                  << consecutive_cached.end_ns
                  << " uncached_total_ipc=" << consecutive_uncached.ipc
                  << " cached_total_ipc=" << consecutive_cached.ipc
                  << " separate_total_ipc=" << consecutive_separate.ipc
                  << " uncached_startup_ms=" << consecutive_uncached.startup_ms
                  << " cached_startup_ms=" << consecutive_cached.startup_ms
                  << " uncached_service_ms=" << consecutive_uncached.service_ms
                  << " cached_service_ms=" << consecutive_cached.service_ms
                  << " separate_service_ms=" << consecutive_separate.service_ms
                  << std::endl;
        invalidation(argv[1]);
        partial_worker_failure(argv[1]);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

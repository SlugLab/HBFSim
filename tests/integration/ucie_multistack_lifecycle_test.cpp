#include <hbfsim/ucie/multistack_frontend.hpp>

#include <cerrno>
#include <csignal>
#include <cstdint>
#include <iostream>
#include <stdexcept>

#include <unistd.h>

namespace {
using namespace hbfsim::ucie;
constexpr std::uint64_t mib=1ULL<<20;
constexpr const char* profile=
    "configs/profiles/ucie/hbf-stage3-small-stack.json";

void require(bool value,const char* message)
{ if (!value) throw std::runtime_error(message); }

void deadline_and_reuse(const char* worker)
{
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile);
    front.add_backing({1,0,64,1,0,1,7,true});
    require(front.try_submit({.request_id=10,.arrival_ns=0,.deadline_ns=1,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=10}),
            "deadline fixture was not admitted");
    front.advance_until(1);
    require(front.peek_completion(10) &&
            front.peek_completion(10)->result==DeviceResult::TimedOut &&
            front.peek_completion(10)->ready_ns==1,
            "AR-propagation deadline did not notify caller at deadline");
    front.cancel(10); // terminal TimedOut must not become Cancelled
    require(front.peek_completion(10)->result==DeviceResult::TimedOut,
            "late caller cancel overwrote an established timeout");
    (void)front.consume_completion(10);
    bool duplicate=false;
    try {
        (void)front.try_submit({.request_id=10,.arrival_ns=1,
            .address=0,.bytes=64,.endpoint_id=7,.axi_id=10});
    } catch (const std::invalid_argument&) { duplicate=true; }
    require(duplicate,"request ID reused while AR remained physically in flight");
    front.advance_until(100);
    require(front.try_submit({.request_id=10,.arrival_ns=100,
                .address=0,.bytes=64,.endpoint_id=7,.axi_id=10}),
            "fully drained request ID could not be reused");
    front.cancel(10);
    require(front.peek_completion(10) &&
            front.consume_completion(10).result==DeviceResult::Cancelled,
            "reused request was not cancelled exactly once");
    front.advance_until(200);
}

void one_child_terminal(const char* worker)
{
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile);
    front.add_backing({1,8*mib-32,64,2,8*mib-32,1,7,true});
    require(front.try_submit({.request_id=20,.arrival_ns=0,
                .address=8*mib-32,.bytes=64,.endpoint_id=7,.axi_id=20}),
            "cross-stack two-child failure fixture not admitted");
    // Controlled worker-side terminal result, independent of parent cancel.
    front.worker(0).cancel(1,0);
    front.advance_until(0);
    const auto terminal=front.peek_completion(20);
    require(terminal && terminal->result==DeviceResult::Cancelled &&
            terminal->ready_ns==0 &&
            terminal->child_count==2,
            "one child terminal status did not produce one parent result");
    (void)front.consume_completion(20);
    require(!front.peek_completion(20) &&
            front.counters().cancelled==1 &&
            front.counters().caller_outstanding==0,
            "parent result was repeated after child terminal");
    front.advance_until(200);
}

void deadline_during_return(const char* worker)
{
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile);
    front.add_backing({1,0,64,3,0,1,7,true});
    constexpr std::uint64_t deadline=10315; // fixture-specific measured phase
    require(front.try_submit({.request_id=30,.arrival_ns=0,
                .deadline_ns=deadline,.address=0,.bytes=64,
                .endpoint_id=7,.axi_id=30}),
            "return-deadline fixture was not admitted");
    front.advance_until(deadline);
    const auto requests=front.worker(0).request_evidence(0);
    const auto result=front.peek_completion(30);
    require(requests.size()==1 &&
            requests[0].media_ready_ns>0 &&
            requests[0].media_ready_ns<deadline &&
            requests[0].r_delivered_ns==0 &&
            result && result->result==DeviceResult::TimedOut &&
            result->ready_ns==deadline,
            "deadline did not fire while R was in propagation");
    (void)front.consume_completion(30);
    front.advance_until(deadline+50);
    require(front.worker(0).stats().physical_outstanding==0 &&
            front.counters().caller_outstanding==0,
            "late return packet did not drain after caller timeout");
}

void failed_worker_reaps_others(const char* worker)
{
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile);
    front.add_backing({1,0,64,401,0,1,7,true});
    front.add_backing({2,8*mib,64,402,8*mib,1,7,true});
    require(front.try_submit({.request_id=41,.arrival_ns=0,.address=0,
                .bytes=64,.endpoint_id=7,.axi_id=41}) &&
            front.try_submit({.request_id=42,.arrival_ns=0,.address=8*mib,
                .bytes=64,.endpoint_id=7,.axi_id=42}),
            "failure fixture did not accept both parents");
    const auto dead=static_cast<pid_t>(front.worker(0).stats().process_id);
    const auto peer=static_cast<pid_t>(front.worker(1).stats().process_id);
    require(dead>0 && peer>0 && dead!=peer,
            "worker failure fixture lacks two owned processes");
    require(::kill(dead,SIGKILL)==0,"could not inject exact owned worker failure");
    bool strict=false;
    try { front.advance_until(0); }
    catch (const std::exception&) { strict=true; }
    errno=0;
    const auto alive=::kill(peer,0);
    require(strict && alive<0 && errno==ESRCH,
            "strict worker failure did not immediately reap healthy peer");
    const auto ids=front.ready_ids();
    require(ids.size()==2 && front.peek_completion(41)->result==
                DeviceResult::BackendError &&
            front.peek_completion(42)->result==DeviceResult::BackendError,
            "worker crash did not propagate a terminal to both parents");
    require(front.consume_completion(41).result==DeviceResult::BackendError &&
            front.consume_completion(42).result==DeviceResult::BackendError &&
            front.ready_ids().empty() &&
            front.counters().caller_outstanding==0 &&
            front.counters().failed==2 &&
            front.counters().reassembly_reserved_bytes==0,
            "fatal backend results were not exactly once or host resources leaked");
    bool duplicate=false;
    try { (void)front.consume_completion(41); }
    catch (const std::invalid_argument&) { duplicate=true; }
    require(duplicate,"fatal backend result was consumed twice");
}

void worker_dies_during_consume(const char* worker)
{
    MultistackFrontend front(ContiguousStackMap(16*mib,2,2),worker,profile);
    front.add_backing({1,0,64,501,0,1,7,true});
    front.add_backing({2,8*mib,64,502,8*mib,1,7,true});
    require(front.try_submit({.request_id=51,.arrival_ns=0,.address=0,
                .bytes=64,.endpoint_id=7,.axi_id=51}) &&
            front.try_submit({.request_id=52,.arrival_ns=0,.address=8*mib,
                .bytes=64,.endpoint_id=7,.axi_id=52}),
            "consume-failure fixture rejected a parent");
    bool ready=false;
    for (std::uint64_t h=0;h<100000;++h) {
        front.advance_until(h);
        if (front.ready_ids().size()==2) { ready=true; break; }
    }
    require(ready,"consume-failure fixture never assembled both reads");
    const auto dead=static_cast<pid_t>(front.worker(0).stats().process_id);
    require(::kill(dead,SIGKILL)==0,"could not stop exact ready worker");
    const auto first=front.consume_completion(51);
    require(first.result==DeviceResult::Ready &&
            front.peek_completion(52)->result==DeviceResult::BackendError &&
            front.ready_ids().size()==1,
            "consumed stable Ready result was lost or peer not poisoned");
    const auto second=front.consume_completion(52);
    require(second.result==DeviceResult::BackendError &&
            front.ready_ids().empty() &&
            front.counters().succeeded==1 &&
            front.counters().failed==1 &&
            front.counters().caller_outstanding==0 &&
            front.counters().reassembly_reserved_bytes==0,
            "consume failure did not close both parent outcomes exactly once");
}
}

int main(int argc,char** argv)
{
    if (argc!=2) throw std::invalid_argument("worker executable required");
    deadline_and_reuse(argv[1]);
    one_child_terminal(argv[1]);
    deadline_during_return(argv[1]);
    failed_worker_reaps_others(argv[1]);
    worker_dies_during_consume(argv[1]);
    std::cout << "multistack_lifecycle timeout/reuse child-terminal worker-reap PASS\n";
}

#include <hbfsim/ucie/device_frontend.hpp>

#include <cstdint>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <vector>

namespace {
void require(bool value,const char* why)
{ if (!value) throw std::runtime_error(why); }

class FakeMedia final : public hbfsim::ucie::DeviceMediaPort {
public:
    explicit FakeMedia(hbfsim::ucie::HbfBankLayout layout):layout_(layout) {}
    void submit(const hbfsim::HbfRequest& r) override
    {
        submitted.push_back(r);
        const auto delay=delay_ns.contains(r.logical_address/4096)
            ? delay_ns.at(r.logical_address/4096) : 0;
        pending.emplace(r.arrival_ns+delay,hbfsim::HbfCompletion{
            .request_id=r.request_id,.modeled_completion_ns=r.arrival_ns+delay,
            .modeled_ns=delay,.status=static_cast<std::uint32_t>(
                failed_pages.contains(r.logical_address/4096)
                ? hbfsim::RequestStatus::IoError
                : hbfsim::RequestStatus::Ready)});
    }
    std::optional<hbfsim::HbfCompletion> run_until(std::uint64_t horizon) override
    {
        if (horizon<now) throw std::logic_error("fake clock moved backward");
        if (!pending.empty() && pending.begin()->first<=horizon) {
            now=pending.begin()->first;
            auto result=pending.begin()->second;
            pending.erase(pending.begin());
            return result;
        }
        now=horizon;
        return std::nullopt;
    }
    std::uint64_t current_time_ns() const override {return now;}
    hbfsim::ucie::HbfReadBank inspect(std::uint64_t page) override
    {return hbfsim::ucie::hbf_cold_read_bank(page/4096,layout_);}
    void expect(std::uint64_t token,std::uint64_t page,
                const hbfsim::ucie::HbfReadBank& bank) override
    {expectations.emplace(token,std::pair{page,bank});}
    hbfsim::NativeReadProof finish(std::uint64_t token) override
    {
        const auto page=expectations.at(token).first;
        expectations.erase(token);
        return hbfsim::NativeReadProof{
            .request_id=token,.issued_commands=1,.phase_events={1,1,1,1,1},
            .expected_logical_page=page/4096,
            .observed_logical_page=page/4096,.observed_media_bytes=4096,
            .logical_page_match=true,.bank_match=token<=valid_proof_through};
    }
    std::uint64_t now{};
    std::map<std::uint64_t,std::uint64_t> delay_ns;
    std::set<std::uint64_t> failed_pages;
    std::uint64_t valid_proof_through=UINT64_MAX;
    std::vector<hbfsim::HbfRequest> submitted;
    std::multimap<std::uint64_t,hbfsim::HbfCompletion> pending;
    std::map<std::uint64_t,std::pair<std::uint64_t,hbfsim::ucie::HbfReadBank>> expectations;
private:
    hbfsim::ucie::HbfBankLayout layout_;
};

hbfsim::Profile profile()
{
    auto p=hbfsim::load_profile("configs/profiles/nominal.json");
    p.capacity_bytes=8ULL<<20;
    p.channels=1;
    p.page_bytes=4096;
    if (p.hbm_cache_bytes>p.capacity_bytes) p.hbm_cache_bytes=p.capacity_bytes;
    return p;
}
hbfsim::ucie::LinkProfile link(std::uint64_t propagation=20)
{
    hbfsim::ucie::LinkProfile p;
    p.name="device-fake";
    p.local_capacity_bytes=8ULL<<20;
    p.max_accepted=4;
    p.initial_ar_granules=12;
    p.initial_r_granules=56;
    p.propagation_ns=propagation;
    return p;
}
hbfsim::ucie::DeviceRead read(std::uint64_t id,std::uint64_t address,
                               std::uint64_t deadline=0,
                               std::uint32_t axi_id=0)
{
    return {.request_id=id,.deadline_ns=deadline,.local_address=address,
            .axi_id=axi_id,.endpoint_id=7};
}
void register_all(hbfsim::ucie::UcieDeviceFrontend& front)
{ front.add_backing({1,0,8ULL<<20,1,0,1,0,0,7,true}); }

void test_deadline_in_ar()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    auto* fake=port.get();
    UcieDeviceFrontend front(link(),profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    require(front.try_submit(read(1,0,5)),"AR timeout fixture not accepted");
    front.advance_until(5);
    const auto done=front.poll_completion();
    require(done && done->result==DeviceResult::TimedOut &&
            done->consumed_ns==5 && fake->submitted.empty(),
            "AR-propagation deadline was delayed or started NAND");
    bool duplicate=false;
    auto too_early_reuse=read(1,0);
    too_early_reuse.arrival_ns=front.current_time_ns();
    try { (void)front.try_submit(too_early_reuse); }
    catch (const std::invalid_argument&) { duplicate=true; }
    require(duplicate,"request ID reused while old AR still in link");
    front.drain_until_idle(1'000'000);
    require(front.outstanding()==0 && fake->submitted.empty(),
            "timed-out AR did not drain without NAND work");
    auto reuse=read(1,0);
    reuse.arrival_ns=front.current_time_ns();
    require(front.try_submit(reuse),"request ID remained blocked after full drain");
    front.drain_until_idle(2'000'000);
    require(front.counters().accepted==2 && front.counters().succeeded==1,
            "reused user ID was confused with old AR callback");
}

void test_alias_and_registration_boundaries()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    auto* fake=port.get();
    UcieDeviceFrontend front(link(1),profile(),{1,1,2,true},0,0,std::move(port));
    front.add_backing({1,0,4096,11,0,7,0,0,7,true});
    front.add_backing({2,8192,4096,11,0,7,0,0,7,true});
    require(front.try_submit(read(1,0)) &&
            front.try_submit(read(2,8192)),"explicit alias ARs rejected");
    front.drain_until_idle(1'000'000);
    require(fake->submitted.size()==1 && front.counters().succeeded==2 &&
            front.request_records()[0].group_token==
                front.request_records()[1].group_token,
            "alias canonical page failed to merge into one native group");
    front.retire_backing_generation(11,7,7);
    bool retired=false;
    try { (void)front.try_submit(read(3,0)); }
    catch (const std::invalid_argument&) { retired=true; }
    require(retired && front.counters().accepted==2,
            "retired canonical generation accepted another read");
}

void test_deadline_in_r()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    UcieDeviceFrontend front(link(),profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    require(front.try_submit(read(1,0,30)),"R timeout fixture not accepted");
    front.advance_until(30);
    const auto done=front.poll_completion();
    require(done && done->result==DeviceResult::TimedOut &&
            done->consumed_ns==30 && done->axi_payload_bytes==0,
            "R-propagation deadline waited for physical R");
    front.drain_until_idle(1'000'000);
    require(front.counters().axi_payload_bytes==64 &&
            front.counters().native_commands==1 && front.outstanding()==0,
            "timed-out in-flight R or native media failed to drain");
}

void test_slow_consumer_and_other_bank()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    auto* fake=port.get();
    UcieDeviceFrontend front(link(1),profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    require(front.try_submit(read(1,0,0,1)) &&
            front.try_submit(read(2,2*4096,0,2)) &&
            front.try_submit(read(3,4*4096,0,3)) &&
            front.try_submit(read(4,4096,0,4)),
            "slow-consumer fixture was not accepted");
    front.advance_until(1000); // caller intentionally does not poll
    require(fake->submitted.size()==3 &&
            front.coalescer().occupied_slots(0,0)==2 &&
            front.coalescer().occupied_slots(0,1)==1,
            "third same-bank page bypassed two pinned slots or blocked other bank");
    bool found_bank0=false;
    for (int i=0;i<4;++i) {
        const auto done=front.poll_completion();
        if (!done) break;
        if (done->request.request_id==1 || done->request.request_id==2)
            found_bank0=true;
        if (found_bank0) break;
    }
    require(found_bank0 && fake->submitted.size()==4,
            "caller consume did not free one bank slot for queued miss");
    front.drain_until_idle(1'000'000);
    require(front.counters().succeeded==4 && front.outstanding()==0,
            "slow-consumer fixture failed to close");
}

void test_same_id_and_cross_id_order()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    port->delay_ns[0]=100;
    auto* fake=port.get();
    UcieDeviceFrontend front(link(1),profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    require(front.try_submit(read(1,0,0,3)) &&
            front.try_submit(read(2,2*4096,0,3)) &&
            front.try_submit(read(3,4096,0,4)),
            "order fixture admission failed");
    front.advance_until(50);
    const auto early=front.poll_completion();
    require(early && early->request.request_id==3 &&
            front.poll_completion()==std::nullopt &&
            fake->submitted.size()==3,
            "different ID failed to return before older slow same-ID read");
    front.advance_until(200);
    const auto first=front.poll_completion();
    const auto second=front.poll_completion();
    require(first && second && first->request.request_id==1 &&
            second->request.request_id==2 &&
            first->response_delivered_ns<=second->response_delivered_ns,
            "same AXI ID returned in media completion order");
    bool packed=false;
    for (const auto& flit:front.link().flits()) {
        if (flit.direction!=Direction::Return) continue;
        bool one=false,two=false;
        for (const auto& fragment:flit.fragments) {
            if (fragment.request_id==1) one=true;
            if (fragment.request_id==2) two=true;
        }
        if (one && two) packed=true;
    }
    require(packed,"ready same-ID responses incurred stop-and-wait flits");
    front.drain_until_idle(1'000'000);
}

void test_cancel_and_failure_lifecycle()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    port->delay_ns[0]=100;
    port->delay_ns[1]=50;
    port->failed_pages.insert(1);
    auto* fake=port.get();
    UcieDeviceFrontend front(link(1),profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    auto forged=read(99,0);
    forged.expected_media_page=4096;
    bool rejected=false;
    try { (void)front.try_submit(forged); }
    catch (const std::invalid_argument&) { rejected=true; }
    require(rejected && front.counters().accepted==0 && fake->submitted.empty(),
            "forged media page caused side effects");
    forged.expected_media_page=0;
    forged.expected_generation=2;
    rejected=false;
    try { (void)front.try_submit(forged); }
    catch (const std::invalid_argument&) { rejected=true; }
    require(rejected && front.counters().accepted==0,
            "forged backing generation was accepted");
    require(front.try_submit(read(1,0)) && front.try_submit(read(2,4096)),
            "cancel/error fixture admission failed");
    front.advance_until(10); // AR arrived, first native read still pending
    front.cancel(1);
    const auto cancelled=front.poll_completion();
    require(cancelled && cancelled->result==DeviceResult::Cancelled &&
            cancelled->request.request_id==1 && fake->submitted.size()==2,
            "submitted native read cancellation was not immediate");
    front.advance_until(200);
    const auto failure=front.poll_completion();
    require(failure && failure->result==DeviceResult::BackendError &&
            failure->axi_payload_bytes==0,
            "non-Ready backend completion was misreported as data");
    front.drain_until_idle(1'000'000);
    require(front.counters().accepted==2 && front.counters().cancelled==1 &&
            front.counters().failed==1 && front.outstanding()==0,
            "cancelled submitted group or failed read leaked lifecycle");
}

void test_timeout_after_r_delivery()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    UcieDeviceFrontend front(link(),profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    require(front.try_submit(read(1,0,100)),"post-R timeout admission failed");
    front.advance_until(100); // R arrived, caller deliberately has not polled
    const auto done=front.poll_completion();
    require(done && done->result==DeviceResult::TimedOut &&
            done->response_delivered_ns>0 && done->consumed_ns==100,
            "deadline after delivered R did not override unconsumed result");
    front.drain_until_idle(1'000'000);
}

void test_one_cancelled_waiter_of_eight()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    port->delay_ns[0]=100;
    auto* fake=port.get();
    auto lp=link(1);
    lp.max_accepted=8;
    lp.initial_ar_granules=24;
    lp.initial_r_granules=112;
    UcieDeviceFrontend front(lp,profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    for (std::uint64_t id=1;id<=8;++id)
        require(front.try_submit(read(id,(id-1)*64,0,
                                      static_cast<std::uint32_t>(id))),
                "eight-waiter admission failed");
    front.advance_until(10);
    front.cancel(1);
    const auto cancelled=front.poll_completion();
    require(cancelled && cancelled->request.request_id==1 &&
            cancelled->result==DeviceResult::Cancelled,
            "one waiter cancellation was delayed");
    front.drain_until_idle(1'000'000);
    require(fake->submitted.size()==1 && front.counters().native_commands==1 &&
            front.counters().cancelled==1 && front.counters().succeeded==7 &&
            front.coalescer().group_count()==0,
            "one cancelled waiter spoiled seven same-page readers");
}

void test_last_waiting_waiter_cancel()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    auto* fake=port.get();
    UcieDeviceFrontend front(link(1),profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    require(front.try_submit(read(1,0,0,1)) &&
            front.try_submit(read(2,2*4096,0,2)) &&
            front.try_submit(read(3,4*4096,0,3)),
            "waiting cancellation admission failed");
    front.advance_until(100);
    require(fake->submitted.size()==2 && front.coalescer().group_count()==3,
            "third page was not waiting behind two pinned buffers");
    front.cancel(3);
    require(front.coalescer().group_count()==2 && fake->submitted.size()==2,
            "last waiting waiter cancellation did not remove unsensed group");
    front.drain_until_idle(1'000'000);
    require(front.counters().succeeded==2 && front.counters().cancelled==1 &&
            front.outstanding()==0 && front.coalescer().group_count()==0,
            "waiting cancellation awaited nonexistent native callback");
}

void test_equal_time_completion_before_ar()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    port->delay_ns[0]=97; // first AR at 3, media ready at 100
    auto* fake=port.get();
    UcieDeviceFrontend front(link(1),profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    auto second=read(2,64,0,2);
    second.arrival_ns=97; // second AR delivered at 100
    require(front.try_submit(read(1,0,0,1)) && front.try_submit(second),
            "equal-time fixture admission failed");
    front.drain_until_idle(1'000'000);
    require(fake->submitted.size()==2 &&
            front.request_records()[0].group_token!=
                front.request_records()[1].group_token,
            "same-ns AR joined group after modeled completion");
}

void test_generation_change_with_old_callback()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    port->delay_ns[0]=100;
    auto* fake=port.get();
    UcieDeviceFrontend front(link(1),profile(),{1,1,2,true},0,0,std::move(port));
    front.add_backing({1,0,4096,11,0,7,0,0,7,true});
    require(front.try_submit(read(1,0,0,1)),"old generation admission failed");
    front.advance_until(10);
    front.retire_backing_generation(11,7,7);
    front.add_backing({2,0,4096,11,0,8,0,0,7,true});
    auto second=read(2,0,0,2);
    second.arrival_ns=front.current_time_ns();
    require(front.try_submit(second),"replacement generation admission failed");
    front.drain_until_idle(1'000'000);
    require(fake->submitted.size()==2 && front.counters().succeeded==2 &&
            front.request_records()[0].generation==7 &&
            front.request_records()[1].generation==8 &&
            front.request_records()[0].group_token!=
                front.request_records()[1].group_token,
            "late old-generation callback joined replacement group");
}

void test_native_validation_after_trace_limit()
{
    using namespace hbfsim::ucie;
    auto port=std::make_unique<FakeMedia>(HbfBankLayout{1,1,2,true});
    auto* fake=port.get();
    UcieDeviceFrontend front(link(1),profile(),{1,1,2,true},0,0,std::move(port));
    register_all(front);
    for (std::uint64_t id=1;id<=700;++id) {
        auto request=read(id,0);
        request.arrival_ns=front.current_time_ns();
        require(front.try_submit(request),"trace-limit fixture admission failed");
        front.drain_until_idle(front.current_time_ns()+1000);
    }
    require(front.counters().trace_dropped>0 &&
            front.counters().native_commands==700,
            "bounded trace was not saturated independently of native commands");
    fake->valid_proof_through=700;
    auto bad=read(701,0);
    bad.arrival_ns=front.current_time_ns();
    require(front.try_submit(bad),"post-trace-limit read admission failed");
    bool caught=false;
    try { (void)front.run_next_completion_until(front.current_time_ns()+1000); }
    catch (const std::logic_error&) { caught=true; }
    require(caught && front.native_proofs().size()==701 &&
            !front.native_proofs().back().proof.bank_match,
            "bad native bank proof passed after trace records were dropped");
}
}

int main()
{
    test_deadline_in_ar();
    test_alias_and_registration_boundaries();
    test_deadline_in_r();
    test_slow_consumer_and_other_bank();
    test_same_id_and_cross_id_order();
    test_cancel_and_failure_lifecycle();
    test_timeout_after_r_delivery();
    test_one_cancelled_waiter_of_eight();
    test_last_waiting_waiter_cancel();
    test_equal_time_completion_before_ar();
    test_generation_change_with_old_callback();
    test_native_validation_after_trace_limit();
}

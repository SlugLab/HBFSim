#include <hbfsim/profile.hpp>
#include <hbfsim/ucie/mqsim_frontend.hpp>

#include <NVM_PHY_ONFI_NVDDR2.h>

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <stdexcept>
#include <vector>

namespace {
void require(bool yes, const char* reason)
{
    if (!yes) throw std::runtime_error(reason);
}
hbfsim::Profile media_profile()
{
    auto p=hbfsim::load_profile("configs/profiles/nominal.json");
    p.capacity_bytes=16ULL<<30;
    p.page_bytes=4096;
    p.hbm_cache_bytes=64ULL<<20;
    hbfsim::validate_profile(p);
    require(hbfsim::blocks_per_plane(p)==16,"4KiB test geometry changed");
    return p;
}
hbfsim::ucie::LinkProfile link_profile()
{
    auto p=hbfsim::ucie::load_link_profile(
        "configs/profiles/ucie/hbf-grade2-single-module.json");
    return p;
}
hbfsim::ucie::AxiRead axi_read(std::uint64_t id, std::uint64_t address,
                            std::uint32_t axi_id=1)
{
    return {.request_id=id,.arrival_ns=0,.local_address=address,
            .axi_id=axi_id};
}
class FakeMedia final : public hbfsim::ucie::MediaPort {
public:
    explicit FakeMedia(bool fail=false):fail_(fail){}
    void submit(const hbfsim::HbfRequest& r) override
    {
        require(r.bytes==4096 && r.logical_address%4096==0,
                "frontend did not submit a cold page to fake media");
        const auto delay=r.request_id==1 ? 1000ULL : 10ULL;
        const auto done=r.arrival_ns+delay;
        pending_.emplace(done,hbfsim::HbfCompletion{
            .request_id=r.request_id,.modeled_completion_ns=done,
            .modeled_ns=delay,.status=fail_
                ? static_cast<std::uint32_t>(hbfsim::RequestStatus::IoError)
                : static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready)});
    }
    std::optional<hbfsim::HbfCompletion> run_until(std::uint64_t horizon) override
    {
        if (horizon<now_) throw std::invalid_argument("fake media clock moved backward");
        if (!pending_.empty() && pending_.begin()->first<=horizon) {
            now_=pending_.begin()->first;
            auto value=pending_.begin()->second;
            pending_.erase(pending_.begin());
            return value;
        }
        now_=horizon;
        return std::nullopt;
    }
    std::uint64_t current_time_ns() const override { return now_; }
    std::vector<hbfsim::MqsimObservation> take_observations() override { return {}; }
private:
    bool fail_;
    std::uint64_t now_{};
    std::multimap<std::uint64_t,hbfsim::HbfCompletion> pending_;
};
}

int main()
{
    using namespace hbfsim::ucie;
    auto lp=link_profile();
    auto mp=media_profile();
    {
        lp.max_accepted=1;
        UcieMqsimFrontend front(lp,mp,std::make_unique<FakeMedia>());
        require(front.try_submit(axi_read(1,64)),"first request was not accepted");
        require(!front.try_submit(axi_read(2,4096)),"bounded accepted state did not backpressure");
        bool rejected=false;
        try { front.try_submit(axi_read(1,128)); }
        catch (const std::invalid_argument&) { rejected=true; }
        require(rejected,"duplicate active request ID was accepted");
        rejected=false;
        try { front.try_submit(axi_read(3,65)); }
        catch (const std::invalid_argument&) { rejected=true; }
        require(rejected,"misaligned 64B beat was accepted");
        rejected=false;
        auto write=axi_read(4,4096);write.operation=1;
        try { front.try_submit(write); }
        catch (const std::invalid_argument&) { rejected=true; }
        require(rejected,"write was accepted");
        rejected=false;
        auto burst=axi_read(5,4096);burst.bytes=128;
        try { front.try_submit(burst); }
        catch (const std::invalid_argument&) { rejected=true; }
        require(rejected,"burst was accepted");
        require(front.counters().accepted==1,"rejected input changed accepted count");
        const auto first=front.run_next_completion_until(2000);
        require(first && first->request.request_id==1 && first->interface_bytes==64 &&
                first->media_submit_bytes==4096 && first->media_ready_ns==1002 &&
                first->response_delivered_ns>=first->media_ready_ns,
                "fake media completion was not returned causally");
        require(front.outstanding()==0,"consumer poll did not release accepted slot");
        rejected=false;
        try { front.run_next_completion_until(front.current_time_ns()-1); }
        catch (const std::invalid_argument&) { rejected=true; }
        require(rejected,"backward horizon was accepted");
    }
    {
        lp.max_accepted=2;
        UcieMqsimFrontend front(lp,mp,std::make_unique<FakeMedia>());
        auto future=axi_read(30,0);future.arrival_ns=100;
        require(front.try_submit(future),"future read not accepted");
        bool reversed_arrival=false;
        try { front.try_submit(axi_read(31,4096)); }
        catch (const std::invalid_argument&) { reversed_arrival=true; }
        require(reversed_arrival,"out-of-order accepted arrival changed AXI issue order");
    }
    {
        lp.max_accepted=2;
        lp.propagation_ns=3;
        UcieMqsimFrontend front(lp,mp,std::make_unique<FakeMedia>());
        require(front.try_submit(axi_read(1,0,7)) &&
                front.try_submit(axi_read(2,4096,7)),"same-ID fixture not accepted");
        const auto first=front.run_next_completion_until(2000);
        const auto second=front.run_next_completion_until(2000);
        require(first && second && first->request.request_id==1 &&
                second->request.request_id==2 &&
                second->media_ready_ns<first->media_ready_ns &&
                second->response_delivered_ns==first->response_delivered_ns,
                "forced reversed media completion broke AXI same-ID delivery");
        bool packed=false;
        for (const auto& f:front.link().flits())
            if (f.direction==Direction::Return && f.message_granules==28 &&
                f.fragments.size()==2 &&
                f.fragments[0].request_id==1 && f.fragments[1].request_id==2)
                packed=true;
        require(packed,"ready same-ID responses were unnecessarily stop-and-wait serialized");
    }
    {
        lp.max_accepted=1;
        UcieMqsimFrontend front(lp,mp,std::make_unique<FakeMedia>(true));
        require(front.try_submit(axi_read(1,0)),"failure fixture not accepted");
        const auto error=front.run_next_completion_until(2000);
        require(error && error->media.status==static_cast<std::uint32_t>(
                    hbfsim::RequestStatus::IoError) && error->interface_bytes==0 &&
                front.outstanding()==0,"native failure was lost or counted as sent data");
        require(!front.run_next_completion_until(3000),"failure result duplicated");
    }
    {
        lp=link_profile();
        std::set<std::uint64_t> read_command_ids;
        std::uint64_t read_command_events=0;
        SSD_Components::NVM_PHY_ONFI_NVDDR2::Set_hbf_command_observation_sink(
            [&](const SSD_Components::HBF_Command_Observation& o) {
                if (o.phase==SSD_Components::HBF_Command_Observation_Phase::COMMAND_ISSUED &&
                    o.command_code==CMD_READ) {
                    ++read_command_events;
                    for (const auto& tr:o.transactions)
                        if (tr.external_request_id) read_command_ids.insert(tr.external_request_id);
                }
            });
        {
            UcieMqsimFrontend front(lp,mp);
            require(front.try_submit(axi_read(10,0,1)),"native read10 not accepted");
            require(front.try_submit(axi_read(11,4096,2)),"native read11 not accepted");
            require(front.try_submit(axi_read(12,8192,3)),"native read12 not accepted");
            bool second_rejected=false;
            try { UcieMqsimFrontend second(lp,mp); }
            catch (const std::logic_error&) { second_rejected=true; }
            require(second_rejected,"concurrent process-global MQSim frontend admitted");
            std::set<std::uint64_t> returned;
            for (unsigned i=0;i<3;++i) {
                const auto out=front.run_next_completion_until(1000000);
                require(out && out->media.status==static_cast<std::uint32_t>(
                            hbfsim::RequestStatus::Ready),"native MQSim completion missing");
                require(out->request_delivered_ns<=out->media_ready_ns &&
                        out->media_ready_ns<=out->response_delivered_ns,
                        "native request/media/return times noncausal");
                returned.insert(out->request.request_id);
            }
            std::uint64_t arrivals=0, admissions=0, callbacks=0;
            for (const auto& o: front.take_media_observations()) {
                if (o.kind==hbfsim::MqsimEventKind::Arrival) ++arrivals;
                if (o.kind==hbfsim::MqsimEventKind::Admission) ++admissions;
                if (o.kind==hbfsim::MqsimEventKind::Completion) ++callbacks;
            }
            require(returned==std::set<std::uint64_t>{10,11,12} &&
                    arrivals==3 && admissions==3 && callbacks==3 &&
                    front.counters().application_bytes==192 &&
                    front.counters().media_submit_bytes==12288 &&
                    front.counters().media_completions==3 &&
                    front.counters().consumed==3,
                    "native frontend/MQSim byte and callback closure failed");
            std::cout << "native_mqsim arrivals=" << arrivals << " admissions="
                      << admissions << " callbacks=" << callbacks << '\n';
        }
        require(!SSD_Components::NVM_PHY_ONFI_NVDDR2::Hbf_command_observation_failed(),
                "native NAND observer callback failed");
        SSD_Components::NVM_PHY_ONFI_NVDDR2::Clear_hbf_command_observation_sink();
        require(read_command_events>=3 &&
                read_command_ids==std::set<std::uint64_t>{10,11,12},
                "native NAND READ commands did not reference all frontend requests");
        std::cout << "native_nand_read_commands=" << read_command_events << '\n';
    }
    {
        lp=link_profile();
        auto slow=mp;
        slow.aggregate_bandwidth_bytes_per_s=100000; // existing media service cap
        UcieMqsimFrontend front(lp,slow);
        require(front.try_submit(axi_read(20,12288)),"slow-cap native read not accepted");
        require(!front.run_next_completion_until(1000000),
                "return escaped before MQSim modeled-ready bound");
        std::uint64_t native_callback_ns=0, modeled_ready_ns=0;
        for (const auto& o:front.take_media_observations())
            if (o.kind==hbfsim::MqsimEventKind::Completion) {
                native_callback_ns=o.time_ns;
                modeled_ready_ns=o.modeled_completion_ns;
            }
        require(native_callback_ns>0 && modeled_ready_ns>native_callback_ns &&
                modeled_ready_ns>=40960000,
                "raw NAND callback was conflated with existing service cap");
        const auto out=front.run_next_completion_until(50000000);
        require(out && out->media_ready_ns==modeled_ready_ns &&
                out->response_delivered_ns>=modeled_ready_ns,
                "UCIe return did not wait for bounded MQSim completion");
        std::cout << "native_callback_ns=" << native_callback_ns
                  << " existing_cap_ready_ns=" << modeled_ready_ns
                  << " ucie_return_ns=" << out->response_delivered_ns << '\n';
    }
    std::cout << "PASS fake fault/order and real MQSim/NAND CPU integration\n";
}

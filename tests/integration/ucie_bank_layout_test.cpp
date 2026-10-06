#include <hbfsim/mqsim_online.hpp>
#include <hbfsim/ucie/bank_layout.hpp>

#include <Flash_Command.h>
#include <NVM_PHY_ONFI_NVDDR2.h>

#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>

namespace {
void require(bool value, const char* why)
{
    if (!value) throw std::runtime_error(why);
}

hbfsim::Profile profile(std::uint64_t capacity, std::uint32_t channels)
{
    auto p=hbfsim::load_profile("configs/profiles/nominal.json");
    p.capacity_bytes=capacity;
    p.channels=channels;
    p.page_bytes=4096;
    if (p.hbm_cache_bytes>capacity) p.hbm_cache_bytes=capacity;
    return p;
}
}

int main()
{
    using hbfsim::ucie::HbfBankLayout;
    constexpr std::uint64_t gib=1ULL<<30;
    auto large=profile(512*gib,16);
    for (const auto d:std::array<std::uint32_t,5>{1,2,4,8,16}) {
        const HbfBankLayout layout{16,d,16,false};
        const auto geometry=hbfsim::ucie::hbf_mqsim_geometry(large,layout);
        require(geometry.channels==16 && geometry.chips_per_channel==16*d &&
                geometry.dies_per_chip==1 && geometry.planes_per_die==1 &&
                hbfsim::blocks_per_plane(large,geometry)==2048/d,
                "512GiB native chip geometry oracle failed");
        const auto last_lpa=hbfsim::ucie::hbf_media_lpa(
            (512*gib/4096/16)-1,15,layout);
        require(last_lpa==512*gib/4096-1,
                "last 512GiB module page did not cover last media LPA");
        const auto bank=hbfsim::ucie::hbf_cold_read_bank(
            hbfsim::ucie::hbf_media_lpa(16*d-1,15,layout),layout);
        require(bank.host_channel==15 && bank.core_die==d-1 &&
                bank.bank==15 && bank.native_chip==16*d-1,
                "CWDP last bank ID oracle failed");
    }
    bool bad=false;
    try { (void)hbfsim::ucie::hbf_mqsim_geometry(large,{16,3,16,false}); }
    catch (const std::invalid_argument&) { bad=true; }
    require(bad,"invalid NCDU admitted");
    bad=false;
    try { (void)hbfsim::ucie::hbf_mqsim_geometry(large,{16,4,4,false}); }
    catch (const std::invalid_argument&) { bad=true; }
    require(bad,"research banks admitted without scenario tag");
    require(HbfBankLayout{}.core_dies_per_channel==1,
            "default NCDU must be one");
    bad=false;
    try { (void)hbfsim::ucie::hbf_cold_read_bank(0,{16,16,0x10000000U,true}); }
    catch (const std::invalid_argument&) { bad=true; }
    require(bad,"oversized bank count admitted or multiplied with wraparound");
    bad=false;
    try { (void)hbfsim::ucie::hbf_media_lpa(0,0,{0,1,16,true}); }
    catch (const std::invalid_argument&) { bad=true; }
    require(bad,"invalid channel count admitted");
    auto wrong_scheme=large;
    wrong_scheme.plane_allocation_scheme=hbfsim::PlaneAllocationScheme::Cwpd;
    bad=false;
    try { (void)hbfsim::ucie::hbf_mqsim_geometry(wrong_scheme,{16,1,16,false}); }
    catch (const std::invalid_argument&) { bad=true; }
    require(bad,"non-CWDP HBF bank mapping admitted");
    bad=false;
    try { hbfsim::MqsimOnlineEngine invalid(wrong_scheme,{16,1,16,false}); }
    catch (const std::invalid_argument&) { bad=true; }
    require(bad,"non-CWDP HBF engine construction admitted");
    const auto reduced_eight=hbfsim::ucie::hbf_mqsim_geometry(
        large,{16,1,8,true});
    require(reduced_eight.chips_per_channel==8,
            "research eight-bank fixture rejected");

    // 1 channel x 16 NCDU x 16 banks x 4 blocks/chip x 256 pages/block.
    auto small=profile(1*gib,1);
    const HbfBankLayout chip255_layout{1,16,16,true};
    std::uint32_t observed_chip=UINT32_MAX;
    std::uint64_t native_reads=0;
    SSD_Components::NVM_PHY_ONFI_NVDDR2::Set_hbf_command_observation_sink(
        [&](const SSD_Components::HBF_Command_Observation& o) {
            if (o.phase!=SSD_Components::HBF_Command_Observation_Phase::COMMAND_ISSUED ||
                o.command_code!=CMD_READ) return;
            for (const auto& tr:o.transactions) {
                if (tr.external_request_id!=9001) continue;
                observed_chip=tr.chip;
                ++native_reads;
            }
        });
    {
        hbfsim::MqsimOnlineEngine engine(small,chip255_layout);
        const auto page=hbfsim::ucie::hbf_media_lpa(255,0,chip255_layout)*4096;
        const auto before=engine.current_time_ns();
        const auto cold=engine.inspect_read_bank(page);
        require(!cold.mapped && cold.native_channel==0 && cold.native_chip==255 &&
                engine.current_time_ns()==before && native_reads==0,
                "cold bank inspection mutated MQSim");
        engine.submit(hbfsim::HbfRequest{.request_id=9001,.sequence=9001,
            .arrival_ns=0,.logical_address=page,.bytes=4096,
            .operation=static_cast<std::uint32_t>(hbfsim::RequestOperation::Read)});
        const auto done=engine.run_next_completion_until(1000000000);
        require(done && done->request_id==9001 &&
                done->status==static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready),
                "native chip255 read did not complete");
        const auto mapped=engine.inspect_read_bank(page);
        require(mapped.mapped && mapped.native_chip==255,
                "mapped FTL bank inspection changed resource identity");
    }
    require(!SSD_Components::NVM_PHY_ONFI_NVDDR2::Hbf_command_observation_failed(),
            "native command observer failed");
    SSD_Components::NVM_PHY_ONFI_NVDDR2::Clear_hbf_command_observation_sink();
    require(native_reads==1 && observed_chip==255,
            "native COMMAND_ISSUED did not use chip ID 255");
    std::cout << "ncdus=1,2,4,8,16 chip255_native_reads=" << native_reads << '\n';
}

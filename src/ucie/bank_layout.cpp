#include <hbfsim/ucie/bank_layout.hpp>

#include <limits>
#include <stdexcept>

namespace hbfsim::ucie {
namespace {
bool valid_ncdu(std::uint32_t n)
{
    return n==1 || n==2 || n==4 || n==8 || n==16;
}
void validate_layout(const HbfBankLayout& layout)
{
    if (!valid_ncdu(layout.core_dies_per_channel))
        throw std::invalid_argument("HBF NCDU must be 1, 2, 4, 8, or 16");
    if (layout.host_channels==0 || layout.host_channels>16 ||
        (layout.host_channels!=16 && !layout.scenario_assumption))
        throw std::invalid_argument("reduced HBF host channels require a scenario fixture");
    if ((layout.banks_per_die!=16 && layout.banks_per_die!=1 &&
         layout.banks_per_die!=2 && layout.banks_per_die!=4 &&
         layout.banks_per_die!=8) ||
        (layout.banks_per_die!=16 && !layout.scenario_assumption))
        throw std::invalid_argument("reduced HBF banks per die require a scenario fixture");
}
}

MqsimGeometry hbf_mqsim_geometry(const Profile& profile,
                                  const HbfBankLayout& layout)
{
    validate_layout(layout);
    if (profile.plane_allocation_scheme!=PlaneAllocationScheme::Cwdp)
        throw std::invalid_argument("HBF bank layout requires CWDP plane allocation");
    if (profile.page_bytes!=4096 || profile.channels!=layout.host_channels ||
        profile.capacity_bytes%4096!=0)
        throw std::invalid_argument("HBF layout requires matched host channels and 4KiB pages");
    const auto chips=layout.core_dies_per_channel*layout.banks_per_die;
    MqsimGeometry geometry{layout.host_channels,chips,1,1};
    validate_profile(profile,geometry);
    const auto blocks=blocks_per_plane(profile,geometry);
    if (blocks<4 || blocks>std::numeric_limits<unsigned int>::max())
        throw std::invalid_argument("HBF bank layout requires 4..UINT_MAX blocks per native chip");
    if (profile.capacity_bytes/4096>std::numeric_limits<unsigned int>::max() ||
        profile.capacity_bytes/512>std::numeric_limits<unsigned int>::max())
        throw std::invalid_argument("HBF stack exceeds MQSim page/sector count limits");
    return geometry;
}

std::uint64_t hbf_media_lpa(std::uint64_t q, std::uint32_t module,
                            const HbfBankLayout& layout)
{
    validate_layout(layout);
    if (module>=layout.host_channels)
        throw std::invalid_argument("invalid HBF module identity");
    if (q>(std::numeric_limits<std::uint64_t>::max()-module)/layout.host_channels)
        throw std::overflow_error("HBF media LPA overflow");
    return q*layout.host_channels+module;
}

HbfReadBank hbf_cold_read_bank(std::uint64_t lpa,
                               const HbfBankLayout& layout)
{
    validate_layout(layout);
    const auto ch=static_cast<std::uint32_t>(lpa%layout.host_channels);
    const auto chip=static_cast<std::uint32_t>(
        (lpa/layout.host_channels)%
        (layout.core_dies_per_channel*layout.banks_per_die));
    return HbfReadBank{.host_channel=ch,
        .core_die=chip/layout.banks_per_die,
        .bank=chip%layout.banks_per_die,
        .native_channel=ch,.native_chip=chip,
        .native_die=0,.native_plane=0,.mapped=false};
}
} // namespace hbfsim::ucie

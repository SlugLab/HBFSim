#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>

namespace hbfsim
{

    struct EmpiricalVmemPoint
    {
        std::uint32_t pages;
        std::uint64_t cumulative_ns;
        std::uint64_t p95_ns;
    };

    struct EmpiricalVmemProfile
    {
        std::string source_kind;
        std::string source_sha256;
        std::uint64_t source_capacity_bytes;
        std::string quantile;
        std::uint32_t sample_count;
        std::array<EmpiricalVmemPoint, 6> read_curve;
        std::uint64_t program_p50_ns;
        std::uint64_t program_p95_ns;
    };

    // One profile-wide flash cell type. QLC is not a native MQSim technology; the
    // four LSB/CSB/MSB/TSB latency levels below are what patches/mqsim/0002-qlc-
    // support.patch adds. Mixing multiple cell types within one device (e.g. an
    // SLC-cache/QLC-capacity design) is not supported: MQSim configures one
    // technology for the whole device.
    enum class NandTechnology
    {
        Slc,
        Mlc,
        Tlc,
        Qlc
    };

    // Host-side channel/die/plane striping order, forwarded to MQSim's
    // Flash_Plane_Allocation_Scheme_Type. This is MQSim's existing address-
    // interleaving-order knob (not previously exposed by HBFSim); it is the
    // closest available proxy for the spec's host-side AXI address interleaving,
    // expressed as which addressing axis (channel/way/die/plane) advances
    // fastest rather than as a raw byte granularity.
    enum class PlaneAllocationScheme
    {
        Cwdp,
        Cwpd,
        Cdwp,
        Cdpw,
        Cpwd,
        Cpdw,
        Wcdp,
        Wcpd,
        Wdcp,
        Wdpc,
        Wpcd,
        Wpdc,
        Dcwp,
        Dcpw,
        Dwcp,
        Dwpc,
        Dpcw,
        Dpwc,
        Pcwd,
        Pcdw,
        Pwcd,
        Pwdc,
        Pdcw,
        Pdwc,
    };

    struct Profile
    {
        std::string name;
        std::uint64_t capacity_bytes;
        std::uint32_t page_bytes;
        std::uint64_t read_latency_ns;
        std::uint64_t program_latency_ns;
        std::uint32_t channels;
        std::uint32_t dies_per_channel;
        std::uint32_t planes_per_die;
        std::uint32_t pages_per_block;
        std::uint32_t channel_width_bits;
        std::uint32_t channel_transfer_rate_mtps;
        std::uint32_t queue_depth;
        std::uint64_t aggregate_bandwidth_bytes_per_s;
        std::uint64_t hbm_cache_bytes;
        // Pages the capacity-mode readahead queues after a demand miss.
        // 0 switches it off, which is the default a profile gets when the
        // field is absent, so no existing profile changes behaviour.
        std::uint32_t readahead_pages;
        double reference_sample_rate;
        std::uint32_t reference_warmup_requests;
        std::uint32_t time_scale;
        std::uint64_t timing_tolerance_ns;
        NandTechnology nand_technology{NandTechnology::Slc};
        std::uint64_t page_read_latency_lsb_ns{0};
        std::uint64_t page_read_latency_csb_ns{0};
        std::uint64_t page_read_latency_msb_ns{0};
        std::uint64_t page_read_latency_tsb_ns{0};
        std::uint64_t page_program_latency_lsb_ns{0};
        std::uint64_t page_program_latency_csb_ns{0};
        std::uint64_t page_program_latency_msb_ns{0};
        std::uint64_t page_program_latency_tsb_ns{0};
        PlaneAllocationScheme plane_allocation_scheme{PlaneAllocationScheme::Cwdp};
        std::optional<EmpiricalVmemProfile> empirical_vmem;
    };

    std::string to_string(NandTechnology technology);
    NandTechnology nand_technology_from_string(const std::string &text);
    std::string to_string(PlaneAllocationScheme scheme);
    PlaneAllocationScheme plane_allocation_scheme_from_string(const std::string &text);

    class ProfileError : public std::runtime_error
    {
    public:
        using std::runtime_error::runtime_error;
    };

    Profile load_profile(const std::filesystem::path &path);
    void validate_profile(const Profile &profile);
    std::uint64_t blocks_per_plane(const Profile &profile);

} // namespace hbfsim

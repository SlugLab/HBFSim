#include <hbfsim/profile.hpp>

#include <json.hpp>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <string>
#include <unordered_map>

namespace hbfsim
{

    std::string to_string(NandTechnology technology)
    {
        switch (technology)
        {
        case NandTechnology::Slc:
            return "SLC";
        case NandTechnology::Mlc:
            return "MLC";
        case NandTechnology::Tlc:
            return "TLC";
        case NandTechnology::Qlc:
            return "QLC";
        }
        throw ProfileError("unknown nand_technology");
    }

    NandTechnology nand_technology_from_string(const std::string &text)
    {
        if (text == "SLC")
            return NandTechnology::Slc;
        if (text == "MLC")
            return NandTechnology::Mlc;
        if (text == "TLC")
            return NandTechnology::Tlc;
        if (text == "QLC")
            return NandTechnology::Qlc;
        throw ProfileError("nand_technology must be one of SLC, MLC, TLC, QLC");
    }

    std::string to_string(PlaneAllocationScheme scheme)
    {
        switch (scheme)
        {
        case PlaneAllocationScheme::Cwdp:
            return "CWDP";
        case PlaneAllocationScheme::Cwpd:
            return "CWPD";
        case PlaneAllocationScheme::Cdwp:
            return "CDWP";
        case PlaneAllocationScheme::Cdpw:
            return "CDPW";
        case PlaneAllocationScheme::Cpwd:
            return "CPWD";
        case PlaneAllocationScheme::Cpdw:
            return "CPDW";
        case PlaneAllocationScheme::Wcdp:
            return "WCDP";
        case PlaneAllocationScheme::Wcpd:
            return "WCPD";
        case PlaneAllocationScheme::Wdcp:
            return "WDCP";
        case PlaneAllocationScheme::Wdpc:
            return "WDPC";
        case PlaneAllocationScheme::Wpcd:
            return "WPCD";
        case PlaneAllocationScheme::Wpdc:
            return "WPDC";
        case PlaneAllocationScheme::Dcwp:
            return "DCWP";
        case PlaneAllocationScheme::Dcpw:
            return "DCPW";
        case PlaneAllocationScheme::Dwcp:
            return "DWCP";
        case PlaneAllocationScheme::Dwpc:
            return "DWPC";
        case PlaneAllocationScheme::Dpcw:
            return "DPCW";
        case PlaneAllocationScheme::Dpwc:
            return "DPWC";
        case PlaneAllocationScheme::Pcwd:
            return "PCWD";
        case PlaneAllocationScheme::Pcdw:
            return "PCDW";
        case PlaneAllocationScheme::Pwcd:
            return "PWCD";
        case PlaneAllocationScheme::Pwdc:
            return "PWDC";
        case PlaneAllocationScheme::Pdcw:
            return "PDCW";
        case PlaneAllocationScheme::Pdwc:
            return "PDWC";
        }
        throw ProfileError("unknown plane_allocation_scheme");
    }

    PlaneAllocationScheme plane_allocation_scheme_from_string(const std::string &text)
    {
        static const std::unordered_map<std::string, PlaneAllocationScheme> schemes{
            {"CWDP", PlaneAllocationScheme::Cwdp},
            {"CWPD", PlaneAllocationScheme::Cwpd},
            {"CDWP", PlaneAllocationScheme::Cdwp},
            {"CDPW", PlaneAllocationScheme::Cdpw},
            {"CPWD", PlaneAllocationScheme::Cpwd},
            {"CPDW", PlaneAllocationScheme::Cpdw},
            {"WCDP", PlaneAllocationScheme::Wcdp},
            {"WCPD", PlaneAllocationScheme::Wcpd},
            {"WDCP", PlaneAllocationScheme::Wdcp},
            {"WDPC", PlaneAllocationScheme::Wdpc},
            {"WPCD", PlaneAllocationScheme::Wpcd},
            {"WPDC", PlaneAllocationScheme::Wpdc},
            {"DCWP", PlaneAllocationScheme::Dcwp},
            {"DCPW", PlaneAllocationScheme::Dcpw},
            {"DWCP", PlaneAllocationScheme::Dwcp},
            {"DWPC", PlaneAllocationScheme::Dwpc},
            {"DPCW", PlaneAllocationScheme::Dpcw},
            {"DPWC", PlaneAllocationScheme::Dpwc},
            {"PCWD", PlaneAllocationScheme::Pcwd},
            {"PCDW", PlaneAllocationScheme::Pcdw},
            {"PWCD", PlaneAllocationScheme::Pwcd},
            {"PWDC", PlaneAllocationScheme::Pwdc},
            {"PDCW", PlaneAllocationScheme::Pdcw},
            {"PDWC", PlaneAllocationScheme::Pdwc},
        };
        const auto iterator = schemes.find(text);
        if (iterator == schemes.end())
        {
            throw ProfileError(
                "plane_allocation_scheme must be one of the 24 channel/way/die/plane "
                "orderings, e.g. CWDP");
        }
        return iterator->second;
    }

    namespace
    {

        bool is_power_of_two(std::uint32_t value)
        {
            return value != 0 && (value & (value - 1)) == 0;
        }

        void require_nonzero(std::uint32_t value, const char *field)
        {
            if (value == 0)
            {
                throw ProfileError(std::string(field) + " must be greater than zero");
            }
        }

        std::uint64_t calculate_blocks_per_plane(const Profile &profile)
        {
            if (profile.page_bytes == 0 || profile.channels == 0 ||
                profile.dies_per_channel == 0 || profile.planes_per_die == 0 ||
                profile.pages_per_block == 0)
            {
                throw ProfileError(
                    "capacity geometry must contain an integral number of blocks per plane");
            }

            const auto denominator =
                static_cast<unsigned __int128>(profile.page_bytes) * profile.channels *
                profile.dies_per_channel * profile.planes_per_die *
                profile.pages_per_block;
            if (denominator > std::numeric_limits<std::uint64_t>::max() ||
                profile.capacity_bytes % static_cast<std::uint64_t>(denominator) != 0)
            {
                throw ProfileError(
                    "capacity geometry must contain an integral number of blocks per plane");
            }

            const auto blocks =
                profile.capacity_bytes / static_cast<std::uint64_t>(denominator);
            if (blocks == 0)
            {
                throw ProfileError(
                    "capacity geometry must contain an integral number of blocks per plane");
            }
            return blocks;
        }

        std::optional<EmpiricalVmemProfile> parse_empirical_vmem(
            const nlohmann::json &document)
        {
            const auto iterator = document.find("empirical_vmem");
            if (iterator == document.end())
            {
                return std::nullopt;
            }

            const auto &empirical = *iterator;
            const auto &curve = empirical.at("read_curve");
            if (!curve.is_array() || curve.size() != 6)
            {
                throw ProfileError(
                    "empirical_vmem read_curve must contain exactly 6 points");
            }

            EmpiricalVmemProfile parsed{
                .source_kind = empirical.at("source_kind").get<std::string>(),
                .source_sha256 = empirical.at("source_sha256").get<std::string>(),
                .source_capacity_bytes =
                    empirical.at("source_capacity_bytes").get<std::uint64_t>(),
                .quantile = empirical.at("quantile").get<std::string>(),
                .sample_count = empirical.at("sample_count").get<std::uint32_t>(),
                .read_curve = {},
                .program_p50_ns =
                    empirical.at("program_p50_ns").get<std::uint64_t>(),
                .program_p95_ns =
                    empirical.at("program_p95_ns").get<std::uint64_t>(),
            };
            for (std::size_t index = 0; index < parsed.read_curve.size(); ++index)
            {
                parsed.read_curve[index] = EmpiricalVmemPoint{
                    .pages = curve.at(index).at("pages").get<std::uint32_t>(),
                    .cumulative_ns =
                        curve.at(index).at("cumulative_ns").get<std::uint64_t>(),
                    .p95_ns = curve.at(index).at("p95_ns").get<std::uint64_t>(),
                };
            }
            return parsed;
        }

    } // namespace

    Profile load_profile(const std::filesystem::path &path)
    {
        std::ifstream input(path);
        if (!input)
        {
            throw ProfileError("could not open profile: " + path.string());
        }

        try
        {
            const auto document = nlohmann::json::parse(input);
            Profile profile{
                .name = document.at("name").get<std::string>(),
                .capacity_bytes = document.at("capacity_bytes").get<std::uint64_t>(),
                .page_bytes = document.at("page_bytes").get<std::uint32_t>(),
                .read_latency_ns =
                    document.at("read_latency_ns").get<std::uint64_t>(),
                .program_latency_ns =
                    document.at("program_latency_ns").get<std::uint64_t>(),
                .channels = document.at("channels").get<std::uint32_t>(),
                .dies_per_channel =
                    document.at("dies_per_channel").get<std::uint32_t>(),
                .planes_per_die =
                    document.at("planes_per_die").get<std::uint32_t>(),
                .pages_per_block =
                    document.at("pages_per_block").get<std::uint32_t>(),
                .channel_width_bits =
                    document.at("channel_width_bits").get<std::uint32_t>(),
                .channel_transfer_rate_mtps =
                    document.at("channel_transfer_rate_mtps").get<std::uint32_t>(),
                .queue_depth = document.at("queue_depth").get<std::uint32_t>(),
                .aggregate_bandwidth_bytes_per_s =
                    document.at("aggregate_bandwidth_bytes_per_s")
                        .get<std::uint64_t>(),
                .hbm_cache_bytes =
                    document.at("hbm_cache_bytes").get<std::uint64_t>(),
                // Optional: absent means the readahead stays off.
                .readahead_pages = document.value("readahead_pages",
                                                  std::uint32_t{0}),
                .reference_sample_rate =
                    document.at("reference_sample_rate").get<double>(),
                .reference_warmup_requests =
                    document.at("reference_warmup_requests").get<std::uint32_t>(),
                .time_scale = document.at("time_scale").get<std::uint32_t>(),
                .timing_tolerance_ns =
                    document.at("timing_tolerance_ns").get<std::uint64_t>(),
                .nand_technology = document.contains("nand_technology")
                                       ? nand_technology_from_string(
                                             document.at("nand_technology").get<std::string>())
                                       : NandTechnology::Slc,
                .plane_allocation_scheme = document.contains("plane_allocation_scheme")
                                               ? plane_allocation_scheme_from_string(
                                                     document.at("plane_allocation_scheme").get<std::string>())
                                               : PlaneAllocationScheme::Cwdp,
                .empirical_vmem = parse_empirical_vmem(document),
            };
            // Per-level LSB/CSB/MSB/TSB latencies default to the flat scalar
            // fields so existing profiles behave exactly as before.
            profile.page_read_latency_lsb_ns = document.value(
                "page_read_latency_lsb_ns", profile.read_latency_ns);
            profile.page_read_latency_csb_ns = document.value(
                "page_read_latency_csb_ns", profile.read_latency_ns);
            profile.page_read_latency_msb_ns = document.value(
                "page_read_latency_msb_ns", profile.read_latency_ns);
            profile.page_read_latency_tsb_ns = document.value(
                "page_read_latency_tsb_ns", profile.read_latency_ns);
            profile.page_program_latency_lsb_ns = document.value(
                "page_program_latency_lsb_ns", profile.program_latency_ns);
            profile.page_program_latency_csb_ns = document.value(
                "page_program_latency_csb_ns", profile.program_latency_ns);
            profile.page_program_latency_msb_ns = document.value(
                "page_program_latency_msb_ns", profile.program_latency_ns);
            profile.page_program_latency_tsb_ns = document.value(
                "page_program_latency_tsb_ns", profile.program_latency_ns);
            validate_profile(profile);
            return profile;
        }
        catch (const ProfileError &)
        {
            throw;
        }
        catch (const nlohmann::json::exception &error)
        {
            throw ProfileError("invalid profile JSON: " + std::string(error.what()));
        }
    }

    void validate_profile(const Profile &profile)
    {
        if (profile.capacity_bytes == 0)
        {
            throw ProfileError("capacity_bytes must be greater than zero");
        }
        if (!is_power_of_two(profile.page_bytes))
        {
            throw ProfileError("page_bytes must be a power of two");
        }
        if (profile.page_bytes < 512)
        {
            throw ProfileError(
                "page_bytes must be at least 512 for MQSim sector alignment");
        }
        if (profile.read_latency_ns == 0)
        {
            throw ProfileError("read_latency_ns must be greater than zero");
        }
        if (profile.program_latency_ns == 0)
        {
            throw ProfileError("program_latency_ns must be greater than zero");
        }
        require_nonzero(profile.channels, "channels");
        require_nonzero(profile.dies_per_channel, "dies_per_channel");
        require_nonzero(profile.planes_per_die, "planes_per_die");
        require_nonzero(profile.pages_per_block, "pages_per_block");
        require_nonzero(profile.channel_width_bits, "channel_width_bits");
        require_nonzero(profile.channel_transfer_rate_mtps,
                        "channel_transfer_rate_mtps");
        require_nonzero(profile.queue_depth, "queue_depth");
        if (profile.aggregate_bandwidth_bytes_per_s == 0)
        {
            throw ProfileError(
                "aggregate_bandwidth_bytes_per_s must be greater than zero");
        }
        if (profile.hbm_cache_bytes > profile.capacity_bytes)
        {
            throw ProfileError("hbm_cache_bytes must not exceed capacity_bytes");
        }
        if (!std::isfinite(profile.reference_sample_rate) ||
            profile.reference_sample_rate < 0.0 ||
            profile.reference_sample_rate > 1.0)
        {
            throw ProfileError("reference_sample_rate must be in [0, 1]");
        }
        require_nonzero(profile.time_scale, "time_scale");
        calculate_blocks_per_plane(profile);

        if (profile.page_read_latency_lsb_ns == 0 ||
            profile.page_read_latency_csb_ns == 0 ||
            profile.page_read_latency_msb_ns == 0 ||
            profile.page_read_latency_tsb_ns == 0 ||
            profile.page_program_latency_lsb_ns == 0 ||
            profile.page_program_latency_csb_ns == 0 ||
            profile.page_program_latency_msb_ns == 0 ||
            profile.page_program_latency_tsb_ns == 0)
        {
            throw ProfileError(
                "per-level page read/program latencies must be greater than zero");
        }

        if (!profile.empirical_vmem)
        {
            return;
        }
        const auto &empirical = *profile.empirical_vmem;
        if (profile.page_bytes != 4096)
        {
            throw ProfileError("empirical_vmem requires page_bytes == 4096");
        }
        const auto valid_sha = empirical.source_sha256.size() == 64 &&
                               std::all_of(empirical.source_sha256.begin(),
                                           empirical.source_sha256.end(),
                                           [](char character)
                                           {
                                               return (character >= '0' &&
                                                       character <= '9') ||
                                                      (character >= 'a' &&
                                                       character <= 'f');
                                           });
        if (!valid_sha)
        {
            throw ProfileError(
                "empirical_vmem source_sha256 must be lowercase hexadecimal SHA256");
        }
        if (empirical.source_kind.empty())
        {
            throw ProfileError("empirical_vmem source_kind must not be empty");
        }
        if (empirical.source_capacity_bytes == 0)
        {
            throw ProfileError(
                "empirical_vmem source_capacity_bytes must be greater than zero");
        }
        if (empirical.quantile != "p50")
        {
            throw ProfileError("empirical_vmem quantile must be p50");
        }
        if (empirical.sample_count == 0)
        {
            throw ProfileError(
                "empirical_vmem sample_count must be greater than zero");
        }

        for (std::size_t index = 0; index < empirical.read_curve.size(); ++index)
        {
            const auto &point = empirical.read_curve[index];
            if (index != 0 &&
                point.pages <= empirical.read_curve[index - 1].pages)
            {
                throw ProfileError(
                    "empirical_vmem pages must be strictly increasing");
            }
            if (point.cumulative_ns == 0 ||
                (index != 0 && point.cumulative_ns <=
                                   empirical.read_curve[index - 1].cumulative_ns))
            {
                throw ProfileError(
                    "empirical_vmem P50 latency must be strictly increasing");
            }
            if (point.p95_ns < point.cumulative_ns)
            {
                throw ProfileError(
                    "empirical_vmem P95 latency must not be below P50");
            }
        }
        if (empirical.read_curve.back().pages > 1023)
        {
            throw ProfileError("empirical_vmem final page must not exceed 1023");
        }
        if (empirical.program_p50_ns == 0)
        {
            throw ProfileError(
                "empirical_vmem program P50 must be greater than zero");
        }
        if (empirical.program_p95_ns < empirical.program_p50_ns)
        {
            throw ProfileError(
                "empirical_vmem program P95 must not be below P50");
        }
    }

    std::uint64_t blocks_per_plane(const Profile &profile)
    {
        return calculate_blocks_per_plane(profile);
    }

} // namespace hbfsim

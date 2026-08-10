#include <hbfsim/profile.hpp>

#include <json.hpp>

#include <cmath>
#include <fstream>
#include <limits>
#include <string>

namespace hbfsim {
namespace {

bool is_power_of_two(std::uint32_t value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

void require_nonzero(std::uint32_t value, const char* field)
{
    if (value == 0) {
        throw ProfileError(std::string(field) + " must be greater than zero");
    }
}

std::uint64_t calculate_blocks_per_plane(const Profile& profile)
{
    if (profile.page_bytes == 0 || profile.channels == 0 ||
        profile.dies_per_channel == 0 || profile.planes_per_die == 0 ||
        profile.pages_per_block == 0) {
        throw ProfileError(
            "capacity geometry must contain an integral number of blocks per plane");
    }

    const auto denominator =
        static_cast<unsigned __int128>(profile.page_bytes) * profile.channels *
        profile.dies_per_channel * profile.planes_per_die *
        profile.pages_per_block;
    if (denominator > std::numeric_limits<std::uint64_t>::max() ||
        profile.capacity_bytes % static_cast<std::uint64_t>(denominator) != 0) {
        throw ProfileError(
            "capacity geometry must contain an integral number of blocks per plane");
    }

    const auto blocks =
        profile.capacity_bytes / static_cast<std::uint64_t>(denominator);
    if (blocks == 0) {
        throw ProfileError(
            "capacity geometry must contain an integral number of blocks per plane");
    }
    return blocks;
}

}  // namespace

Profile load_profile(const std::filesystem::path& path)
{
    std::ifstream input(path);
    if (!input) {
        throw ProfileError("could not open profile: " + path.string());
    }

    try {
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
            .reference_sample_rate =
                document.at("reference_sample_rate").get<double>(),
            .reference_warmup_requests =
                document.at("reference_warmup_requests").get<std::uint32_t>(),
            .time_scale = document.at("time_scale").get<std::uint32_t>(),
            .timing_tolerance_ns =
                document.at("timing_tolerance_ns").get<std::uint64_t>(),
        };
        validate_profile(profile);
        return profile;
    } catch (const ProfileError&) {
        throw;
    } catch (const nlohmann::json::exception& error) {
        throw ProfileError("invalid profile JSON: " + std::string(error.what()));
    }
}

void validate_profile(const Profile& profile)
{
    if (profile.capacity_bytes == 0) {
        throw ProfileError("capacity_bytes must be greater than zero");
    }
    if (!is_power_of_two(profile.page_bytes)) {
        throw ProfileError("page_bytes must be a power of two");
    }
    if (profile.page_bytes < 512) {
        throw ProfileError(
            "page_bytes must be at least 512 for MQSim sector alignment");
    }
    if (profile.read_latency_ns == 0) {
        throw ProfileError("read_latency_ns must be greater than zero");
    }
    if (profile.program_latency_ns == 0) {
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
    if (profile.aggregate_bandwidth_bytes_per_s == 0) {
        throw ProfileError(
            "aggregate_bandwidth_bytes_per_s must be greater than zero");
    }
    if (profile.hbm_cache_bytes > profile.capacity_bytes) {
        throw ProfileError("hbm_cache_bytes must not exceed capacity_bytes");
    }
    if (!std::isfinite(profile.reference_sample_rate) ||
        profile.reference_sample_rate < 0.0 ||
        profile.reference_sample_rate > 1.0) {
        throw ProfileError("reference_sample_rate must be in [0, 1]");
    }
    require_nonzero(profile.time_scale, "time_scale");
    calculate_blocks_per_plane(profile);
}

std::uint64_t blocks_per_plane(const Profile& profile)
{
    return calculate_blocks_per_plane(profile);
}

}  // namespace hbfsim

#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <stdexcept>
#include <string>

namespace hbfsim {

struct EmpiricalVmemPoint {
    std::uint32_t pages;
    std::uint64_t cumulative_ns;
    std::uint64_t p95_ns;
};

struct EmpiricalVmemProfile {
    std::string source_kind;
    std::string source_sha256;
    std::uint64_t source_capacity_bytes;
    std::string quantile;
    std::uint32_t sample_count;
    std::array<EmpiricalVmemPoint, 6> read_curve;
    std::uint64_t program_p50_ns;
    std::uint64_t program_p95_ns;
};

struct Profile {
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
    double reference_sample_rate;
    std::uint32_t reference_warmup_requests;
    std::uint32_t time_scale;
    std::uint64_t timing_tolerance_ns;
    std::optional<EmpiricalVmemProfile> empirical_vmem;
};

class ProfileError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

Profile load_profile(const std::filesystem::path& path);
void validate_profile(const Profile& profile);
std::uint64_t blocks_per_plane(const Profile& profile);

}  // namespace hbfsim

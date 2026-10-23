#pragma once

#include <hbfsim/ucie/device_profile.hpp>

#include <cstdint>
#include <filesystem>
#include <string>

namespace hbfsim::ucie {

// These are finite host-process resources, not HBF hardware parameters.
struct MultistackSoftwareLimits {
    std::uint32_t max_parent_requests{1024};
    std::uint32_t max_child_records{4096};
    std::uint32_t max_read_bytes{65536};
    std::uint32_t max_backing_ranges{1024};
    std::uint64_t host_reassembly_capacity_bytes{4ULL<<20};
};

struct MultistackProfile {
    std::string name;
    std::filesystem::path top_profile_path;
    std::filesystem::path link_profile_path;
    std::filesystem::path media_profile_path;
    std::uint32_t stack_count{};
    std::uint32_t modules_per_stack{};
    std::uint64_t stack_capacity_bytes{};
    std::uint64_t total_service_bandwidth_bytes_per_s{};
    std::uint64_t per_stack_service_bandwidth_bytes_per_s{};
    bool shared_upstream{};
    DeviceProfile device;
    MultistackSoftwareLimits software;
};

// Strict Stage 3 input. The single service cap is divided exactly across
// independent stack workers; the legacy media profile file is never changed.
[[nodiscard]] MultistackProfile load_multistack_profile(
    const std::filesystem::path& path);

} // namespace hbfsim::ucie

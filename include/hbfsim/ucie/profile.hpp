#pragma once

#include <cstdint>
#include <filesystem>
#include <string>

namespace hbfsim::ucie {

// This is an initialized Streaming/AoU link state, not training or discovery.
struct LinkProfile {
    std::string name;
    std::uint32_t version{1};
    std::uint32_t ucie_format{6};
    std::uint32_t aou_hbf_profile_id{1};
    std::uint32_t aou_hbf_revision{0};
    std::uint32_t aou_hbf_option{0};
    std::uint32_t lanes{64};
    std::uint32_t gt_per_second{16};
    std::uint32_t axi_ports{1};
    std::uint64_t local_capacity_bytes{0};
    std::uint32_t initial_ar_granules{12};
    std::uint32_t initial_r_granules{56};
    std::uint32_t max_accepted{4};
    std::uint64_t propagation_ns{0};
};

LinkProfile load_link_profile(const std::filesystem::path& path);
void validate_link_profile(const LinkProfile& profile);
std::uint64_t flit_serialization_ns(const LinkProfile& profile);

} // namespace hbfsim::ucie

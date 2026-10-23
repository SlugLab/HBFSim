#pragma once

#include <hbfsim/profile.hpp>

#include <cstdint>

namespace hbfsim::ucie {

// OCP-facing identity. Native MQSim chips are an internal bank-service proxy,
// not physical HBF chips or dies. Reduced channels/banks are CPU fixtures only.
struct HbfBankLayout {
    std::uint32_t host_channels{16};
    std::uint32_t core_dies_per_channel{1}; // OCP NCDU: 1, 2, 4, 8, or 16
    std::uint32_t banks_per_die{16};        // OCP Figure 4 reference
    bool scenario_assumption{false};
};

struct HbfReadBank {
    std::uint32_t host_channel{};
    std::uint32_t core_die{};
    std::uint32_t bank{};
    std::uint32_t native_channel{};
    std::uint32_t native_chip{};
    std::uint32_t native_die{};
    std::uint32_t native_plane{};
    bool mapped{};
};

[[nodiscard]] MqsimGeometry hbf_mqsim_geometry(const Profile& profile,
                                               const HbfBankLayout& layout);
[[nodiscard]] std::uint64_t hbf_media_lpa(std::uint64_t module_local_page,
                                           std::uint32_t module_id,
                                           const HbfBankLayout& layout);
[[nodiscard]] HbfReadBank hbf_cold_read_bank(std::uint64_t media_lpa,
                                             const HbfBankLayout& layout);

} // namespace hbfsim::ucie

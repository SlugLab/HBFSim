#pragma once

#include <hbfsim/profile.hpp>
#include <hbfsim/ucie/bank_layout.hpp>
#include <hbfsim/ucie/profile.hpp>

#include <cstdint>
#include <filesystem>
#include <string>

namespace hbfsim::ucie {

struct DeviceProfile {
    std::string name;
    LinkProfile link;
    Profile media;
    HbfBankLayout layout;
    std::uint32_t stack_id{};
    std::uint32_t module_id{};
    bool coalescing{true};
    bool buffer_cache{false};
};

// Only the new UCIe/HBF CPU bench uses this strict profile. Legacy Profile
// and its existing JSON schema remain unchanged.
[[nodiscard]] DeviceProfile load_device_profile(const std::filesystem::path& path);

} // namespace hbfsim::ucie

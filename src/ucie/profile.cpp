#include <hbfsim/ucie/profile.hpp>

#include <json.hpp>

#include <fstream>
#include <limits>
#include <set>
#include <stdexcept>

namespace hbfsim::ucie {
namespace {
using nlohmann::json;
void check_keys(const json& value, const std::set<std::string>& expected)
{
    if (!value.is_object() || value.size() != expected.size())
        throw std::invalid_argument("UCIe profile contains missing or unknown fields");
    for (auto it=value.begin(); it!=value.end(); ++it)
        if (!expected.contains(it.key()))
            throw std::invalid_argument("unknown UCIe profile field: " + it.key());
}
std::uint64_t read_uint(const json& object, const char* key, std::uint64_t max)
{
    const auto& value=object.at(key);
    if (!value.is_number_integer())
        throw std::invalid_argument(std::string("UCIe profile requires integer: ")+key);
    std::uint64_t result;
    if (value.is_number_unsigned()) result=value.get<std::uint64_t>();
    else {
        const auto signed_value=value.get<std::int64_t>();
        if (signed_value<0)
            throw std::invalid_argument(std::string("UCIe profile requires nonnegative: ")+key);
        result=static_cast<std::uint64_t>(signed_value);
    }
    if (result>max)
        throw std::invalid_argument(std::string("UCIe profile integer out of range: ")+key);
    return result;
}
} // namespace

void validate_link_profile(const LinkProfile& p)
{
    if (p.version != 1 || p.ucie_format != 6 || p.aou_hbf_profile_id != 1 ||
        p.aou_hbf_revision != 0 || p.aou_hbf_option != 0)
        throw std::invalid_argument("unsupported UCIe/AoU HBF profile");
    if (p.name.empty()) throw std::invalid_argument("UCIe profile name is empty");
    if (p.lanes != 64 || (p.gt_per_second != 8 && p.gt_per_second != 16 &&
                          p.gt_per_second != 32))
        throw std::invalid_argument("HBF UCIe link requires x64 at 8, 16 or 32 GT/s");
    if (p.axi_ports != 1)
        throw std::invalid_argument("Stage 1 models exactly one AXI port");
    if (p.local_capacity_bytes == 0 || p.local_capacity_bytes > (1ULL << 36) ||
        p.local_capacity_bytes % 4096 != 0)
        throw std::invalid_argument("module-local capacity must be 4KiB aligned and at most 2^36 bytes");
    if (p.initial_ar_granules < 3 || p.initial_r_granules < 14 ||
        p.initial_ar_granules > 65535 || p.initial_r_granules > 65535)
        throw std::invalid_argument("initial AoU credits cannot carry one message or exceed 16-bit counter");
    if (p.max_accepted == 0 || p.max_accepted > 65535)
        throw std::invalid_argument("max_accepted must be finite and positive");
    if (p.propagation_ns > 1'000'000'000ULL)
        throw std::invalid_argument("propagation_ns is outside the Stage 1 scenario domain");
}

std::uint64_t flit_serialization_ns(const LinkProfile& p)
{
    validate_link_profile(p);
    return 32 / p.gt_per_second; // 256 B / (x64 * GT/s / 8) = 32/GT ns
}

LinkProfile load_link_profile(const std::filesystem::path& path)
{
    std::ifstream in(path);
    if (!in) throw std::runtime_error("could not open UCIe profile: " + path.string());
    const auto j = nlohmann::json::parse(in);
    check_keys(j,{"name","version","ucie_format","aou_hbf_profile_id",
        "aou_hbf_revision","aou_hbf_option","lanes","gt_per_second",
        "axi_ports","local_capacity_bytes","scenario_assumption"});
    const auto& scenario=j.at("scenario_assumption");
    check_keys(scenario,{"initial_ar_granules","initial_r_granules",
        "max_accepted","propagation_ns"});
    if (!j.at("name").is_string())
        throw std::invalid_argument("UCIe profile name must be a string");
    LinkProfile p{
        .name=j.at("name").get<std::string>(),
        .version=static_cast<std::uint32_t>(read_uint(j,"version",UINT32_MAX)),
        .ucie_format=static_cast<std::uint32_t>(read_uint(j,"ucie_format",UINT32_MAX)),
        .aou_hbf_profile_id=static_cast<std::uint32_t>(read_uint(j,"aou_hbf_profile_id",UINT32_MAX)),
        .aou_hbf_revision=static_cast<std::uint32_t>(read_uint(j,"aou_hbf_revision",UINT32_MAX)),
        .aou_hbf_option=static_cast<std::uint32_t>(read_uint(j,"aou_hbf_option",UINT32_MAX)),
        .lanes=static_cast<std::uint32_t>(read_uint(j,"lanes",UINT32_MAX)),
        .gt_per_second=static_cast<std::uint32_t>(read_uint(j,"gt_per_second",UINT32_MAX)),
        .axi_ports=static_cast<std::uint32_t>(read_uint(j,"axi_ports",UINT32_MAX)),
        .local_capacity_bytes=read_uint(j,"local_capacity_bytes",UINT64_MAX),
        .initial_ar_granules=static_cast<std::uint32_t>(read_uint(scenario,"initial_ar_granules",UINT32_MAX)),
        .initial_r_granules=static_cast<std::uint32_t>(read_uint(scenario,"initial_r_granules",UINT32_MAX)),
        .max_accepted=static_cast<std::uint32_t>(read_uint(scenario,"max_accepted",UINT32_MAX)),
        .propagation_ns=read_uint(scenario,"propagation_ns",UINT64_MAX),
    };
    validate_link_profile(p);
    return p;
}

} // namespace hbfsim::ucie

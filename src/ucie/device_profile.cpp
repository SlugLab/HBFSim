#include <hbfsim/ucie/device_profile.hpp>

#include <json.hpp>

#include <algorithm>
#include <fstream>
#include <limits>
#include <set>
#include <stdexcept>

namespace hbfsim::ucie {
namespace {
using json=nlohmann::json;
void exact_keys(const json& value,const std::set<std::string>& keys)
{
    if (!value.is_object()) throw std::invalid_argument("HBF device profile must be an object");
    for (auto it=value.begin();it!=value.end();++it)
        if (!keys.contains(it.key()))
            throw std::invalid_argument("unknown HBF device profile key: "+it.key());
    for (const auto& key:keys)
        if (!value.contains(key))
            throw std::invalid_argument("missing HBF device profile key: "+key);
}
std::uint64_t u64(const json& value,const char* key,std::uint64_t maximum)
{
    const auto& item=value.at(key);
    if (!item.is_number_integer())
        throw std::invalid_argument(std::string("HBF device integer required: ")+key);
    std::uint64_t number;
    if (item.is_number_unsigned()) number=item.get<std::uint64_t>();
    else {
        const auto signed_number=item.get<std::int64_t>();
        if (signed_number<0)
            throw std::invalid_argument(std::string("negative HBF device value: ")+key);
        number=static_cast<std::uint64_t>(signed_number);
    }
    if (number>maximum)
        throw std::invalid_argument(std::string("HBF device value out of range: ")+key);
    return number;
}
std::string string(const json& value,const char* key)
{
    if (!value.at(key).is_string())
        throw std::invalid_argument(std::string("HBF device string required: ")+key);
    return value.at(key).get<std::string>();
}
bool boolean(const json& value,const char* key)
{
    if (!value.at(key).is_boolean())
        throw std::invalid_argument(std::string("HBF device bool required: ")+key);
    return value.at(key).get<bool>();
}
}

DeviceProfile load_device_profile(const std::filesystem::path& path)
{
    std::ifstream input(path);
    if (!input) throw std::runtime_error("cannot open HBF device profile: "+path.string());
    const auto document=json::parse(input);
    exact_keys(document,{"name","version","link_profile","media_profile",
        "stack_capacity_bytes","host_channels","ncdus","banks_per_die",
        "buffers_per_bank","stack_id","module_id","scenario_assumption"});
    const auto& scenario=document.at("scenario_assumption");
    exact_keys(scenario,{"reduced_topology","coalescing","buffer_cache"});
    if (u64(document,"version",1)!=1 ||
        u64(document,"buffers_per_bank",2)!=2)
        throw std::invalid_argument("unsupported HBF device version or bank buffer count");
    DeviceProfile result;
    result.name=string(document,"name");
    if (result.name.empty()) throw std::invalid_argument("empty HBF device profile name");
    result.layout=HbfBankLayout{
        static_cast<std::uint32_t>(u64(document,"host_channels",16)),
        static_cast<std::uint32_t>(u64(document,"ncdus",16)),
        static_cast<std::uint32_t>(u64(document,"banks_per_die",16)),
        boolean(scenario,"reduced_topology")};
    result.stack_id=static_cast<std::uint32_t>(u64(document,"stack_id",UINT32_MAX));
    result.module_id=static_cast<std::uint32_t>(u64(document,"module_id",UINT32_MAX));
    result.coalescing=boolean(scenario,"coalescing");
    result.buffer_cache=boolean(scenario,"buffer_cache");
    result.link=load_link_profile(string(document,"link_profile"));
    result.media=load_profile(string(document,"media_profile"));
    result.media.capacity_bytes=u64(document,"stack_capacity_bytes",UINT64_MAX);
    result.media.channels=result.layout.host_channels;
    result.media.page_bytes=4096;
    // The legacy profile field is required by old validation but MQSim's
    // HBF flow is TURNED_OFF; no HBM-cache behavior is introduced here.
    result.media.hbm_cache_bytes=std::min(result.media.hbm_cache_bytes,
                                          result.media.capacity_bytes);
    (void)hbf_mqsim_geometry(result.media,result.layout);
    if (result.module_id>=result.layout.host_channels ||
        result.media.capacity_bytes%result.layout.host_channels ||
        result.media.capacity_bytes/result.layout.host_channels!=
            result.link.local_capacity_bytes)
        throw std::invalid_argument("HBF stack/module capacity mismatch");
    return result;
}
} // namespace hbfsim::ucie

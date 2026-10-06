#include <hbfsim/ucie/multistack_profile.hpp>

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
    if (!value.is_object())
        throw std::invalid_argument("multistack profile object required");
    for (auto it=value.begin();it!=value.end();++it)
        if (!keys.contains(it.key()))
            throw std::invalid_argument("unknown multistack key: "+it.key());
    for (const auto& key:keys)
        if (!value.contains(key))
            throw std::invalid_argument("missing multistack key: "+key);
}

std::uint64_t u64(const json& value,const char* key,
                  std::uint64_t minimum,std::uint64_t maximum)
{
    const auto& item=value.at(key);
    if (!item.is_number_integer())
        throw std::invalid_argument(std::string("integer required: ")+key);
    std::uint64_t number;
    if (item.is_number_unsigned()) number=item.get<std::uint64_t>();
    else {
        const auto signed_number=item.get<std::int64_t>();
        if (signed_number<0)
            throw std::invalid_argument(std::string("negative integer: ")+key);
        number=static_cast<std::uint64_t>(signed_number);
    }
    if (number<minimum || number>maximum)
        throw std::invalid_argument(std::string("integer out of range: ")+key);
    return number;
}

std::string string(const json& value,const char* key)
{
    if (!value.at(key).is_string())
        throw std::invalid_argument(std::string("string required: ")+key);
    auto result=value.at(key).get<std::string>();
    if (result.empty())
        throw std::invalid_argument(std::string("empty string: ")+key);
    return result;
}

bool boolean(const json& value,const char* key)
{
    if (!value.at(key).is_boolean())
        throw std::invalid_argument(std::string("boolean required: ")+key);
    return value.at(key).get<bool>();
}

std::filesystem::path source_path(const std::filesystem::path& top,
                                  const std::string& value)
{
    const std::filesystem::path path(value);
    return path.is_absolute()?path:top.parent_path()/path;
}
}

MultistackProfile load_multistack_profile(const std::filesystem::path& path)
{
    std::ifstream input(path);
    if (!input)
        throw std::runtime_error("cannot open multistack profile: "+path.string());
    const auto document=json::parse(input);
    exact_keys(document,{"schema_version","name","stack_count",
        "modules_per_stack","total_capacity_bytes",
        "total_service_bandwidth_bytes_per_s","link_profile","media_profile",
        "ncdus","banks_per_die","buffers_per_bank","upstream_topology",
        "coalescing","buffer_cache","software_limits"});
    if (u64(document,"schema_version",1,1)!=1 ||
        u64(document,"buffers_per_bank",2,2)!=2)
        throw std::invalid_argument("unsupported multistack schema/buffers");
    MultistackProfile result;
    result.name=string(document,"name");
    result.top_profile_path=std::filesystem::weakly_canonical(path);
    result.stack_count=static_cast<std::uint32_t>(
        u64(document,"stack_count",1,UINT32_MAX));
    result.modules_per_stack=static_cast<std::uint32_t>(
        u64(document,"modules_per_stack",1,16));
    const auto total_capacity=u64(document,"total_capacity_bytes",4096,
                                  UINT64_MAX);
    if (total_capacity%result.stack_count)
        throw std::invalid_argument("total capacity must divide by stack count");
    result.stack_capacity_bytes=total_capacity/result.stack_count;
    result.total_service_bandwidth_bytes_per_s=u64(document,
        "total_service_bandwidth_bytes_per_s",1,UINT64_MAX);
    if (result.total_service_bandwidth_bytes_per_s%result.stack_count)
        throw std::invalid_argument("B_total must divide evenly by stack count");
    result.per_stack_service_bandwidth_bytes_per_s=
        result.total_service_bandwidth_bytes_per_s/result.stack_count;
    if (result.stack_capacity_bytes%result.modules_per_stack ||
        result.stack_capacity_bytes/result.modules_per_stack>(1ULL<<36) ||
        result.stack_capacity_bytes/4096==0)
        throw std::invalid_argument("multistack capacity or module address invalid");
    const auto module_bytes=result.stack_capacity_bytes/result.modules_per_stack;
    if (module_bytes%4096)
        throw std::invalid_argument("module capacity must align to HBF page");
    const auto topology=string(document,"upstream_topology");
    if (topology!="shared" && topology!="independent")
        throw std::invalid_argument("unknown upstream topology");
    result.shared_upstream=topology=="shared";
    const auto ncdus=static_cast<std::uint32_t>(u64(document,"ncdus",1,16));
    if (ncdus!=1 && ncdus!=2 && ncdus!=4 && ncdus!=8 && ncdus!=16)
        throw std::invalid_argument("unsupported OCP NCDU encoding");
    const auto banks=static_cast<std::uint32_t>(
        u64(document,"banks_per_die",1,16));
    if (banks!=1 && banks!=2 && banks!=4 && banks!=8 && banks!=16)
        throw std::invalid_argument("unsupported research bank count");
    result.device.name=result.name+"-derived-stack";
    result.device.layout=HbfBankLayout{result.modules_per_stack,ncdus,banks,
        result.modules_per_stack!=16 || banks!=16};
    result.link_profile_path=std::filesystem::weakly_canonical(
        source_path(path,string(document,"link_profile")));
    result.media_profile_path=std::filesystem::weakly_canonical(
        source_path(path,string(document,"media_profile")));
    result.device.link=load_link_profile(result.link_profile_path);
    result.device.media=load_profile(result.media_profile_path);
    result.device.media.capacity_bytes=result.stack_capacity_bytes;
    result.device.media.channels=result.modules_per_stack;
    result.device.media.page_bytes=4096;
    result.device.media.aggregate_bandwidth_bytes_per_s=
        result.per_stack_service_bandwidth_bytes_per_s;
    result.device.media.hbm_cache_bytes=std::min(
        result.device.media.hbm_cache_bytes,result.stack_capacity_bytes);
    result.device.coalescing=boolean(document,"coalescing");
    result.device.buffer_cache=boolean(document,"buffer_cache");
    if (result.device.link.local_capacity_bytes!=module_bytes)
        throw std::invalid_argument("link and derived module capacity mismatch");
    (void)hbf_mqsim_geometry(result.device.media,result.device.layout);

    const auto& limits=document.at("software_limits");
    exact_keys(limits,{"max_parent_requests","max_child_records",
        "max_read_bytes","max_backing_ranges",
        "host_reassembly_capacity_bytes"});
    result.software.max_parent_requests=static_cast<std::uint32_t>(
        u64(limits,"max_parent_requests",1,UINT32_MAX));
    result.software.max_child_records=static_cast<std::uint32_t>(
        u64(limits,"max_child_records",1,UINT32_MAX));
    result.software.max_read_bytes=static_cast<std::uint32_t>(
        u64(limits,"max_read_bytes",1,65536));
    result.software.max_backing_ranges=static_cast<std::uint32_t>(
        u64(limits,"max_backing_ranges",1,UINT32_MAX));
    result.software.host_reassembly_capacity_bytes=u64(limits,
        "host_reassembly_capacity_bytes",64,UINT64_MAX);
    return result;
}
} // namespace hbfsim::ucie

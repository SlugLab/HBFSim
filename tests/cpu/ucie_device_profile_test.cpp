#include <hbfsim/ucie/device_profile.hpp>

#include <json.hpp>

#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>

namespace {
void require(bool value,const char* why)
{ if (!value) throw std::runtime_error(why); }
bool rejected(nlohmann::json document)
{
    const auto path=std::filesystem::temp_directory_path()/
        "hbfsim-ucie-device-invalid-profile.json";
    { std::ofstream out(path); out << document.dump(); }
    bool failed=false;
    try { (void)hbfsim::ucie::load_device_profile(path); }
    catch (const std::exception&) { failed=true; }
    std::filesystem::remove(path);
    return failed;
}
}

int main()
{
    using hbfsim::ucie::load_device_profile;
    const auto reference=load_device_profile(
        "configs/profiles/ucie/hbf-stage2-reference.json");
    require(reference.media.capacity_bytes==512ULL*(1ULL<<30) &&
            reference.layout.host_channels==16 &&
            reference.layout.core_dies_per_channel==1 &&
            reference.layout.banks_per_die==16 &&
            reference.link.local_capacity_bytes==32ULL*(1ULL<<30),
            "OCP-scale Stage2 profile did not resolve without allocation");
    const auto on=load_device_profile(
        "configs/profiles/ucie/hbf-stage2-fixture-on.json");
    const auto off=load_device_profile(
        "configs/profiles/ucie/hbf-stage2-fixture-off.json");
    require(on.coalescing && !off.coalescing && !on.buffer_cache &&
            on.layout.scenario_assumption && on.media.capacity_bytes==(8ULL<<20),
            "ON/OFF fixture profile mismatch");
    std::ifstream input("configs/profiles/ucie/hbf-stage2-fixture-on.json");
    auto value=nlohmann::json::parse(input);
    auto unknown=value; unknown["unrecognized"]=1;
    require(rejected(unknown),"unknown device profile key was admitted");
    auto fractional=value; fractional["ncdus"]=1.5;
    require(rejected(fractional),"fractional NCDU was truncated");
    auto buffer=value; buffer["buffers_per_bank"]=3;
    require(rejected(buffer),"three bank buffers were admitted");
    auto untagged=value; untagged["scenario_assumption"]["reduced_topology"]=false;
    require(rejected(untagged),"research reduced banks omitted scenario tag");
    auto capacity=value; capacity["stack_capacity_bytes"]=-4096;
    require(rejected(capacity),"negative stack capacity was admitted");
}

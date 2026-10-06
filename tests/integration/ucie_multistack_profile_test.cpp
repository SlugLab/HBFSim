#include <hbfsim/ucie/multistack_frontend.hpp>
#include <hbfsim/ucie/multistack_profile.hpp>

#include <json.hpp>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

#include <unistd.h>

namespace {
using nlohmann::json;
using namespace hbfsim::ucie;

void require(bool value,const char* message)
{ if (!value) throw std::runtime_error(message); }

std::filesystem::path write(const std::filesystem::path& directory,
                            const char* name,const json& value)
{
    const auto path=directory/name;
    std::ofstream output(path);
    output << value.dump(2) << '\n';
    if (!output) throw std::runtime_error("cannot write profile fixture");
    return path;
}

void reject(const std::filesystem::path& directory,const char* name,
            const json& value)
{
    const auto path=write(directory,name,value);
    bool caught=false;
    try { (void)load_multistack_profile(path); }
    catch (const std::exception&) { caught=true; }
    require(caught,"invalid top profile was accepted");
}

std::uint64_t one_read(const std::filesystem::path& config,
                       const char* worker)
{
    MultistackFrontend front(config,worker);
    front.add_backing({1,0,64,10,0,1,7,true});
    require(front.try_submit({.request_id=1,.arrival_ns=0,.address=0,
            .bytes=64,.endpoint_id=7,.axi_id=1}),
            "real top-profile worker rejected read");
    for (std::uint64_t h=0;h<150000;++h) {
        front.advance_until(h);
        if (const auto result=front.peek_completion(1)) {
            require(result->result==DeviceResult::Ready,
                    "real top-profile worker failed read");
            const auto ready=result->ready_ns;
            (void)front.consume_completion(1);
            require(front.worker(0).stats().native_commands==1,
                    "service-cap test did not execute native MQSim");
            return ready;
        }
    }
    throw std::runtime_error("top-profile read did not finish");
}
}

int main(int argc,char** argv)
{
    if (argc!=2) return 2;
    try {
        const auto base=load_multistack_profile(
            "configs/profiles/ucie/hbf-stage3-4x512gib-top.json");
        require(base.stack_count==4 && base.modules_per_stack==16 &&
                base.stack_capacity_bytes==(512ULL<<30) &&
                base.device.link.local_capacity_bytes==(32ULL<<30) &&
                base.per_stack_service_bandwidth_bytes_per_s==128000000000ULL,
                "2 TiB/4 top profile resolved incorrectly");
        const auto directory=std::filesystem::temp_directory_path()/
            ("ucie-stage3-profile-"+std::to_string(::getpid()));
        std::filesystem::create_directories(directory);
        auto cleanup=[&]() { std::filesystem::remove_all(directory); };
        try {
            json top={
                {"schema_version",1},{"name","stage3-cap-test"},
                {"stack_count",1},{"modules_per_stack",1},
                {"total_capacity_bytes",4ULL<<20},
                {"total_service_bandwidth_bytes_per_s",100000000ULL},
                {"link_profile",std::filesystem::absolute(
                    "configs/profiles/ucie/hbf-stage3-small-link.json").string()},
                {"media_profile",std::filesystem::absolute(
                    "configs/profiles/nominal.json").string()},
                {"ncdus",1},{"banks_per_die",1},{"buffers_per_bank",2},
                {"upstream_topology","independent"},
                {"coalescing",false},{"buffer_cache",false},
                {"software_limits",{{"max_parent_requests",2},
                    {"max_child_records",2},{"max_read_bytes",8192},
                    {"max_backing_ranges",2},
                    {"host_reassembly_capacity_bytes",128}}}};
            const auto slow=write(directory,"slow.json",top);
            require(load_multistack_profile(slow).software.max_read_bytes==8192 &&
                    load_multistack_profile(slow).software.
                        host_reassembly_capacity_bytes==128,
                    "legal small reassembly capacity was rejected");
            auto bad=top; bad["unknown"]=1;
            reject(directory,"unknown.json",bad);
            bad=top; bad["stack_count"]=1.5;
            reject(directory,"float.json",bad);
            bad=top; bad["stack_count"]=-1;
            reject(directory,"negative.json",bad);
            bad=top; bad["stack_count"]=3;
            reject(directory,"capacity-division.json",bad);
            bad=top; bad["stack_count"]=2;
            bad["total_service_bandwidth_bytes_per_s"]=100000001;
            reject(directory,"service-division.json",bad);
            bad=top; bad["total_capacity_bytes"]=(1ULL<<36)+4096;
            reject(directory,"address-width.json",bad);
            bad=top; bad["media_profile"]="nominal.json";
            reject(directory,"relative-source.json",bad);

            const auto slow_ns=one_read(slow,argv[1]);
            top["total_service_bandwidth_bytes_per_s"]=1000000000ULL;
            const auto fast=write(directory,"fast.json",top);
            const auto fast_ns=one_read(fast,argv[1]);
            require(slow_ns>fast_ns,
                    "changing B_total did not change real media completion");
            std::cout << "top_profile total=2199023255552 stack=549755813888"
                      << " per_stack_service=128000000000"
                      << " slow_B=100000000 slow_ns=" << slow_ns
                      << " fast_B=1000000000 fast_ns=" << fast_ns
                      << " native_each=1\n";
        } catch (...) { cleanup(); throw; }
        cleanup();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}

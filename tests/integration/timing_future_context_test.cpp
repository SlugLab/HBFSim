// TEST_ONLY: actual public context construction with a CPU fake CUDA driver.
// The v4 callback deliberately fails after retaining a generation; no daemon
// or GPU payload starts, and the process owns all retained fixture mappings.
#include <hbfsim/api.h>
#include <hbfsim/launch_gate_abi.hpp>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <sys/mman.h>
#include <unistd.h>

extern "C" void fakeCudaSetCurrentDomain(std::uintptr_t,int);
extern "C" void fakeCudaResetLifecycleCounts();
extern "C" int fakeCudaUnregisterCount();
extern "C" int fakeCudaLaunchCount();
namespace {
std::uintptr_t retained_alias=0;
hbfsim::timing_future::Capabilities captured{};
unsigned calls=0,retire_calls=0;
#define CHECK(x) do { if (!(x)) { std::fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); std::exit(1); } } while(false)
int legacy(std::uintptr_t,std::uintptr_t,std::uintptr_t,int,std::uint64_t*) noexcept {return -1;}
int publish(std::uintptr_t,std::uint64_t,std::uintptr_t,std::uintptr_t,hbfsim::LaunchGatePublishRange,void*) noexcept {return -1;}
int policy(std::uintptr_t,std::uint64_t,std::uintptr_t,std::uintptr_t,hbfsim::LaunchGateRangePolicy,hbfsim::LaunchGatePublishRange,void*) noexcept {return -1;}
int begin(std::uintptr_t,std::uint64_t,std::uintptr_t*) noexcept {++retire_calls;return -1;}
int end(std::uintptr_t) noexcept {return -1;}
int activate(std::uintptr_t owner,std::uintptr_t alias,std::uintptr_t context,int device,
    const hbfsim::timing_future::Capabilities* capabilities,std::uint64_t* generation) noexcept
{
    CHECK(owner && alias && context==0xCA00 && device==3 && capabilities && generation);
    retained_alias=alias;captured=*capabilities;++calls;*generation=42;
    return -1;
}
const hbfsim::LaunchGateApiV4 api{4,sizeof(hbfsim::LaunchGateApiV4),legacy,publish,publish,
    begin,end,end,end,policy,activate};
}
extern "C" const void* hbfsim_launch_gate_get_api(std::uint32_t version) noexcept
{return version==4 ? &api : nullptr;}

int main(int argc,char** argv)
{
    CHECK(argc==3);
    char directory[]=".future-context-XXXXXX";CHECK(::mkdtemp(directory));
    const std::string scenario=argv[1];std::ifstream input(argv[2]);
    std::string profile((std::istreambuf_iterator<char>(input)),{});
    auto pos=profile.find("\"time_scale\": 100");CHECK(pos!=std::string::npos);
    if(scenario!="scaled")profile.replace(pos,17,"\"time_scale\": 1");
    const auto path=std::filesystem::path(directory)/"profile.json";
    {std::ofstream file(path);file<<profile;}
    const auto profile_path=path.string();
    const hbfsim_options options{.profile_path=profile_path.c_str(),.report_dir=directory,
        .mode=scenario=="reference"?0U:scenario=="hybrid"?2U:1U,
        .ring_capacity=8,.request_timeout_ns=1000000000};
    fakeCudaSetCurrentDomain(0xCA00,3);fakeCudaResetLifecycleCounts();
    // Constructor validates the daemon path before activating; this fixture
    // always fails activation, so the executable is never spawned.
    CHECK(::setenv("HBFSIM_DAEMON_PATH","/bin/false",1)==0);
    hbfsim_context* context=nullptr;
    const auto status=hbfsim_context_create(&options,&context);
    if(status!=HBFSIM_CUDA_ERROR)std::fprintf(stderr,"context status=%d captured=%u\n",status,calls);
    CHECK(status==HBFSIM_CUDA_ERROR);
    CHECK(context==nullptr && calls==1 && retire_calls==1);
    CHECK(hbfsim::timing_future::valid_capabilities(captured));
    CHECK(captured.bits==(scenario=="fast" ? hbfsim::timing_future::kFastScalarTiming : 0));
    CHECK(fakeCudaUnregisterCount()==0 && fakeCudaLaunchCount()==0);
    unsigned char mapped=0;
    CHECK(::mincore(reinterpret_cast<void*>(retained_alias),static_cast<std::size_t>(::sysconf(_SC_PAGESIZE)),&mapped)==0);
    std::filesystem::remove_all(directory);
    std::puts("PASS: actual context-derived capability and failed-activation retained mapping (TEST_ONLY)");
}

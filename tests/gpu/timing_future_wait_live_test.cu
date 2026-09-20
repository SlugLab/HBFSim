#define HBFSIM_ENABLE_TIMING_FUTURES 1
#include "../../src/cuda_runtime/device/hbf_device.cuh"
#include <cstddef>
#include <cstdint>

namespace live {
namespace tf=hbfsim::timing_future;
constexpr unsigned instruction=17, ring=2; constexpr std::uint64_t generation=7;
enum class Case:unsigned {Ready,Timeout,Shutdown,Generation,Native,Mixed};
struct Result {std::uint64_t value,elapsed,begin,target,reservation;unsigned issue,poll,status,state,mask,leader;};
}

#ifdef HBFSIM_LIVE_DEVICE_IMAGE
#include "../../src/cuda_runtime/device/hbf_device.cu"
extern "C" __device__ unsigned timing_future_live_wait_entered=0;
__device__ __forceinline__ std::uint64_t live_clock(){std::uint64_t n;asm volatile("mov.u64 %0, %%globaltimer;":"=l"(n)::"memory");return n;}
extern "C" __global__ void timing_future_wait_live(live::Case kind,const std::uint32_t* modeled,
 const std::uint32_t* native_value,live::Result* out,std::uint64_t fault_delay)
{
 using namespace live;const unsigned lane=threadIdx.x;
 if(blockIdx.x==1){if(lane||!(kind==Case::Shutdown||kind==Case::Generation))return;
  while(atomicAdd(&timing_future_live_wait_entered,0U)==0U){}
  const auto start=live_clock();while(live_clock()-start<fault_delay){}
  auto* h=reinterpret_cast<hbfsim::device::SharedControlHeader*>(__hbfsim_timing_future_config_v1.control_alias);
  if(kind==Case::Shutdown)h->shutdown=1;else h->control_generation=generation+1;__threadfence_system();return;}
 if(kind!=Case::Mixed&&lane)return;
 const unsigned index=kind==Case::Mixed?(lane<16?0:32):0;
 const auto* source=kind==Case::Native?native_value:modeled+index;
 tf::TimingFutureLaneMetadataV1 meta{};
 auto f=__hbfsim_timing_future_issue_v1(reinterpret_cast<std::uint64_t>(source),4,instruction,0,&meta);
 const auto issue=f.status;const auto native=std::uint64_t(*source);
 const auto poll=__hbfsim_timing_future_poll_v1(&f,&meta,instruction,4);
 if(kind==Case::Shutdown||kind==Case::Generation)atomicExch(&timing_future_live_wait_entered,1U);
 const auto begin=live_clock();const auto got=__hbfsim_timing_future_wait_v1(&f,&meta,native,instruction,4,0);const auto end=live_clock();
 out[lane]={native,end-begin,begin,kind==Case::Timeout?f.deadline_ns:f.ready_ns,f.reservation_id,
  issue,poll,got.status,unsigned(got.state),meta.group_mask,meta.group_leader};
}
#else
#include <cuda.h>
#include <algorithm>
#include <cstdio>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>
namespace {
using namespace live;using hbfsim::device::SharedControlHeader;using hbfsim::device::SharedRangeRecord;
using hbfsim::device::SharedRequestSlot;using hbfsim::device::SharedCompletionSlot;using hbfsim::device::PageEntry;
void ck(CUresult s,const char* w){if(s==CUDA_SUCCESS)return;const char* x="CUDA error";cuGetErrorString(s,&x);throw std::runtime_error(std::string(w)+": "+x);}
void req(bool v,const char* w){if(!v)throw std::runtime_error(w);}
std::size_t control_bytes(){return sizeof(SharedControlHeader)+sizeof(SharedRangeRecord)*hbfsim::device::kRangeCapacity+sizeof(SharedRequestSlot)*ring+sizeof(SharedCompletionSlot)*ring+sizeof(PageEntry)*ring;}
const char* cname(Case c){const char* n[]={"ready","timeout","shutdown_midwait","generation_midwait","native","mixed_groups"};return n[unsigned(c)];}
struct Fixture{
 CUmodule mod{};CUfunction fn{};CUdeviceptr control{},values{},native{},results{},traces{};
 Fixture(const char* path){std::ifstream f(path,std::ios::binary);req(bool(f),"open PTX");std::string p{std::istreambuf_iterator<char>(f),{}};ck(cuModuleLoadData(&mod,p.c_str()),"load PTX");ck(cuModuleGetFunction(&fn,mod,"timing_future_wait_live"),"get kernel");
  ck(cuMemAlloc(&control,control_bytes()),"alloc control");ck(cuMemAlloc(&values,256),"alloc values");ck(cuMemAlloc(&native,4),"alloc native");ck(cuMemAlloc(&results,32*sizeof(Result)),"alloc results");ck(cuMemAlloc(&traces,128*sizeof(tf::Trace)),"alloc traces");}
 ~Fixture(){if(traces)cuMemFree(traces);if(results)cuMemFree(results);if(native)cuMemFree(native);if(values)cuMemFree(values);if(control)cuMemFree(control);if(mod)cuModuleUnload(mod);}
 std::pair<CUdeviceptr,std::size_t> sym(const char* n){CUdeviceptr p{};std::size_t z{};ck(cuModuleGetGlobal(&p,&z,mod,n),n);return {p,z};}
 template<class T>void put(const char* n,const T& v){auto[p,z]=sym(n);req(z==sizeof(v),"symbol size");ck(cuMemcpyHtoD(p,&v,sizeof(v)),"write symbol");}
 tf::Counters counts(){tf::Counters c{};auto[p,z]=sym("__hbfsim_timing_future_counters_v1");req(z==sizeof(c),"counter size");ck(cuMemcpyDtoH(&c,p,sizeof(c)),"read counter");return c;}
 void reset(std::uint64_t latency,std::uint64_t timeout){std::vector<std::byte>b(control_bytes());auto*h=reinterpret_cast<SharedControlHeader*>(b.data());h->magic=hbfsim::device::kControlMagic;h->abi_version=4;h->header_bytes=sizeof(*h);h->region_bytes=b.size();h->ring_capacity=ring;h->range_capacity=hbfsim::device::kRangeCapacity;h->page_capacity=ring;h->range_count=1;h->range_offset=sizeof(*h);h->request_offset=h->range_offset+sizeof(SharedRangeRecord)*hbfsim::device::kRangeCapacity;h->completion_offset=h->request_offset+sizeof(SharedRequestSlot)*ring;h->page_offset=h->completion_offset+sizeof(SharedCompletionSlot)*ring;h->heartbeat_ns=1;h->request_timeout_ns=timeout;h->heartbeat_timeout_ns=10000000000ULL;h->time_scale=1;h->control_generation=generation;h->read_latency_ns=latency;h->program_latency_ns=1;h->aggregate_bandwidth_bytes_per_s=1000000000ULL;h->timing_model=1;
  auto*r=reinterpret_cast<SharedRangeRecord*>(b.data()+h->range_offset);*r={values,256,0,1,1,1,0,0,0,128,0};std::uint32_t v[64];for(unsigned i=0;i<64;i++)v[i]=0xabc00000U+i;const std::uint32_t seed=0x13579bdfU;ck(cuMemcpyHtoD(control,b.data(),b.size()),"write control");ck(cuMemcpyHtoD(values,v,sizeof(v)),"write values");ck(cuMemcpyHtoD(native,&seed,4),"write native");ck(cuMemsetD8(results,0,32*sizeof(Result)),"clear results");ck(cuMemsetD8(traces,0,128*sizeof(tf::Trace)),"clear trace");
  tf::ModuleConfig cfg{};cfg.enabled=1;cfg.control_alias=control;cfg.control_generation=generation;cfg.trace_address=traces;cfg.trace_capacity=128;tf::Counters c{};const unsigned long long a=control,g=generation;const unsigned wait_entered=0;put("__hbfsim_control",a);put("__hbfsim_control_generation",g);put("__hbfsim_timing_future_config_v1",cfg);put("__hbfsim_timing_future_counters_v1",c);put("timing_future_live_wait_entered",wait_entered);}
 std::vector<Result> run(Case c,unsigned threads,std::uint64_t delay=0){unsigned blocks=c==Case::Shutdown||c==Case::Generation?2:1;void*args[]={&c,&values,&native,&results,&delay};ck(cuLaunchKernel(fn,blocks,1,1,threads,1,1,0,nullptr,args,nullptr),"launch");ck(cuCtxSynchronize(),"sync");std::vector<Result>o(threads);ck(cuMemcpyDtoH(o.data(),results,o.size()*sizeof(Result)),"read results");return o;}
};
void receipt(Case k,const Result&r,const tf::Counters&c){std::printf("{\"case\":\"%s\",\"issue\":%u,\"poll\":%u,\"status\":%u,\"state\":%u,\"value\":%llu,\"elapsed_ns\":%llu,\"begin_ns\":%llu,\"target_ns\":%llu,\"issued\":%llu,\"pending\":%llu,\"ready\":%llu,\"consumed\":%llu,\"errors\":%llu,\"groups_issued\":%llu,\"groups_completed\":%llu}\n",cname(k),r.issue,r.poll,r.status,r.state,(unsigned long long)r.value,(unsigned long long)r.elapsed,(unsigned long long)r.begin,(unsigned long long)r.target,(unsigned long long)c.issued,(unsigned long long)c.pending,(unsigned long long)c.model_ready,(unsigned long long)c.consumed,(unsigned long long)c.terminal_error,(unsigned long long)c.groups_issued,(unsigned long long)c.groups_completed);}
}
int main(int ac,char**av){CUcontext ctx{};try{req(ac==2,"usage: test DEVICE.ptx");ck(cuInit(0),"init");CUdevice d{};ck(cuDeviceGet(&d,0),"device");ck(cuCtxCreate(&ctx,nullptr,0,d),"context");Fixture f(av[1]);
 auto one=[&](Case k,std::uint64_t l,std::uint64_t t,std::uint64_t fault=0){f.reset(l,t);auto o=f.run(k,1,fault);auto c=f.counts();receipt(k,o[0],c);return std::pair{o[0],c};};
 auto[ready,rc]=one(Case::Ready,2000000,20000000);req(ready.issue==tf::kPending&&ready.poll==tf::kPending,"ready not pending");req(ready.status==tf::kReady&&ready.state==unsigned(tf::State::Consumed)&&ready.value==0xabc00000U,"ready result");req(ready.begin>=ready.target||ready.elapsed>=ready.target-ready.begin,"early ready");req(rc.issued==1&&rc.model_ready==1&&rc.consumed==1&&rc.pending==0,"ready counters");
 auto[timed,tc]=one(Case::Timeout,20000000,2000000);req(timed.issue==tf::kPending&&timed.poll==tf::kPending,"timeout not pending");req(timed.status==tf::kTimeout&&timed.state==unsigned(tf::State::TerminalError),"timeout result");req(timed.begin>=timed.target||timed.elapsed>=timed.target-timed.begin,"early timeout");
 auto[shut,sc]=one(Case::Shutdown,20000000,50000000,1000000);req(shut.poll==tf::kPending&&shut.status==tf::kDaemonLost,"shutdown result");auto[gen,gc]=one(Case::Generation,20000000,50000000,1000000);req(gen.poll==tf::kPending&&gen.status==tf::kUnsupported,"generation result");
 auto[nat,nc]=one(Case::Native,2000000,20000000);req(nat.issue==tf::kReady&&nat.poll==tf::kReady&&nat.status==tf::kReady&&nat.value==0x13579bdfU&&nat.reservation==0&&nc.native_loads==1&&nc.issued==0,"native result");
 f.reset(2000000,20000000);auto m=f.run(Case::Mixed,32);auto mc=f.counts();receipt(Case::Mixed,m[0],mc);req(std::all_of(m.begin(),m.end(),[](const Result&r){return r.issue==tf::kPending&&r.poll==tf::kPending&&r.status==tf::kReady&&r.state==unsigned(tf::State::Consumed);}),"mixed states");for(unsigned i=0;i<32;i++)req(m[i].value==0xabc00000U+(i<16?0:32),"mixed data");req(m[0].mask==0xffff&&m[0].leader==0&&m[16].mask==0xffff0000U&&m[16].leader==16&&mc.issued==32&&mc.consumed==32&&mc.pending==0&&mc.groups_issued==2&&mc.groups_completed==2,"mixed accounting");
 std::puts("{\"status\":\"PASS\",\"device_storage_upper_mib\":7}");cuCtxDestroy(ctx);return 0;}catch(const std::exception&e){std::fprintf(stderr,"FAIL: %s\n",e.what());if(ctx)cuCtxDestroy(ctx);return 1;}}
#endif

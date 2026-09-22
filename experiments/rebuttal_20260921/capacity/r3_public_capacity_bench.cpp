#include <hbfsim/api.h>
#include <cuda.h>
#include <cuda_runtime_api.h>
#include <algorithm>
#include <bit>
#include <chrono>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <fcntl.h>
#include <unistd.h>
namespace {
using u64=std::uint64_t;
constexpr u64 LOGICAL=110ULL<<30,CACHE=2ULL<<30,REGION=64ULL<<10;
constexpr u64 SEED=0x5233504147455345ULL,SALT=0x48424653494dULL;
constexpr size_t NREG=65;
struct Opt{std::string profile,report,backing,output,ptx,workload;u64 page=0,denom=1;};
struct RR{size_t index;u64 base,expected,actual,mismatches;bool pass;};
struct Stage{std::string temp;u64 wall,submitted,completed;hbfsim_capacity_stats_v1 before{},after{};std::vector<RR> regions;bool pass;};
[[noreturn]]void die(const std::string&s){throw std::runtime_error(s);}
void req(bool v,const std::string&s){if(!v)die(s);}
void cok(cudaError_t e,const char*s){if(e!=cudaSuccess)die(std::string(s)+": "+cudaGetErrorString(e));}
void dok(CUresult e,const char*s){if(e==CUDA_SUCCESS)return;const char*t=nullptr;cuGetErrorString(e,&t);die(std::string(s)+": "+(t?t:"CUDA error"));}
u64 num(const char*s){char*e=nullptr;errno=0;auto v=std::strtoull(s,&e,10);if(errno||!e||*e)die("bad integer");return v;}
Opt parse(int ac,char**av){Opt o;for(int i=1;i<ac;i++){req(i+1<ac,"missing value");std::string k=av[i],v=av[++i];
 if(k=="--profile")o.profile=v;else if(k=="--report-dir")o.report=v;else if(k=="--backing-dir")o.backing=v;
 else if(k=="--output")o.output=v;else if(k=="--ptx")o.ptx=v;else if(k=="--workload")o.workload=v;
 else if(k=="--page-bytes")o.page=num(v.c_str());else if(k=="--coverage-denominator")o.denom=num(v.c_str());else die("unknown "+k);}
 const std::vector<u64>allowed{4096,8192,16384,32768,65536};req(std::find(allowed.begin(),allowed.end(),o.page)!=allowed.end(),"bad page");
 req(o.workload=="fixed_dense"||o.workload=="fixed_sparse"||o.workload=="relative","bad workload");
 req(!o.profile.empty()&&!o.report.empty()&&!o.backing.empty()&&!o.output.empty()&&!o.ptx.empty(),"missing path");
 req(o.workload=="relative"?(o.denom==1||o.denom==4||o.denom==16):o.denom==1,"bad fraction");return o;}
u64 mix(u64 v){v+=0x9e3779b97f4a7c15ULL;v=(v^(v>>30))*0xbf58476d1ce4e5b9ULL;v=(v^(v>>27))*0x94d049bb133111ebULL;return v^(v>>31);}
u64 value(u64 off){return mix(SEED^off);}
u64 checksum(u64 base,u64 words){u64 c=mix(SEED^SALT);for(u64 i=0;i<words;i++)c=std::rotl(c^(value(base+i*8)+i),13);return c;}
u64 actual_checksum(const std::vector<u64>&v){u64 c=mix(SEED^SALT);for(u64 i=0;i<v.size();i++)c=std::rotl(c^(v[i]+i),13);return c;}
std::vector<u64>bases(){u64 last=LOGICAL-REGION;std::vector<u64>v;for(u64 i=0;i<NREG;i++){u64 x=(last/(NREG-1))*i+((last%(NREG-1))*i)/(NREG-1);v.push_back((x/REGION)*REGION);}
 v[NREG/2]=(LOGICAL/2/REGION)*REGION;v.front()=0;v.back()=last;std::ranges::sort(v);v.erase(std::unique(v.begin(),v.end()),v.end());
 req(v.size()==NREG&&v.front()==0&&v.back()==last&&std::find(v.begin(),v.end(),LOGICAL/2)!=v.end(),"bad regions");return v;}
u64 access(const Opt&o){return o.workload=="fixed_dense"?REGION:o.workload=="fixed_sparse"?4096:o.page/o.denom;}
void pwrite_all(int fd,const void*p,size_t n,u64 off){auto*c=(const char*)p;size_t d=0;while(d<n){auto r=::pwrite(fd,c+d,n-d,off+d);if(r>0)d+=r;else if(r<0&&errno==EINTR)continue;else die("pwrite");}}
std::filesystem::path backing(const Opt&o,const std::vector<u64>&b,u64 bytes){std::filesystem::create_directories(o.backing);auto s=(std::filesystem::path(o.backing)/"r3-pages-XXXXXX").string();std::vector<char>p(s.begin(),s.end());p.push_back(0);int fd=mkstemp(p.data());if(fd<0)die("mkstemp");
 try{if(ftruncate(fd,LOGICAL))die("ftruncate");std::vector<u64>x(bytes/8);for(auto base:b){for(u64 i=0;i<x.size();i++)x[i]=value(base+i*8);pwrite_all(fd,x.data(),x.size()*8,base);}if(fdatasync(fd))die("fdatasync");if(close(fd))die("close");}
 catch(...){close(fd);unlink(p.data());throw;}return p.data();}
std::string hex(u64 x){std::ostringstream s;s<<"0x"<<std::hex<<x;return s.str();}
hbfsim_capacity_stats_v1 capacity_stats(hbfsim_context*c){hbfsim_capacity_stats_v1 v{};v.struct_size=sizeof(v);v.version=HBFSIM_CAPACITY_STATS_V1_VERSION;if(hbfsim_get_capacity_stats_v1(c,&v)!=HBFSIM_OK)die("capacity stats v1 unavailable");return v;}
u64 delta(u64 a,u64 b){req(b>=a,"counter regressed");return b-a;}
void journal(const Opt&o,const std::string&temp,const RR&r){
 std::filesystem::create_directories(o.report);std::ofstream j(std::filesystem::path(o.report)/"region-progress.jsonl",std::ios::app);if(!j)die("progress journal");
 j<<"{\"stage\":\""<<temp<<"\",\"index\":"<<r.index<<",\"logical_offset\":"<<r.base<<",\"expected_checksum\":\""<<hex(r.expected)<<"\",\"actual_checksum\":\""<<hex(r.actual)<<"\",\"word_mismatches\":"<<r.mismatches<<",\"passed\":"<<(r.pass?"true":"false")<<"}\n";j.flush();if(!j)die("progress journal flush");}
Stage run(const Opt&o,std::string temp,hbfsim_context*c,CUfunction f,void*logical,u64*dout,const std::vector<u64>&b,u64 bytes){
 Stage s{.temp=std::move(temp)};hbfsim_stats a{},z{};if(hbfsim_get_stats(c,&a)!=HBFSIM_OK)die("stats before");s.before=capacity_stats(c);req(s.before.enabled==1,"capacity stats v1 disabled");std::vector<u64>actual(bytes/8);auto t0=std::chrono::steady_clock::now();
 for(size_t r=0;r<b.size();r++){u64 base=b[r],words=actual.size();void*args[]{&logical,&base,&words,&dout};dok(cuLaunchKernel(f,1,1,1,1,1,1,0,nullptr,args,nullptr),"loads");cok(cudaDeviceSynchronize(),"sync");cok(cudaMemcpy(actual.data(),dout,bytes,cudaMemcpyDeviceToHost),"result");
  u64 mismatches=0;for(u64 i=0;i<words;i++)mismatches+=actual[i]!=value(base+i*8);RR rr{r,base,checksum(base,words),actual_checksum(actual),mismatches,mismatches==0};s.regions.push_back(rr);journal(o,s.temp,rr);}
 auto t1=std::chrono::steady_clock::now();if(hbfsim_get_stats(c,&z)!=HBFSIM_OK)die("stats after");s.after=capacity_stats(c);req(s.after.enabled==1&&s.before.page_bytes==s.after.page_bytes&&s.before.vmm_granularity==s.after.vmm_granularity&&s.before.pool_allocated_bytes==s.after.pool_allocated_bytes,"capacity geometry changed");s.wall=std::chrono::duration_cast<std::chrono::nanoseconds>(t1-t0).count();s.submitted=z.requests_submitted-a.requests_submitted;s.completed=z.requests_completed-a.requests_completed;s.pass=std::ranges::all_of(s.regions,[](auto&r){return r.pass;});return s;}
void output(const Opt&o,const std::vector<u64>&b,u64 bytes,const std::vector<Stage>&ss,void*logical){
 std::ofstream q(o.output);if(!q)die("output");bool pass=std::ranges::all_of(ss,[](auto&s){return s.pass;});u64 ming=~0ULL,maxg=0;for(size_t i=1;i<b.size();i++){ming=std::min(ming,b[i]-b[i-1]);maxg=std::max(maxg,b[i]-b[i-1]);}
 q<<"{\n\"schema_version\":2,\"experiment\":\"R3_PUBLIC_PAGE_PROTOCOL\",\"status\":\""<<(pass?"PASS":"FAIL")<<"\",\n"
  <<"\"workload\":\""<<o.workload<<"\",\"page_bytes\":"<<o.page<<",\"coverage_fraction\":\"1/"<<o.denom<<"\","
  <<"\"access_bytes_per_region\":"<<bytes<<",\"words_per_region\":"<<bytes/8<<",\"valid_bytes_per_stage\":"<<bytes*b.size()<<","
  <<"\"logical_bytes\":"<<LOGICAL<<",\"cache_bytes\":"<<CACHE<<",\"logical_frame_count\":"<<ss.front().after.logical_frame_count<<",\"vmm_granularity\":"<<ss.front().after.vmm_granularity<<",\"pool_allocated_bytes\":"<<ss.front().after.pool_allocated_bytes<<",\"region_count\":"<<b.size()<<","
  <<"\"region_alignment_bytes\":"<<REGION<<",\"region_gap_min_bytes\":"<<ming<<",\"region_gap_max_bytes\":"<<maxg<<","
  <<"\"region_gap_min_pages\":"<<ming/o.page<<",\"region_gap_max_pages\":"<<maxg/o.page<<","
  <<"\"global_cache_hit_rate\":null,\"global_cache_hit_rate_status\":\"DEVICE_FAST_PATH_NOT_OBSERVED\","
  <<"\"capacity_counter_status\":\"SERVICE_SCOPED_V1\",\"frame_observation\":\"UNSUPPORTED_PUBLIC_API\",\"stages\":[\n";
 for(size_t si=0;si<ss.size();si++){auto&s=ss[si];q<<"{\"temperature\":\""<<s.temp<<"\",\"same_order\":true,\"wall_ns\":"<<s.wall<<",\"requests_submitted\":"<<s.submitted<<",\"requests_completed\":"<<s.completed
   <<",\"service_resolve_calls\":"<<delta(s.before.service_resolve_calls,s.after.service_resolve_calls)
   <<",\"service_resident_hits\":"<<delta(s.before.service_resident_hits,s.after.service_resident_hits)
   <<",\"service_reclaimed_hits\":"<<delta(s.before.service_reclaimed_hits,s.after.service_reclaimed_hits)
   <<",\"service_misses\":"<<delta(s.before.service_misses,s.after.service_misses)
   <<",\"successful_backing_read_pages\":"<<delta(s.before.successful_backing_read_pages,s.after.successful_backing_read_pages)
   <<",\"successful_backing_read_bytes\":"<<delta(s.before.successful_backing_read_bytes,s.after.successful_backing_read_bytes)
   <<",\"successful_h2d_fill_pages\":"<<delta(s.before.successful_h2d_fill_pages,s.after.successful_h2d_fill_pages)
   <<",\"successful_h2d_fill_bytes\":"<<delta(s.before.successful_h2d_fill_bytes,s.after.successful_h2d_fill_bytes)
   <<",\"successful_resolve_evictions\":"<<delta(s.before.successful_resolve_evictions,s.after.successful_resolve_evictions)
   <<",\"service_ready_results\":"<<delta(s.before.service_ready_results,s.after.service_ready_results)
   <<",\"passed\":"<<(s.pass?"true":"false")<<",\"regions\":[\n";
  for(size_t i=0;i<s.regions.size();i++){auto&r=s.regions[i];u64 va=(uintptr_t)logical+r.base;q<<"{\"index\":"<<r.index<<",\"position\":\""<<(i==0?"first":i==NREG/2?"middle":i+1==NREG?"last":"uniform")<<"\",\"logical_offset\":"<<r.base<<",\"logical_page\":"<<r.base/o.page<<",\"first_gpu_va\":\""<<hex(va)<<"\",\"last_gpu_va\":\""<<hex(va+bytes-8)<<"\",\"frame\":null,\"frame_status\":\"UNSUPPORTED_PUBLIC_API\",\"expected_checksum\":\""<<hex(r.expected)<<"\",\"actual_checksum\":\""<<hex(r.actual)<<"\",\"word_mismatches\":"<<r.mismatches<<",\"verification\":\"PER_WORD_CPU_EQUALITY\",\"passed\":"<<(r.pass?"true":"false")<<"}"<<(i+1==s.regions.size()?"\n":",\n");}
  q<<"]}"<<(si+1==ss.size()?"\n":",\n");}q<<"]}\n";}
}
int main(int ac,char**av){hbfsim_context*c=nullptr;void*logical=nullptr;CUmodule m=nullptr;u64*out=nullptr;std::filesystem::path path,pending;
 try{auto o=parse(ac,av);auto b=bases();u64 bytes=access(o);req(bytes>=256&&bytes<=REGION&&bytes%8==0,"bad access");path=backing(o,b,REGION);pending=o.output+".pending";std::filesystem::remove(pending);cok(cudaFree(nullptr),"init");
 hbfsim_options co{.profile_path=o.profile.c_str(),.report_dir=o.report.c_str(),.mode=HBFSIM_MODEL_REFERENCE,.ring_capacity=65536,.request_timeout_ns=30000000000ULL};if(hbfsim_context_create(&co,&c)!=HBFSIM_OK)die("context");
 hbfsim_range_options ro{.mode=HBFSIM_RANGE_MODE_CAPACITY,.permissions=HBFSIM_RANGE_READ,.cache_policy=HBFSIM_CACHE_POLICY_NONE,.stream_id=0};if(hbfsim_map_file(c,path.c_str(),0,LOGICAL,&ro,&logical)!=HBFSIM_OK)die("map");
 cok(cudaMalloc((void**)&out,bytes),"output alloc");std::ifstream in(o.ptx,std::ios::binary);if(!in)die("ptx");std::string ptx{std::istreambuf_iterator<char>(in),{}};dok(cuModuleLoadDataEx(&m,ptx.c_str(),0,nullptr,nullptr),"module");CUfunction f=nullptr;dok(cuModuleGetFunction(&f,m,"r3_page_read_kernel"),"function");
 std::vector<Stage>ss;ss.push_back(run(o,"cold",c,f,logical,out,b,bytes));if(o.workload!="relative")ss.push_back(run(o,"warm",c,f,logical,out,b,bytes));for(auto&stage:ss){auto&g=stage.after;req(g.page_bytes==o.page,"actual page mismatch");req(g.logical_frame_count==CACHE/o.page,"frame count mismatch");req(g.vmm_granularity&&g.pool_allocated_bytes==((CACHE+g.vmm_granularity-1)/g.vmm_granularity)*g.vmm_granularity,"pool allocation mismatch");}Opt write=o;write.output=pending;output(write,b,bytes,ss,logical);bool pass=std::ranges::all_of(ss,[](auto&s){return s.pass;});
 dok(cuModuleUnload(m),"unload");m=nullptr;cok(cudaFree(out),"free");out=nullptr;if(hbfsim_unregister(c,logical)!=HBFSIM_OK)die("unregister");logical=nullptr;hbfsim_context_destroy(c);c=nullptr;if(unlink(path.c_str()))die("backing unlink");path.clear();std::filesystem::rename(pending,o.output);pending.clear();return pass?0:2;
 }catch(const std::exception&e){fprintf(stderr,"r3_public_capacity_bench: %s\n",e.what());if(m)cuModuleUnload(m);if(out)cudaFree(out);if(c&&logical)hbfsim_unregister(c,logical);if(c)hbfsim_context_destroy(c);if(!path.empty())unlink(path.c_str());if(!pending.empty())std::filesystem::remove(pending);return 1;}}

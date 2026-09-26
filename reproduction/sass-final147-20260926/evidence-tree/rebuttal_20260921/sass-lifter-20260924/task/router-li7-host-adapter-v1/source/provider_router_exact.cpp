#include <cublasLt.h>
#include <limits>
#include <cublas_v2.h>
#include <cuda.h>
#include <cupti.h>
#include <cupti_activity.h>
#include <cupti_callbacks.h>
#include <cupti_driver_cbid.h>
#include <openssl/sha.h>
#include "library_identity_core.hpp"
#include "qkv_live_identity_v1.hpp"

#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <fcntl.h>
#include <fstream>
#include <mutex>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unordered_map>
#include <unistd.h>
#include <vector>

// QKV capture extension: the CUPTI callback copies host launch data only.
// No CUDA API is called from a CUPTI callback.

namespace {
struct ModuleBlob { uint32_t id; std::string sha; std::size_t bytes; std::string kind; bool durable; };
// CUDA shutdown may still deliver CUPTI unload callbacks after C++ static
// destructors. Keep callback-visible identity state alive for this process.
std::mutex& state_mu() { static auto* v = new std::mutex; return *v; }
std::mutex& write_mu() { static auto* v = new std::mutex; return *v; }
std::string& case_name() { static auto* v = new std::string; return *v; }
std::uintptr_t& weight_begin() { static std::uintptr_t v = 0; return v; }
std::uintptr_t& weight_end() { static std::uintptr_t v = 0; return v; }
std::unordered_map<std::uintptr_t, std::vector<ModuleBlob>>& modules() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::vector<ModuleBlob>>; return *v;
}
std::unordered_map<std::uintptr_t, std::vector<ModuleBlob>>& libraries() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::vector<ModuleBlob>>; return *v;
}
std::unordered_map<std::uintptr_t, std::vector<ModuleBlob>>& kernels() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::vector<ModuleBlob>>; return *v;
}
std::unordered_map<std::uintptr_t, std::vector<ModuleBlob>>& functions() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::vector<ModuleBlob>>; return *v;
}
std::unordered_map<std::uintptr_t, std::uintptr_t>& function_modules() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::uintptr_t>; return *v;
}
std::unordered_map<std::uintptr_t, std::uintptr_t>& kernel_libraries() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::uintptr_t>; return *v;
}
std::unordered_map<std::uintptr_t, std::uintptr_t>& function_libraries() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::uintptr_t>; return *v;
}
std::unordered_map<std::uintptr_t, std::uint64_t>& library_generations() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::uint64_t>; return *v;
}
std::unordered_map<std::uintptr_t, std::uint64_t>& kernel_generations() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::uint64_t>; return *v;
}
std::unordered_map<std::uintptr_t, std::uint64_t>& function_generations() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::uint64_t>; return *v;
}
QkvLiveAssociations& qkv_live_associations() { static auto* v = new QkvLiveAssociations; return *v; }
struct ModuleParent { std::uintptr_t library; std::uint64_t generation; };
std::unordered_map<std::uintptr_t, ModuleParent>& module_parents() {
  static auto* v = new std::unordered_map<std::uintptr_t, ModuleParent>; return *v;
}
std::unordered_map<std::uintptr_t, std::string>& kernel_names() {
  static auto* v = new std::unordered_map<std::uintptr_t, std::string>; return *v;
}
hbfsim_provider::LineageStore& lineage_store() { static auto* v = new hbfsim_provider::LineageStore; return *v; }
std::unordered_map<std::string, std::size_t>& durable_blobs() {
  static auto* v = new std::unordered_map<std::string, std::size_t>; return *v;
}
thread_local std::vector<ModuleBlob> pending_blobs;
std::atomic<unsigned long long> next_call{1}, durable_matches{0};
std::atomic<int> correlation_ready{0};
std::atomic<unsigned long long> dropped_records{0}, append_failures{0};
std::atomic<unsigned long long> activity_parser_errors{0}, flush_failures{0};
std::atomic<unsigned long long> module_save_failures{0};
CUpti_SubscriberHandle subscriber = nullptr;
constexpr std::size_t kQkvParamBytes = 152;
constexpr const char* kQkvFatbinSha = "d15cf2497649902226341d260eee82160e485d38e76cdcb9cb581eca82167eb0";
constexpr const char* kQkvSymbol = "_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi6ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_";
constexpr const char* kLi7Symbol = "_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi7ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_";
const char* selected_symbol() {
  const char* target = std::getenv("HBFSIM_QKV_TARGET_KIND");
  return target && std::strcmp(target, "router_li7") == 0 ? kLi7Symbol : kQkvSymbol;
}
thread_local bool inside_qkv_blas = false;
struct QkvLaunchCapture {
  unsigned long long count = 0, unsupported = 0;
  unsigned int grid[3]{}, block[3]{}, shared = 0;
  unsigned long long correlation = 0;
  std::uintptr_t function = 0, stream = 0, context = 0;
  unsigned char params[kQkvParamBytes]{};
};
QkvLaunchCapture& qkv_capture() { static QkvLaunchCapture c; return c; }
std::mutex& qkv_capture_mu() { static std::mutex m; return m; }
struct QkvBlasCapture {
  unsigned long long count = 0;
  std::uintptr_t a = 0, b = 0, c = 0;
  int m = 0, n = 0, k = 0, ldc = 0, c_type = 0;
  std::size_t c_bytes = 0;
  int copy_status = -1;
  std::vector<unsigned char> c_prestate;
};
QkvBlasCapture& qkv_blas_capture() { static QkvBlasCapture c; return c; }
std::mutex& qkv_blas_mu() { static std::mutex m; return m; }

unsigned long long now_ns() {
  timespec ts{}; clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
  return static_cast<unsigned long long>(ts.tv_sec) * 1000000000ULL + ts.tv_nsec;
}
long tid() { return static_cast<long>(syscall(SYS_gettid)); }
std::string esc(const char* s) {
  std::string o; if (!s) return o;
  for (; *s; ++s) { unsigned char c=*s; if(c=='"'||c=='\\'){o+='\\';o+=char(c);} else if(c>=0x20)o+=char(c); }
  return o;
}
std::string ptr(const void* p) { char b[40]; std::snprintf(b,sizeof(b),"0x%llx",(unsigned long long)(uintptr_t)p); return b; }
std::string hex_sha(const void* data, std::size_t n) {
  unsigned char out[SHA256_DIGEST_LENGTH]; SHA256(static_cast<const unsigned char*>(data),n,out);
  static const char h[]="0123456789abcdef"; std::string s(64,'0');
  for(size_t i=0;i<sizeof(out);++i){s[2*i]=h[out[i]>>4];s[2*i+1]=h[out[i]&15];} return s;
}
std::string byte_kind(const void* data, std::size_t n) {
  auto parsed=hbfsim_provider::inspect_image(data,n,n);
  if(parsed.ok&&parsed.kind==hbfsim_provider::ImageKind::Elf64) return "ELF_CUBIN_BYTES";
  if(parsed.ok&&parsed.kind==hbfsim_provider::ImageKind::Fatbin) return "FATBIN_BYTES";
  if(parsed.ok&&parsed.kind==hbfsim_provider::ImageKind::Ptx) return "PTX_TEXT_BYTES";
  return "UNKNOWN_MODULE_BYTES";
}
bool write_bytes_fd(int fd,const char* data,std::size_t bytes){size_t d=0;while(d<bytes){ssize_t n=write(fd,data+d,bytes-d);if(n<=0)return false;d+=size_t(n);}return true;}
bool write_all_fd(int fd,const std::string& line){return write_bytes_fd(fd,line.data(),line.size());}
bool append_path(const char* env,const std::string& line){const char* p=std::getenv(env);if(!p||!*p)return false;std::lock_guard<std::mutex> g(write_mu());int fd=open(p,O_WRONLY|O_CREAT|O_APPEND|O_CLOEXEC,0644);if(fd<0)return false;bool wrote=write_all_fd(fd,line);bool closed=close(fd)==0;return wrote&&closed;}
bool append_blas(const std::string& s){bool ok=append_path("HBFSIM_PROVIDER_TRACE_LOG",s);if(!ok)append_failures.fetch_add(1);return ok;}
bool append_corr(const std::string& s){bool ok=append_path("HBFSIM_PROVIDER_CORRELATION_LOG",s);if(!ok)append_failures.fetch_add(1);return ok;}
bool in_weight(const void* p){auto v=(uintptr_t)p;std::lock_guard<std::mutex>g(state_mu());return weight_begin()!=0&&v>=weight_begin()&&v<weight_end();}
std::string snap_case(){std::lock_guard<std::mutex>g(state_mu());return case_name();}
template<class T>T next(const char* n){return reinterpret_cast<T>(dlsym(RTLD_NEXT,n));}
template<class T>T loaded_cuda12_symbol(const char* soname,const char* symbol){
  dlerror();
  void* handle=dlopen(soname,RTLD_NOW|RTLD_LOCAL|RTLD_NOLOAD);
  const char* open_error_raw=dlerror();
  const std::string open_error=open_error_raw?open_error_raw:"";
  void* address=nullptr;
  std::string symbol_error;
  std::string owner;
  if(handle){
    dlerror();
    address=dlvsym(handle,symbol,soname);
    const char* symbol_error_raw=dlerror();
    symbol_error=symbol_error_raw?symbol_error_raw:"";
    Dl_info info{};
    if(address&&dladdr(address,&info)!=0&&info.dli_fname)owner=info.dli_fname;
  }
  const bool exact=address&&!owner.empty()&&owner.find(soname)!=std::string::npos;
  std::ostringstream o;
  o<<"{\"schema\":\"hbfsim.provider.blas_resolver.v1\",\"event\":\"blas_resolver\","
   <<"\"symbol\":\""<<esc(symbol)<<"\",\"expected_soname\":\""<<esc(soname)
   <<"\",\"address\":\""<<ptr(address)<<"\",\"owner\":\""<<esc(owner.c_str())
   <<"\",\"open_error\":\""<<esc(open_error.c_str())<<"\",\"symbol_error\":\""
   <<esc(symbol_error.c_str())<<"\",\"exact_cuda12_owner\":"<<(exact?"true":"false")<<"}\n";
  append_corr(o.str());
  return exact?reinterpret_cast<T>(address):nullptr;
}
bool api_success(const CUpti_CallbackData* d){return d&&d->callbackSite==CUPTI_API_EXIT&&d->functionReturnValue&&*static_cast<CUresult*>(d->functionReturnValue)==CUDA_SUCCESS;}

void save_blob(const CUpti_ModuleResourceData* m){
  if(!m||!m->pCubin||!m->cubinSize)return;
  ModuleBlob b{m->moduleId,hex_sha(m->pCubin,m->cubinSize),m->cubinSize,byte_kind(m->pCubin,m->cubinSize),false};
  const char* dir=std::getenv("HBFSIM_PROVIDER_MODULE_DIR"); std::string status="NO_MODULE_DIR";
  if(dir&&*dir){std::lock_guard<std::mutex> file_guard(write_mu());std::string path=std::string(dir)+"/"+b.sha+".bin";int fd=open(path.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0644);
    if(fd>=0){bool wrote=write_bytes_fd(fd,m->pCubin,m->cubinSize);bool closed=close(fd)==0;b.durable=wrote&&closed;status=b.durable?"SAVED":"WRITE_FAILED";if(b.durable)durable_blobs()[b.sha]=b.bytes;}
    else if(errno==EEXIST){auto it=durable_blobs().find(b.sha);b.durable=it!=durable_blobs().end()&&it->second==b.bytes;status=b.durable?"DEDUP_VERIFIED_IN_PROCESS":"DEDUP_UNVERIFIED";}else status="OPEN_FAILED";
  }
  if(!b.durable)module_save_failures.fetch_add(1);
  pending_blobs.push_back(b);
  std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.module_blob.v2\",\"event\":\"module_blob\",\"module_id\":"<<b.id<<",\"sha256\":\""<<b.sha<<"\",\"bytes\":"<<b.bytes<<",\"byte_kind\":\""<<b.kind<<"\",\"durable\":"<<(b.durable?"true":"false")<<",\"save_status\":\""<<status<<"\"}\n";append_corr(o.str());
}
std::vector<ModuleBlob> take_pending(std::size_t start){if(start>pending_blobs.size())return{};std::vector<ModuleBlob> r(pending_blobs.begin()+start,pending_blobs.end());pending_blobs.resize(start);return r;}
hbfsim_provider::LineageEvidence lineage_evidence(const std::vector<ModuleBlob>& b){hbfsim_provider::LineageEvidence e;for(const auto&x:b)if(x.durable)e.byte_ids.push_back(x.sha);return e;}
bool same_candidates(const std::vector<ModuleBlob>&a,const std::vector<ModuleBlob>&b){if(a.size()!=b.size())return false;for(size_t i=0;i<a.size();++i)if(a[i].sha!=b[i].sha||a[i].bytes!=b[i].bytes||a[i].durable!=b[i].durable)return false;return true;}
void assoc_module(CUmodule m,const std::vector<ModuleBlob>& b,CUlibrary parent=nullptr){if(!m)return;std::lock_guard<std::mutex>g(state_mu());auto mh=(uintptr_t)m;qkv_live_associations().erase_module(mh);modules()[mh]=b;module_parents().erase(mh);if(parent){auto lh=(uintptr_t)parent;auto gi=library_generations().find(lh);if(gi!=library_generations().end())module_parents()[mh]={lh,gi->second};}}
void assoc_library(CUlibrary l,const std::vector<ModuleBlob>& b){if(!l)return;std::lock_guard<std::mutex>g(state_mu());auto h=(uintptr_t)l;for(auto it=kernel_libraries().begin();it!=kernel_libraries().end();)if(it->second==h){auto kh=it->first;kernels().erase(kh);kernel_generations().erase(kh);kernel_names().erase(kh);it=kernel_libraries().erase(it);}else ++it;for(auto it=function_libraries().begin();it!=function_libraries().end();)if(it->second==h){auto fh=it->first;functions().erase(fh);function_modules().erase(fh);function_generations().erase(fh);it=function_libraries().erase(it);}else ++it;libraries()[h]=b;library_generations()[h]=lineage_store().record_library(h,lineage_evidence(b));}
std::vector<ModuleBlob> module_candidates(CUmodule m){std::lock_guard<std::mutex>g(state_mu());auto it=modules().find((uintptr_t)m);return it==modules().end()?std::vector<ModuleBlob>{}:it->second;}
void assoc_kernel(CUkernel k,CUlibrary l,const char* name,const std::vector<ModuleBlob>& b){if(!k||!l)return;std::lock_guard<std::mutex>g(state_mu());auto kh=(uintptr_t)k,lh=(uintptr_t)l;kernels()[kh]=b;kernel_libraries()[kh]=lh;kernel_names()[kh]=name?name:"";auto gi=library_generations().find(lh);kernel_generations()[kh]=gi==library_generations().end()?0:gi->second;lineage_store().record_kernel(kh,lh,name?name:"");}
void assoc_function(CUfunction f,CUmodule m,CUcontext context,const char* name,const std::vector<ModuleBlob>& b){if(!f)return;std::lock_guard<std::mutex>g(state_mu());auto h=(uintptr_t)f;functions()[h]=b;if(m)function_modules()[h]=(uintptr_t)m;lineage_store().record_function(h,(uintptr_t)m,lineage_evidence(b));qkv_live_associations().record(h,(uintptr_t)m,(uintptr_t)context,name);}
void assoc_function_from_kernel(CUfunction f,CUkernel k,const std::vector<ModuleBlob>& b){if(!f||!k)return;std::lock_guard<std::mutex>g(state_mu());auto fh=(uintptr_t)f,kh=(uintptr_t)k;qkv_live_associations().erase_function(fh);functions()[fh]=b;auto li=kernel_libraries().find(kh);if(li!=kernel_libraries().end()){function_libraries()[fh]=li->second;auto gi=kernel_generations().find(kh);function_generations()[fh]=gi==kernel_generations().end()?0:gi->second;}lineage_store().record_function_from_kernel(fh,kh);}
void unload_library(CUlibrary l){if(!l)return;std::lock_guard<std::mutex>g(state_mu());auto lh=(uintptr_t)l;lineage_store().unload_library(lh);libraries().erase(lh);library_generations().erase(lh);for(auto it=module_parents().begin();it!=module_parents().end();){if(it->second.library==lh){qkv_live_associations().erase_module(it->first);it=module_parents().erase(it);}else ++it;}for(auto it=kernel_libraries().begin();it!=kernel_libraries().end();){if(it->second==lh){auto kh=it->first;kernels().erase(kh);kernel_generations().erase(kh);kernel_names().erase(kh);it=kernel_libraries().erase(it);}else ++it;}for(auto it=function_libraries().begin();it!=function_libraries().end();){if(it->second==lh){auto fh=it->first;qkv_live_associations().erase_function(fh);functions().erase(fh);function_modules().erase(fh);function_generations().erase(fh);it=function_libraries().erase(it);}else ++it;}}
void unload_module(CUmodule module){if(!module)return;std::lock_guard<std::mutex>g(state_mu());auto mh=(uintptr_t)module;qkv_live_associations().erase_module(mh);module_parents().erase(mh);modules().erase(mh);lineage_store().unload_module(mh);for(auto it=function_modules().begin();it!=function_modules().end();){if(it->second==mh){auto fh=it->first;functions().erase(fh);function_libraries().erase(fh);function_generations().erase(fh);it=function_modules().erase(it);}else ++it;}}
void unload_context(CUcontext context){if(!context)return;std::lock_guard<std::mutex>g(state_mu());qkv_live_associations().erase_context((uintptr_t)context);}
std::string exact_qkv_api_fields(const CUpti_CallbackData* d,CUpti_CallbackId id){
  if(!d||!d->symbolName||std::strcmp(d->symbolName,selected_symbol())!=0)return {};
  const char* api=id==CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel?"cuLaunchKernel":
                  id==CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel_ptsz?"cuLaunchKernel_ptsz":
                  id==CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx?"cuLaunchKernelEx":
                  id==CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx_ptsz?"cuLaunchKernelEx_ptsz":"UNKNOWN";
  std::ostringstream o;o<<",\"cbid\":"<<id<<",\"api\":\""<<api
    <<"\",\"callback_tid\":"<<static_cast<long>(syscall(SYS_gettid));
  return o.str();
}
void log_launch(const CUpti_CallbackData* d,CUpti_CallbackId id,CUfunction f){
  if(!d||!f)return;
  std::vector<ModuleBlob> c;std::uintptr_t mod=0,lib=0;std::uint64_t gen=0;std::string handle_path="MISSING";bool conflict=false;hbfsim_provider::ResolveResult resolved;
  {std::lock_guard<std::mutex>g(state_mu());auto h=(uintptr_t)f;auto fi=functions().find(h),ki=kernels().find(h);if(fi!=functions().end()){c=fi->second;handle_path="FUNCTION";auto li=function_libraries().find(h);if(li!=function_libraries().end())lib=li->second;auto gi=function_generations().find(h);if(gi!=function_generations().end())gen=gi->second;}if(ki!=kernels().end()){if(fi!=functions().end()&&!same_candidates(fi->second,ki->second))conflict=true;else if(fi==functions().end()){c=ki->second;handle_path="DIRECT_CONTEXTLESS_KERNEL";auto li=kernel_libraries().find(h);if(li!=kernel_libraries().end())lib=li->second;auto gi=kernel_generations().find(h);if(gi!=kernel_generations().end())gen=gi->second;}}auto mi=function_modules().find(h);if(mi!=function_modules().end())mod=mi->second;resolved=lineage_store().resolve_launch(h);}
  auto encode=[&](const std::vector<ModuleBlob>&v){std::ostringstream a;a<<"[";for(size_t i=0;i<v.size();++i){if(i)a<<",";a<<"{\"module_id\":"<<v[i].id<<",\"sha256\":\""<<v[i].sha<<"\",\"bytes\":"<<v[i].bytes<<",\"byte_kind\":\""<<v[i].kind<<"\",\"durable\":"<<(v[i].durable?"true":"false")<<"}";}a<<"]";return a.str();};
  const char* state=conflict?"AMBIGUOUS":resolved.state==hbfsim_provider::ResolveState::Exact?"EXACT":resolved.state==hbfsim_provider::ResolveState::TopologyOnly?"TOPOLOGY_ONLY":resolved.state==hbfsim_provider::ResolveState::Ambiguous?"AMBIGUOUS":"MISSING";
  std::vector<ModuleBlob> admitted=(std::string(state)=="EXACT")?c:std::vector<ModuleBlob>{};
  std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.driver_launch.v3\",\"event\":\"driver_launch\",\"correlation_id\":"<<d->correlationId<<",\"function_handle\":\""<<ptr(f)<<"\",\"module_handle\":\""<<ptr((void*)mod)<<"\",\"library_handle\":\""<<ptr((void*)lib)<<"\",\"library_generation\":"<<gen<<",\"handle_path\":\""<<handle_path<<"\",\"identity_state\":\""<<state<<"\",\"symbol_observation\":\""<<esc(d->symbolName)<<"\""<<exact_qkv_api_fields(d,id)<<",\"module_candidates\":"<<encode(admitted)<<",\"diagnostic_candidates\":"<<encode(c)<<"}\n";append_corr(o.str());
}

constexpr std::size_t kMaxLibraryImageBytes=1024ULL*1024ULL*1024ULL;
void log_library_capture(const char* origin,const hbfsim_provider::ImageCapture&r){std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.library_input.v1\",\"event\":\"library_input\",\"origin\":\""<<origin<<"\",\"status\":\""<<(r.ok?"CAPTURED":"REJECTED")<<"\",\"kind\":\""<<hbfsim_provider::image_kind_name(r.kind)<<"\",\"bytes\":"<<r.bytes<<",\"reason\":\""<<r.reason<<"\"}\n";append_corr(o.str());}
void capture_library_memory(const void* code){auto avail=hbfsim_provider::readable_mapping_bytes(code,std::numeric_limits<std::size_t>::max());auto r=hbfsim_provider::inspect_image(code,avail,kMaxLibraryImageBytes,true);log_library_capture("cuLibraryLoadData",r);if(r.ok&&r.payload){CUpti_ModuleResourceData m{};m.moduleId=0;m.cubinSize=r.bytes;m.pCubin=static_cast<const char*>(r.payload);save_blob(&m);}}
void capture_library_file(const char* name){hbfsim_provider::ImageCapture r;if(!name||!*name){r.reason="EMPTY_FILENAME";log_library_capture("cuLibraryLoadFromFile",r);return;}std::ifstream f(name,std::ios::binary|std::ios::ate);if(!f){r.reason="OPEN_FAILED";log_library_capture("cuLibraryLoadFromFile",r);return;}auto end=f.tellg();if(end<=0||static_cast<unsigned long long>(end)>kMaxLibraryImageBytes){r.reason="FILE_SIZE_OUT_OF_BOUNDS";log_library_capture("cuLibraryLoadFromFile",r);return;}std::vector<char>b(static_cast<size_t>(end));f.seekg(0);if(!f.read(b.data(),b.size())){r.reason="READ_FAILED";log_library_capture("cuLibraryLoadFromFile",r);return;}r=hbfsim_provider::inspect_image(b.data(),b.size(),b.size(),false);log_library_capture("cuLibraryLoadFromFile",r);if(r.ok&&r.bytes==b.size()){CUpti_ModuleResourceData m{};m.moduleId=0;m.cubinSize=r.bytes;m.pCubin=b.data();save_blob(&m);}}

void capture_qkv_launch(const CUpti_CallbackData* d, CUpti_CallbackId id, CUfunction f) {
  if (!inside_qkv_blas || !d || !d->symbolName ||
      std::strcmp(d->symbolName, selected_symbol()) != 0) return;
  std::lock_guard<std::mutex> g(qkv_capture_mu());
  auto& c = qkv_capture();
  ++c.count;
  bool exact_module = false;
  {
    std::lock_guard<std::mutex> state_guard(state_mu());
    const auto resolved = lineage_store().resolve_launch(reinterpret_cast<std::uintptr_t>(f));
    auto it = functions().find(reinterpret_cast<std::uintptr_t>(f));
    if (resolved.state == hbfsim_provider::ResolveState::Exact &&
        resolved.evidence.byte_ids.size() == 1 &&
        resolved.evidence.byte_ids.front() == kQkvFatbinSha &&
        it != functions().end() && it->second.size() == 1) {
      const auto& b = it->second.front();
      exact_module = b.durable && b.sha == kQkvFatbinSha;
    }
  }
  if (!exact_module) { ++c.unsupported; return; }
  // The observed QKV entry has one 152-byte aggregate argument. Other API
  // forms are reported as unsupported rather than being guessed or truncated.
  if (id != CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel &&
      id != CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel_ptsz) { ++c.unsupported; return; }
  const cuLaunchKernel_params* p =
      id == CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel
          ? static_cast<const cuLaunchKernel_params*>(d->functionParams) : nullptr;
  const cuLaunchKernel_ptsz_params* q =
      id == CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel_ptsz
          ? static_cast<const cuLaunchKernel_ptsz_params*>(d->functionParams) : nullptr;
  void** args = p ? p->kernelParams : q ? q->kernelParams : nullptr;
  void** extra = p ? p->extra : q ? q->extra : nullptr;
  if (!args || !args[0] || extra) { ++c.unsupported; return; }
  if (c.count != 1) { ++c.unsupported; return; }
  c.grid[0] = p ? p->gridDimX : q->gridDimX;
  c.grid[1] = p ? p->gridDimY : q->gridDimY;
  c.grid[2] = p ? p->gridDimZ : q->gridDimZ;
  c.block[0] = p ? p->blockDimX : q->blockDimX;
  c.block[1] = p ? p->blockDimY : q->blockDimY;
  c.block[2] = p ? p->blockDimZ : q->blockDimZ;
  c.shared = p ? p->sharedMemBytes : q->sharedMemBytes;
  c.stream = reinterpret_cast<std::uintptr_t>(p ? p->hStream : q->hStream);
  c.function = reinterpret_cast<std::uintptr_t>(p ? p->f : q->f);
  c.context = reinterpret_cast<std::uintptr_t>(d->context);
  c.correlation = d->correlationId;
  std::memcpy(c.params, args[0], kQkvParamBytes);
}
void capture_qkv_blas_before(const void* a, const void* b, const void* c,
                             int m, int n, int k, int ldc, cudaDataType ct) {
  std::lock_guard<std::mutex> g(qkv_blas_mu());
  auto& s = qkv_blas_capture();
  ++s.count;
  if (s.count != 1) return;
  s.a = reinterpret_cast<std::uintptr_t>(a);
  s.b = reinterpret_cast<std::uintptr_t>(b);
  s.c = reinterpret_cast<std::uintptr_t>(c);
  s.m = m; s.n = n; s.k = k; s.ldc = ldc; s.c_type = static_cast<int>(ct);
  constexpr std::size_t max_c_bytes = 64ULL * 1024ULL * 1024ULL;
  if (!c || ct != CUDA_R_16BF || m <= 0 || n <= 0 || ldc < m ||
      static_cast<std::size_t>(n) > max_c_bytes / 2 ||
      static_cast<std::size_t>(ldc) > max_c_bytes /
          (static_cast<std::size_t>(n) * 2)) return;
  s.c_bytes = static_cast<std::size_t>(ldc) * static_cast<std::size_t>(n) * 2;
  s.c_prestate.resize(s.c_bytes);
  void* libcuda = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL | RTLD_NOLOAD);
  if (!libcuda) return;
  using Copy = CUresult (*)(void*, CUdeviceptr, std::size_t);
  auto copy = reinterpret_cast<Copy>(dlsym(libcuda, "cuMemcpyDtoH_v2"));
  if (copy) s.copy_status = static_cast<int>(copy(s.c_prestate.data(),
                                                    reinterpret_cast<CUdeviceptr>(c),
                                                    s.c_bytes));
  dlclose(libcuda);
}
CUmodule output_module(CUpti_CallbackId id,const CUpti_CallbackData* d){if(!api_success(d))return nullptr;switch(id){
  case CUPTI_DRIVER_TRACE_CBID_cuModuleLoad:{auto*p=static_cast<const cuModuleLoad_params*>(d->functionParams);return p&&p->module?*p->module:nullptr;}
  case CUPTI_DRIVER_TRACE_CBID_cuModuleLoadData:{auto*p=static_cast<const cuModuleLoadData_params*>(d->functionParams);return p&&p->module?*p->module:nullptr;}
  case CUPTI_DRIVER_TRACE_CBID_cuModuleLoadDataEx:{auto*p=static_cast<const cuModuleLoadDataEx_params*>(d->functionParams);return p&&p->module?*p->module:nullptr;}
  case CUPTI_DRIVER_TRACE_CBID_cuModuleLoadFatBinary:{auto*p=static_cast<const cuModuleLoadFatBinary_params*>(d->functionParams);return p&&p->module?*p->module:nullptr;}
  default:return nullptr;}}
CUlibrary output_library(CUpti_CallbackId id,const CUpti_CallbackData* d){if(!api_success(d))return nullptr;if(id==CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadData){auto*p=static_cast<const cuLibraryLoadData_params*>(d->functionParams);return p&&p->library?*p->library:nullptr;}if(id==CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadFromFile){auto*p=static_cast<const cuLibraryLoadFromFile_params*>(d->functionParams);return p&&p->library?*p->library:nullptr;}return nullptr;}

void CUPTIAPI callback(void*,CUpti_CallbackDomain domain,CUpti_CallbackId id,const void* raw){
  if(domain==CUPTI_CB_DOMAIN_RESOURCE&&id==CUPTI_CBID_RESOURCE_CONTEXT_DESTROY_STARTING){auto*r=static_cast<const CUpti_ResourceData*>(raw);if(r)unload_context(r->context);return;}
  if(domain==CUPTI_CB_DOMAIN_RESOURCE&&id==CUPTI_CBID_RESOURCE_MODULE_LOADED){save_blob(static_cast<const CUpti_ModuleResourceData*>(raw));return;}
  if(domain!=CUPTI_CB_DOMAIN_DRIVER_API)return;
  auto* d=static_cast<const CUpti_CallbackData*>(raw);
  const bool load=id==CUPTI_DRIVER_TRACE_CBID_cuModuleLoad||id==CUPTI_DRIVER_TRACE_CBID_cuModuleLoadData||id==CUPTI_DRIVER_TRACE_CBID_cuModuleLoadDataEx||id==CUPTI_DRIVER_TRACE_CBID_cuModuleLoadFatBinary||id==CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadData||id==CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadFromFile;
  if(load&&d->callbackSite==CUPTI_API_ENTER){if(d->correlationData)*d->correlationData=pending_blobs.size();if(id==CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadData){auto*p=static_cast<const cuLibraryLoadData_params*>(d->functionParams);if(p)capture_library_memory(p->code);}else if(id==CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadFromFile){auto*p=static_cast<const cuLibraryLoadFromFile_params*>(d->functionParams);if(p)capture_library_file(p->fileName);}return;}
  if(load&&d->callbackSite==CUPTI_API_EXIT){
    auto blobs=d->correlationData?take_pending((size_t)*d->correlationData):std::vector<ModuleBlob>{};
    auto module=output_module(id,d);
    auto library=output_library(id,d);
    if(module)assoc_module(module,blobs);
    if(library)assoc_library(library,blobs);
    if(id==CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadData||
       id==CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadFromFile){
      std::uint64_t generation=0;
      if(library){
        std::lock_guard<std::mutex>g(state_mu());
        auto gi=library_generations().find((uintptr_t)library);
        if(gi!=library_generations().end())generation=gi->second;
      }
      std::ostringstream o;
      o<<"{\"schema\":\"hbfsim.provider.library_association.v1\","
       <<"\"event\":\"library_association\",\"correlation_id\":"
       <<d->correlationId<<",\"cbid\":"<<id<<",\"api_success\":"
       <<(api_success(d)?"true":"false")<<",\"library_handle\":\""
       <<ptr(library)<<"\",\"library_generation\":"<<generation
       <<",\"blob_count\":"<<blobs.size()<<",\"blob_sha256\":[";
      for(size_t i=0;i<blobs.size();++i){if(i)o<<",";o<<"\""<<blobs[i].sha<<"\"";}
      o<<"]}\n";
      append_corr(o.str());
    }
    return;
  }
  if(id==CUPTI_DRIVER_TRACE_CBID_cuLibraryGetModule&&api_success(d)){auto*p=static_cast<const cuLibraryGetModule_params*>(d->functionParams);std::vector<ModuleBlob>b;{std::lock_guard<std::mutex>g(state_mu());auto it=libraries().find((uintptr_t)p->library);if(it!=libraries().end())b=it->second;}CUmodule m=p->pMod?*p->pMod:nullptr;if(m)assoc_module(m,b,p->library);std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.module_association.v1\",\"event\":\"module_association\",\"correlation_id\":"<<d->correlationId<<",\"cbid\":"<<id<<",\"library_handle\":\""<<ptr(p->library)<<"\",\"module_handle\":\""<<ptr(m)<<"\",\"blob_count\":"<<b.size()<<"}\n";append_corr(o.str());return;}
  if(id==CUPTI_DRIVER_TRACE_CBID_cuModuleGetFunction&&api_success(d)){auto*p=static_cast<const cuModuleGetFunction_params*>(d->functionParams);CUfunction f=p->hfunc?*p->hfunc:nullptr;auto b=module_candidates(p->hmod);if(f)assoc_function(f,p->hmod,d->context,p->name,b);std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.function_association.v1\",\"event\":\"function_association\",\"correlation_id\":"<<d->correlationId<<",\"cbid\":"<<id<<",\"source_kind\":\"module\",\"module_handle\":\""<<ptr(p->hmod)<<"\",\"function_handle\":\""<<ptr(f)<<"\",\"blob_count\":"<<b.size()<<"}\n";append_corr(o.str());return;}
  if(id==CUPTI_DRIVER_TRACE_CBID_cuLibraryGetKernel&&api_success(d)){
    auto*p=static_cast<const cuLibraryGetKernel_params*>(d->functionParams);
    std::vector<ModuleBlob>b;
    std::uint64_t generation=0;
    {std::lock_guard<std::mutex>g(state_mu());
      auto it=libraries().find((uintptr_t)p->library);
      if(it!=libraries().end())b=it->second;
      auto gi=library_generations().find((uintptr_t)p->library);
      if(gi!=library_generations().end())generation=gi->second;
    }
    CUkernel kernel=p->pKernel?*p->pKernel:nullptr;
    if(kernel)assoc_kernel(kernel,p->library,p->name,b);
    std::ostringstream o;
    o<<"{\"schema\":\"hbfsim.provider.kernel_association.v1\","
     <<"\"event\":\"kernel_association\",\"library_handle\":\""
     <<ptr(p->library)<<"\",\"correlation_id\":"<<d->correlationId
     <<",\"cbid\":"<<id<<",\"kernel_handle\":\""<<ptr(kernel)
     <<"\",\"library_generation\":"<<generation<<",\"blob_count\":"
     <<b.size()<<",\"name_observation\":\""<<esc(p->name)<<"\"}\n";
    append_corr(o.str());
    return;
  }
  if(id==CUPTI_DRIVER_TRACE_CBID_cuKernelGetFunction&&api_success(d)){auto*p=static_cast<const cuKernelGetFunction_params*>(d->functionParams);std::vector<ModuleBlob>b;{std::lock_guard<std::mutex>g(state_mu());auto it=kernels().find((uintptr_t)p->kernel);if(it!=kernels().end())b=it->second;}CUfunction f=p->pFunc?*p->pFunc:nullptr;if(f)assoc_function_from_kernel(f,p->kernel,b);std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.function_association.v1\",\"event\":\"function_association\",\"correlation_id\":"<<d->correlationId<<",\"cbid\":"<<id<<",\"source_kind\":\"kernel\",\"kernel_handle\":\""<<ptr(p->kernel)<<"\",\"function_handle\":\""<<ptr(f)<<"\",\"blob_count\":"<<b.size()<<"}\n";append_corr(o.str());return;}
  if(id==CUPTI_DRIVER_TRACE_CBID_cuLibraryUnload&&api_success(d)){auto*p=static_cast<const cuLibraryUnload_params*>(d->functionParams);if(p)unload_library(p->library);return;}
  if(id==CUPTI_DRIVER_TRACE_CBID_cuModuleUnload&&api_success(d)){auto*p=static_cast<const cuModuleUnload_params*>(d->functionParams);if(p)unload_module(p->hmod);return;}
  if(d->callbackSite!=CUPTI_API_ENTER)return;
  CUfunction f=nullptr;
  if(id==CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel)f=static_cast<const cuLaunchKernel_params*>(d->functionParams)->f;
  else if(id==CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel_ptsz)f=static_cast<const cuLaunchKernel_ptsz_params*>(d->functionParams)->f;
  else if(id==CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx)f=static_cast<const cuLaunchKernelEx_params*>(d->functionParams)->f;
  else if(id==CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx_ptsz)f=static_cast<const cuLaunchKernelEx_ptsz_params*>(d->functionParams)->f;
  if(f) { log_launch(d,id,f); capture_qkv_launch(d,id,f); }
}

void CUPTIAPI request_buffer(uint8_t** b,size_t* size,size_t* max){*size=1<<20;*max=0;void*p=nullptr;if(posix_memalign(&p,8,*size)!=0)p=nullptr;*b=static_cast<uint8_t*>(p);}
void CUPTIAPI complete_buffer(CUcontext context,uint32_t stream_id,uint8_t* b,size_t,size_t valid){
  if(!b){activity_parser_errors.fetch_add(1);return;}
  CUpti_Activity* r=nullptr;CUptiResult next_result=CUPTI_SUCCESS;while((next_result=cuptiActivityGetNextRecord(b,valid,&r))==CUPTI_SUCCESS){
    if(r->kind==CUPTI_ACTIVITY_KIND_EXTERNAL_CORRELATION){auto*x=reinterpret_cast<CUpti_ActivityExternalCorrelation*>(r);std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.external_correlation.v2\",\"event\":\"external_correlation\",\"external_kind\":"<<int(x->externalKind)<<",\"external_id\":"<<x->externalId<<",\"correlation_id\":"<<x->correlationId<<"}\n";append_corr(o.str());}
    else if(r->kind==CUPTI_ACTIVITY_KIND_DRIVER||r->kind==CUPTI_ACTIVITY_KIND_RUNTIME){auto*a=reinterpret_cast<CUpti_ActivityAPI*>(r);std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.cuda_api_activity.v1\",\"event\":\"cuda_api_activity\",\"activity_kind\":"<<int(a->kind)<<",\"cbid\":"<<a->cbid<<",\"correlation_id\":"<<a->correlationId<<",\"process_id\":"<<a->processId<<",\"thread_id\":"<<a->threadId<<"}\n";append_corr(o.str());}
    else if(r->kind==CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL||r->kind==CUPTI_ACTIVITY_KIND_KERNEL){auto*k=reinterpret_cast<CUpti_ActivityKernel9*>(r);std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.kernel_activity.v2\",\"event\":\"kernel_activity\",\"correlation_id\":"<<k->correlationId<<",\"name_observation\":\""<<esc(k->name)<<"\",\"device_id\":"<<k->deviceId<<",\"context_id\":"<<k->contextId<<",\"stream_id\":"<<k->streamId<<",\"start_ns\":"<<k->start<<",\"end_ns\":"<<k->end<<"}\n";append_corr(o.str());}
  }
  if(next_result!=CUPTI_ERROR_MAX_LIMIT_REACHED)activity_parser_errors.fetch_add(1);
  size_t dropped=0;CUptiResult dropped_result=cuptiActivityGetNumDroppedRecords(context,stream_id,&dropped);
  if(dropped_result==CUPTI_SUCCESS)dropped_records.fetch_add(dropped);else activity_parser_errors.fetch_add(1);
  free(b);
}
bool enable_cb(CUpti_CallbackDomain d,CUpti_CallbackId id){return cuptiEnableCallback(1,subscriber,d,id)==CUPTI_SUCCESS;}
__attribute__((constructor)) void init_correlation(){
  const char* log=std::getenv("HBFSIM_PROVIDER_CORRELATION_LOG");const char* dir=std::getenv("HBFSIM_PROVIDER_MODULE_DIR");if(!log||!*log||!dir||!*dir)return;
  int fd=open(log,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0644);if(fd<0)return;close(fd);if(mkdir(dir,0755)!=0)return;
  if(cuptiActivityRegisterCallbacks(request_buffer,complete_buffer)!=CUPTI_SUCCESS)return;
  if(cuptiSubscribe(&subscriber,callback,nullptr)!=CUPTI_SUCCESS)return;
  bool ok=enable_cb(CUPTI_CB_DOMAIN_RESOURCE,CUPTI_CBID_RESOURCE_MODULE_LOADED);
  ok=enable_cb(CUPTI_CB_DOMAIN_RESOURCE,CUPTI_CBID_RESOURCE_CONTEXT_DESTROY_STARTING)&&ok;
  const CUpti_CallbackId ids[]={CUPTI_DRIVER_TRACE_CBID_cuModuleLoad,CUPTI_DRIVER_TRACE_CBID_cuModuleLoadData,CUPTI_DRIVER_TRACE_CBID_cuModuleLoadDataEx,CUPTI_DRIVER_TRACE_CBID_cuModuleLoadFatBinary,CUPTI_DRIVER_TRACE_CBID_cuModuleUnload,CUPTI_DRIVER_TRACE_CBID_cuModuleGetFunction,CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadData,CUPTI_DRIVER_TRACE_CBID_cuLibraryLoadFromFile,CUPTI_DRIVER_TRACE_CBID_cuLibraryUnload,CUPTI_DRIVER_TRACE_CBID_cuLibraryGetKernel,CUPTI_DRIVER_TRACE_CBID_cuLibraryGetModule,CUPTI_DRIVER_TRACE_CBID_cuKernelGetFunction,CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel,CUPTI_DRIVER_TRACE_CBID_cuLaunchKernel_ptsz,CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx,CUPTI_DRIVER_TRACE_CBID_cuLaunchKernelEx_ptsz};
  for(auto id:ids)ok=enable_cb(CUPTI_CB_DOMAIN_DRIVER_API,id)&&ok;
  const auto driver_activity=cuptiActivityEnable(CUPTI_ACTIVITY_KIND_DRIVER);
  const auto runtime_activity=cuptiActivityEnable(CUPTI_ACTIVITY_KIND_RUNTIME);
  const auto external_activity=cuptiActivityEnable(CUPTI_ACTIVITY_KIND_EXTERNAL_CORRELATION);
  const auto kernel_activity=cuptiActivityEnable(CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL);
  ok=driver_activity==CUPTI_SUCCESS&&runtime_activity==CUPTI_SUCCESS&&
     external_activity==CUPTI_SUCCESS&&kernel_activity==CUPTI_SUCCESS&&ok;
  {std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider.activity_enable.v1\",\"event\":\"activity_enable\",\"driver_result\":"<<int(driver_activity)<<",\"runtime_result\":"<<int(runtime_activity)<<",\"external_result\":"<<int(external_activity)<<",\"kernel_result\":"<<int(kernel_activity)<<",\"ready\":"<<(ok?"true":"false")<<"}\n";append_corr(o.str());}
  correlation_ready.store(ok?1:-1);
}
}

extern "C" int hbfsim_provider_trace_correlation_ready(){return correlation_ready.load();}
extern "C" int hbfsim_qkv_live_identity_v1(std::uint64_t function,
                                             std::uint64_t expected_context,
                                             QkvLiveIdentityV1* out) {
  if (!out || out->struct_size != sizeof(QkvLiveIdentityV1) ||
      !function || !expected_context) return -1;
  std::memset(out, 0, sizeof(*out));
  out->struct_size = sizeof(*out);
  std::lock_guard<std::mutex> g(state_mu());
  const auto* a = qkv_live_associations().find(function);
  if (!a || a->context != expected_context || a->name != selected_symbol())
    return 0;
  const auto module = modules().find(a->module);
  const auto func = functions().find(function);
  const auto fm = function_modules().find(function);
  const auto parent = module_parents().find(a->module);
  if (module == modules().end() || func == functions().end() ||
      fm == function_modules().end() || fm->second != a->module ||
      parent == module_parents().end() ||
      !same_candidates(module->second, func->second)) return 0;
  const auto live_parent = library_generations().find(parent->second.library);
  if (live_parent == library_generations().end() ||
      live_parent->second != parent->second.generation) return 0;
  const auto resolved = lineage_store().resolve_launch(function);
  if (resolved.state != hbfsim_provider::ResolveState::Exact ||
      resolved.evidence.byte_ids.size() != 1 ||
      resolved.evidence.byte_ids.front() != kQkvFatbinSha ||
      module->second.size() != 1 || !module->second.front().durable ||
      module->second.front().sha != kQkvFatbinSha) return 0;
  out->context = a->context;
  out->module = a->module;
  out->association_token = a->token;
  std::memcpy(out->image_sha256, kQkvFatbinSha, 65);
  return 1;
}
#ifdef HBFSIM_QKV_IDENTITY_CPU_TEST
// Built only into the isolated CPU fixture DSO; absent from production DSO.
extern "C" void hbfsim_qkv_test_inject(std::uint64_t function,
                                        std::uint64_t module,
                                        std::uint64_t library,
                                        std::uint64_t context,
                                        const char* name,
                                        const char* image_sha) {
  std::lock_guard<std::mutex> g(state_mu());
  const std::string sha = image_sha ? image_sha : "";
  const std::vector<ModuleBlob> blob{{0, sha, 176152136, "FATBIN_BYTES", true}};
  libraries()[library] = blob;
  library_generations()[library] =
      lineage_store().record_library(library, lineage_evidence(blob));
  modules()[module] = blob;
  module_parents()[module] = {library, library_generations()[library]};
  functions()[function] = blob;
  function_modules()[function] = module;
  lineage_store().record_function(function, module, lineage_evidence(blob));
  qkv_live_associations().record(function, module, context, name);
}
extern "C" void hbfsim_qkv_test_unload_module(std::uint64_t module) {
  unload_module(reinterpret_cast<CUmodule>(module));
}
extern "C" void hbfsim_qkv_test_unload_library(std::uint64_t library) {
  unload_library(reinterpret_cast<CUlibrary>(library));
}
extern "C" void hbfsim_qkv_test_destroy_context(std::uint64_t context) {
  CUpti_ResourceData data{};
  data.context = reinterpret_cast<CUcontext>(context);
  callback(nullptr, CUPTI_CB_DOMAIN_RESOURCE,
           CUPTI_CBID_RESOURCE_CONTEXT_DESTROY_STARTING, &data);
}
#endif
extern "C" int hbfsim_qkv_capture_write(const char* path) {
  if (!path || !*path) return -1;
  QkvLaunchCapture c;
  { std::lock_guard<std::mutex> g(qkv_capture_mu()); c = qkv_capture(); }
  if (c.count != 1 || c.unsupported != 0) return -2;
  int fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
  if (fd < 0) return -3;
  static const char h[] = "0123456789abcdef";
  std::string hex(kQkvParamBytes * 2, '0');
  for (std::size_t i = 0; i < kQkvParamBytes; ++i) {
    hex[2*i] = h[c.params[i] >> 4]; hex[2*i+1] = h[c.params[i] & 15];
  }
  std::ostringstream o;
  o << "{\"schema\":\"hbfsim.qkv_native_launch_capture.v1\",\"status\":\"CAPTURED_NOT_REPLAYED\""
    << ",\"correlation_id\":" << c.correlation
    << ",\"function_handle\":\"" << ptr(reinterpret_cast<void*>(c.function)) << "\""
    << ",\"context_handle\":\"" << ptr(reinterpret_cast<void*>(c.context)) << "\""
    << ",\"stream_handle\":\"" << ptr(reinterpret_cast<void*>(c.stream)) << "\""
    << ",\"grid\":[" << c.grid[0] << ',' << c.grid[1] << ',' << c.grid[2] << ']'
    << ",\"block\":[" << c.block[0] << ',' << c.block[1] << ',' << c.block[2] << ']'
    << ",\"dynamic_shared_bytes\":" << c.shared
    << ",\"parameter_mode\":\"kernelParams_one_152_byte_aggregate\""
    << ",\"symbol\":\"" << selected_symbol() << "\""
    << ",\"fatbin_sha256\":\"" << kQkvFatbinSha << "\""
    << ",\"parameter_bytes_hex\":\"" << hex << "\"}\n";
  bool ok = write_all_fd(fd, o.str());
  bool closed = close(fd) == 0;
  return ok && closed ? 0 : -4;
}
extern "C" int hbfsim_qkv_blas_capture_write(const char* json_path,
                                              const char* prestate_path) {
  if (!json_path || !*json_path || !prestate_path || !*prestate_path) return -1;
  QkvBlasCapture s;
  { std::lock_guard<std::mutex> g(qkv_blas_mu()); s = qkv_blas_capture(); }
  if (s.count != 1 || s.copy_status != CUDA_SUCCESS ||
      s.c_prestate.size() != s.c_bytes || s.c_bytes == 0) return -2;
  int data_fd = open(prestate_path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
  if (data_fd < 0) return -3;
  bool data_ok = write_bytes_fd(data_fd,
      reinterpret_cast<const char*>(s.c_prestate.data()), s.c_prestate.size());
  data_ok = close(data_fd) == 0 && data_ok;
  if (!data_ok) return -4;
  int fd = open(json_path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
  if (fd < 0) return -5;
  std::ostringstream o;
  o << "{\"schema\":\"hbfsim.qkv_blas_capture.v1\",\"A\":\""
    << ptr(reinterpret_cast<void*>(s.a)) << "\",\"B\":\""
    << ptr(reinterpret_cast<void*>(s.b)) << "\",\"C\":\""
    << ptr(reinterpret_cast<void*>(s.c)) << "\",\"m\":" << s.m
    << ",\"n\":" << s.n << ",\"k\":" << s.k
    << ",\"ldc\":" << s.ldc << ",\"C_type\":" << s.c_type
    << ",\"C_bytes\":" << s.c_bytes
    << ",\"C_prestate_sha256\":\"" << hex_sha(s.c_prestate.data(), s.c_bytes)
    << "\"}\n";
  bool ok = write_all_fd(fd, o.str());
  return close(fd) == 0 && ok ? 0 : -6;
}
extern "C" int hbfsim_provider_trace_flush(){auto result=cuptiActivityFlushAll(CUPTI_ACTIVITY_FLAG_FLUSH_FORCED);if(result!=CUPTI_SUCCESS)flush_failures.fetch_add(1);return result==CUPTI_SUCCESS?0:-1;}
extern "C" void hbfsim_provider_trace_health_v3(unsigned long long* dropped,
    unsigned long long* append, unsigned long long* parser,
    unsigned long long* flush, unsigned long long* module_save){
  if(dropped)*dropped=dropped_records.load();
  if(append)*append=append_failures.load();
  if(parser)*parser=activity_parser_errors.load();
  if(flush)*flush=flush_failures.load();
  if(module_save)*module_save=module_save_failures.load();
}
extern "C" int hbfsim_provider_trace_set_case(const char* n,const void* w,std::size_t bytes,long long m){
  if(!n||!*n||!w||!bytes||correlation_ready.load()!=1)return -1;
  const char* p=std::getenv("HBFSIM_PROVIDER_TRACE_LOG");if(!p||!*p)return -3;int fd=open(p,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0644);if(fd<0)return -4;if(close(fd)!=0)return -5;
  {std::lock_guard<std::mutex>g(state_mu());case_name()=n;weight_begin()=(uintptr_t)w;if(__builtin_add_overflow(weight_begin(),bytes,&weight_end()))return -2;durable_matches.store(0);}
  std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider_trace.case.v2\",\"event\":\"case\",\"case\":\""<<esc(n)<<"\",\"weight_begin\":\""<<ptr(w)<<"\",\"weight_bytes\":"<<bytes<<",\"m\":"<<m<<",\"tid\":"<<tid()<<"}\n";return append_blas(o.str())?0:-6;
}
extern "C" unsigned long long hbfsim_provider_trace_matched_calls(){return durable_matches.load();}

extern "C" cublasStatus_t cublasGemmEx(cublasHandle_t h,cublasOperation_t ta,cublasOperation_t tb,int m,int n,int k,const void* alpha,const void*A,cudaDataType at,int lda,const void*B,cudaDataType bt,int ldb,const void*beta,void*C,cudaDataType ct,int ldc,cublasComputeType_t comp,cublasGemmAlgo_t algo){
  using Fn=cublasStatus_t(*)(cublasHandle_t,cublasOperation_t,cublasOperation_t,int,int,int,const void*,const void*,cudaDataType,int,const void*,cudaDataType,int,const void*,void*,cudaDataType,int,cublasComputeType_t,cublasGemmAlgo_t);static Fn real=loaded_cuda12_symbol<Fn>("libcublas.so.12","cublasGemmEx");if(!real)return CUBLAS_STATUS_NOT_INITIALIZED;
  auto id=next_call.fetch_add(1);bool am=in_weight(A),bm=in_weight(B),cm=in_weight(C);const auto cell=snap_case();bool target=(am||bm)&&(cell=="qkv-m1"||(selected_symbol()==kLi7Symbol&&cell=="gate-m1"));if(target)capture_qkv_blas_before(A,B,C,m,n,k,ldc,ct);CUptiResult push=cuptiActivityPushExternalCorrelationId(CUPTI_EXTERNAL_CORRELATION_KIND_CUSTOM0,id);auto begin=now_ns();bool prior=inside_qkv_blas;inside_qkv_blas=target;auto st=real(h,ta,tb,m,n,k,alpha,A,at,lda,B,bt,ldb,beta,C,ct,ldc,comp,algo);inside_qkv_blas=prior;uint64_t popped=0;CUptiResult pop=push==CUPTI_SUCCESS?cuptiActivityPopExternalCorrelationId(CUPTI_EXTERNAL_CORRELATION_KIND_CUSTOM0,&popped):push;
  std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider_trace.blas.v2\",\"api\":\"cublasGemmEx\",\"call_id\":"<<id<<",\"case\":\""<<esc(snap_case().c_str())<<"\",\"tid\":"<<tid()<<",\"begin_ns\":"<<begin<<",\"end_ns\":"<<now_ns()<<",\"status\":"<<int(st)<<",\"A_weight\":"<<(am?"true":"false")<<",\"B_weight\":"<<(bm?"true":"false")<<",\"C_weight\":"<<(cm?"true":"false")<<",\"cupti_push\":"<<int(push)<<",\"cupti_pop\":"<<int(pop)<<",\"popped_id\":"<<popped<<"}\n";bool logged=append_blas(o.str());if(st==CUBLAS_STATUS_SUCCESS&&(am||bm)&&logged&&push==CUPTI_SUCCESS&&pop==CUPTI_SUCCESS&&popped==id)durable_matches.fetch_add(1);return st;
}
extern "C" cublasStatus_t cublasLtMatmul(cublasLtHandle_t h,cublasLtMatmulDesc_t op,const void*alpha,const void*A,cublasLtMatrixLayout_t ad,const void*B,cublasLtMatrixLayout_t bd,const void*beta,const void*C,cublasLtMatrixLayout_t cd,void*D,cublasLtMatrixLayout_t dd,const cublasLtMatmulAlgo_t*algo,void*ws,std::size_t wsz,cudaStream_t s){
  using Fn=cublasStatus_t(*)(cublasLtHandle_t,cublasLtMatmulDesc_t,const void*,const void*,cublasLtMatrixLayout_t,const void*,cublasLtMatrixLayout_t,const void*,const void*,cublasLtMatrixLayout_t,void*,cublasLtMatrixLayout_t,const cublasLtMatmulAlgo_t*,void*,std::size_t,cudaStream_t);static Fn real=loaded_cuda12_symbol<Fn>("libcublasLt.so.12","cublasLtMatmul");if(!real)return CUBLAS_STATUS_NOT_INITIALIZED;
  auto id=next_call.fetch_add(1);bool am=in_weight(A),bm=in_weight(B);CUptiResult push=cuptiActivityPushExternalCorrelationId(CUPTI_EXTERNAL_CORRELATION_KIND_CUSTOM0,id);auto begin=now_ns();auto st=real(h,op,alpha,A,ad,B,bd,beta,C,cd,D,dd,algo,ws,wsz,s);uint64_t popped=0;CUptiResult pop=push==CUPTI_SUCCESS?cuptiActivityPopExternalCorrelationId(CUPTI_EXTERNAL_CORRELATION_KIND_CUSTOM0,&popped):push;
  std::ostringstream o;o<<"{\"schema\":\"hbfsim.provider_trace.blas.v2\",\"api\":\"cublasLtMatmul\",\"call_id\":"<<id<<",\"case\":\""<<esc(snap_case().c_str())<<"\",\"tid\":"<<tid()<<",\"begin_ns\":"<<begin<<",\"end_ns\":"<<now_ns()<<",\"status\":"<<int(st)<<",\"A_weight\":"<<(am?"true":"false")<<",\"B_weight\":"<<(bm?"true":"false")<<",\"cupti_push\":"<<int(push)<<",\"cupti_pop\":"<<int(pop)<<",\"popped_id\":"<<popped<<"}\n";bool logged=append_blas(o.str());if(st==CUBLAS_STATUS_SUCCESS&&(am||bm)&&logged&&push==CUPTI_SUCCESS&&pop==CUPTI_SUCCESS&&popped==id)durable_matches.fetch_add(1);return st;
}

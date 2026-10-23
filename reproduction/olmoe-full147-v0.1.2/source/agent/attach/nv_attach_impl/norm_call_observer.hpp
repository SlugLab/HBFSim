#pragma once
// Internal diagnostic only; no public ABI or CUDA ownership changes.
#include <array>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <cstdlib>
#include <cstdio>
#include <cstdint>
#include <fstream>
#include <sstream>
#include <string>
#include <mutex>
#include <fcntl.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <sys/uio.h>
#include <time.h>
namespace norm_observer {
inline constexpr const char *symbol="_ZN4vllm25fused_add_rms_norm_kernelIN3c108BFloat16ELi8EEENSt9enable_ifIXaagtT0_Li0Esr4vllm12_typeConvertIT_EE6existsEvE4typeEPS4_lS7_PKS4_fii";
inline bool enabled() noexcept { const char *v=getenv("HBFSIM_NORM_CALL_OBSERVER_V1");return v && strcmp(v,"1")==0; }
inline uint64_t now() noexcept { timespec t{};return clock_gettime(CLOCK_MONOTONIC,&t)==0?uint64_t(t.tv_sec)*1000000000+t.tv_nsec:0; }
inline bool host_read(void *dst,const void *src,size_t n) noexcept { int f=open("/proc/self/mem",O_RDONLY|O_CLOEXEC);if(f<0)return false;ssize_t r=pread(f,dst,n,static_cast<off_t>(reinterpret_cast<uintptr_t>(src)));int saved=errno;close(f);errno=saved;return r==static_cast<ssize_t>(n); }
inline bool slots(void **args,void **extra,std::array<uint64_t,7>& out) noexcept {
 if(extra||!args)return false;void *p[7]{};if(!host_read(p,args,sizeof p))return false;
 for(int i=0;i<7;i++)if(!p[i]||!host_read(&out[i],p[i],i<4?8:4))return false;return true;
}
inline std::string start_ticks(){std::ifstream f("/proc/self/stat");std::string s;getline(f,s);auto p=s.rfind(')');if(p==std::string::npos)return "UNKNOWN";std::istringstream in(s.substr(p+2));std::string v;for(int i=3;i<=22;i++)if(!(in>>v))return "UNKNOWN";return v;}
inline std::string boot(){std::ifstream f("/proc/sys/kernel/random/boot_id");std::string s;getline(f,s);return s.empty()?"UNKNOWN":s;}
struct sink {
 std::mutex lock;size_t count=0;std::atomic<bool> incomplete{false};int fd=-1,health=-1;
 // Test injection is confined to this internal header; defaults are real POSIX I/O.
 struct io_ops {int(*sync)(int)=::fsync;int(*close_fd)(int)=::close;ssize_t(*put)(int,const void*,size_t,off_t)=::pwrite;int(*truncate)(int,off_t)=::ftruncate;} io;
 bool finalized=false,marker_sent=false;
 void failure_marker() noexcept {if(!marker_sent){marker_sent=true;const char m[]="HBFSIM_NORM_OBSERVER_FINALIZATION_FAILED\n";::write(STDERR_FILENO,m,sizeof(m)-1);}}
 void finalize() noexcept {
  if(finalized)return;finalized=true;int saved=errno;
  // Never publish terminal success before raw synchronization AND close succeed.
  if(fd>=0){if(io.sync(fd)!=0)incomplete=true;if(io.close_fd(fd)!=0)incomplete=true;fd=-1;}
  else incomplete=true;
  if(health>=0){
   char b[160];int n=snprintf(b,sizeof b,"{\"status\":\"%s\",\"records\":%zu}\n",incomplete.load()?"INCOMPLETE":"CLOSED_REQUIRES_EXTERNAL_COVERAGE_JOIN",count);
   bool bad=io.put(health,b,n,0)!=n;
   if(io.truncate(health,n)!=0)bad=true;
   if(io.sync(health)!=0)bad=true;
   if(bad){incomplete=true;const char f[]="{\"status\":\"INCOMPLETE\"}\n";
    // Best effort only. The independently captured marker invalidates even a stale CLOSED.
    io.put(health,f,sizeof(f)-1,0);io.truncate(health,sizeof(f)-1);io.sync(health);}
   if(io.close_fd(health)!=0)incomplete=true;health=-1;
  }else incomplete=true;
  if(incomplete.load())failure_marker();errno=saved;
 }
 ~sink(){finalize();}
 bool append(const std::string &s) noexcept {
  try {std::lock_guard<std::mutex> g(lock);if(fd<0){const char *p=getenv("HBFSIM_NORM_CALL_OBSERVER_PATH");if(!p){incomplete=true;return false;}fd=open(p,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);std::string hp=std::string(p)+".health";health=open(hp.c_str(),O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);}
  if(fd<0||health<0||count>=2048||s.size()>2048){incomplete=true;}else{size_t off=0;while(off<s.size()){ssize_t n=write(fd,s.data()+off,s.size()-off);if(n<=0){incomplete=true;break;}off+=n;}++count;}
  char b[160];int n=snprintf(b,sizeof b,"{\"status\":\"%s\",\"records\":%zu}\n",incomplete?"INCOMPLETE":"OBSERVED_NOT_TERMINAL",count);if(health>=0){if(pwrite(health,b,n,0)!=n||ftruncate(health,n))incomplete=true;}return !incomplete;
  }catch(...){incomplete=true;return false;}
 }
};
inline sink& output(){static sink s;return s;}
struct association {uint64_t original=0,patched=0,id=0;std::string name;};
inline thread_local association *current_association=nullptr;
inline std::atomic<uint64_t> association_ids{1};
struct association_scope {association data;association *prior;bool on;
 explicit association_scope(bool v):prior(current_association),on(v){if(on){data.id=association_ids.fetch_add(1);current_association=&data;}}
 ~association_scope(){if(on)current_association=prior;}
};
struct record {
 bool active=false,args_ok=false,dispatched=false;const char *layer="DRIVER_CALL";uint64_t binding_id=0,incoming=0;uint64_t original=0,actual=0,context=0,parent=0,entry=0,end=0;int context_rc=-1,result=-1;std::array<uint64_t,7> bits{};std::array<unsigned,8> geometry{};std::array<int,7> attr_rc{},attrs{};uint64_t stream=0;const char *api="UNKNOWN";
 template<class Name> record(bool on,Name name,uint64_t fn,void **args,void **extra,const char *a,uint64_t p) noexcept {
  if(!on)return;int saved=errno;try {if(name()!=symbol){errno=saved;return;}active=true;entry=now();original=fn;incoming=fn;if(current_association){binding_id=current_association->id;if(current_association->patched==fn){original=current_association->original;layer="DRIVER_CHILD";}}api=a;parent=p;attr_rc.fill(-1);args_ok=slots(args,extra,bits);}catch(...){active=false;output().incomplete=true;}errno=saved;
 }
 template<class Call,class Context,class Attr> int invoke(uint64_t fn,Call call,Context ctx,Attr attr) {
  if(!active)return call();int saved=errno;dispatched=true;actual=fn;try{context_rc=ctx(context);if(context_rc!=0)output().incomplete=true;for(int i=0;i<7;i++){attr_rc[i]=attr(i,attrs[i]);if(attr_rc[i]!=0)output().incomplete=true;}}catch(...){output().incomplete=true;}errno=saved;entry=now();int r=call();end=now();return r;
 }
 std::string encode() const {
  std::ostringstream s;s<<"{\"schema\":\"norm.host_call.v1\",\"pid\":"<<getpid()<<",\"tid\":"<<syscall(SYS_gettid)<<",\"start_ticks\":\""<<start_ticks()<<"\",\"boot_id\":\""<<boot()<<"\",\"api\":\""<<api<<"\",\"runtime_parent_id\":"<<parent<<",\"binding_id\":"<<binding_id<<",\"layer\":\""<<layer<<"\",\"incoming\":"<<incoming<<",\"original\":"<<original<<",\"actual\":"<<actual<<",\"route\":\""<<(!dispatched?"REJECTED_OR_NO_DISPATCH":actual==original?"ORIGINAL":"PATCHED")<<"\",\"generation\":\"UNKNOWN_UNCACHED\",\"context\":"<<context<<",\"context_rc\":"<<context_rc<<",\"entry_ns\":"<<entry<<",\"return_ns\":"<<end<<",\"rc\":"<<result<<",\"args_status\":\""<<(args_ok?"HOST_BITS_COPIED":"INCOMPLETE_UNSUPPORTED_OR_UNREADABLE")<<"\",\"slots\":[";
  for(int i=0;i<7;i++)s<<(i?",":"")<<bits[i];s<<"],\"geometry\":[";for(int i=0;i<8;i++)s<<(i?",":"")<<geometry[i];s<<"],\"stream\":"<<stream<<",\"attrs\":[";for(int i=0;i<7;i++)s<<(i?",":"")<<"["<<i<<","<<attr_rc[i]<<","<<(attr_rc[i]==0?std::to_string(attrs[i]):"null")<<"]";s<<"]}\n";return s.str();
 }
 int finish(int rc) noexcept {if(!active)return rc;int saved=errno;result=rc;if(!end)end=now();try{if(!args_ok)output().incomplete=true;output().append(encode());}catch(...){output().incomplete=true;}errno=saved;return rc;}
};
}

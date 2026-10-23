#pragma once
// Private TU-local diagnostic only; no public ABI, CUDA call or research-state lock.
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <exception>
#include <fcntl.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#ifndef HBFSIM_HOST_TIMING_WRITE
#define HBFSIM_HOST_TIMING_WRITE write
#endif
#ifndef HBFSIM_HOST_TIMING_FSYNC
#define HBFSIM_HOST_TIMING_FSYNC fsync
#endif
#ifndef HBFSIM_HOST_TIMING_CLOCK
#define HBFSIM_HOST_TIMING_CLOCK clock_gettime
#endif
namespace { namespace hbfsim_module_host_timing {
struct KeepErrno { int saved=errno; ~KeepErrno() noexcept {errno=saved;} };
struct Json {
 char data[2048];size_t size=0;bool ok=true,first=true;
 void add(const char*p,size_t n) noexcept {if(n>sizeof(data)-size){ok=false;return;}memcpy(data+size,p,n);size+=n;}
 void text(const char*p) noexcept {add(p,strlen(p));}
 void quoted(const char*p) noexcept {text("\"");for(const unsigned char*c=(const unsigned char*)(p?p:"");*c;++c){if(*c=='"'||*c=='\\'){char z[2]={'\\',char(*c)};add(z,2);}else if(*c<32||*c>126){char z[7];snprintf(z,sizeof(z),"\\u%04x",unsigned(*c));add(z,6);}else{char z=char(*c);add(&z,1);}}text("\"");}
 void key(const char*k) noexcept {if(!first)text(",");first=false;quoted(k);text(":");}
 void str(const char*k,const char*v) noexcept {key(k);quoted(v);}
 void num(const char*k,uint64_t n) noexcept {key(k);char z[32];int len=snprintf(z,sizeof(z),"%llu",(unsigned long long)n);if(len>0)add(z,size_t(len));else ok=false;}
 void integer(const char*k,long long n) noexcept {key(k);char z[32];int len=snprintf(z,sizeof(z),"%lld",n);if(len>0)add(z,size_t(len));else ok=false;}
 void finish() noexcept {text("}\n");}
};
struct Logger {
 bool on=false;int fd=-1;char path[1024]={},boot[64]={};uint64_t pid=0,start_ticks=0;
 std::atomic<uint64_t> ids{0},attempted{0},emitted{0},dropped{0},errors{0},clock_errors{0},active{0},raw_bytes{0};std::atomic<bool> closed{false};
 Logger() noexcept {
  KeepErrno guard;const char*flag=getenv("HBFSIM_MODULE_HOST_TIMING_V1");on=flag&&strcmp(flag,"1")==0;if(!on)return;
  const char*p=getenv("HBFSIM_MODULE_HOST_TIMING_PATH");if(!p||p[0]!='/'||strlen(p)>=sizeof(path)-8){errors++;return;}strcpy(path,p);pid=getpid();
  int f=open("/proc/sys/kernel/random/boot_id",O_RDONLY|O_CLOEXEC);if(f>=0){ssize_t n=read(f,boot,sizeof(boot)-1);if(n>0){boot[n]=0;boot[strcspn(boot,"\r\n")]=0;}else errors++;close(f);}else errors++;
  char statbuf[4096]={};f=open("/proc/self/stat",O_RDONLY|O_CLOEXEC);if(f>=0){ssize_t n=read(f,statbuf,sizeof(statbuf)-1);close(f);if(n>0){char*last=strrchr(statbuf,')');if(last&&last[1]==' '){char*save=nullptr;char*t=strtok_r(last+2," ",&save);for(int i=0;t&&i<19;++i)t=strtok_r(nullptr," ",&save);if(t)start_ticks=strtoull(t,nullptr,10);}}}
  if(!start_ticks||!boot[0])errors++;
  fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_APPEND|O_CLOEXEC|O_NOFOLLOW,0600);if(fd<0)errors++;
 }
 uint64_t now() noexcept {if(!on)return 0;KeepErrno guard;timespec t{};if(HBFSIM_HOST_TIMING_CLOCK(CLOCK_MONOTONIC,&t)!=0||t.tv_sec<0||t.tv_nsec<0||t.tv_nsec>=1000000000||uint64_t(t.tv_sec)>UINT64_MAX/1000000000ULL||(uint64_t(t.tv_sec)==UINT64_MAX/1000000000ULL&&uint64_t(t.tv_nsec)>UINT64_MAX%1000000000ULL)){clock_errors++;errors++;return 0;}return uint64_t(t.tv_sec)*1000000000ULL+uint64_t(t.tv_nsec);}
 void row(Json&j) noexcept {KeepErrno guard;uint64_t n=attempted.fetch_add(1);if(n>=1024){dropped++;return;}if(closed||!j.ok||j.size>2048||fd<0){dropped++;errors++;return;}ssize_t written=HBFSIM_HOST_TIMING_WRITE(fd,j.data,j.size);if(written>0)raw_bytes+=uint64_t(written);if(written!=ssize_t(j.size)){errors++;dropped++;return;}emitted++;}
 void finish() noexcept {
  KeepErrno guard;if(!on||closed.exchange(true))return;
  // Never close/reassign a raw descriptor while an observation may publish.
  const bool inflight=active.load()!=0;
  if(inflight){errors++;}
  else if(fd>=0){if(HBFSIM_HOST_TIMING_FSYNC(fd)!=0)errors++;if(close(fd)!=0)errors++;fd=-1;}else errors++;
  bool complete=!errors&&!dropped&&!clock_errors&&!active&&attempted==emitted;
  Json j;j.text("{");j.str("schema","hbfsim.module_host_timing.health.v1");j.str("status",complete?"CLOSED_COMPLETE":"INCOMPLETE");j.str("clock","CLOCK_MONOTONIC");j.num("pid",pid);j.num("start_ticks",start_ticks);j.str("boot_id",boot);j.num("attempted",attempted);j.num("emitted",emitted);j.num("dropped",dropped);j.num("errors",errors);j.num("clock_errors",clock_errors);j.num("active",active);j.num("raw_bytes",raw_bytes);j.finish();
  char health[1032];snprintf(health,sizeof(health),"%s.health",path);int h=path[0]?open(health,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600):-1;bool failure=inflight||h<0||!j.ok;
  if(h>=0){if(HBFSIM_HOST_TIMING_WRITE(h,j.data,j.size)!=ssize_t(j.size))failure=true;if(HBFSIM_HOST_TIMING_FSYNC(h)!=0)failure=true;if(close(h)!=0)failure=true;}
  if(failure){static const char marker[]="HBFSIM_MODULE_HOST_TIMING_FINALIZATION_FAILED\n";(void)HBFSIM_HOST_TIMING_WRITE(STDERR_FILENO,marker,sizeof(marker)-1);}
 }
 ~Logger() noexcept {finish();}
};
inline Logger&logger() noexcept {static Logger value;return value;}
inline uint64_t next_id() noexcept {auto&l=logger();return l.on?++l.ids:0;}
inline uint64_t stamp() noexcept {return logger().now();}
static thread_local uint64_t parent_batch=0;
struct Observation {
 bool on;uint64_t id,parent,tid=0,start=0,end=0,queued=0,prep_start=0,prep_end=0,call_start=0,call_end=0,join_start=0,join_end=0,module=0;long long rc=0;bool called=false,threw=false;int exceptions=0;bool metadata_overflow=false;char namebuf[256]={},rawbuf[128]={},keybuf[128]={};uint64_t record_ptr=0,impl_ptr=0,ptx_pool_ptr=0,module_pool_ptr=0;const char*kind;const char*name="";const char*raw="";const char*key="";const char*cache="UNKNOWN";const char*phase="UNKNOWN";const char*outcome="RETURNED";
 Observation(const char*k,uint64_t ordinal=0,uint64_t parent_id=0,uint64_t queue_ns=0) noexcept:on(logger().on),id(ordinal),parent(parent_id),kind(k){KeepErrno guard;if(!on)return;if(!id)id=next_id();tid=uint64_t(syscall(SYS_gettid));start=stamp();queued=queue_ns;exceptions=std::uncaught_exceptions();logger().active++;}
 void copy(char*dest,size_t cap,const char*src)noexcept{size_t n=strlen(src);if(n>=cap){metadata_overflow=true;return;}memcpy(dest,src,n+1);}
 void meta(const char*n,const char*r) noexcept {if(on){copy(namebuf,sizeof(namebuf),n);copy(rawbuf,sizeof(rawbuf),r);name=namebuf;raw=rawbuf;}}
 void lookup(const char*k,const char*result) noexcept {if(on){copy(keybuf,sizeof(keybuf),k);key=keybuf;cache=result;}}
 template<class F> auto call(F&&f)->decltype(f()) {
  if(!on)return f();called=true;call_start=stamp();try{auto value=f();int saved=errno;rc=(long long)value;call_end=stamp();errno=saved;return value;}catch(...){KeepErrno guard;threw=true;call_end=stamp();throw;}
 }
 template<class F> auto prepare(F&&f)->decltype(f()) {if(!on)return f();prep_start=stamp();try{auto value=f();int saved=errno;prep_end=stamp();errno=saved;return value;}catch(...){KeepErrno guard;threw=true;prep_end=stamp();throw;}}
 ~Observation() noexcept {
  KeepErrno guard;if(!on)return;end=stamp();if(end<start||(queued&&queued>start)||(call_start&&call_end<call_start)||(prep_start&&prep_end<prep_start)||(join_start&&join_end<join_start)){logger().clock_errors++;logger().errors++;}if(std::uncaught_exceptions()>exceptions||threw)outcome="EXCEPTION";
  Json j;j.text("{");j.str("schema","hbfsim.module_host_timing.row.v1");j.str("kind",kind);j.str("clock","CLOCK_MONOTONIC");j.str("unit","ns");j.num("pid",logger().pid);j.num("start_ticks",logger().start_ticks);j.str("boot_id",logger().boot);j.num("tid",tid);j.num("id",id);j.num("parent_id",parent);j.str("module_name",name);j.str("raw",raw);j.str("cache_key",key);j.str("cache",cache);j.str("phase",phase);j.str("outcome",outcome);j.num("enqueue_ns",queued);j.num("start_ns",start);j.num("end_ns",end);j.num("prep_start_ns",prep_start);j.num("prep_end_ns",prep_end);j.num("call_start_ns",call_start);j.num("call_end_ns",call_end);j.str("call_status",called?(threw?"THREW":"RETURNED"):"NOT_CALLED");if(called&&!threw)j.integer("call_rc",rc);j.num("join_start_ns",join_start);j.num("join_end_ns",join_end);j.num("module_handle",module);j.num("record_ptr",record_ptr);j.num("impl_ptr",impl_ptr);j.num("ptx_pool_ptr",ptx_pool_ptr);j.num("module_pool_ptr",module_pool_ptr);j.str("generation","UNKNOWN");j.finish();if(metadata_overflow)j.ok=false;logger().row(j);logger().active--;
 }
};
struct Batch:Observation {uint64_t previous;Batch(const char*kind)noexcept:Observation(kind,0,parent_batch),previous(parent_batch){if(on)parent_batch=id;}~Batch()noexcept{if(on)parent_batch=previous;}};
} }

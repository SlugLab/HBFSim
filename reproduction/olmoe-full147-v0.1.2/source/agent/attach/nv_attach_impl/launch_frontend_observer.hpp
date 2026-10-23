#pragma once
// Private C2 observer; no CUDA dependencies, public symbols or lifecycle calls.
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <fcntl.h>
#include <mutex>
#include <new>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <utility>
#include <exception>
#include <type_traits>
namespace { namespace hbfsim_frontend_c2 {
constexpr size_t max_rows=16384, max_row_bytes=896, health_reserve=65536;
constexpr size_t budget_bytes=16*1024*1024;
static_assert(max_rows*max_row_bytes+health_reserve<=budget_bytes,"bounded capture");
enum class phase { disabled, open, sealing, sealed, failed };
enum point : unsigned { admitted_at, state_ready, context_begin, context_end,
 metadata_begin, metadata_end, attributes_begin, attributes_end, selection_begin, selection_end,
 dispatch_begin, dispatch_end, old_log_begin, old_log_end, return_ready, point_count };
struct operations {
 int(*clock)(clockid_t,timespec*)=::clock_gettime;
 ssize_t(*put)(int,const void*,size_t)=::write;
 int(*sync)(int)=::fsync; int(*close_fd)(int)=::close;
 int(*at_exit)(void(*)())=::atexit;
};
struct state {
 std::mutex mu; phase p=phase::disabled; operations io;
 uint64_t activation=0,seal=0,admitted=0,rows=0,active=0,next_id=1,last_clock=0;
 std::atomic<uint64_t> clock_errors{0},write_errors{0},cap_errors{0},entry_errors{0};
 size_t bytes=0,limit=max_rows; int raw=-1,health=-1; bool configured=false;
 long pid=0; unsigned long long start=0; char boot[80]{};
 char health_final[4096]{},health_pending[4096]{};
};
static std::atomic<state*> current{nullptr}; // Deliberately never freed; callback owns no destructor.
static thread_local uint64_t parent_id=0;
#ifdef HBFSIM_FRONTEND_SOFTWARE_TEST
static operations injected_operations;
#endif
static uint64_t clock_locked(state& s) noexcept {
 timespec t{};if(s.io.clock(CLOCK_MONOTONIC,&t)||t.tv_sec<0||t.tv_nsec<0||t.tv_nsec>=1000000000){++s.clock_errors;return 0;}
 if(uint64_t(t.tv_sec)>(UINT64_MAX-uint64_t(t.tv_nsec))/1000000000ULL){++s.clock_errors;return 0;}
 uint64_t value=uint64_t(t.tv_sec)*1000000000ULL+uint64_t(t.tv_nsec);
 if(value==0||value<s.last_clock){++s.clock_errors;return 0;}s.last_clock=value;return value;
}
static bool write_all(state& s,int fd,const char* p,size_t n) noexcept {
 while(n){ssize_t v=s.io.put(fd,p,n);if(v<0&&errno==EINTR)continue;
  if(v<=0||size_t(v)>n){++s.write_errors;return false;}p+=v;n-=size_t(v);}return true;
}
static bool errors(const state& s) noexcept {return s.clock_errors||s.write_errors||s.cap_errors||s.entry_errors;}
static void seal_callback() noexcept {
 int saved=errno;state* s=current.load(std::memory_order_acquire);if(!s){errno=saved;return;}
 try {std::lock_guard<std::mutex> g(s->mu);if(s->p!=phase::open){errno=saved;return;}
  s->p=phase::sealing;s->seal=clock_locked(*s); // Same cut as admission.
  bool ok=!errors(*s)&&s->active==0&&s->admitted==s->rows&&s->seal>=s->activation;
  if(s->raw<0)ok=false;else{if(s->io.sync(s->raw))ok=false;if(s->io.close_fd(s->raw))ok=false;s->raw=-1;}
  char b[2048];int n=std::snprintf(b,sizeof b,
   "{\"schema\":\"hbfsim.frontend.c2.health.v1\",\"status\":\"%s\",\"coverage\":\"COVERED_TARGET_WINDOWS_ONLY\",\"post_seal_activity\":\"UNKNOWN_NOT_OBSERVED\",\"clock\":\"CLOCK_MONOTONIC\",\"pid\":%ld,\"start_ticks\":%llu,\"boot_id\":\"%s\",\"activation_ns\":%llu,\"seal_ns\":%llu,\"admitted\":%llu,\"rows\":%llu,\"active\":%llu,\"raw_bytes\":%zu,\"clock_errors\":%llu,\"write_errors\":%llu,\"cap_errors\":%llu,\"entry_errors\":%llu}\n",
   ok?"SEALED_REQUIRES_AUTHENTICATED_TARGET_CONTAINMENT":"FAILED",s->pid,s->start,s->boot,
   (unsigned long long)s->activation,(unsigned long long)s->seal,(unsigned long long)s->admitted,
   (unsigned long long)s->rows,(unsigned long long)s->active,s->bytes,
   (unsigned long long)s->clock_errors.load(),(unsigned long long)s->write_errors.load(),
   (unsigned long long)s->cap_errors.load(),(unsigned long long)s->entry_errors.load());
  if(n<=0||size_t(n)>=sizeof b||s->health<0)ok=false;
  else {if(!write_all(*s,s->health,b,size_t(n)))ok=false;if(s->io.sync(s->health))ok=false;}
  if(s->health>=0){if(s->io.close_fd(s->health))ok=false;s->health=-1;}
  // Only a completely synced and closed health inode may receive the final name.
  // link is exclusive and atomic; a pending file is never accepted as success.
  if(ok && ::link(s->health_pending,s->health_final)!=0)ok=false;
  s->p=ok?phase::sealed:phase::failed;
  if(ok)::unlink(s->health_pending); // Best-effort redundant-name cleanup only.
  // Independent terminal archive must authenticate final raw + final health.
  // Namespace crash durability is not asserted by this process-local callback.
 }catch(...){s->p=phase::failed;}errno=saved;
}
static bool read_identity(state& s) noexcept {
 s.pid=long(::getpid());char data[4096]{};int fd=::open("/proc/self/stat",O_RDONLY|O_CLOEXEC);
 if(fd<0)return false;ssize_t n=::read(fd,data,sizeof(data)-1);int rc=::close(fd);if(n<=0||rc)return false;
 char* end=std::strrchr(data,')');if(!end)return false;char* token=end+2;
 for(int field=3;field<=22;++field){while(*token==' ')++token;if(!*token)return false;
  char* stop=token;while(*stop&&*stop!=' ')++stop;
  if(field==22){char keep=*stop;*stop=0;char* tail=nullptr;s.start=std::strtoull(token,&tail,10);bool ok=tail&&!*tail&&s.start;*stop=keep;if(!ok)return false;break;}token=stop;}
 fd=::open("/proc/sys/kernel/random/boot_id",O_RDONLY|O_CLOEXEC);if(fd<0)return false;
 n=::read(fd,s.boot,sizeof(s.boot)-1);rc=::close(fd);if(n<=0||rc)return false;
 while(n&& (s.boot[n-1]=='\n'||s.boot[n-1]=='\r'))s.boot[--n]=0;
 for(ssize_t i=0;i<n;++i)if(!((s.boot[i]>='a'&&s.boot[i]<='f')||(s.boot[i]>='0'&&s.boot[i]<='9')||s.boot[i]=='-'))return false;
 return n==36;
}
static void activate() noexcept {
 int saved=errno;try {const char* flag=::getenv("HBFSIM_LAUNCH_FRONTEND_SPANS_V1");
 if(!flag||!std::strcmp(flag,"0")){errno=saved;return;}
 // Setup is single-threaded before hook publication. No lazy entry activation.
 static std::mutex setup_mutex;std::lock_guard<std::mutex> setup_lock(setup_mutex);
 if(current.load(std::memory_order_acquire)){errno=saved;return;}
 state* s=new(std::nothrow) state;if(!s){errno=saved;return;}std::lock_guard<std::mutex> state_setup(s->mu);current.store(s,std::memory_order_release);s->configured=true;
#ifdef HBFSIM_FRONTEND_SOFTWARE_TEST
 s->io=injected_operations;
#endif
 if(std::strcmp(flag,"1")){s->p=phase::failed;errno=saved;return;}
 const char* path=::getenv("HBFSIM_LAUNCH_FRONTEND_SPANS_PATH");
 const char* hp=::getenv("HBFSIM_LAUNCH_FRONTEND_SPANS_HEALTH_PATH");
 const char* cap=::getenv("HBFSIM_LAUNCH_FRONTEND_SPANS_MAX_ENTRIES");
 bool valid=path&&hp&&path[0]=='/'&&hp[0]=='/'&&std::strcmp(path,hp);
 if(cap){char* end=nullptr;errno=0;auto v=std::strtoull(cap,&end,10);valid=valid&& !errno&&end&&!*end&&v>0&&v<=max_rows;s->limit=size_t(v);}
 if(!valid||!read_identity(*s)){s->p=phase::failed;errno=saved;return;}
 if(std::strlen(hp)+9>=sizeof(s->health_pending)){s->p=phase::failed;errno=saved;return;}
 std::snprintf(s->health_final,sizeof s->health_final,"%s",hp);
 std::snprintf(s->health_pending,sizeof s->health_pending,"%s.pending",hp);
 s->raw=::open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
 s->health=::open(s->health_pending,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
 if(s->raw<0||s->health<0||s->io.at_exit(seal_callback)){s->p=phase::failed;errno=saved;return;}
 s->activation=clock_locked(*s);s->p=errors(*s)?phase::failed:phase::open;errno=saved;
 }catch(...){auto* failed=current.load(std::memory_order_acquire);if(failed)failed->p=phase::failed;errno=saved;}
}
struct entry {
 state* s=nullptr;uint64_t id=0,parent=0,tid=0,t[point_count]{};
 uint64_t strict_id=0,binding_id=0,decision_id=0;unsigned route=0,mask=0,dispatch_calls=0;int uncaught=0,result=0;bool result_valid=false;
 explicit entry(unsigned r) noexcept {
  int saved=errno;s=current.load(std::memory_order_acquire);if(!s){errno=saved;return;}
  try{std::lock_guard<std::mutex> g(s->mu);if(s->p!=phase::open){s=nullptr;errno=saved;return;}
   ++s->admitted;++s->active;id=s->next_id++;parent=parent_id;parent_id=id;route=r;
   tid=uint64_t(::syscall(SYS_gettid));uncaught=std::uncaught_exceptions();
   t[admitted_at]=clock_locked(*s);mask=1U<<admitted_at;
   if(s->admitted>s->limit)++s->cap_errors;
  }catch(...){if(s)++s->entry_errors;s=nullptr;}errno=saved;
 }
 entry(const entry&)=delete;entry& operator=(const entry&)=delete;
 void mark(point p) noexcept {if(!s)return;int saved=errno;try{std::lock_guard<std::mutex> g(s->mu);t[p]=clock_locked(*s);mask|=1U<<p;}catch(...){++s->entry_errors;}errno=saved;}
 void ids(uint64_t a,uint64_t b=0,uint64_t c=0) noexcept {if(!s)return;strict_id=a;binding_id=b;decision_id=c;}
 template<class T> T ret(T v) noexcept {if(s){result=int(v);result_valid=true;}return v;}
 template<class F> auto dispatch(F&& f)->decltype(f()) {
  if(!s)return f();if(++dispatch_calls>1)++s->entry_errors;if((mask&(1U<<selection_begin))&&!(mask&(1U<<selection_end)))mark(selection_end);mark(dispatch_begin);
  try{auto result=f();mark(dispatch_end);return result;}catch(...){mark(dispatch_end);throw;}
 }
 template<class F> auto log(F&& f)->decltype(f()) {
  if(!s)return f();mark(old_log_begin);
  try{if constexpr(std::is_void<decltype(f())>::value){f();mark(old_log_end);return;}
   else {auto result=f();mark(old_log_end);return result;}}
  catch(...){mark(old_log_end);throw;}
 }
 ~entry() noexcept {
  if(!s)return;int saved=errno;try{std::lock_guard<std::mutex> g(s->mu);
   t[return_ready]=clock_locked(*s);mask|=1U<<return_ready;char row[max_row_bytes];
   int n=std::snprintf(row,sizeof row,"{\"schema\":\"frontend.c2.entry.v1\",\"id\":%llu,\"parent\":%llu,\"tid\":%llu,\"route\":%u,\"strict\":%llu,\"binding\":%llu,\"decision\":%llu,\"mask\":%u,\"exception\":%s,\"result_valid\":%s,\"result\":%d,\"ns\":[",
    (unsigned long long)id,(unsigned long long)parent,(unsigned long long)tid,route,
    (unsigned long long)strict_id,(unsigned long long)binding_id,(unsigned long long)decision_id,mask,
    std::uncaught_exceptions()>uncaught?"true":"false",result_valid?"true":"false",result);
   bool good=n>0&&size_t(n)<sizeof row;size_t used=good?size_t(n):0;
   for(unsigned i=0;good&&i<point_count;++i){n=std::snprintf(row+used,sizeof row-used,"%s%llu",i?",":"",(unsigned long long)t[i]);good=n>0&&size_t(n)<sizeof row-used;if(good)used+=size_t(n);}
   if(good&&used+3<sizeof row){std::memcpy(row+used,"]}\n",3);used+=3;}else good=false;
   if(s->p!=phase::open||!good||s->admitted>s->limit||s->bytes+used>budget_bytes-health_reserve){++s->entry_errors;}
   else if(write_all(*s,s->raw,row,used)){++s->rows;s->bytes+=used;}
   parent_id=parent;--s->active; // Only after commit or sticky failure.
  }catch(...){++s->entry_errors;if(s->active)--s->active;parent_id=parent;}errno=saved;
 }
};
} }

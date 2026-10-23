// Private to the core TU. No public ABI, class member, CUDA or lifecycle hook.
#pragma once
#include "native_binding_digest_cache.hpp"
#include <atomic>
#include <array>
#include <memory>
#include <limits>
#include <utility>
#pragma GCC visibility push(hidden)
namespace hbfsim_private_ptx {
namespace id = hbfsim_private_digest;
static const pid_t owner_pid = ::getpid();
static constexpr size_t byte_cap = size_t(2)*1024*1024*1024;
static constexpr size_t entry_cap = 64;
static constexpr size_t metadata_charge = 65536;
static_assert(std::atomic<size_t>::is_always_lock_free,"private cache accounting must be lock-free");
static std::atomic<size_t> charged_bytes{metadata_charge}, charged_entries{0};
static bool parent_process(){ return ::getpid()==owner_pid; }
static bool enabled(){
 if(!parent_process()) return false;
 const char* v=std::getenv("HBFSIM_NATIVE_BINDING_PTX_INPUT_AUTH_CACHE_V1");
 return v && std::strcmp(v,"1")==0;
}
struct ticket {
 size_t bytes=0;
 ticket()=default;
 ticket(const ticket&)=delete;
 ticket& operator=(const ticket&)=delete;
 ~ticket(){
  // No inherited mutex or cache state in a fork child, including destruction.
  if(!parent_process() || bytes==0) return;
  charged_bytes.fetch_sub(bytes,std::memory_order_relaxed);
  charged_entries.fetch_sub(1,std::memory_order_relaxed);
 }
};
static bool reserve(ticket& t,size_t bytes){
 if(!parent_process() || bytes>byte_cap) return false;
 size_t n=charged_entries.load(std::memory_order_relaxed);
 do { if(n>=entry_cap) return false; }
 while(!charged_entries.compare_exchange_weak(n,n+1,std::memory_order_relaxed));
 size_t used=charged_bytes.load(std::memory_order_relaxed);
 do {
  if(used>byte_cap-bytes){charged_entries.fetch_sub(1,std::memory_order_relaxed);return false;}
 } while(!charged_bytes.compare_exchange_weak(used,used+bytes,std::memory_order_relaxed));
 t.bytes=bytes;return true;
}
struct entry {
 ticket budget;
 std::string expected, role, digest;
 id::admission admitted;
 std::vector<char> bytes;
};
struct system_allocation {
 std::shared_ptr<entry> make_entry()const{return std::make_shared<entry>();}
 void resize_bytes(std::vector<char>& bytes,size_t n)const{bytes.resize(n);}
};
static std::mutex cache_mutex;
static std::array<std::shared_ptr<const entry>,entry_cap> entries;
static size_t entries_count=0;
static std::shared_ptr<const entry> evict_locked(){
 if(entries_count==0)return {};
 auto e=std::move(entries[0]);
 for(size_t i=1;i<entries_count;++i)entries[i-1]=std::move(entries[i]);
 entries[--entries_count].reset();return e;
}
struct input {
 std::shared_ptr<const entry> owner;
 std::vector<char> original;
 std::string digest;
 const char* data()const{return owner?owner->bytes.data():original.data();}
 size_t size()const{return owner?owner->bytes.size():original.size();}
};
// All lookup/reservation/IO occur BEFORE caller's final registration lock.
// OriginalRead and Hash are original production functions; IO is a CPU seam.
template<class OriginalRead,class Hash,class IO=id::system_io,class Allocation=system_allocation>
static input read(const std::filesystem::path& path,const std::string& expected,
                  bool raw,OriginalRead original_read,Hash hash,const IO& io=IO{},const Allocation& allocation=Allocation{}){
 auto original=[&](){input v;v.original=original_read();v.digest=hash(v.original.data(),v.original.size());return v;};
 if(!enabled() || expected.size()!=64) return original();
 id::admission admitted;
 if(!id::inspect(path,admitted,io)) return original();
 // Only current manifest-selected original path, not arbitrary cached authorization.
 id::fd_owner parent{io.open_parent(admitted.parent.c_str())};
 if(parent.fd<0) return original();
 id::fd_owner fd{io.open_leaf(parent.fd,path.filename().c_str(),admitted.alias)};
 if(fd.fd<0 || !id::stable(admitted,parent.fd,fd.fd,io)) return original();
 std::shared_ptr<const entry> hit;
 const char* role=raw?"RAW_PTX_BYTES_V1":"STAGED_PTX_DIGEST_V1";
 {
  std::lock_guard<std::mutex> lock(cache_mutex);
  for(size_t i=0;i<entries_count;++i){const auto& e=entries[i];if(e->expected==expected && e->role==role && id::same_admission(e->admitted,admitted)){hit=e;break;}}
 }
 if(hit){
  if(!id::stable(admitted,parent.fd,fd.fd,io)) return original();
  input v;v.owner=std::move(hit);v.digest=v.owner->digest;return v;
 }
 if(admitted.target.size<0 || static_cast<uintmax_t>(admitted.target.size)>std::numeric_limits<size_t>::max())return original();
 const size_t length=static_cast<size_t>(admitted.target.size);
 if(raw && length>byte_cap-metadata_charge)return original();
 std::shared_ptr<entry> fresh;
 // Only cache allocation/admission may decline. No IO/hash exception is caught.
 try { fresh=allocation.make_entry();fresh->admitted=admitted;fresh->expected=expected;fresh->role=role;fresh->digest.resize(64); }
 catch(const std::bad_alloc&){return original();}
 if(!reserve(fresh->budget,metadata_charge+(raw?length:0))){
  std::shared_ptr<const entry> evicted;
  {std::lock_guard<std::mutex> lock(cache_mutex);evicted=evict_locked();}
  evicted.reset();
  if(!reserve(fresh->budget,metadata_charge+(raw?length:0)))return original();
 }
 std::vector<char> bytes;
 try { allocation.resize_bytes(bytes,length); }
 catch(const std::bad_alloc&){fresh.reset();return original();}
 size_t used=0;
 while(used<length){
  const size_t remain=std::min<size_t>(65536,length-used);
  const ssize_t n=io.read(fd.fd,bytes.data()+used,remain);
  if(n<0){if(errno==EINTR)continue;throw std::runtime_error("PTX input cache FD read failed");}
  if(n==0)break;
  if(static_cast<size_t>(n)>remain)throw std::runtime_error("PTX input cache invalid read count");
  used+=static_cast<size_t>(n);
 }
 bytes.resize(used);
 if(raw && bytes.capacity()>length)return original();
 // Detect growth/size disagreement even if a fixture leaves metadata unchanged.
 char extra;ssize_t tail;
 do {tail=io.read(fd.fd,&extra,1);}while(tail<0 && errno==EINTR);
 if(tail<0)throw std::runtime_error("PTX input cache final FD read failed");
 if(tail>0)return original();
 const auto digest=hash(bytes.data(),bytes.size());
 if(!id::stable(admitted,parent.fd,fd.fd,io))return original();
 if(digest.size()!=64)throw std::runtime_error("PTX input cache invalid SHA256 length");
 if(digest!=expected){input v;v.original=std::move(bytes);v.digest=digest;return v;}
 fresh->digest=digest;
 const size_t metadata=sizeof(entry)+fresh->admitted.declared.capacity()+fresh->admitted.parent.capacity()+fresh->admitted.canonical.capacity()+fresh->admitted.link.capacity()+fresh->expected.capacity()+fresh->role.capacity()+fresh->digest.capacity()+7;
 if(metadata>metadata_charge)return original();
 if(raw)fresh->bytes=std::move(bytes);
 std::shared_ptr<const entry> published=std::move(fresh),evicted;
 {std::lock_guard<std::mutex> lock(cache_mutex);if(entries_count>=entry_cap)evicted=evict_locked();entries[entries_count++]=published;}
 input v;v.owner=std::move(published);v.digest=digest;return v;
}
struct proof {
 const input* input_owner=nullptr;
 const char* pointer=nullptr;
 size_t length=0;
 const std::string* digest=nullptr;
 bool consumed=false;
};
// Trivial TLS pointer: no local-static initialization guard or new owner protocol.
static thread_local proof* active_proof=nullptr;
struct proof_scope {
 proof state;proof* previous=nullptr;bool armed=false;
 proof_scope(const input& bytes,bool valid){
  if(!enabled() || !valid)return;
  state={&bytes,bytes.data(),bytes.size(),&bytes.digest,false};
  previous=active_proof;active_proof=&state;armed=true;
 }
 ~proof_scope(){if(!parent_process() || !armed)return;active_proof=previous;}
 proof_scope(const proof_scope&)=delete;
};
template<class Hash>static std::string bind_digest(const char* pointer,size_t size,Hash hash){
 if(!enabled())return hash(pointer,size);
 proof* p=active_proof;
 if(p && !p->consumed && p->input_owner && p->pointer==pointer && p->length==size &&
    p->input_owner->data()==pointer && p->input_owner->size()==size && p->digest==&p->input_owner->digest){
  p->consumed=true; // Before dlsym, gate or any possible reentrancy.
  return *p->digest;
 }
 return hash(pointer,size);
}
} // namespace hbfsim_private_ptx
#pragma GCC visibility pop

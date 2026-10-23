// Private implementation; no public ABI. All state belongs to this TU.
#pragma once
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <fcntl.h>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <string>
#include <vector>
#include <stdexcept>
#include <algorithm>
namespace hbfsim_private_digest {
// Namespace-scope initialization: no function-local static guard on the call path.
// Before any state/mutex access child compares this plain inherited PID.
static const pid_t owner_pid = ::getpid();
struct fingerprint {
 dev_t dev; ino_t ino; mode_t mode; nlink_t nlink; uid_t uid; gid_t gid;
 off_t size; timespec mt, ct;
 bool operator==(const fingerprint& x) const {
  return dev==x.dev && ino==x.ino && mode==x.mode && nlink==x.nlink && uid==x.uid && gid==x.gid && size==x.size && mt.tv_sec==x.mt.tv_sec && mt.tv_nsec==x.mt.tv_nsec && ct.tv_sec==x.ct.tv_sec && ct.tv_nsec==x.ct.tv_nsec;
 }
};
static fingerprint fp(const struct stat& s) { return {s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_uid,s.st_gid,s.st_size,s.st_mtim,s.st_ctim}; }
struct entry { std::string path, digest; fingerprint identity; };
static std::mutex cache_mutex;
static std::vector<entry> entries; // at most 32, each path <=4096/digest64
struct fd_owner { int fd; ~fd_owner(){ if(fd>=0) ::close(fd); } };
struct system_io {
 int open(const char* p) const { return ::open(p,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); }
 int open_parent(const char* p) const { return ::open(p,O_RDONLY|O_CLOEXEC|O_DIRECTORY|O_NOFOLLOW); }
 int open_leaf(int parent,const char* p,bool alias) const { return ::openat(parent,p,O_RDONLY|O_CLOEXEC|(alias?0:O_NOFOLLOW)); }
 ssize_t readlink(const char* p,char* b,size_t n) const { return ::readlink(p,b,n); }
 int fstat(int f,struct stat* s) const { return ::fstat(f,s); }
 int lstat(const char* p,struct stat* s) const { return ::lstat(p,s); }
 ssize_t read(int f,char* p,size_t n) const { return ::read(f,p,n); }
};
struct admission {
 std::string declared, parent, canonical, link;
 fingerprint leaf{}, directory{}, target{};
 bool alias=false;
};
template<class IO> static bool inspect(const std::filesystem::path& path,admission& out,const IO& io) {
 out.declared=path.string();
 if(out.declared.empty() || out.declared.size()>4096 || !path.is_absolute() || path.lexically_normal()!=path) return false;
 std::error_code ec;
 const auto parent=path.parent_path();
 if(std::filesystem::canonical(parent,ec)!=parent || ec) return false;
 struct stat parent_stat{},leaf_stat{},target_stat{};
 if(io.lstat(parent.c_str(),&parent_stat)!=0 || !S_ISDIR(parent_stat.st_mode) || io.lstat(path.c_str(),&leaf_stat)!=0) return false;
 out.parent=parent.string();out.directory=fp(parent_stat);out.leaf=fp(leaf_stat);
 auto target=path;
 if(S_ISLNK(leaf_stat.st_mode)) {
  char bytes[4097];const auto n=io.readlink(path.c_str(),bytes,sizeof(bytes));
  if(n<=0 || n>4096) return false;
  out.alias=true;out.link.assign(bytes,static_cast<size_t>(n));
  const std::filesystem::path link(out.link);
  target=(link.is_absolute()?link:parent/link).lexically_normal();
 } else if(!S_ISREG(leaf_stat.st_mode)) return false;
 // A single admitted leaf: neither parent nor target contains further aliases.
 const auto canonical=std::filesystem::canonical(target,ec);
 if(ec || canonical!=target || io.lstat(target.c_str(),&target_stat)!=0 || !S_ISREG(target_stat.st_mode)) return false;
 out.canonical=target.string();out.target=fp(target_stat);return true;
}
static bool same_admission(const admission& a,const admission& b) {
 return a.declared==b.declared && a.parent==b.parent && a.canonical==b.canonical && a.link==b.link && a.alias==b.alias && a.leaf==b.leaf && a.directory==b.directory && a.target==b.target;
}
template<class IO> static bool stable(const admission& initial,int parent_fd,int fd,const IO& io) {
 admission now;struct stat parent{},file{};
 return inspect(initial.declared,now,io) && same_admission(initial,now) && io.fstat(parent_fd,&parent)==0 && fp(parent)==initial.directory && io.fstat(fd,&file)==0 && S_ISREG(file.st_mode) && fp(file)==initial.target;
}
// Injected IO is a compile-time fixture seam; production always uses system_io.
// Hash is the original caller SHA, not a second implementation.
template<class Original,class Hash,class IO=system_io>
static std::string digest(const std::filesystem::path& path,const std::filesystem::path& qualified_target,Original original,Hash hash,const IO& io=IO{}) {
 const char* flag=std::getenv("HBFSIM_NATIVE_BINDING_DSO_DIGEST_CACHE_V1");
 if(flag==nullptr || std::strcmp(flag,"1")!=0) return original();
 // This precedes canonicalization, cache object access and mutex acquisition.
 if(::getpid()!=owner_pid || qualified_target.empty()) return original();
 admission admitted;
 if(!inspect(path,admitted,io) || admitted.canonical!=qualified_target.string()) return original();
 fd_owner directory{io.open_parent(admitted.parent.c_str())};
 if(directory.fd<0) return original();
 // Open the ORIGINAL registered leaf through its admitted parent, never the
 // canonical target directly. Only this explicit one-leaf branch follows it.
 fd_owner fd{io.open_leaf(directory.fd,path.filename().c_str(),admitted.alias)};
 if(fd.fd<0) return original();
 if(!stable(admitted,directory.fd,fd.fd,io)) return original();
 const auto& name=admitted.canonical;
 const auto& identity=admitted.target;
 std::string hit;
 {
  std::lock_guard<std::mutex> lock(cache_mutex);
  for(const auto& e:entries) if(e.path==name && e.identity==identity) { hit=e.digest; break; }
 }
 if(!hit.empty()) {
  if(stable(admitted,directory.fd,fd.fd,io)) return hit;
  return original();
 }
 std::vector<char> bytes;
 char block[65536];
 for(;;) {
  const ssize_t n=io.read(fd.fd,block,sizeof(block));
  if(n<0) { if(errno==EINTR) continue; throw std::runtime_error("native binding digest cache FD read failed"); }
  if(n==0) break;
  if(static_cast<size_t>(n)>sizeof(block)) throw std::runtime_error("native binding digest cache invalid read count");
  bytes.insert(bytes.end(),block,block+n);
 }
 // Actual read/hash exceptions propagate to original auto_bind catch/100.
 const auto result=hash(bytes.data(),bytes.size());
 if(!stable(admitted,directory.fd,fd.fd,io)) return original();
 if(result.size()!=64) throw std::runtime_error("native binding digest cache invalid SHA256 length");
 {
  std::lock_guard<std::mutex> lock(cache_mutex);
  auto i=std::find_if(entries.begin(),entries.end(),[&](const entry& e){return e.path==name;});
  if(i!=entries.end()) entries.erase(i);
  if(entries.size()==32) entries.erase(entries.begin());
  entries.push_back({name,result,identity});
 }
 return result;
}
} // namespace hbfsim_private_digest

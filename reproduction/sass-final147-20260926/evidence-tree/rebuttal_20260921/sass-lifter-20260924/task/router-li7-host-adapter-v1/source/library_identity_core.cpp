#include "library_identity_core.hpp"
#include <algorithm>
#include <cstring>
#include <elf.h>
#include <fstream>
#include <limits>
#include <sstream>

namespace hbfsim_provider {
namespace {
bool add_ok(std::size_t a,std::size_t b,std::size_t* out){return !__builtin_add_overflow(a,b,out);}
bool mul_ok(std::size_t a,std::size_t b,std::size_t* out){return !__builtin_mul_overflow(a,b,out);}
bool same_ids(const LineageEvidence&a,const LineageEvidence&b){return a.byte_ids==b.byte_ids;}
}
const char* image_kind_name(ImageKind k){switch(k){case ImageKind::Elf64:return "elf64";case ImageKind::Fatbin:return "fatbin";case ImageKind::Ptx:return "ptx";default:return "unknown";}}

ImageCapture inspect_image(const void* data,std::size_t avail,std::size_t maximum_bytes,
                           bool follow_process_wrapper){
 ImageCapture r; if(!data){r.reason="NULL_DATA";return r;} if(!avail){r.reason="NO_READABLE_BYTES";return r;}
 const std::size_t input_cap=std::min(maximum_bytes,avail); const auto*p=static_cast<const unsigned char*>(data);
 if(input_cap>=sizeof(Elf64_Ehdr)&&std::memcmp(p,ELFMAG,SELFMAG)==0){
  Elf64_Ehdr h{};std::memcpy(&h,p,sizeof(h));
  if(h.e_ident[EI_CLASS]!=ELFCLASS64||h.e_ident[EI_DATA]!=ELFDATA2LSB){r.reason="ELF_UNSUPPORTED_CLASS_OR_ENDIAN";return r;}
  if(h.e_phnum==PN_XNUM||(h.e_shnum==0&&h.e_shoff!=0)||h.e_shstrndx==SHN_XINDEX){r.reason="ELF_EXTENDED_NUMBERING_UNSUPPORTED";return r;}
  std::size_t end=std::max<std::size_t>(sizeof(h),h.e_ehsize),table=0;
  if(h.e_phnum){if(h.e_phentsize!=sizeof(Elf64_Phdr)||!mul_ok(h.e_phnum,h.e_phentsize,&table)||!add_ok(h.e_phoff,table,&table)||table>input_cap){r.reason="ELF_BAD_PROGRAM_TABLE";return r;}end=std::max(end,table);for(unsigned i=0;i<h.e_phnum;i++){Elf64_Phdr ph{};std::memcpy(&ph,p+h.e_phoff+i*sizeof(ph),sizeof(ph));std::size_t x=0;if(!add_ok(ph.p_offset,ph.p_filesz,&x)||x>input_cap){r.reason="ELF_PROGRAM_OUT_OF_BOUNDS";return r;}end=std::max(end,x);}}
  if(h.e_shnum){if(h.e_shentsize!=sizeof(Elf64_Shdr)||!mul_ok(h.e_shnum,h.e_shentsize,&table)||!add_ok(h.e_shoff,table,&table)||table>input_cap){r.reason="ELF_BAD_SECTION_TABLE";return r;}end=std::max(end,table);for(unsigned i=0;i<h.e_shnum;i++){Elf64_Shdr sh{};std::memcpy(&sh,p+h.e_shoff+i*sizeof(sh),sizeof(sh));if(sh.sh_type==SHT_NOBITS)continue;std::size_t x=0;if(!add_ok(sh.sh_offset,sh.sh_size,&x)||x>input_cap){r.reason="ELF_SECTION_OUT_OF_BOUNDS";return r;}end=std::max(end,x);}}
  if(!end||end>input_cap){r.reason="ELF_SIZE_INVALID";return r;}r={true,ImageKind::Elf64,end,"OK",data};return r;
 }
 auto raw_chain=[&](const void* payload,std::size_t cap,std::size_t readable_extent)->ImageCapture{
  ImageCapture x; const auto*q=static_cast<const unsigned char*>(payload);std::size_t off=0;bool any=false;
  while(off<cap){
   const std::size_t remain=cap-off;
   if(remain<16){x.reason=any?"FATBIN_CHAIN_TRUNCATED":"FATBIN_HEADER_TRUNCATED";return x;}
   std::uint32_t magic=0;std::uint16_t header=0;std::uint64_t payload_size=0;
   std::memcpy(&magic,q+off,4);
   if(magic!=0xba55ed50u){if(any)return {true,ImageKind::Fatbin,off,"OK",payload};x.reason="WRAPPER_PAYLOAD_NOT_RAW_FATBIN";return x;}
   std::memcpy(&header,q+off+6,2);std::memcpy(&payload_size,q+off+8,8);
   std::size_t record=0,next=0;
   if(header<16||payload_size>std::numeric_limits<std::size_t>::max()||!add_ok(header,static_cast<std::size_t>(payload_size),&record)||!add_ok(off,record,&next)||next>cap||next<=off){x.reason="FATBIN_OUT_OF_BOUNDS";return x;}
   off=next;any=true;
  }
  if(any&&off==cap&&readable_extent>cap){
   if(readable_extent-cap<4){x.reason="FATBIN_MAXIMUM_BOUNDARY_UNPROVEN";return x;}
   std::uint32_t next_magic=0;std::memcpy(&next_magic,q+cap,4);
   if(next_magic==0xba55ed50u){x.reason="FATBIN_MAXIMUM_TRUNCATES_CHAIN";return x;}
  }
  return any?ImageCapture{true,ImageKind::Fatbin,off,"OK",payload}:x;
 };
 if(input_cap>=4){
  std::uint32_t magic=0;std::memcpy(&magic,p,4);
  if(magic==0xba55ed50u)return raw_chain(data,input_cap,avail);
  if(magic==0x466243b1u){
   if(!follow_process_wrapper){r.reason="FATBIN_WRAPPER_POINTER_DISALLOWED";return r;}
   if(input_cap<24){r.reason="FATBIN_WRAPPER_TRUNCATED";return r;}
   std::int32_t version=0;std::uintptr_t payload_address=0;
   std::memcpy(&version,p+4,4);std::memcpy(&payload_address,p+8,sizeof(payload_address));
   if(version==2){r.reason="FATBIN_LINK_WRAPPER_UNSUPPORTED";return r;}
   if(version!=1){r.reason="FATBIN_WRAPPER_VERSION_UNSUPPORTED";return r;}
   if(!payload_address){r.reason="FATBIN_WRAPPER_NULL_DATA";return r;}
   const void* payload=reinterpret_cast<const void*>(payload_address);
   const std::size_t mapped_extent=readable_mapping_bytes(payload,std::numeric_limits<std::size_t>::max());
   if(mapped_extent<16){r.reason="FATBIN_WRAPPER_DATA_UNREADABLE";return r;}
   const std::size_t payload_cap=std::min(maximum_bytes,mapped_extent);
   if(payload_cap<16){r.reason="FATBIN_WRAPPER_DATA_UNREADABLE";return r;}
   return raw_chain(payload,payload_cap,mapped_extent);
  }
 }
 const void*z=std::memchr(p,0,input_cap);if(z){std::size_t n=static_cast<const unsigned char*>(z)-p+1;std::string text(reinterpret_cast<const char*>(p),n-1);auto pos=text.find(".version");auto target=text.find(".target");auto addr=text.find(".address_size");if(pos!=std::string::npos&&pos<4096&&target!=std::string::npos&&addr!=std::string::npos){r={true,ImageKind::Ptx,n,"OK",data};return r;}}
 r.reason="UNRECOGNIZED_OR_UNBOUNDED_IMAGE";return r;
}

std::size_t readable_mapping_bytes(const void* a,std::size_t cap){
 auto x=reinterpret_cast<std::uintptr_t>(a);std::ifstream f("/proc/self/maps");std::string line;while(std::getline(f,line)){std::istringstream in(line);std::string range,perms;if(!(in>>range>>perms)||perms.empty()||perms[0]!='r')continue;auto dash=range.find('-');if(dash==std::string::npos)continue;try{auto lo=std::stoull(range.substr(0,dash),nullptr,16),hi=std::stoull(range.substr(dash+1),nullptr,16);if(x>=lo&&x<hi)return std::min<std::size_t>(cap,hi-x);}catch(...) {}}
 return 0;
}

std::uint64_t LineageStore::record_library(std::uintptr_t h,LineageEvidence e){if(!h)return 0;auto g=next_generation_++;libraries_[h]={g,std::move(e)};for(auto it=kernels_.begin();it!=kernels_.end();)if(it->second.library==h)it=kernels_.erase(it);else ++it;for(auto it=functions_.begin();it!=functions_.end();)if(it->second.library==h)it=functions_.erase(it);else ++it;return g;}
bool LineageStore::record_kernel(std::uintptr_t k,std::uintptr_t l,std::string n){auto it=libraries_.find(l);if(!k||it==libraries_.end())return false;kernels_[k]={l,it->second.generation,std::move(n),it->second.evidence};return true;}
void LineageStore::record_function(std::uintptr_t f,std::uintptr_t m,LineageEvidence e){if(f)functions_[f]={m,0,0,std::move(e)};}
bool LineageStore::record_function_from_kernel(std::uintptr_t f,std::uintptr_t k){auto it=kernels_.find(k);if(!f||it==kernels_.end())return false;functions_[f]={0,it->second.library,it->second.generation,it->second.evidence};return true;}
ResolveResult LineageStore::resolve_launch(std::uintptr_t h)const{auto f=functions_.find(h);auto k=kernels_.find(h);if(f!=functions_.end()&&k!=kernels_.end()&&!same_ids(f->second.evidence,k->second.evidence))return {ResolveState::Ambiguous,"FUNCTION_KERNEL_CONFLICT",{}};const LineageEvidence*e=nullptr;std::string path;if(f!=functions_.end()){if(f->second.library){auto l=libraries_.find(f->second.library);if(l==libraries_.end()||l->second.generation!=f->second.generation)return {ResolveState::Missing,"STALE_FUNCTION",{}};}e=&f->second.evidence;path="FUNCTION";}else if(k!=kernels_.end()){auto l=libraries_.find(k->second.library);if(l==libraries_.end()||l->second.generation!=k->second.generation)return {ResolveState::Missing,"STALE_KERNEL",{}};e=&k->second.evidence;path="DIRECT_CONTEXTLESS_KERNEL";}else return {};if(e->byte_ids.empty())return {ResolveState::TopologyOnly,path,*e};return {e->byte_ids.size()==1?ResolveState::Exact:ResolveState::Ambiguous,path,*e};}
void LineageStore::unload_library(std::uintptr_t l){libraries_.erase(l);for(auto it=kernels_.begin();it!=kernels_.end();)if(it->second.library==l)it=kernels_.erase(it);else ++it;for(auto it=functions_.begin();it!=functions_.end();)if(it->second.library==l)it=functions_.erase(it);else ++it;}
void LineageStore::unload_module(std::uintptr_t m){for(auto it=functions_.begin();it!=functions_.end();)if(it->second.module==m)it=functions_.erase(it);else ++it;}
bool LineageStore::has_library(std::uintptr_t x)const{return libraries_.count(x);}
bool LineageStore::has_kernel(std::uintptr_t x)const{return kernels_.count(x);}
}

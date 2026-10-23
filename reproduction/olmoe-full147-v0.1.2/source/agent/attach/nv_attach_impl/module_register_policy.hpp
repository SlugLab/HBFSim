#pragma once
// Host-only compile-option selection. No device/helper/ABI or cache changes.
#include "nvptx_opt_policy.hpp"
#include <map>
#include <cerrno>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <string_view>
namespace bpftime::attach::module_register_policy {
struct selection {std::string option;bool raw_override=false;};
struct config {
 bool enabled=false;
 std::map<std::string,std::string> by_raw;
 selection select(std::string_view raw,const std::string &global) const {
  auto found=by_raw.find(std::string(raw));
  return found==by_raw.end()?selection{global,false}:selection{found->second,true};
 }
};
inline config parse(const char *text) {
 config out;if(text==nullptr||*text=='\0')return out;out.enabled=true;
 std::string_view input(text);size_t begin=0;
 while(begin<input.size()) {
  const size_t comma=input.find(',',begin);const size_t end=comma==std::string_view::npos?input.size():comma;
  auto token=input.substr(begin,end-begin);
  if(token.size()<66||token[64]!='='||!nvptx_opt_policy::valid_raw_sha256(token.substr(0,64)))
   throw std::runtime_error("HBFSIM_NVPTX_MAX_REGISTERS_BY_RAW_SHA256 expects exact lowercase64hex=value");
  auto value=token.substr(65);std::string option;
  if(value!="none") {
   if(value.empty()||value.size()>3||value[0]<'1'||value[0]>'9')throw std::runtime_error("noncanonical module register limit");
   unsigned count=0;
   for(char c:value){if(c<'0'||c>'9')throw std::runtime_error("module register limit syntax");count=count*10+unsigned(c-'0');}
   if(count>255)throw std::runtime_error("module register limit outside1..255");
   option="--maxrregcount="+std::to_string(count);
  }
  if(!out.by_raw.emplace(std::string(token.substr(0,64)),option).second)throw std::runtime_error("duplicate module register policy digest");
  if(comma==std::string_view::npos)break;
  begin=comma+1;if(begin==input.size())throw std::runtime_error("trailing module register policy comma");
 }
 return out;
}
}

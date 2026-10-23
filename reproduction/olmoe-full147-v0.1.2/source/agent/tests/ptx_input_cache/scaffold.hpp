// Synthetic CPU CUDA handles/registration tables; actual complete adapter+bind bodies.
#include <json.hpp>
#include <openssl/sha.h>
#include <spdlog/spdlog.h>
#include <spdlog/fmt/fmt.h>
#include <fstream>
#include <iterator>
#include <map>
#include <set>
#include <dlfcn.h>
#include <functional>
#include <cstdint>
#include "native_binding_ptx_input_cache.hpp"
namespace fmt_lib=spdlog::fmt_lib;
using CUfunction=void*;
struct registered_symbol_identity_record{std::pair<int,void*> handle_key;std::uint64_t generation=0;std::string symbol,dso_path;};
struct fatbin_record;
struct fatbin_handle_identity{fatbin_record* record=nullptr;std::uint64_t generation=0;};
static std::mutex fatbin_identity_mutex,cuda_symbol_map_mutex;
static std::map<std::pair<int,void*>,registered_symbol_identity_record> registered_symbol_identity;
static std::map<std::pair<int,void*>,fatbin_handle_identity> fatbin_handle_to_record;
static std::map<std::string,CUfunction> patched_kernel_by_ptx_variant;
static std::map<CUfunction,CUfunction> patched_kernel_by_original_function;
static std::map<CUfunction,std::string> kernel_name_by_cufunction;
static bool bootstrap=true;
static bool is_late_bootstrap_done(){return bootstrap;}
static int gate_calls=0,gate_result=0,mutation=0;
static std::atomic<int> raw_hashes{0},sha_calls{0};
static bool throw_after_hash=false;
static std::function<void()> gate_reentrant;
static std::string raw_payload,container_payload;
extern "C" int hbfsim_bind_original_cuda_function(CUfunction,CUfunction){++gate_calls;if(gate_reentrant){auto invoke=std::move(gate_reentrant);invoke();}return gate_result;}
/*ACTUAL_SHA*/
static std::string sha256(const void* data,size_t n){
 ++sha_calls;
 const auto text=std::string(n?static_cast<const char*>(data):"",n);
 if(text==raw_payload)++raw_hashes;
 if(mutation && text==container_payload){if(mutation==13)++registered_symbol_identity.begin()->second.generation;else ++fatbin_handle_to_record.begin()->second.generation;mutation=0;}
 auto result=actual_original_sha256(data,n);
 if(throw_after_hash){throw_after_hash=false;throw std::runtime_error("SYNTHETIC_STOP_AFTER_REENTRANT_SHA");}
 return result;
}
/*ACTUAL_KEY*/
struct nv_attach_impl{int bind_ptx_variant(CUfunction,const char*,size_t,const char*);int auto_bind_registered_function(int,void*,CUfunction);};
struct original_nv_attach_impl{int bind_ptx_variant(CUfunction,const char*,size_t,const char*);int auto_bind_registered_function(int,void*,CUfunction);};
/*ACTUAL_FUNCTIONS*/

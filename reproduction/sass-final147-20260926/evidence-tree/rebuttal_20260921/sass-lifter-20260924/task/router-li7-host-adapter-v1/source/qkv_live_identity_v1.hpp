#pragma once

#include <cstdint>
#include <cstring>
#include <string>
#include <unordered_map>

// Fixed, host-only ABI. The caller initializes struct_size; every other field
// is filled only for an exact live identity. This does not expose CUPTI types.
struct QkvLiveIdentityV1 {
  std::uint32_t struct_size;
  std::uint32_t reserved;
  std::uint64_t context;
  std::uint64_t module;
  std::uint64_t association_token;
  char image_sha256[65];
};
static_assert(sizeof(void*) == 8 && sizeof(QkvLiveIdentityV1) == 104);
extern "C" int hbfsim_qkv_live_identity_v1(std::uint64_t function,
                                             std::uint64_t expected_context,
                                             QkvLiveIdentityV1* out);

// Called only while the provider's existing state_mu() is held. This records
// one actual module-function association, not a general symbol registry.
class QkvLiveAssociations {
 public:
  struct Record {
    std::uintptr_t module;
    std::uintptr_t context;
    std::uint64_t token;
    std::string name;
  };
  void record(std::uintptr_t function, std::uintptr_t module,
              std::uintptr_t context, const char* name) {
    records_.erase(function);
    if (!function || !module || !context || !name || !*name) return;
    records_.emplace(function, Record{module, context, next_token_++, name});
  }
  const Record* find(std::uintptr_t function) const {
    auto it = records_.find(function);
    return it == records_.end() ? nullptr : &it->second;
  }
  void erase_function(std::uintptr_t function) { records_.erase(function); }
  void erase_module(std::uintptr_t module) {
    for (auto it = records_.begin(); it != records_.end();) {
      if (it->second.module == module) it = records_.erase(it);
      else ++it;
    }
  }
  void erase_context(std::uintptr_t context) {
    for (auto it = records_.begin(); it != records_.end();) {
      if (it->second.context == context) it = records_.erase(it);
      else ++it;
    }
  }
 private:
  std::uint64_t next_token_ = 1;
  std::unordered_map<std::uintptr_t, Record> records_;
};

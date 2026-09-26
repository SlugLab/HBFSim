#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace hbfsim_provider {
enum class ImageKind { Unknown, Elf64, Fatbin, Ptx };
struct ImageCapture {
  bool ok = false;
  ImageKind kind = ImageKind::Unknown;
  std::size_t bytes = 0;
  std::string reason;
  const void* payload = nullptr;
};
ImageCapture inspect_image(const void* data, std::size_t readable_bytes,
                           std::size_t maximum_bytes,
                           bool follow_process_wrapper = true);
std::size_t readable_mapping_bytes(const void* address,
                                   std::size_t maximum_bytes);
const char* image_kind_name(ImageKind kind);

struct LineageEvidence {
  std::vector<std::string> byte_ids;
};
struct LibraryNode { std::uint64_t generation=0; LineageEvidence evidence; };
struct KernelNode { std::uintptr_t library=0; std::uint64_t generation=0;
                    std::string name; LineageEvidence evidence; };
struct FunctionNode { std::uintptr_t module=0; std::uintptr_t library=0; std::uint64_t generation=0; LineageEvidence evidence; };
enum class ResolveState { Missing, TopologyOnly, Exact, Ambiguous };
struct ResolveResult { ResolveState state=ResolveState::Missing;
                       std::string path; LineageEvidence evidence; };
class LineageStore {
 public:
  std::uint64_t record_library(std::uintptr_t library, LineageEvidence evidence);
  bool record_kernel(std::uintptr_t kernel, std::uintptr_t library,
                     std::string name);
  void record_function(std::uintptr_t function, std::uintptr_t module,
                       LineageEvidence evidence);
  bool record_function_from_kernel(std::uintptr_t function,
                                   std::uintptr_t kernel);
  ResolveResult resolve_launch(std::uintptr_t handle) const;
  void unload_library(std::uintptr_t library);
  void unload_module(std::uintptr_t module);
  bool has_library(std::uintptr_t library) const;
  bool has_kernel(std::uintptr_t kernel) const;
 private:
  std::uint64_t next_generation_=1;
  std::unordered_map<std::uintptr_t,LibraryNode> libraries_;
  std::unordered_map<std::uintptr_t,KernelNode> kernels_;
  std::unordered_map<std::uintptr_t,FunctionNode> functions_;
};
}

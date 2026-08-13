#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace hbfsim::ptx {

struct CoverageManifest {
    std::uint64_t rewritten_instructions{0};
    std::uint64_t unsupported_instructions{0};
    std::uint64_t excluded_functions{0};
    std::vector<std::string> unsupported_opcodes;
};

struct TransformRequest {
    std::string full_ptx;
    std::string to_patch_kernel;
    std::string global_ebpf_map_info_symbol{"map_info"};
    std::string ebpf_communication_data_symbol{"constData"};
    bool trusted_existing_helper{false};
    bool async_futures{true};
};

struct TransformResult {
    std::string output_ptx;
    CoverageManifest coverage;
    bool modified{false};
};

TransformResult transform_ptx(const TransformRequest& request);

}  // namespace hbfsim::ptx

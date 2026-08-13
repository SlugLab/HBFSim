#pragma once

#include "async_object_analysis.hpp"

#include <cstdint>
#include <string>
#include <string_view>

namespace hbfsim::ptx {

struct TmaTransformResult {
    std::string output_ptx;
    AsyncObjectPlan plan;
    std::uint64_t rewritten_instructions{0};
    bool modified{false};
    std::string rejection_reason;
};

[[nodiscard]] TmaTransformResult transform_tma(
    std::string_view ptx, std::string_view kernel,
    AsyncObjectLimits limits = {});

}  // namespace hbfsim::ptx

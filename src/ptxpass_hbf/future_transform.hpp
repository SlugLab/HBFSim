#pragma once

#include "ptx_analysis.hpp"

#include <cstdint>
#include <string>
#include <string_view>

namespace hbfsim::ptx {

struct FutureTransformResult {
    std::string output_ptx;
    FuturePlan plan;
    std::uint64_t rewritten_futures{0};
    bool modified{false};
    std::string rejection_reason;
};

[[nodiscard]] FutureTransformResult transform_load_futures(
    std::string_view ptx, std::string_view kernel);

[[nodiscard]] FutureTransformResult transform_futures(
    std::string_view ptx, std::string_view kernel);

}  // namespace hbfsim::ptx

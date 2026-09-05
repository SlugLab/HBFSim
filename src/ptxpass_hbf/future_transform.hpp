#pragma once
#include "ptx_analysis.hpp"
#include <cstdint>
#include <string>
#include <string_view>

namespace hbfsim::ptx {
struct FutureEmissionOptions {
    std::uint32_t maximum_thread_futures{16};
    std::uint32_t maximum_block_threads{1024};
};
struct FutureEmission {
    std::string ptx;
    FuturePlan plan;
    // One statically owned local record per producer; no speculative reuse.
    std::uint32_t allocated_thread_futures{0};
    // CTA numbers (including plan.maximum_live.cta_futures) assume this
    // declared upper bound. They are not observations or launch admission:
    // C6.2 must check actual geometry against both it and PTX constraints.
    std::uint32_t assumed_maximum_block_threads{0};
    std::uint32_t allocated_cta_futures{0};
};
// Private CPU/compile-only C6.1 entry point. This is not wired into the public
// transformer, loader identity, configuration enablement, or launch admission.
// C5 kUnitComplete remains false. This text alone is never a gold receipt.
[[nodiscard]] FutureEmission emit_timing_futures(std::string_view source,
    std::string_view kernel, const FutureEmissionOptions& options={});
}

#pragma once

#include "ptx_ir.hpp"

#include <cstdint>
#include <map>
#include <set>
#include <string>

namespace hbfsim::ptx {

struct AsyncObjectLimits {
    std::uint32_t maximum_live_objects{64};
};

struct AsyncObjectPlan {
    std::map<std::uint32_t, std::string> rejection_reasons;
    std::map<std::uint32_t, std::uint64_t> descriptor_generations;
    std::set<std::uint32_t> descriptor_instruction_ids;
    std::set<std::uint32_t> tma_instruction_ids;
    std::set<std::uint32_t> barrier_instruction_ids;
    std::set<std::uint32_t> bulk_group_instruction_ids;
    std::map<std::uint32_t, std::set<std::uint32_t>> multicast_targets;
    std::uint32_t maximum_live_objects{0};

    [[nodiscard]] bool exact_safe() const noexcept;
    [[nodiscard]] std::string reason(std::uint32_t instruction_id) const;
};

[[nodiscard]] AsyncObjectPlan analyze_async_objects(
    const Function& function, AsyncObjectLimits limits = {});

}  // namespace hbfsim::ptx

#pragma once

#include "ptx_ir.hpp"

#include <cstddef>
#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace hbfsim::ptx {

struct ControlFlowGraph {
    std::vector<std::vector<std::size_t>> successors;
    std::vector<std::vector<std::size_t>> predecessors;
    std::vector<bool> reachable;
    std::vector<std::set<std::size_t>> dominators;
};

struct FutureBudgets {
    std::uint32_t thread_futures{0};
    std::uint32_t warp_futures{0};
    std::uint32_t cta_futures{0};
    std::uint32_t cluster_futures{0};
};

struct FuturePlan {
    ControlFlowGraph cfg;
    std::map<std::uint32_t, std::set<std::uint32_t>> first_consumers;
    std::map<std::uint32_t, std::set<std::uint32_t>> drain_points;
    std::map<std::uint32_t, std::string> rejection_reasons;
    FutureBudgets maximum_live;

    [[nodiscard]] bool exact_safe() const noexcept;
    [[nodiscard]] std::string reason(std::uint32_t instruction_id) const;
};

[[nodiscard]] FuturePlan analyze_futures(const Function& function);

}  // namespace hbfsim::ptx

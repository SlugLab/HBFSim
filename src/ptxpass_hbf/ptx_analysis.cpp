#include "ptx_analysis.hpp"

#include <algorithm>
#include <deque>
#include <limits>
#include <map>
#include <set>
#include <string>
#include <utility>

namespace hbfsim::ptx {
namespace {

using DefinitionMap = std::map<std::string, std::set<std::uint32_t>>;

struct DataflowState {
    DefinitionMap definitions;
    std::set<std::uint32_t> pending_futures;

    bool operator==(const DataflowState&) const = default;
};

bool is_branch(const Instruction& instruction)
{
    return instruction.opcode == "bra" ||
           instruction.opcode.starts_with("bra.");
}

bool is_return(const Instruction& instruction)
{
    return instruction.opcode == "ret" || instruction.opcode == "exit" ||
           instruction.opcode.starts_with("ret.") ||
           instruction.opcode.starts_with("exit.");
}

bool is_future(const Instruction& instruction)
{
    return instruction.memory.has_value() &&
           instruction.memory->kind != MemoryKind::None;
}

bool is_ordering_drain(const Instruction& instruction)
{
    if (instruction.opcode == "membar" ||
        instruction.opcode.starts_with("membar.") ||
        instruction.opcode == "fence" ||
        instruction.opcode.starts_with("fence.")) {
        return true;
    }
    if (!instruction.memory.has_value()) {
        return false;
    }
    const auto& qualifiers = instruction.memory->qualifiers;
    return std::find(qualifiers.begin(), qualifiers.end(), "release") !=
               qualifiers.end() ||
           std::find(qualifiers.begin(), qualifiers.end(), "acq_rel") !=
               qualifiers.end() ||
           std::find(qualifiers.begin(), qualifiers.end(), "volatile") !=
               qualifiers.end();
}

void append_unique(std::vector<std::size_t>& output, std::size_t value)
{
    if (std::find(output.begin(), output.end(), value) == output.end()) {
        output.push_back(value);
    }
}

ControlFlowGraph build_cfg(const Function& function,
                           std::map<std::uint32_t, std::string>& rejections)
{
    ControlFlowGraph cfg;
    const auto count = function.blocks.size();
    cfg.successors.resize(count);
    cfg.predecessors.resize(count);
    cfg.reachable.assign(count, false);
    cfg.dominators.resize(count);

    std::map<std::string, std::size_t> labels;
    for (std::size_t block = 0; block < count; ++block) {
        labels.emplace(function.blocks[block].label, block);
    }
    for (std::size_t block = 0; block < count; ++block) {
        const auto& basic_block = function.blocks[block];
        if (basic_block.instructions.empty()) {
            if (block + 1 < count) {
                append_unique(cfg.successors[block], block + 1);
            }
            continue;
        }
        const auto& terminator =
            function.instructions.at(basic_block.instructions.back());
        if (is_branch(terminator)) {
            if (terminator.branch_targets.size() != 1) {
                rejections.emplace(terminator.instruction_id,
                                   "unsupported_branch_target");
            } else {
                const auto target = labels.find(terminator.branch_targets[0]);
                if (target == labels.end()) {
                    rejections.emplace(terminator.instruction_id,
                                       "unknown_branch_target");
                } else {
                    append_unique(cfg.successors[block], target->second);
                }
            }
            if (!terminator.predicate.empty() && block + 1 < count) {
                append_unique(cfg.successors[block], block + 1);
            }
        } else if (!is_return(terminator) && block + 1 < count) {
            append_unique(cfg.successors[block], block + 1);
        }
    }
    for (std::size_t block = 0; block < count; ++block) {
        for (const auto successor : cfg.successors[block]) {
            append_unique(cfg.predecessors[successor], block);
        }
    }

    if (count == 0) {
        return cfg;
    }
    std::deque<std::size_t> queue{0};
    cfg.reachable[0] = true;
    while (!queue.empty()) {
        const auto block = queue.front();
        queue.pop_front();
        for (const auto successor : cfg.successors[block]) {
            if (!cfg.reachable[successor]) {
                cfg.reachable[successor] = true;
                queue.push_back(successor);
            }
        }
    }

    std::set<std::size_t> all_reachable;
    for (std::size_t block = 0; block < count; ++block) {
        if (cfg.reachable[block]) {
            all_reachable.insert(block);
        }
    }
    for (std::size_t block = 0; block < count; ++block) {
        if (!cfg.reachable[block]) {
            continue;
        }
        cfg.dominators[block] = block == 0 ? std::set<std::size_t>{0}
                                           : all_reachable;
    }
    bool changed = true;
    while (changed) {
        changed = false;
        for (std::size_t block = 1; block < count; ++block) {
            if (!cfg.reachable[block]) {
                continue;
            }
            std::set<std::size_t> next = all_reachable;
            bool saw_predecessor = false;
            for (const auto predecessor : cfg.predecessors[block]) {
                if (!cfg.reachable[predecessor]) {
                    continue;
                }
                if (!saw_predecessor) {
                    next = cfg.dominators[predecessor];
                    saw_predecessor = true;
                } else {
                    std::set<std::size_t> intersection;
                    std::set_intersection(
                        next.begin(), next.end(),
                        cfg.dominators[predecessor].begin(),
                        cfg.dominators[predecessor].end(),
                        std::inserter(intersection, intersection.begin()));
                    next = std::move(intersection);
                }
            }
            next.insert(block);
            if (next != cfg.dominators[block]) {
                cfg.dominators[block] = std::move(next);
                changed = true;
            }
        }
    }
    return cfg;
}

DataflowState join_predecessors(const ControlFlowGraph& cfg,
                                const std::vector<DataflowState>& outputs,
                                std::size_t block)
{
    DataflowState result;
    if (block == 0) {
        return result;
    }
    for (const auto predecessor : cfg.predecessors[block]) {
        if (!cfg.reachable[predecessor]) {
            continue;
        }
        result.pending_futures.insert(
            outputs[predecessor].pending_futures.begin(),
            outputs[predecessor].pending_futures.end());
        for (const auto& [reg, definitions] :
             outputs[predecessor].definitions) {
            auto& destination = result.definitions[reg];
            destination.insert(definitions.begin(), definitions.end());
        }
    }
    return result;
}

std::set<std::uint32_t> pending_definitions(
    const DataflowState& state, const std::set<std::uint32_t>& definitions,
    const std::set<std::uint32_t>& future_ids)
{
    std::set<std::uint32_t> result;
    for (const auto definition : definitions) {
        if (future_ids.contains(definition) &&
            state.pending_futures.contains(definition)) {
            result.insert(definition);
        }
    }
    return result;
}

DataflowState transfer_block(
    const Function& function, const BasicBlock& block, DataflowState state,
    const std::set<std::uint32_t>& future_ids, FuturePlan* observations)
{
    const auto observe_maximum = [&] {
        if (observations == nullptr) {
            return;
        }
        const auto live = static_cast<std::uint32_t>(
            std::min<std::size_t>(state.pending_futures.size(),
                                  std::numeric_limits<std::uint32_t>::max()));
        observations->maximum_live.thread_futures =
            std::max(observations->maximum_live.thread_futures, live);
    };

    for (const auto instruction_index : block.instructions) {
        const auto& instruction = function.instructions.at(instruction_index);

        if (is_ordering_drain(instruction) || is_return(instruction)) {
            if (observations != nullptr) {
                for (const auto future : state.pending_futures) {
                    observations->drain_points[future].insert(
                        instruction.instruction_id);
                }
            }
            state.pending_futures.clear();
        }

        std::set<std::uint32_t> consumed;
        for (const auto& reg : instruction.uses) {
            const auto definitions = state.definitions.find(reg);
            if (definitions == state.definitions.end()) {
                continue;
            }
            const auto pending = pending_definitions(
                state, definitions->second, future_ids);
            if (pending.empty()) {
                continue;
            }
            if (observations != nullptr && pending.size() != 1) {
                observations->rejection_reasons.emplace(
                    instruction.instruction_id,
                    "ambiguous_future_definition");
            }
            consumed.insert(pending.begin(), pending.end());
        }
        if (observations != nullptr) {
            for (const auto future : consumed) {
                observations->first_consumers[future].insert(
                    instruction.instruction_id);
            }
        }
        for (const auto future : consumed) {
            state.pending_futures.erase(future);
        }

        const bool future = is_future(instruction);
        for (const auto& reg : instruction.defs) {
            auto& definitions = state.definitions[reg];
            if (instruction.predicate.empty()) {
                definitions.clear();
            }
            definitions.insert(instruction.instruction_id);
        }
        if (future) {
            state.pending_futures.insert(instruction.instruction_id);
            observe_maximum();
        }
    }
    observe_maximum();
    return state;
}

}  // namespace

bool FuturePlan::exact_safe() const noexcept
{
    return rejection_reasons.empty();
}

std::string FuturePlan::reason(std::uint32_t instruction_id) const
{
    const auto found = rejection_reasons.find(instruction_id);
    return found == rejection_reasons.end() ? std::string{} : found->second;
}

FuturePlan analyze_futures(const Function& function)
{
    FuturePlan result;
    result.cfg = build_cfg(function, result.rejection_reasons);
    const auto block_count = function.blocks.size();
    std::vector<DataflowState> inputs(block_count);
    std::vector<DataflowState> outputs(block_count);
    std::set<std::uint32_t> future_ids;
    for (const auto& instruction : function.instructions) {
        if (is_future(instruction)) {
            future_ids.insert(instruction.instruction_id);
        }
    }

    bool changed = true;
    while (changed) {
        changed = false;
        for (std::size_t block = 0; block < block_count; ++block) {
            if (!result.cfg.reachable[block]) {
                continue;
            }
            auto input = join_predecessors(result.cfg, outputs, block);
            auto output = transfer_block(function, function.blocks[block],
                                         input, future_ids, nullptr);
            if (input != inputs[block] || output != outputs[block]) {
                inputs[block] = std::move(input);
                outputs[block] = std::move(output);
                changed = true;
            }
        }
    }

    for (std::size_t block = 0; block < block_count; ++block) {
        if (!result.cfg.reachable[block]) {
            continue;
        }
        (void)transfer_block(function, function.blocks[block], inputs[block],
                             future_ids, &result);
    }
    result.maximum_live.warp_futures =
        result.maximum_live.thread_futures * 32;
    result.maximum_live.cta_futures =
        result.maximum_live.warp_futures * 4;
    result.maximum_live.cluster_futures =
        result.maximum_live.cta_futures * 8;
    return result;
}

}  // namespace hbfsim::ptx

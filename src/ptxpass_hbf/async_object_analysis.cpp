#include "async_object_analysis.hpp"

#include <algorithm>
#include <bit>
#include <charconv>
#include <deque>
#include <limits>
#include <map>
#include <optional>
#include <regex>
#include <set>

namespace hbfsim::ptx {
namespace {

struct DescriptorState {
    std::uint64_t object{0};
    std::uint64_t generation{0};
    bool fenced{false};
};

struct BarrierState {
    std::uint64_t phase{0};
    std::uint32_t pending{0};
    bool initialized{false};
};

std::vector<std::string> registers(std::string_view input)
{
    static const std::regex expression(R"(%[A-Za-z][A-Za-z0-9_$]*)");
    const std::string text(input);
    std::vector<std::string> result;
    for (std::sregex_iterator it(text.begin(), text.end(), expression), end;
         it != end; ++it) {
        if (std::find(result.begin(), result.end(), it->str()) == result.end()) {
            result.push_back(it->str());
        }
    }
    return result;
}

std::optional<std::uint32_t> immediate(std::string_view value)
{
    int base = 10;
    if (value.starts_with("0x") || value.starts_with("0X")) {
        value.remove_prefix(2);
        base = 16;
    }
    std::uint64_t result = 0;
    const auto parsed =
        std::from_chars(value.data(), value.data() + value.size(), result, base);
    if (parsed.ec != std::errc{} || parsed.ptr != value.data() + value.size() ||
        result > std::numeric_limits<std::uint32_t>::max()) {
        return std::nullopt;
    }
    return static_cast<std::uint32_t>(result);
}

void reject(AsyncObjectPlan& plan, std::uint32_t id, std::string reason)
{
    plan.rejection_reasons.try_emplace(id, std::move(reason));
}

}  // namespace

bool AsyncObjectPlan::exact_safe() const noexcept
{
    return rejection_reasons.empty();
}

std::string AsyncObjectPlan::reason(std::uint32_t instruction_id) const
{
    const auto found = rejection_reasons.find(instruction_id);
    return found == rejection_reasons.end() ? std::string{} : found->second;
}

AsyncObjectPlan analyze_async_objects(const Function& function,
                                      AsyncObjectLimits limits)
{
    AsyncObjectPlan plan;
    std::map<std::string, DescriptorState> descriptors;
    std::map<std::uint64_t, std::uint64_t> current_generation;
    std::map<std::string, BarrierState> barriers;
    std::uint64_t next_descriptor_object = 1;
    std::uint32_t uncommitted = 0;
    std::deque<std::uint32_t> committed_groups;

    const auto live_objects = [&] {
        std::uint64_t live = uncommitted;
        for (const auto group : committed_groups) live += group;
        for (const auto& [name, barrier] : barriers) {
            (void)name;
            live += barrier.pending;
        }
        return static_cast<std::uint32_t>(std::min<std::uint64_t>(
            live, std::numeric_limits<std::uint32_t>::max()));
    };
    const auto observe_budget = [&](std::uint32_t instruction_id) {
        plan.maximum_live_objects =
            std::max(plan.maximum_live_objects, live_objects());
        if (plan.maximum_live_objects > limits.maximum_live_objects) {
            reject(plan, instruction_id, "async_object_budget_exceeded");
        }
    };

    for (const auto& instruction : function.instructions) {
        // A descriptor pointer loaded from a kernel parameter has host-side
        // provenance, but is not safe to consume until an acquire fence.
        if (instruction.opcode.starts_with("ld.param.") &&
            !instruction.operands.empty()) {
            const auto destination = registers(instruction.operands[0]);
            if (destination.size() == 1) {
                const auto object = next_descriptor_object++;
                descriptors[destination[0]] = {object, 1, false};
                current_generation[object] = 1;
            }
        }
        if (instruction.opcode.starts_with("mov.") &&
            instruction.operands.size() == 2) {
            const auto destination = registers(instruction.operands[0]);
            const auto source = registers(instruction.operands[1]);
            if (destination.size() == 1 && source.size() == 1) {
                const auto found = descriptors.find(source[0]);
                if (found != descriptors.end()) {
                    descriptors[destination[0]] = found->second;
                }
            }
        }

        if (!instruction.async.has_value()) continue;
        if (const auto* tensor_map =
                std::get_if<TensorMapInstruction>(&*instruction.async)) {
            plan.descriptor_instruction_ids.insert(instruction.instruction_id);
            if (tensor_map->op == TensorMapOp::Replace) {
                const auto found = descriptors.find(tensor_map->address);
                if (found == descriptors.end()) {
                    reject(plan, instruction.instruction_id,
                           "unknown_tensormap");
                } else {
                    auto& generation = current_generation[found->second.object];
                    if (generation == std::numeric_limits<std::uint64_t>::max()) {
                        reject(plan, instruction.instruction_id,
                               "stale_tensormap_generation");
                    } else {
                        ++generation;
                        found->second.generation = generation;
                        found->second.fenced = false;
                        plan.descriptor_generations[instruction.instruction_id] =
                            generation;
                    }
                }
            } else if (tensor_map->op == TensorMapOp::FenceAcquire) {
                const auto found = descriptors.find(tensor_map->address);
                if (found == descriptors.end()) {
                    reject(plan, instruction.instruction_id,
                           "unknown_tensormap");
                } else {
                    found->second.generation =
                        current_generation[found->second.object];
                    found->second.fenced = true;
                }
            } else if (tensor_map->op == TensorMapOp::FenceRelease) {
                for (auto& [name, descriptor] : descriptors) {
                    (void)name;
                    if (descriptor.generation ==
                        current_generation[descriptor.object]) {
                        descriptor.fenced = true;
                    }
                }
            }
            continue;
        }
        if (const auto* barrier =
                std::get_if<BarrierInstruction>(&*instruction.async)) {
            plan.barrier_instruction_ids.insert(instruction.instruction_id);
            auto& state = barriers[barrier->address];
            switch (barrier->op) {
            case BarrierOp::Init:
                if (state.pending != 0) {
                    reject(plan, instruction.instruction_id,
                           "ambiguous_mbarrier_phase");
                }
                state = {.phase = state.phase + 1,
                         .pending = 0,
                         .initialized = true};
                break;
            case BarrierOp::ArriveExpectTx:
            case BarrierOp::Arrive:
            case BarrierOp::CompleteTx:
                if (!state.initialized) {
                    reject(plan, instruction.instruction_id,
                           "ambiguous_mbarrier_phase");
                }
                break;
            case BarrierOp::TestWait:
            case BarrierOp::TryWait:
                if (!state.initialized) {
                    reject(plan, instruction.instruction_id,
                           "ambiguous_mbarrier_phase");
                }
                state.pending = 0;
                break;
            case BarrierOp::Invalidate:
                if (state.pending != 0) {
                    reject(plan, instruction.instruction_id,
                           "ambiguous_mbarrier_phase");
                }
                state.initialized = false;
                break;
            }
            observe_budget(instruction.instruction_id);
            continue;
        }
        if (const auto* group =
                std::get_if<BulkGroupInstruction>(&*instruction.async)) {
            plan.bulk_group_instruction_ids.insert(instruction.instruction_id);
            if (group->op == BulkGroupOp::Commit) {
                committed_groups.push_back(uncommitted);
                uncommitted = 0;
            } else if (group->op == BulkGroupOp::Wait) {
                while (committed_groups.size() > group->pending_limit) {
                    committed_groups.pop_front();
                }
            } else {
                // A .read wait releases source staging but not destination
                // completion, so committed groups remain live.
            }
            observe_budget(instruction.instruction_id);
            continue;
        }

        const auto& tma = std::get<TmaInstruction>(*instruction.async);
        plan.tma_instruction_ids.insert(instruction.instruction_id);
        const auto descriptor = descriptors.find(tma.descriptor);
        if (descriptor == descriptors.end()) {
            reject(plan, instruction.instruction_id, "unknown_tensormap");
        } else if (descriptor->second.generation !=
                   current_generation[descriptor->second.object]) {
            reject(plan, instruction.instruction_id,
                   "stale_tensormap_generation");
        } else if (!descriptor->second.fenced) {
            reject(plan, instruction.instruction_id,
                   "tensormap_fence_missing");
        } else {
            plan.descriptor_generations[instruction.instruction_id] =
                descriptor->second.generation;
        }
        if (tma.completion == CompletionKind::Mbarrier) {
            auto found = barriers.find(tma.barrier);
            if (found == barriers.end() || !found->second.initialized) {
                reject(plan, instruction.instruction_id,
                       "ambiguous_mbarrier_phase");
            } else {
                ++found->second.pending;
            }
        } else if (tma.completion == CompletionKind::BulkGroup) {
            ++uncommitted;
        }
        if (tma.multicast) {
            const auto mask = immediate(tma.multicast_mask);
            if (!mask || *mask == 0 || (*mask & 0xffff0000U) != 0) {
                reject(plan, instruction.instruction_id,
                       "ambiguous_mbarrier_phase");
            } else {
                auto& targets = plan.multicast_targets[instruction.instruction_id];
                for (std::uint32_t target = 0; target < 16; ++target) {
                    if ((*mask & (1U << target)) != 0) targets.insert(target);
                }
            }
        }
        observe_budget(instruction.instruction_id);
    }

    const auto terminal_id = function.instructions.empty()
                                 ? 0
                                 : function.instructions.back().instruction_id;
    if (uncommitted != 0 || !committed_groups.empty()) {
        reject(plan, terminal_id, "bulk_group_unbalanced");
    }
    for (const auto& [address, barrier] : barriers) {
        (void)address;
        if (barrier.pending != 0) {
            reject(plan, terminal_id, "ambiguous_mbarrier_phase");
            break;
        }
    }
    return plan;
}

}  // namespace hbfsim::ptx

#include "tma_transform.hpp"

#include <algorithm>
#include <map>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace hbfsim::ptx {
namespace {

std::vector<std::string> lines(std::string_view ptx)
{
    std::vector<std::string> result;
    std::istringstream input(std::string{ptx});
    std::string line;
    while (std::getline(input, line)) result.push_back(std::move(line));
    return result;
}

std::size_t body_line(const std::vector<std::string>& source,
                      std::string_view kernel)
{
    const std::regex definition(
        R"(\.(?:visible\s+)?(?:entry|func)\s+)" + std::string{kernel} +
        R"((?:\s|\())");
    bool found = false;
    for (std::size_t index = 0; index < source.size(); ++index) {
        if (!found && std::regex_search(source[index], definition)) found = true;
        if (found && source[index].find('{') != std::string::npos) return index;
    }
    throw std::runtime_error("TMA transform could not locate kernel body");
}

std::size_t instruction_end(const std::vector<std::string>& source,
                            std::uint32_t line)
{
    auto index = static_cast<std::size_t>(line - 1);
    while (index < source.size() && source[index].find(';') == std::string::npos) {
        ++index;
    }
    if (index == source.size()) {
        throw std::runtime_error("unterminated TMA instruction");
    }
    return index + 1;
}

std::string token(std::uint32_t id)
{
    return "%hbfsim_tma_" + std::to_string(id) + "_token";
}

std::string emit_issue(const Instruction& instruction,
                       const TmaInstruction& tma)
{
    const auto id = instruction.instruction_id;
    const auto direction =
        tma.direction == TmaDirection::SharedToGlobal ? 1U : 0U;
    const auto barrier = tma.barrier.empty() ? "0" : tma.barrier;
    const auto mask = tma.multicast ? tma.multicast_mask : "0";
    std::ostringstream output;
    output << "    // HBFSim TMA issue " << id << "\n"
           << "    {\n"
           << "    .param .b64 %hbfsim_tma_" << id << "_descriptor;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_instruction;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_direction;\n"
           << "    .param .b64 %hbfsim_tma_" << id << "_barrier;\n"
           << "    .param .b32 %hbfsim_tma_" << id << "_mask;\n"
           << "    .param .b64 %hbfsim_tma_" << id << "_return;\n"
           << "    st.param.b64 [%hbfsim_tma_" << id << "_descriptor], "
           << tma.descriptor << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_instruction], "
           << id << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_direction], "
           << direction << ";\n"
           << "    st.param.b64 [%hbfsim_tma_" << id << "_barrier], "
           << barrier << ";\n"
           << "    st.param.b32 [%hbfsim_tma_" << id << "_mask], "
           << mask << ";\n"
           << "    call.uni (%hbfsim_tma_" << id
           << "_return), __hbfsim_tma_issue, (%hbfsim_tma_" << id
           << "_descriptor, %hbfsim_tma_" << id
           << "_instruction, %hbfsim_tma_" << id
           << "_direction, %hbfsim_tma_" << id
           << "_barrier, %hbfsim_tma_" << id << "_mask);\n"
           << "    ld.param.b64 " << token(id) << ", [%hbfsim_tma_" << id
           << "_return];\n"
           << "    }\n";
    output << "    setp.ne.u64 %hbfsim_tma_" << id << "_valid, "
           << token(id) << ", 0;\n"
           << "    @!%hbfsim_tma_" << id << "_valid trap;\n";
    return output.str();
}

std::string emit_barrier_poll(const Instruction& wait,
                              std::uint32_t issue_id)
{
    const auto predicate = wait.operands.empty() ? std::string{}
                                                  : wait.operands.front();
    if (!predicate.starts_with('%')) {
        throw std::runtime_error("TMA barrier wait predicate is not a register");
    }
    const auto id = wait.instruction_id;
    std::ostringstream output;
    output << "    // HBFSim conjunctive TMA barrier poll " << issue_id << "\n"
           << "    {\n"
           << "    .param .b64 %hbfsim_tma_poll_" << id << "_token;\n"
           << "    .param .b32 %hbfsim_tma_poll_" << id << "_return;\n"
           << "    st.param.b64 [%hbfsim_tma_poll_" << id << "_token], "
           << token(issue_id) << ";\n"
           << "    call.uni (%hbfsim_tma_poll_" << id
           << "_return), __hbfsim_tma_barrier_poll, "
           << "(%hbfsim_tma_poll_" << id << "_token);\n"
           << "    ld.param.b32 %hbfsim_tma_poll_" << id
           << "_ready, [%hbfsim_tma_poll_" << id << "_return];\n"
           << "    setp.ne.u32 %hbfsim_tma_poll_" << id
           << "_predicate, %hbfsim_tma_poll_" << id << "_ready, 0;\n"
           << "    and.pred " << predicate << ", " << predicate
           << ", %hbfsim_tma_poll_" << id << "_predicate;\n"
           << "    }\n";
    return output.str();
}

std::string emit_group_wait(const Instruction& wait,
                            const std::vector<std::uint32_t>& issues,
                            bool read_only)
{
    std::ostringstream output;
    for (const auto issue : issues) {
        output << "    // HBFSim TMA bulk-group "
               << (read_only ? "read " : "full ") << "wait " << issue
               << "\n    {\n"
               << "    .param .b64 %hbfsim_tma_group_" << wait.instruction_id
               << "_" << issue << "_token;\n"
               << "    .param .b32 %hbfsim_tma_group_" << wait.instruction_id
               << "_" << issue << "_read;\n"
               << "    st.param.b64 [%hbfsim_tma_group_"
               << wait.instruction_id << "_" << issue << "_token], "
               << token(issue) << ";\n"
               << "    st.param.b32 [%hbfsim_tma_group_"
               << wait.instruction_id << "_" << issue << "_read], "
               << (read_only ? 1 : 0) << ";\n"
               << "    call.uni __hbfsim_tma_wait_group, "
               << "(%hbfsim_tma_group_" << wait.instruction_id << "_"
               << issue << "_token, %hbfsim_tma_group_"
               << wait.instruction_id << "_" << issue << "_read);\n"
               << "    }\n";
    }
    return output.str();
}

}  // namespace

TmaTransformResult transform_tma(std::string_view ptx,
                                 std::string_view kernel,
                                 AsyncObjectLimits limits)
{
    TmaTransformResult result;
    const auto module = parse_module(ptx);
    const auto& function = module.function(kernel);
    result.plan = analyze_async_objects(function, limits);
    if (!result.plan.exact_safe()) {
        result.rejection_reason = result.plan.rejection_reasons.begin()->second;
        return result;
    }
    if (result.plan.tma_instruction_ids.empty()) {
        result.output_ptx = std::string{ptx};
        return result;
    }
    const auto source = lines(ptx);
    std::map<std::size_t, std::string> before;
    std::map<std::size_t, std::string> after;
    std::ostringstream declarations;
    declarations << "    // HBFSim TMA async state for " << kernel << "\n";

    std::map<std::string, std::uint32_t> barrier_issue;
    std::vector<std::uint32_t> store_issues;
    for (const auto& instruction : function.instructions) {
        if (!instruction.async) continue;
        if (const auto* tma = std::get_if<TmaInstruction>(&*instruction.async)) {
            declarations << "    .reg .b64 " << token(instruction.instruction_id)
                         << ";\n    .reg .pred %hbfsim_tma_"
                         << instruction.instruction_id << "_valid;\n";
            before[instruction.location.line] += emit_issue(instruction, *tma);
            if (tma->completion == CompletionKind::Mbarrier) {
                barrier_issue[tma->barrier] = instruction.instruction_id;
            } else if (tma->completion == CompletionKind::BulkGroup) {
                store_issues.push_back(instruction.instruction_id);
            }
            ++result.rewritten_instructions;
        } else if (const auto* barrier =
                       std::get_if<BarrierInstruction>(&*instruction.async)) {
            if (barrier->op != BarrierOp::TestWait &&
                barrier->op != BarrierOp::TryWait) {
                continue;
            }
            const auto issue = barrier_issue.find(barrier->address);
            if (issue == barrier_issue.end()) {
                result.rejection_reason = "ambiguous_mbarrier_phase";
                return result;
            }
            declarations << "    .reg .b32 %hbfsim_tma_poll_"
                         << instruction.instruction_id << "_ready;\n"
                         << "    .reg .pred %hbfsim_tma_poll_"
                         << instruction.instruction_id << "_predicate;\n";
            after[instruction_end(source, instruction.location.line)] +=
                emit_barrier_poll(instruction, issue->second);
            ++result.rewritten_instructions;
        } else if (const auto* group =
                       std::get_if<BulkGroupInstruction>(&*instruction.async)) {
            if (group->op == BulkGroupOp::Commit) {
                after[instruction_end(source, instruction.location.line)] +=
                    "    call.uni __hbfsim_tma_commit_group, ();\n";
            } else {
                after[instruction_end(source, instruction.location.line)] +=
                    emit_group_wait(instruction, store_issues,
                                    group->op == BulkGroupOp::WaitRead);
                if (group->op == BulkGroupOp::Wait &&
                    group->pending_limit == 0) {
                    store_issues.clear();
                }
            }
            ++result.rewritten_instructions;
        }
    }
    before[body_line(source, kernel) + 2] = declarations.str() +
        before[body_line(source, kernel) + 2];

    std::ostringstream output;
    for (std::size_t index = 1; index <= source.size(); ++index) {
        if (before.contains(index)) output << before[index];
        output << source[index - 1] << '\n';
        if (after.contains(index)) output << after[index];
    }
    result.output_ptx = output.str();
    result.modified = true;
    return result;
}

}  // namespace hbfsim::ptx

#include "future_transform.hpp"

#include "ptx_ir.hpp"

#include <algorithm>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <map>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace hbfsim::ptx {
namespace {

std::vector<std::string> source_lines(std::string_view ptx)
{
    std::vector<std::string> lines;
    std::istringstream input(std::string{ptx});
    std::string line;
    while (std::getline(input, line)) {
        lines.push_back(std::move(line));
    }
    return lines;
}

std::string sanitized(std::string_view value)
{
    std::string result;
    result.reserve(value.size());
    for (const auto character : value) {
        result.push_back(std::isalnum(static_cast<unsigned char>(character))
                             ? character
                             : '_');
    }
    return result;
}

std::string reg(std::uint32_t id, std::string_view field)
{
    return "%hbfsim_f" + std::to_string(id) + "_" + std::string{field};
}

std::string label(std::uint32_t id, std::string_view suffix,
                  std::uint32_t occurrence = 0)
{
    auto result = "$L__hbfsim_f" + std::to_string(id) + "_" +
                  std::string{suffix};
    if (occurrence != 0) {
        result += "_" + std::to_string(occurrence);
    }
    return result;
}

std::string inverse_predicate(std::string_view predicate)
{
    if (predicate.empty() || predicate.front() != '@') {
        return {};
    }
    return predicate.starts_with("@!")
               ? "@" + std::string{predicate.substr(2)}
               : "@!" + std::string{predicate.substr(1)};
}

std::string instruction_text(const Instruction& instruction,
                             bool resolved_address)
{
    auto operands = instruction.operands;
    if (resolved_address && instruction.memory.has_value()) {
        const auto address_index =
            instruction.memory->kind == MemoryKind::Store ? 0U : 1U;
        operands.at(address_index) =
            "[" + reg(instruction.instruction_id, "resolved") + "]";
    }
    std::ostringstream output;
    output << "    " << instruction.opcode;
    if (!operands.empty()) {
        output << ' ';
        for (std::size_t index = 0; index < operands.size(); ++index) {
            if (index != 0) {
                output << ", ";
            }
            output << operands[index];
        }
    }
    output << ";\n";
    return output.str();
}

bool immediate_load_wait(const Instruction& instruction)
{
    if (!instruction.memory.has_value() ||
        instruction.memory->kind != MemoryKind::Load) {
        return false;
    }
    const auto& qualifiers = instruction.memory->qualifiers;
    return std::find(qualifiers.begin(), qualifiers.end(), "acquire") !=
               qualifiers.end() ||
           std::find(qualifiers.begin(), qualifiers.end(), "acq_rel") !=
               qualifiers.end() ||
           std::find(qualifiers.begin(), qualifiers.end(), "volatile") !=
               qualifiers.end();
}

std::string future_declarations(const Instruction& instruction)
{
    const auto id = instruction.instruction_id;
    std::ostringstream output;
    output << "    // HBFSim future registers " << id << "\n"
           << "    .reg .b64 " << reg(id, "ticket") << ";\n"
           << "    .reg .b64 " << reg(id, "original") << ";\n"
           << "    .reg .b64 " << reg(id, "resolved") << ";\n"
           << "    .reg .b64 " << reg(id, "ready") << ";\n"
           << "    .reg .b32 " << reg(id, "bytes") << ";\n"
           << "    .reg .b32 " << reg(id, "instruction") << ";\n"
           << "    .reg .b32 " << reg(id, "channel") << ";\n"
           << "    .reg .b32 " << reg(id, "flags") << ";\n"
           << "    .reg .b32 " << reg(id, "state") << ";\n"
           << "    .reg .b32 " << reg(id, "status") << ";\n"
           << "    .reg .b32 " << reg(id, "temporary") << ";\n"
           << "    .reg .pred " << reg(id, "capacity") << ";\n"
           << "    .reg .pred " << reg(id, "terminal") << ";\n"
           << "    .reg .pred " << reg(id, "skip") << ";\n"
           << "    .reg .pred " << reg(id, "native") << ";\n"
           << "    .reg .pred " << reg(id, "consumed") << ";\n"
           << "    .reg .pred " << reg(id, "deferred") << ";\n"
           << "    .reg .pred " << reg(id, "fault") << ";\n";
    return output.str();
}

std::string load_future_return(std::uint32_t id, std::string_view parameter)
{
    std::ostringstream output;
    output << "    ld.param.b64 " << reg(id, "ticket") << ", ["
           << parameter << "+0];\n"
           << "    ld.param.b64 " << reg(id, "original") << ", ["
           << parameter << "+8];\n"
           << "    ld.param.b64 " << reg(id, "resolved") << ", ["
           << parameter << "+16];\n"
           << "    ld.param.b64 " << reg(id, "ready") << ", ["
           << parameter << "+24];\n"
           << "    ld.param.b32 " << reg(id, "bytes") << ", ["
           << parameter << "+32];\n"
           << "    ld.param.b32 " << reg(id, "instruction") << ", ["
           << parameter << "+36];\n"
           << "    ld.param.b32 " << reg(id, "channel") << ", ["
           << parameter << "+40];\n"
           << "    ld.param.b32 " << reg(id, "flags") << ", ["
           << parameter << "+44];\n"
           << "    ld.param.b32 " << reg(id, "state") << ", ["
           << parameter << "+48];\n"
           << "    ld.param.b32 " << reg(id, "status") << ", ["
           << parameter << "+52];\n";
    return output.str();
}

std::string store_future_argument(std::uint32_t id,
                                  std::string_view parameter)
{
    std::ostringstream output;
    output << "    st.param.b64 [" << parameter << "+0], "
           << reg(id, "ticket") << ";\n"
           << "    st.param.b64 [" << parameter << "+8], "
           << reg(id, "original") << ";\n"
           << "    st.param.b64 [" << parameter << "+16], "
           << reg(id, "resolved") << ";\n"
           << "    st.param.b64 [" << parameter << "+24], "
           << reg(id, "ready") << ";\n"
           << "    st.param.b32 [" << parameter << "+32], "
           << reg(id, "bytes") << ";\n"
           << "    st.param.b32 [" << parameter << "+36], "
           << reg(id, "instruction") << ";\n"
           << "    st.param.b32 [" << parameter << "+40], "
           << reg(id, "channel") << ";\n"
           << "    st.param.b32 [" << parameter << "+44], "
           << reg(id, "flags") << ";\n"
           << "    st.param.b32 [" << parameter << "+48], "
           << reg(id, "state") << ";\n"
           << "    st.param.b32 [" << parameter << "+52], "
           << reg(id, "status") << ";\n";
    return output.str();
}

std::string emit_issue(const Instruction& instruction)
{
    const auto id = instruction.instruction_id;
    const auto prefix = "%hbfsim_issue_" + std::to_string(id);
    const auto false_label = label(id, "predicate_false");
    const auto skip_native = label(id, "skip_native");
    std::ostringstream output;
    output << "    // HBFSim future issue " << id << "\n"
           << "    mov.u32 " << reg(id, "state") << ", 5;\n";
    if (!instruction.predicate.empty()) {
        output << "    " << inverse_predicate(instruction.predicate)
               << " bra " << false_label << ";\n";
    }
    output << "    {\n"
           << "    .param .b64 " << prefix << "_address;\n"
           << "    .param .b32 " << prefix << "_bytes;\n"
           << "    .param .b32 " << prefix << "_operation;\n"
           << "    .param .b32 " << prefix << "_instruction;\n"
           << "    .param .align 16 .b8 " << prefix << "_return[64];\n"
           << "    cvta.to.global.u64 " << reg(id, "original") << ", "
           << instruction.memory->address_base << ";\n";
    if (instruction.memory->signed_offset != 0) {
        output << "    add.s64 " << reg(id, "original") << ", "
               << reg(id, "original") << ", "
               << instruction.memory->signed_offset << ";\n";
    }
    output << "    st.param.b64 [" << prefix << "_address], "
           << reg(id, "original") << ";\n"
           << "    st.param.b32 [" << prefix << "_bytes], "
           << instruction.memory->bytes << ";\n"
           << "    st.param.b32 [" << prefix << "_operation], 0;\n"
           << "    st.param.b32 [" << prefix << "_instruction], " << id
           << ";\n"
           << "    call (" << prefix
           << "_return), __hbfsim_future_issue, (" << prefix
           << "_address, " << prefix << "_bytes, " << prefix
           << "_operation, " << prefix << "_instruction);\n"
           << load_future_return(id, prefix + "_return")
           << "    }\n"
           << "    and.b32 " << reg(id, "temporary") << ", "
           << reg(id, "flags") << ", 4;\n"
           << "    setp.ne.u32 " << reg(id, "capacity") << ", "
           << reg(id, "temporary") << ", 0;\n"
           << "    setp.eq.u32 " << reg(id, "terminal") << ", "
           << reg(id, "state") << ", 4;\n"
           << "    or.pred " << reg(id, "skip") << ", "
           << reg(id, "capacity") << ", " << reg(id, "terminal") << ";\n"
           << "    @" << reg(id, "skip") << " bra "
           << skip_native << ";\n"
           << instruction_text(instruction, false)
           << "    and.b32 " << reg(id, "temporary") << ", "
           << reg(id, "flags") << ", 1;\n"
           << "    setp.ne.u32 " << reg(id, "native") << ", "
           << reg(id, "temporary") << ", 0;\n"
           << "    @" << reg(id, "native") << " mov.u32 "
           << reg(id, "state") << ", 5;\n"
           << skip_native << ":\n";
    if (!instruction.predicate.empty()) {
        output << false_label << ":\n";
    }
    return output.str();
}

std::string emit_wait(const Instruction& future,
                      const Instruction* guarded_consumer,
                      std::uint32_t occurrence, std::uint32_t wait_kind)
{
    const auto id = future.instruction_id;
    const auto prefix = "%hbfsim_wait_" + std::to_string(id) + "_" +
                        std::to_string(occurrence);
    const auto done = label(id, "wait_done", occurrence);
    const auto materialized = label(id, "wait_materialized", occurrence);
    const auto guard_done = label(id, "wait_guard_false", occurrence);
    std::ostringstream output;
    output << "    // HBFSim future wait " << id << "\n";
    if (guarded_consumer != nullptr &&
        !guarded_consumer->predicate.empty()) {
        output << "    " << inverse_predicate(guarded_consumer->predicate)
               << " bra " << guard_done << ";\n";
    }
    output << "    setp.eq.u32 " << reg(id, "consumed") << ", "
           << reg(id, "state") << ", 5;\n"
           << "    @" << reg(id, "consumed") << " bra " << done
           << ";\n"
           << "    {\n"
           << "    .param .align 16 .b8 " << prefix << "_future[64];\n"
           << "    .param .b32 " << prefix << "_kind;\n"
           << "    .param .align 16 .b8 " << prefix << "_return[64];\n"
           << store_future_argument(id, prefix + "_future")
           << "    st.param.b32 [" << prefix << "_kind], " << wait_kind
           << ";\n"
           << "    call (" << prefix
           << "_return), __hbfsim_future_wait, (" << prefix
           << "_future, " << prefix << "_kind);\n"
           << load_future_return(id, prefix + "_return")
           << "    }\n"
           << "    setp.ne.u32 " << reg(id, "fault") << ", "
           << reg(id, "status") << ", 1;\n"
           << "    {\n"
           << "    .param .b32 " << prefix << "_fault_status;\n"
           << "    .param .b32 " << prefix << "_fault_instruction;\n"
           << "    st.param.b32 [" << prefix << "_fault_status], "
           << reg(id, "status") << ";\n"
           << "    st.param.b32 [" << prefix << "_fault_instruction], "
           << id << ";\n"
           << "    @" << reg(id, "fault")
           << " call __hbfsim_future_fault, (" << prefix
           << "_fault_status, " << prefix << "_fault_instruction);\n"
           << "    }\n"
           << "    setp.eq.u32 " << reg(id, "deferred") << ", "
           << reg(id, "state") << ", 3;\n"
           << "    @!" << reg(id, "deferred") << " bra "
           << materialized << ";\n"
           << instruction_text(future, true)
           << materialized << ":\n"
           << "    mov.u32 " << reg(id, "state") << ", 5;\n"
           << done << ":\n";
    if (guarded_consumer != nullptr &&
        !guarded_consumer->predicate.empty()) {
        output << guard_done << ":\n";
    }
    return output.str();
}

std::size_t function_body_line(const std::vector<std::string>& lines,
                               std::string_view kernel)
{
    const std::regex entry(
        R"(\.(?:visible\s+)?(?:entry|func)\s+)" + sanitized(kernel) +
        R"(\s*\()",
        std::regex::ECMAScript);
    bool found = false;
    for (std::size_t index = 0; index < lines.size(); ++index) {
        if (!found && std::regex_search(lines[index], entry)) {
            found = true;
        }
        if (found && lines[index].find('{') != std::string::npos) {
            return index + 1;
        }
    }
    throw ParseError("selected PTX function body was not found");
}

}  // namespace

FutureTransformResult transform_load_futures(std::string_view ptx,
                                             std::string_view kernel)
{
    FutureTransformResult result;
    const auto module = parse_module(ptx);
    const auto& function = module.function(kernel);
    result.plan = analyze_futures(function);
    if (!result.plan.exact_safe()) {
        result.rejection_reason =
            result.plan.rejection_reasons.begin()->second;
        return result;
    }

    std::map<std::uint32_t, const Instruction*> by_id;
    std::vector<const Instruction*> futures;
    for (const auto& instruction : function.instructions) {
        by_id.emplace(instruction.instruction_id, &instruction);
        if (!instruction.memory.has_value()) {
            continue;
        }
        if (instruction.memory->kind != MemoryKind::Load) {
            result.rejection_reason = "non_load_future_in_load_transform";
            result.output_ptx.clear();
            return result;
        }
        futures.push_back(&instruction);
    }
    if (futures.empty()) {
        result.output_ptx = std::string{ptx};
        return result;
    }

    auto lines = source_lines(ptx);
    const auto body_line = function_body_line(lines, kernel);
    std::map<std::size_t, std::string> before;
    std::map<std::size_t, std::string> replacement;
    std::set<std::size_t> skipped;
    std::ostringstream declarations;
    declarations << "    // HBFSim async kernel " << kernel << "\n";
    for (const auto* future : futures) {
        declarations << future_declarations(*future);
        auto issue = emit_issue(*future);
        if (immediate_load_wait(*future)) {
            issue += emit_wait(*future, nullptr, 1, 0);
        }
        replacement[future->location.line] = std::move(issue);
        auto end = static_cast<std::size_t>(future->location.line);
        while (end <= lines.size() &&
               lines.at(end - 1).find(';') == std::string::npos) {
            ++end;
        }
        if (end > lines.size()) {
            result.rejection_reason = "unterminated_future_instruction";
            return result;
        }
        for (auto line = static_cast<std::size_t>(future->location.line) + 1;
             line <= end; ++line) {
            skipped.insert(line);
        }
    }
    before[body_line + 1] = declarations.str();

    std::map<std::uint32_t, std::uint32_t> occurrences;
    for (const auto& [future_id, consumers] : result.plan.first_consumers) {
        const auto* future = by_id.at(future_id);
        if (immediate_load_wait(*future)) {
            continue;
        }
        for (const auto consumer_id : consumers) {
            const auto* consumer = by_id.at(consumer_id);
            const auto occurrence = ++occurrences[future_id];
            before[consumer->location.line] +=
                emit_wait(*future, consumer, occurrence, 0);
        }
    }
    for (const auto& [future_id, drains] : result.plan.drain_points) {
        if (result.plan.first_consumers.contains(future_id) ||
            immediate_load_wait(*by_id.at(future_id))) {
            continue;
        }
        for (const auto drain_id : drains) {
            const auto occurrence = ++occurrences[future_id];
            before[by_id.at(drain_id)->location.line] +=
                emit_wait(*by_id.at(future_id), by_id.at(drain_id),
                          occurrence, 1);
        }
    }

    std::ostringstream output;
    for (std::size_t line = 1; line <= lines.size(); ++line) {
        if (const auto inserted = before.find(line); inserted != before.end()) {
            output << inserted->second;
        }
        if (const auto replaced = replacement.find(line);
            replaced != replacement.end()) {
            output << replaced->second;
            continue;
        }
        if (!skipped.contains(line)) {
            output << lines[line - 1] << '\n';
        }
    }
    result.output_ptx = output.str();
    result.rewritten_futures = futures.size();
    result.modified = true;
    return result;
}

}  // namespace hbfsim::ptx

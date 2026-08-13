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

std::string regex_escaped(std::string_view value)
{
    static constexpr std::string_view special = R"(\.^$|()[]{}*+?)";
    std::string result;
    for (const auto character : value) {
        if (special.find(character) != std::string_view::npos) {
            result.push_back('\\');
        }
        result.push_back(character);
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

std::vector<std::string> register_tokens(std::string_view input)
{
    static const std::regex expression(R"(%[A-Za-z][A-Za-z0-9_$]*)");
    const std::string text(input);
    std::vector<std::string> result;
    for (std::sregex_iterator it(text.begin(), text.end(), expression), end;
         it != end; ++it) {
        if (std::find(result.begin(), result.end(), it->str()) ==
            result.end()) {
            result.push_back(it->str());
        }
    }
    return result;
}

std::uint32_t value_bits(const Instruction& instruction)
{
    static const std::regex type(
        R"(\.(?:[subf])(8|16|32|64)|\.f16x2|\.b(8|16|32|64)(?:\.|$))");
    std::smatch match;
    if (!std::regex_search(instruction.opcode, match, type)) {
        return 0;
    }
    if (match.str().find("f16x2") != std::string::npos) {
        return 32;
    }
    for (std::size_t index = 1; index < match.size(); ++index) {
        if (match[index].matched) {
            return static_cast<std::uint32_t>(
                std::stoul(match[index].str()));
        }
    }
    return 0;
}

std::vector<std::string> snapshot_sources(const Instruction& instruction)
{
    std::vector<std::string> result;
    if (!instruction.memory.has_value()) {
        return result;
    }
    const auto begin = instruction.memory->kind == MemoryKind::Store
                           ? std::size_t{1}
                           : instruction.memory->kind == MemoryKind::AtomicRmw
                                 ? std::size_t{2}
                                 : instruction.operands.size();
    for (auto index = begin; index < instruction.operands.size(); ++index) {
        for (const auto& token : register_tokens(instruction.operands[index])) {
            if (std::find(result.begin(), result.end(), token) == result.end()) {
                result.push_back(token);
            }
        }
    }
    return result;
}

std::map<std::string, std::string> snapshot_map(
    const Instruction& instruction)
{
    std::map<std::string, std::string> result;
    const auto sources = snapshot_sources(instruction);
    for (std::size_t index = 0; index < sources.size(); ++index) {
        result.emplace(sources[index],
                       reg(instruction.instruction_id,
                           "snapshot_" + std::to_string(index)));
    }
    return result;
}

std::string replace_snapshot_registers(
    std::string_view operand,
    const std::map<std::string, std::string>& snapshots)
{
    static const std::regex expression(R"(%[A-Za-z][A-Za-z0-9_$]*)");
    const std::string text(operand);
    std::ostringstream output;
    std::size_t cursor = 0;
    for (std::sregex_iterator it(text.begin(), text.end(), expression), end;
         it != end; ++it) {
        const auto position = static_cast<std::size_t>(it->position());
        output << text.substr(cursor, position - cursor);
        const auto replacement = snapshots.find(it->str());
        output << (replacement == snapshots.end() ? it->str()
                                                   : replacement->second);
        cursor = position + static_cast<std::size_t>(it->length());
    }
    output << text.substr(cursor);
    return output.str();
}

std::string instruction_text(const Instruction& instruction,
                             bool resolved_address, bool snapshots)
{
    auto operands = instruction.operands;
    if (resolved_address && instruction.memory.has_value()) {
        const auto address_index =
            instruction.memory->kind == MemoryKind::Store ? 0U : 1U;
        operands.at(address_index) =
            "[" + reg(instruction.instruction_id, "resolved") + "]";
    }
    if (snapshots) {
        const auto replacements = snapshot_map(instruction);
        const auto begin = instruction.memory->kind == MemoryKind::Store
                               ? std::size_t{1}
                               : instruction.memory->kind ==
                                         MemoryKind::AtomicRmw
                                     ? std::size_t{2}
                                     : operands.size();
        for (auto index = begin; index < operands.size(); ++index) {
            operands[index] =
                replace_snapshot_registers(operands[index], replacements);
        }
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
    const auto bits = value_bits(instruction);
    const auto sources = snapshot_sources(instruction);
    for (std::size_t index = 0; index < sources.size(); ++index) {
        output << "    .reg .b" << bits << ' '
               << reg(id, "snapshot_" + std::to_string(index)) << ";\n";
    }
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
    const auto bits = value_bits(instruction);
    const auto snapshots = snapshot_map(instruction);
    for (const auto& [source, destination] : snapshots) {
        output << "    mov.b" << bits << ' ' << destination << ", "
               << source << ";\n";
    }
    const auto operation =
        instruction.memory->kind == MemoryKind::Load
            ? 0U
            : instruction.memory->kind == MemoryKind::Store ? 1U : 2U;
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
           << "    st.param.b32 [" << prefix << "_operation], "
           << operation << ";\n"
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
           << instruction_text(instruction, true, true)
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
           << instruction_text(future, true, true)
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
        R"(\.(?:visible\s+)?(?:entry|func)\s+)" + regex_escaped(kernel) +
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

std::string selected_function_source(const std::vector<std::string>& lines,
                                     std::string_view kernel)
{
    const std::regex entry(
        R"(\.(?:visible\s+)?(?:entry|func)\s+)" + regex_escaped(kernel) +
        R"(\s*\()",
        std::regex::ECMAScript);
    std::size_t begin = lines.size();
    std::size_t end = lines.size();
    int depth = 0;
    bool body = false;
    for (std::size_t index = 0; index < lines.size(); ++index) {
        if (begin == lines.size() && std::regex_search(lines[index], entry)) {
            begin = index;
        }
        if (begin == lines.size() || index < begin) {
            continue;
        }
        for (const auto character : lines[index]) {
            if (character == '{') {
                ++depth;
                body = true;
            } else if (character == '}' && body) {
                --depth;
            }
        }
        if (body && depth == 0) {
            end = index;
            break;
        }
    }
    if (begin == lines.size() || end == lines.size()) {
        throw ParseError("selected PTX function extent was not found");
    }
    std::ostringstream output;
    for (std::size_t index = 0; index < lines.size(); ++index) {
        if (index >= begin && index <= end) {
            output << lines[index];
        }
        output << '\n';
    }
    return output.str();
}

bool supported_atomic(const Instruction& instruction)
{
    if (!instruction.memory.has_value() ||
        instruction.memory->kind != MemoryKind::AtomicRmw) {
        return true;
    }
    static const std::regex type(R"(\.(?:[usb](?:32|64))(?:\.|$))");
    return std::regex_search(instruction.opcode, type);
}

}  // namespace

FutureTransformResult transform_futures(std::string_view ptx,
                                        std::string_view kernel)
{
    FutureTransformResult result;
    auto lines = source_lines(ptx);
    const auto body_line = function_body_line(lines, kernel);
    const auto marker = "// HBFSim async kernel " + std::string{kernel};
    if (body_line < lines.size() && lines[body_line].find(marker) !=
                                        std::string::npos) {
        result.output_ptx = std::string{ptx};
        return result;
    }
    const auto module = parse_module(selected_function_source(lines, kernel));
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
        if (!supported_atomic(instruction)) {
            result.rejection_reason = "unsupported_atomic_type";
            result.output_ptx.clear();
            return result;
        }
        if (!snapshot_sources(instruction).empty() &&
            (value_bits(instruction) == 0 || value_bits(instruction) > 64)) {
            result.rejection_reason = "unsupported_snapshot_width";
            result.output_ptx.clear();
            return result;
        }
        futures.push_back(&instruction);
    }
    if (futures.empty()) {
        result.output_ptx = std::string{ptx};
        return result;
    }

    std::map<std::size_t, std::string> before;
    std::map<std::size_t, std::string> replacement;
    std::set<std::size_t> skipped;
    std::ostringstream declarations;
    declarations << "    // HBFSim async kernel " << kernel << "\n";
    std::ostringstream initializers;
    initializers << "    // HBFSim initialize unissued futures\n";
    for (const auto* future : futures) {
        declarations << future_declarations(*future);
        initializers << "    mov.u32 "
                     << reg(future->instruction_id, "state")
                     << ", 5;\n";
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
    before[function.instructions.front().location.line] += initializers.str();

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
        if (immediate_load_wait(*by_id.at(future_id))) {
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

FutureTransformResult transform_load_futures(std::string_view ptx,
                                             std::string_view kernel)
{
    return transform_futures(ptx, kernel);
}

}  // namespace hbfsim::ptx

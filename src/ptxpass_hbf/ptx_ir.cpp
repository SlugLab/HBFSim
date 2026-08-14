#include "ptx_ir.hpp"

#include <algorithm>
#include <charconv>
#include <cctype>
#include <limits>
#include <regex>
#include <set>
#include <sstream>

namespace hbfsim::ptx {
namespace {

std::string trim(std::string_view input)
{
    const auto begin = input.find_first_not_of(" \t\r\n");
    if (begin == std::string_view::npos) {
        return {};
    }
    const auto end = input.find_last_not_of(" \t\r\n");
    return std::string(input.substr(begin, end - begin + 1));
}

std::string without_comment(std::string_view line)
{
    const auto comment = line.find("//");
    return std::string(line.substr(0, comment));
}

std::vector<std::string> split_top_level(std::string_view input)
{
    std::vector<std::string> result;
    std::size_t begin = 0;
    int square = 0;
    int brace = 0;
    int parenthesis = 0;
    for (std::size_t index = 0; index < input.size(); ++index) {
        switch (input[index]) {
        case '[': ++square; break;
        case ']': --square; break;
        case '{': ++brace; break;
        case '}': --brace; break;
        case '(': ++parenthesis; break;
        case ')': --parenthesis; break;
        case ',':
            if (square == 0 && brace == 0 && parenthesis == 0) {
                result.push_back(trim(input.substr(begin, index - begin)));
                begin = index + 1;
            }
            break;
        default: break;
        }
        if (square < 0 || brace < 0 || parenthesis < 0) {
            throw ParseError("unbalanced PTX instruction operand");
        }
    }
    if (square != 0 || brace != 0 || parenthesis != 0) {
        throw ParseError("unbalanced PTX instruction operand");
    }
    if (begin < input.size() || !input.empty()) {
        result.push_back(trim(input.substr(begin)));
    }
    return result;
}

std::vector<std::string> registers(std::string_view input)
{
    static const std::regex expression(R"(%[A-Za-z][A-Za-z0-9_$]*)");
    const std::string text(input);
    std::vector<std::string> result;
    std::set<std::string> found;
    for (std::sregex_iterator it(text.begin(), text.end(), expression), end;
         it != end; ++it) {
        auto value = it->str();
        if (found.insert(value).second) {
            result.push_back(std::move(value));
        }
    }
    return result;
}

void append_unique(std::vector<std::string>& output,
                   const std::vector<std::string>& input)
{
    for (const auto& value : input) {
        if (std::find(output.begin(), output.end(), value) == output.end()) {
            output.push_back(value);
        }
    }
}

std::optional<std::int64_t> parse_integer(std::string text)
{
    if (text.empty()) {
        return 0;
    }
    bool negative = false;
    if (text.front() == '+' || text.front() == '-') {
        negative = text.front() == '-';
        text.erase(text.begin());
    }
    int base = 10;
    if (text.starts_with("0x") || text.starts_with("0X")) {
        base = 16;
        text.erase(0, 2);
    }
    std::uint64_t magnitude = 0;
    const auto parsed =
        std::from_chars(text.data(), text.data() + text.size(), magnitude, base);
    if (text.empty() || parsed.ec != std::errc{} ||
        parsed.ptr != text.data() + text.size()) {
        return std::nullopt;
    }
    if (negative) {
        if (magnitude > (std::uint64_t{1} << 63)) {
            return std::nullopt;
        }
        return magnitude == (std::uint64_t{1} << 63)
                   ? std::numeric_limits<std::int64_t>::min()
                   : -static_cast<std::int64_t>(magnitude);
    }
    if (magnitude > static_cast<std::uint64_t>(
                        std::numeric_limits<std::int64_t>::max())) {
        return std::nullopt;
    }
    return static_cast<std::int64_t>(magnitude);
}

std::vector<std::string> opcode_parts(std::string_view opcode)
{
    std::vector<std::string> result;
    std::size_t begin = 0;
    while (begin <= opcode.size()) {
        const auto dot = opcode.find('.', begin);
        result.emplace_back(opcode.substr(
            begin, dot == std::string_view::npos ? opcode.size() - begin
                                                  : dot - begin));
        if (dot == std::string_view::npos) {
            break;
        }
        begin = dot + 1;
    }
    return result;
}

std::optional<std::uint32_t> memory_bytes(
    const std::vector<std::string>& parts)
{
    std::uint32_t lanes = 1;
    std::optional<std::uint32_t> bits;
    for (const auto& part : parts) {
        if (part == "v2") {
            lanes = 2;
        } else if (part == "v4") {
            lanes = 4;
        }
        static const std::regex type(R"([subf]?(8|16|32|64|128)|f16x2)");
        std::smatch match;
        if (std::regex_match(part, match, type)) {
            bits = part == "f16x2"
                       ? 32
                       : static_cast<std::uint32_t>(
                             std::stoul(match[1].str()));
        }
    }
    return bits.has_value() ? std::optional<std::uint32_t>{lanes * *bits / 8}
                            : std::nullopt;
}

std::optional<MemoryInstruction> parse_memory(
    const std::string& opcode, const std::vector<std::string>& operands)
{
    const auto parts = opcode_parts(opcode);
    if (parts.empty()) {
        return std::nullopt;
    }
    MemoryKind kind = MemoryKind::None;
    if (parts.front() == "ld") {
        kind = MemoryKind::Load;
    } else if (parts.front() == "st") {
        kind = MemoryKind::Store;
    } else if (parts.front() == "atom") {
        kind = MemoryKind::AtomicRmw;
    } else {
        return std::nullopt;
    }
    const auto global = std::find(parts.begin(), parts.end(), "global");
    if (global == parts.end()) {
        return std::nullopt;
    }
    const auto bytes = memory_bytes(parts);
    if (!bytes.has_value()) {
        throw ParseError("global memory instruction has no supported type");
    }
    const std::size_t address_index = kind == MemoryKind::Store ? 0 : 1;
    if (operands.size() <= address_index) {
        throw ParseError("global memory instruction has too few operands");
    }
    static const std::regex address(
        R"(^\[\s*(%[A-Za-z][A-Za-z0-9_$]*)(?:\s*([+-])\s*(-?(?:0[xX])?[0-9A-Fa-f]+))?\s*\]$)");
    std::smatch address_match;
    if (!std::regex_match(operands[address_index], address_match, address)) {
        throw ParseError("unsupported global address expression");
    }
    std::string offset;
    if (address_match[2].matched) {
        auto magnitude = address_match[3].str();
        const bool magnitude_negative = magnitude.starts_with('-');
        if (magnitude_negative) {
            magnitude.erase(magnitude.begin());
        }
        const bool negative =
            (address_match[2].str() == "-") != magnitude_negative;
        offset = (negative ? "-" : "+") + magnitude;
    }
    const auto signed_offset = parse_integer(offset);
    if (!signed_offset.has_value()) {
        throw ParseError("invalid global address offset");
    }
    std::vector<std::string> qualifiers;
    for (auto it = global + 1; it != parts.end(); ++it) {
        if (*it != "volatile") {
            qualifiers.push_back(*it);
        }
    }
    // PTX permits volatile before the state-space token.
    if (std::find(parts.begin(), global, "volatile") != global) {
        qualifiers.insert(qualifiers.begin(), "volatile");
    }
    std::vector<std::string> values;
    if (kind == MemoryKind::Load) {
        values.push_back(operands.front());
    } else if (kind == MemoryKind::Store) {
        values.insert(values.end(), operands.begin() + 1, operands.end());
    } else {
        values.push_back(operands.front());
        values.insert(values.end(), operands.begin() + 2, operands.end());
    }
    return MemoryInstruction{
        .kind = kind,
        .state_space = "global",
        .qualifiers = std::move(qualifiers),
        .value_operands = std::move(values),
        .address_base = address_match[1].str(),
        .signed_offset = *signed_offset,
        .bytes = *bytes,
    };
}

Instruction parse_instruction(std::string text, SourceLocation location,
                              std::uint32_t id)
{
    Instruction result{.instruction_id = id,
                       .location = location,
                       .text = trim(text)};
    if (result.text.empty() || result.text.back() != ';') {
        throw ParseError("PTX instruction is not terminated");
    }
    result.text.pop_back();
    result.text = trim(result.text);
    auto remainder = result.text;
    if (!remainder.empty() && remainder.front() == '@') {
        const auto space = remainder.find_first_of(" \t\r\n");
        if (space == std::string::npos) {
            throw ParseError("predicated PTX instruction has no opcode");
        }
        result.predicate = remainder.substr(0, space);
        remainder = trim(remainder.substr(space));
        append_unique(result.uses, registers(result.predicate));
    }
    if (remainder.starts_with("asm(") ||
        remainder.starts_with("asm volatile(")) {
        result.opcode = "asm";
        result.operands.push_back(remainder);
        append_unique(result.uses, registers(remainder));
        return result;
    }
    const auto space = remainder.find_first_of(" \t\r\n");
    result.opcode = remainder.substr(0, space);
    const auto operand_text =
        space == std::string::npos ? std::string{} : trim(remainder.substr(space));
    result.operands = split_top_level(operand_text);
    if (result.operands.size() == 1 && result.operands.front().empty()) {
        result.operands.clear();
    }
    result.memory = parse_memory(result.opcode, result.operands);
    result.async = parse_async_instruction(result.opcode, result.operands);
    if (result.memory.has_value()) {
        const auto& memory = *result.memory;
        if (memory.kind == MemoryKind::Load) {
            append_unique(result.defs, registers(result.operands.front()));
            append_unique(result.uses, registers(result.operands.at(1)));
        } else if (memory.kind == MemoryKind::Store) {
            append_unique(result.uses, registers(result.operands.front()));
            for (std::size_t index = 1; index < result.operands.size(); ++index) {
                append_unique(result.uses, registers(result.operands[index]));
            }
        } else {
            append_unique(result.defs, registers(result.operands.front()));
            append_unique(result.uses, registers(result.operands.at(1)));
            for (std::size_t index = 2; index < result.operands.size(); ++index) {
                append_unique(result.uses, registers(result.operands[index]));
            }
        }
    } else if (result.opcode == "bra" ||
               result.opcode.starts_with("bra.")) {
        if (result.operands.size() != 1 || result.operands.front().empty()) {
            throw ParseError("branch requires one target");
        }
        result.branch_targets.push_back(result.operands.front());
    } else if (!result.operands.empty() &&
               (result.opcode.starts_with("mov.") ||
                result.opcode.starts_with("cvt.") ||
                result.opcode.starts_with("setp.") ||
                result.opcode.starts_with("add.") ||
                result.opcode.starts_with("sub.") ||
                result.opcode.starts_with("mul.") ||
                result.opcode.starts_with("and.") ||
                result.opcode.starts_with("or.") ||
                result.opcode.starts_with("xor."))) {
        append_unique(result.defs, registers(result.operands.front()));
        for (std::size_t index = 1; index < result.operands.size(); ++index) {
            append_unique(result.uses, registers(result.operands[index]));
        }
    } else {
        for (const auto& operand : result.operands) {
            append_unique(result.uses, registers(operand));
        }
    }
    return result;
}

bool declaration(std::string_view line)
{
    const auto value = trim(line);
    return value.starts_with(".reg ") || value.starts_with(".shared ") ||
           value.starts_with(".local ") || value.starts_with(".param ") ||
           value.starts_with(".pragma ") ||
           value.starts_with(".maxntid ") || value.starts_with(".minnctapersm ");
}

}  // namespace

const Function& Module::function(std::string_view name) const
{
    const auto found = std::find_if(
        functions.begin(), functions.end(),
        [&](const Function& value) { return value.name == name; });
    if (found == functions.end()) {
        throw std::out_of_range("PTX function not found");
    }
    return *found;
}

Module parse_module(std::string_view ptx)
{
    Module module;
    std::istringstream input(std::string{ptx});
    std::string raw;
    Function* function = nullptr;
    bool waiting_for_body = false;
    bool body_open = false;
    bool pending_fallthrough = false;
    std::uint32_t nested_scope = 0;
    std::string instruction_text;
    SourceLocation instruction_location;
    std::uint32_t line_number = 0;
    std::uint32_t next_id = 1;
    static const std::regex function_expression(
        R"(\.(?:visible\s+)?(?:entry|func)\s+([A-Za-z0-9_$.]+))");
    static const std::regex label_expression(
        R"(^\s*([A-Za-z_$][A-Za-z0-9_$.]*)\s*:\s*$)");

    const auto new_block = [&](Function& value, std::string label) {
        // Consecutive PTX labels are aliases for the same instruction.  Keep
        // each alias as an empty fall-through block so branches to either
        // spelling remain resolvable by CFG construction.
        value.blocks.push_back({.label = std::move(label)});
    };

    while (std::getline(input, raw)) {
        ++line_number;
        auto line = without_comment(raw);
        auto clean = trim(line);
        if (function == nullptr) {
            std::smatch match;
            if (std::regex_search(line, match, function_expression)) {
                module.functions.push_back({.name = match[1].str()});
                function = &module.functions.back();
                waiting_for_body = true;
                if (line.find('{') != std::string::npos) {
                    waiting_for_body = false;
                    body_open = true;
                    new_block(*function, function->name + "$entry");
                }
            }
            continue;
        }
        if (waiting_for_body) {
            if (line.find('{') != std::string::npos) {
                waiting_for_body = false;
                body_open = true;
                new_block(*function, function->name + "$entry");
            }
            continue;
        }
        if (!body_open) {
            throw ParseError("invalid PTX function state");
        }
        if (instruction_text.empty() && clean.size() >= 2 &&
            clean.front() == '{' && clean.back() == '}' &&
            clean.find(';') != std::string::npos) {
            clean = trim(std::string_view{clean}.substr(1, clean.size() - 2));
            line = clean;
        }
        if (instruction_text.empty() && clean == "{") {
            ++nested_scope;
            continue;
        }
        if (instruction_text.empty() && clean == "}" && nested_scope != 0) {
            --nested_scope;
            continue;
        }
        if (instruction_text.empty() && clean == "}") {
            body_open = false;
            function = nullptr;
            pending_fallthrough = false;
            nested_scope = 0;
            continue;
        }
        if (instruction_text.empty()) {
            if (clean.empty() || declaration(clean)) {
                continue;
            }
            std::smatch label;
            if (std::regex_match(line, label, label_expression)) {
                new_block(*function, label[1].str());
                pending_fallthrough = false;
                continue;
            }
            if (pending_fallthrough) {
                new_block(*function,
                          function->name + "$fallthrough$" +
                              std::to_string(function->blocks.size()));
                pending_fallthrough = false;
            }
            const auto first = line.find_first_not_of(" \t");
            instruction_location = {
                .line = line_number,
                .column = static_cast<std::uint32_t>(first + 1),
            };
        }
        if (!instruction_text.empty()) {
            instruction_text.push_back(' ');
        }
        instruction_text += clean;
        if (clean.find(';') == std::string::npos) {
            continue;
        }
        Instruction instruction;
        try {
            instruction = parse_instruction(
                std::move(instruction_text), instruction_location, next_id++);
        } catch (const ParseError& error) {
            throw ParseError(std::string{error.what()} + " at PTX line " +
                             std::to_string(instruction_location.line));
        }
        instruction_text.clear();
        const auto index = function->instructions.size();
        const bool terminator = instruction.opcode == "bra" ||
                                instruction.opcode.starts_with("bra.") ||
                                instruction.opcode == "ret" ||
                                instruction.opcode.starts_with("ret.") ||
                                instruction.opcode == "exit" ||
                                instruction.opcode.starts_with("exit.");
        function->instructions.push_back(std::move(instruction));
        if (function->blocks.empty()) {
            new_block(*function, function->name + "$entry");
        }
        function->blocks.back().instructions.push_back(index);
        pending_fallthrough = terminator;
    }
    if (function != nullptr || waiting_for_body || body_open ||
        !instruction_text.empty()) {
        throw ParseError("unterminated PTX function or instruction");
    }
    return module;
}

}  // namespace hbfsim::ptx

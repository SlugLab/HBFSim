#include "ptx_ir.hpp"
#include "ptx_source.hpp"

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
    static const std::regex expression(R"(%[A-Za-z][A-Za-z0-9_$]*(?:\.[A-Za-z][A-Za-z0-9_$]*)?)");
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
                result.opcode.starts_with("cvta.") ||
                result.opcode.starts_with("ld.param.") ||
                result.opcode.starts_with("mad.") ||
                result.opcode.starts_with("shl.") ||
                result.opcode.starts_with("shr.") ||
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
    bool inside_block_comment = false;
    std::uint32_t nested_scope = 0;
    std::string instruction_text;
    SourceLocation instruction_location;
    std::uint32_t line_number = 0;
    std::uint32_t next_id = 1;
    static const std::regex function_expression(
        R"(\.(?:visible\s+)?(?:entry|func)\s+([A-Za-z0-9_$.]+))");
    static const std::regex function_directive(R"(\.(?:entry|func)\b)");
    static const std::regex location_expression(
        R"(^\.loc[ \t]+[0-9]+[ \t]+[0-9]+[ \t]+[0-9]+[ \t]*$)");
    static const std::regex label_expression(
        R"(^\s*([A-Za-z_$][A-Za-z0-9_$.]*)\s*:\s*$)");
    static const std::regex inline_label_expression(
        R"(^\s*([A-Za-z_$][A-Za-z0-9_$.]*)\s*:(?!:))");

    const auto new_block = [&](Function& value, std::string label) {
        // Consecutive PTX labels are aliases for the same instruction.  Keep
        // each alias as an empty fall-through block so branches to either
        // spelling remain resolvable by CFG construction.
        value.blocks.push_back({.label = std::move(label)});
    };

    while (std::getline(input, raw)) {
        ++line_number;
        auto line = detail::code_without_comments(raw, inside_block_comment);
        auto clean = trim(line);
        if (function == nullptr) {
            std::smatch match;
            if (std::regex_search(line, match, function_expression)) {
                if (line.find(';') != std::string::npos) {
                    throw ParseError("PTX function prototypes are unsupported");
                }
                module.functions.push_back({.name = match[1].str()});
                function = &module.functions.back();
                waiting_for_body = true;
                if (line.find('{') != std::string::npos) {
                    if (!trim(line.substr(line.find('{') + 1)).empty()) {
                        throw ParseError("packed PTX function body is unsupported");
                    }
                    waiting_for_body = false;
                    body_open = true;
                    new_block(*function, function->name + "$entry");
                }
            } else if (std::regex_search(line, function_directive)) {
                throw ParseError("unsupported PTX function header");
            }
            continue;
        }
        if (waiting_for_body) {
            if (line.find(';') != std::string::npos ||
                std::regex_search(line, function_directive)) {
                throw ParseError("PTX function declaration has no body");
            }
            if (line.find('{') != std::string::npos) {
                if (!trim(line.substr(line.find('{') + 1)).empty()) {
                    throw ParseError("packed PTX function body is unsupported");
                }
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
        const auto semicolon = clean.find(';');
        if (semicolon != std::string::npos &&
            !trim(clean.substr(semicolon + 1)).empty()) {
            throw ParseError("packed PTX statements are unsupported");
        }
        if (instruction_text.empty()) {
            // Debug locations terminate at newline, not at a semicolon.
            // Never join one to the next memory instruction.
            if (clean == ".loc" || clean.starts_with(".loc ") ||
                clean.starts_with(".loc\t")) {
                if (!std::regex_match(clean, location_expression)) {
                    throw ParseError("unsupported PTX location directive");
                }
                continue;
            }
            if (clean.empty() || declaration(clean)) {
                continue;
            }
            std::smatch label;
            if (std::regex_match(line, label, label_expression)) {
                new_block(*function, label[1].str());
                pending_fallthrough = false;
                continue;
            }
            if (std::regex_search(line, inline_label_expression)) {
                throw ParseError("inline PTX label/instruction packing is unsupported");
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
    if (inside_block_comment) {
        throw ParseError("unterminated PTX block comment");
    }
    if (function != nullptr || waiting_for_body || body_open ||
        !instruction_text.empty()) {
        throw ParseError("unterminated PTX function or instruction");
    }
    return module;
}

namespace {
// Preserve one byte for every input byte. Offsets refer to the original PTX,
// including comments and newlines, rather than a normalized reconstruction.
std::string span_mask(std::string_view source, std::vector<SourceSpan>& strings)
{
    std::string code(source);
    enum class Lex { Code, Line, Block, String } state = Lex::Code;
    bool escaped = false;
    for (std::size_t i=0;i<source.size();++i) {
        const char c=source[i], next=i+1<source.size()?source[i+1]:'\0';
        if (state==Lex::Line) {
            if (c=='\n') state=Lex::Code; else code[i]=' ';
        } else if (state==Lex::Block) {
            if(c=='*' && next=='/') {code[i]=code[i+1]=' ';++i;state=Lex::Code;}
            else if(c!='\n') code[i]=' ';
        } else if(state==Lex::String) {
            if(c!='\n') code[i]=' ';
            if(c=='"' && !escaped) {strings.back().end=i+1;state=Lex::Code;}
            escaped=c=='\\' && !escaped;
        } else if(c=='/' && (next=='/' || next=='*')) {
            state=next=='/'?Lex::Line:Lex::Block;code[i]=code[i+1]=' ';++i;
        } else if(c=='"') {
            strings.push_back({i,0});state=Lex::String;escaped=false;code[i]=' ';
        }
    }
    if(state==Lex::Block || state==Lex::String) throw ParseError("unterminated PTX comment/string");
    return code;
}

void add_registers(Function& function, const std::string& declaration)
{
    static const std::regex decl(R"(^\.reg\s+\.(pred|[busf](?:16|32|64))\s+(.+);$)");
    static const std::regex reg(R"(^(%[A-Za-z][A-Za-z0-9_$]*)(?:<([0-9]+)>)?$)");
    std::smatch type;
    if(!std::regex_match(declaration,type,decl)) throw ParseError("unsupported register declaration");
    for(const auto& item:split_top_level(type[2].str())) {
        std::smatch name;
        if(!std::regex_match(item,name,reg)) throw ParseError("unsupported register name");
        const auto count=name[2].matched?std::stoull(name[2].str()):1;
        if(count==0 || count>65536) throw ParseError("register declaration bound");
        for(std::size_t i=0;i<count;++i) {
            const auto key=name[1].str()+(name[2].matched?std::to_string(i):"");
            if(!function.register_types.emplace(key,type[1].str()).second)
                throw ParseError("duplicate register declaration");
        }
    }
}
} // namespace

Module parse_module_spanned(std::string_view source, std::string_view selected_entry)
{
    std::vector<SourceSpan> strings;
    const auto code=span_mask(source,strings);
    Module result;
    // Header positions come from comment/string-masked code, with the same
    // byte offsets as the original. A comment mentioning a directive cannot
    // choose the insertion point, and only the supported 64-bit form binds it.
    static const std::regex address_directive(R"(^[ \t]*\.address_size\b[^\n]*(?:\n|$))",
        std::regex::ECMAScript | std::regex::multiline);
    static const std::regex address_form(R"(^\s*\.address_size[ \t]+(64)[ \t\r]*\n?$)");
    for(std::sregex_iterator it(code.begin(),code.end(),address_directive),end;it!=end;++it) {
        const auto line=it->str();
        std::smatch header;
        if(result.address_size_directive.end || !std::regex_match(line,header,address_form))
            throw ParseError("missing or conflicting 64-bit PTX address-size header");
        // End at the validated numeric token, not its physical newline: a
        // trailing multiline block comment may contain that newline. The
        // emitter inserts newline-prefixed declarations at this code boundary
        // and retains all original trailing whitespace/comments after them.
        result.address_size_directive={static_cast<std::size_t>(it->position()),
            static_cast<std::size_t>(it->position()+header.position(1)+header.length(1))};
    }
    if(!result.address_size_directive.end)throw ParseError("missing 64-bit PTX address-size header");
    static const std::regex directive(R"(\.(entry|func)\b)");
    static const std::regex name_expression(R"(^\s*([A-Za-z_$][A-Za-z0-9_$.]*)\s*\()");
    static const std::regex parameter(R"(^\.param\s+\.([busf](?:8|16|32|64))\s+(?:\.ptr\s+(?:\.(?:global|const|local|shared)\s+)?(?:\.align\s+[0-9]+\s+)?)?([A-Za-z_$][A-Za-z0-9_$.]*)$)");
    std::size_t cursor=0;
    std::uint32_t next_id=1;
    while(cursor<code.size()) {
        std::smatch match;
        const auto tail=code.substr(cursor);
        if(!std::regex_search(tail,match,directive)) break;
        if(result.address_size_directive.end>cursor+match.position())
            throw ParseError("PTX address-size header must precede functions");
        const bool entry=match[1].str()=="entry";
        std::size_t pos=cursor+match.position()+match.length();
        while(pos<code.size() && std::isspace(static_cast<unsigned char>(code[pos]))) ++pos;
        // Unrelated return-valued functions and prototypes are preserved.
        if(pos<code.size() && code[pos]=='(') {
            int depth=1;++pos;
            while(pos<code.size() && depth) {if(code[pos]=='(')++depth;if(code[pos]==')')--depth;++pos;}
            if(depth || entry) throw ParseError("invalid PTX return parameter header");
        }
        const auto header=code.substr(pos);
        if(!std::regex_search(header,match,name_expression)) throw ParseError("unsupported PTX function header");
        const auto name=match[1].str();
        pos+=match.length();
        const auto params_begin=pos;
        int depth=1;
        while(pos<code.size() && depth) {if(code[pos]=='(')++depth;if(code[pos]==')')--depth;if(depth)++pos;}
        if(depth) throw ParseError("unterminated PTX parameters");
        const auto params=code.substr(params_begin,pos-params_begin);
        ++pos;
        const auto boundary=code.find_first_of("{;",pos);
        if(boundary==std::string::npos) throw ParseError("missing PTX function body");
        if(code[boundary]==';') {cursor=boundary+1;continue;}
        // Only explicit launch-bound directives may sit between header/body.
        const auto attributes=trim(code.substr(pos,boundary-pos));
        static const std::regex launch_attributes(R"(^(?:(?:\.maxntid|\.reqntid)\s+[0-9]+(?:\s*,\s*[0-9]+){0,2}\s*)*$)");
        if(!std::regex_match(attributes,launch_attributes)) throw ParseError("unsupported function attributes");
        pos=boundary+1;depth=1;
        while(pos<code.size() && depth) {if(code[pos]=='{')++depth;if(code[pos]=='}')--depth;if(depth)++pos;}
        if(depth) throw ParseError("unterminated PTX body");
        Function function{.name=name,.body_begin=boundary,.body_end=pos,.entry=entry};
        if(std::any_of(result.functions.begin(),result.functions.end(),[&](const auto& f){return f.name==name;})) throw ParseError("duplicate PTX function");
        // Non-entry function internals are deliberately opaque: no rewriting,
        // admission, or future coverage claim is made for them.
        if(!entry || (!selected_entry.empty() && name!=selected_entry)) {result.functions.push_back(std::move(function));cursor=pos+1;continue;}
        // Strings in module metadata are opaque, but executable scalar
        // statements admit no strings. Preserve their lexical identity so
        // masking cannot turn an invalid load into an accepted/repaired one.
        // Quotes inside comments never enter the lexer String state.
        if(std::any_of(strings.begin(),strings.end(),[&](const SourceSpan& s) {
            return s.begin>function.body_begin && s.begin<function.body_end;
        })) throw ParseError("quoted token inside selected executable region");
        static const std::regex launch_attribute(R"(\.(maxntid|reqntid)\s+([0-9]+(?:\s*,\s*[0-9]+){0,2}))");
        for(std::sregex_iterator it(attributes.begin(),attributes.end(),launch_attribute),end;it!=end;++it) {
            // PTX forbids reqntid and maxntid together. Reject duplicates too;
            // no last-directive-wins interpretation is part of this subset.
            if(function.required_thread_dimensions || function.maximum_thread_dimensions)
                throw ParseError("conflicting PTX launch dimensions");
            std::array<std::uint32_t,3> dimensions{1,1,1};
            const auto values=split_top_level((*it)[2].str());
            std::uint64_t product=1;
            for(std::size_t axis=0;axis<values.size();++axis) {
                const auto dimension=parse_integer(values[axis]);
                if(!dimension || *dimension<=0 || std::uint64_t(*dimension)>UINT32_MAX ||
                    product>UINT64_MAX/std::uint64_t(*dimension))
                    throw ParseError("invalid or overflowing PTX launch dimensions");
                dimensions[axis]=static_cast<std::uint32_t>(*dimension);
                product*=std::uint64_t(*dimension);
            }
            if((*it)[1].str()=="reqntid")function.required_thread_dimensions=dimensions;
            else function.maximum_thread_dimensions=dimensions;
        }
        for(const auto& item:split_top_level(params)) {
            if(item.empty()) continue;
            std::smatch p;
            if(!std::regex_match(item,p,parameter) ||
               !function.parameter_types.emplace(p[2].str(),p[1].str()).second)
                throw ParseError("unsupported or duplicate kernel parameter");
        }
        function.blocks.push_back({.label=name+"$entry"});
        std::size_t statement=boundary+1;
        while(statement<pos) {
            while(statement<pos && std::isspace(static_cast<unsigned char>(code[statement]))) ++statement;
            if(statement==pos) break;
            if(code.compare(statement,4,".loc")==0) {
                const auto newline=code.find('\n',statement);
                const auto location=trim(code.substr(statement,std::min(newline,pos)-statement));
                static const std::regex loc(R"(^\.loc\s+[0-9]+\s+[0-9]+\s+[0-9]+$)");
                if(!std::regex_match(location,loc)) throw ParseError("unsupported PTX location directive");
                statement=std::min(newline,pos);continue;
            }
            const auto semi=code.find(';',statement);
            if(semi==std::string::npos || semi>=pos) throw ParseError("unterminated PTX statement");
            const auto text=trim(code.substr(statement,semi+1-statement));
            if(text.starts_with(".reg")) add_registers(function,text);
            else {
                if(text.find_first_of("{}:")!=std::string::npos || text.front()=='.')
                    throw ParseError("outside single straight-line statement region");
                const auto line=1+std::count(code.begin(),code.begin()+statement,'\n');
                const auto previous=code.rfind('\n',statement);
                auto instruction=parse_instruction(text,{static_cast<std::uint32_t>(line),static_cast<std::uint32_t>(statement-(previous==std::string::npos?0:previous+1)+1)},next_id++);
                instruction.span={statement,semi+1};
                function.blocks.front().instructions.push_back(function.instructions.size());
                function.instructions.push_back(std::move(instruction));
            }
            statement=semi+1;
        }
        result.functions.push_back(std::move(function));cursor=pos+1;
    }
    return result;
}

}  // namespace hbfsim::ptx

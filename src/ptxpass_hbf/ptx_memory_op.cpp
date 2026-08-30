#include "ptx_memory_op.hpp"

#include <charconv>
#include <limits>
#include <regex>
#include <string>

namespace hbfsim::ptx {
namespace {

std::optional<std::int64_t> parse_offset(std::string text)
{
    if (text.empty()) {
        return 0;
    }
    const bool negative = text.front() == '-';
    text.erase(text.begin());
    int base = 10;
    if (text.starts_with("0x") || text.starts_with("0X")) {
        base = 16;
        text.erase(0, 2);
    }
    std::uint64_t magnitude = 0;
    const auto result =
        std::from_chars(text.data(), text.data() + text.size(), magnitude, base);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size()) {
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

std::optional<std::uint32_t> access_bytes(const std::string& opcode)
{
    static const std::regex vector_expression(R"(\.v(2|4)(?:\.|$))");
    static const std::regex type_expression(
        R"(\.(?:[sub](8|16|32|64)|f(16|16x2|32|64)|b(8|16|32|64|128))(?:\.|$))");
    std::smatch match;
    std::uint32_t lanes = 1;
    if (std::regex_search(opcode, match, vector_expression)) {
        lanes = static_cast<std::uint32_t>(std::stoul(match[1].str()));
    }
    if (!std::regex_search(opcode, match, type_expression)) {
        return std::nullopt;
    }
    std::string width_text;
    for (std::size_t index = 1; index < match.size(); ++index) {
        if (match[index].matched) {
            width_text = match[index].str();
            break;
        }
    }
    std::uint32_t bits = width_text == "16x2"
                             ? 32
                             : static_cast<std::uint32_t>(
                                   std::stoul(width_text));
    return lanes * bits / 8;
}

}  // namespace

std::optional<PtxMemoryOp> parse_memory_op(std::string_view input)
{
    std::string line(input);
    if (const auto comment = line.find("//"); comment != std::string::npos) {
        line.erase(comment);
    }
    static const std::regex instruction(
        R"(^\s*((?:@!?%[A-Za-z0-9_$]+)?)\s*((?:ld|st)(?:\.volatile)?\.global(?:\.[^\s]+)*)\s+(.+);\s*$)");
    std::smatch match;
    if (!std::regex_match(line, match, instruction)) {
        return std::nullopt;
    }

    const auto opcode = match[2].str();
    const auto bytes = access_bytes(opcode);
    if (!bytes.has_value()) {
        return std::nullopt;
    }
    const bool load = opcode.starts_with("ld.");
    const auto operands = match[3].str();
    static const std::regex address(
        R"(\[\s*(%rd[A-Za-z0-9_$]*)(?:\s*([+-])\s*(-?(?:0[xX])?[0-9A-Fa-f]+))?\s*\])");
    std::smatch address_match;
    if (!std::regex_search(operands, address_match, address)) {
        return std::nullopt;
    }

    std::string signed_offset;
    if (address_match[2].matched) {
        auto magnitude = address_match[3].str();
        const bool magnitude_negative = magnitude.starts_with('-');
        if (magnitude_negative) {
            magnitude.erase(magnitude.begin());
        }
        const bool negative =
            (address_match[2].str() == "-") != magnitude_negative;
        signed_offset = (negative ? "-" : "+") + magnitude;
    }
    const auto offset = parse_offset(signed_offset);
    if (!offset.has_value()) {
        return std::nullopt;
    }

    return PtxMemoryOp{
        .predicate = match[1].str(),
        .kind = load ? AccessKind::Read : AccessKind::Write,
        .opcode = opcode,
        .address_space = "global",
        .bytes = *bytes,
        .base_register = address_match[1].str(),
        .offset = *offset,
        .original_line = std::string(input),
    };
}

}  // namespace hbfsim::ptx

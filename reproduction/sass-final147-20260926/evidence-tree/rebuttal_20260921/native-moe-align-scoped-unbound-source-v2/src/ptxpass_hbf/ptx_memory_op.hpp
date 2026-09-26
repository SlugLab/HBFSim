#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace hbfsim::ptx {

enum class AccessKind : std::uint32_t { Read = 0, Write = 1 };

struct PtxMemoryOp {
    std::string predicate;
    AccessKind kind;
    std::string opcode;
    std::string address_space;
    std::uint32_t bytes;
    std::string base_register;
    std::int64_t offset;
    std::string original_line;
};

std::optional<PtxMemoryOp> parse_memory_op(std::string_view line);

}  // namespace hbfsim::ptx

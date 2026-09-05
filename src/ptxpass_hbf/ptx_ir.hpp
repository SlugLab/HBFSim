#pragma once

#include "ptx_async_op.hpp"

#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace hbfsim::ptx {

enum class MemoryKind : std::uint32_t { None = 0, Load = 1, Store = 2,
                                        AtomicRmw = 3 };

struct SourceLocation {
    std::uint32_t line{0};
    std::uint32_t column{0};
};

struct MemoryInstruction {
    MemoryKind kind{MemoryKind::None};
    std::string state_space;
    std::vector<std::string> qualifiers;
    std::vector<std::string> value_operands;
    std::string address_base;
    std::int64_t signed_offset{0};
    std::uint32_t bytes{0};
};

struct Instruction {
    std::uint32_t instruction_id{0};
    SourceLocation location;
    std::string text;
    std::string opcode;
    std::string predicate;
    std::vector<std::string> operands;
    std::vector<std::string> defs;
    std::vector<std::string> uses;
    std::vector<std::string> branch_targets;
    std::optional<MemoryInstruction> memory;
    std::optional<AsyncInstruction> async;
};

struct BasicBlock {
    std::string label;
    std::vector<std::size_t> instructions;
};

struct Function {
    std::string name;
    std::vector<Instruction> instructions;
    std::vector<BasicBlock> blocks;
};

struct Module {
    std::vector<Function> functions;
    [[nodiscard]] const Function& function(std::string_view name) const;
};

class ParseError : public std::runtime_error {
  public:
    using std::runtime_error::runtime_error;
};

[[nodiscard]] Module parse_module(std::string_view ptx);

}  // namespace hbfsim::ptx

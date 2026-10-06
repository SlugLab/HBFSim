#pragma once
#include <array>
#include <cstdint>
namespace hbfsim::ucie::compact_consume_v1 {
inline constexpr std::uint32_t version=1,max_count=16,max_diagnostic=96;
enum class Stop:std::uint8_t { Complete=0,SemanticError=1 };
struct Request { std::uint64_t horizon{}; std::uint32_t module{},count{}; std::array<std::uint64_t,max_count> ids{}; };
struct Reply {
    std::uint32_t confirmed{}; Stop stop{Stop::Complete}; std::uint32_t failing_index{max_count},error_code{};
    std::array<std::uint32_t,4> results{}; // Actual successful core-return classes, not leader guesses.
    std::uint32_t diagnostic_size{}; std::array<char,max_diagnostic> diagnostic{};
};
} // namespace hbfsim::ucie::compact_consume_v1

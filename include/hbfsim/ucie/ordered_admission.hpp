#pragma once
// Internal admission contract, not a new public scheduling interface.
#include <hbfsim/ucie/stack_frontend.hpp>
#include <array>
#include <cstdint>
#include <limits>
#include <optional>
namespace hbfsim::ucie::ordered_admission_v1 {
constexpr std::uint32_t version=1,max_count=16,modules=16,max_diagnostic=1024;
constexpr std::uint32_t no_index=std::numeric_limits<std::uint32_t>::max();
struct Record {
    std::uint64_t token{};
    DeviceRead read{};
    std::optional<PacketBackingProof> proof;
};
enum class Stop : std::uint8_t { End=0,ReserveDenied=1,SubmitFalse=2,SemanticError=3 };
struct Item {
    std::uint64_t token{},request_id{};
    bool reserved{},accepted{};
};
struct Request {
    std::uint64_t horizon{};
    std::uint32_t module{},count{};
    std::array<Record,max_count> records;
};
struct Reply {
    std::uint32_t completed{};
    Stop stop{Stop::End};
    std::uint32_t failing_index{no_index},error_code{},diagnostic_size{};
    // Opaque diagnostic bytes; truncation makes no UTF-8 claim.
    std::array<char,max_diagnostic> diagnostic{};
    std::array<Item,max_count> items{};
};
} // namespace hbfsim::ucie::ordered_admission_v1

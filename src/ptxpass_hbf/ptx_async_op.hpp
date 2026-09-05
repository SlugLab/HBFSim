#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace hbfsim::ptx {

enum class TmaDirection : std::uint32_t {
    GlobalToShared,
    SharedToGlobal,
    Prefetch,
};
enum class TensorMode : std::uint32_t {
    Tile,
    Gather4,
    Scatter4,
    Im2col,
    Im2colWide,
};
enum class CompletionKind : std::uint32_t { Mbarrier, BulkGroup, None };

struct TmaInstruction {
    TmaDirection direction{TmaDirection::GlobalToShared};
    TensorMode mode{TensorMode::Tile};
    std::uint32_t dimensions{0};
    CompletionKind completion{CompletionKind::None};
    std::uint32_t cta_group{1};
    bool multicast{false};
    bool cache_hint{false};
    std::string reduction;
    std::string destination;
    std::string shared_address;
    std::string shared_state_space;
    std::string descriptor;
    std::string barrier;
    std::string multicast_mask;
    std::vector<std::string> coordinates;
    std::vector<std::string> im2col_offsets;
};

enum class BarrierOp : std::uint32_t {
    Init,
    Arrive,
    ArriveExpectTx,
    CompleteTx,
    TestWait,
    TryWait,
    Invalidate,
};
struct BarrierInstruction {
    BarrierOp op{BarrierOp::Init};
    std::string address;
    std::string phase;
    std::optional<std::uint32_t> expected_bytes;
};

enum class BulkGroupOp : std::uint32_t { Commit, Wait, WaitRead };
struct BulkGroupInstruction {
    BulkGroupOp op{BulkGroupOp::Commit};
    std::uint32_t pending_limit{0};
};

enum class TensorMapOp : std::uint32_t {
    Replace,
    CopyFence,
    FenceRelease,
    FenceAcquire,
    FenceAsync,
};
struct TensorMapInstruction {
    TensorMapOp op{TensorMapOp::Replace};
    std::string address;
    std::string source;
    std::string state_space;
    std::string field;
    std::string value;
    std::optional<std::uint32_t> ordinal;
};

using AsyncInstruction =
    std::variant<TmaInstruction, BarrierInstruction, BulkGroupInstruction,
                 TensorMapInstruction>;

[[nodiscard]] std::optional<AsyncInstruction> parse_async_instruction(
    const std::string& opcode, const std::vector<std::string>& operands);

}  // namespace hbfsim::ptx

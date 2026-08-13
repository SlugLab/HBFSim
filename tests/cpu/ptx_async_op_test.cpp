#include "ptx_async_op.hpp"
#include "ptx_ir.hpp"

#include <stdexcept>
#include <string>
#include <variant>
#include <vector>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

hbfsim::ptx::TmaInstruction tma(const std::string& opcode,
                                std::vector<std::string> operands)
{
    auto parsed = hbfsim::ptx::parse_async_instruction(opcode, operands);
    require(parsed && std::holds_alternative<hbfsim::ptx::TmaInstruction>(*parsed),
            "expected typed TMA instruction");
    return std::get<hbfsim::ptx::TmaInstruction>(*parsed);
}

void rejects(const std::string& opcode, std::vector<std::string> operands)
{
    try {
        (void)hbfsim::ptx::parse_async_instruction(opcode, operands);
    } catch (const hbfsim::ptx::ParseError&) {
        return;
    }
    throw std::runtime_error("malformed async instruction was accepted: " + opcode);
}

}  // namespace

int main()
{
    for (std::uint32_t dimensions = 1; dimensions <= 5; ++dimensions) {
        std::string coordinates = "%tm, {";
        for (std::uint32_t index = 0; index < dimensions; ++index) {
            if (index != 0) coordinates += ", ";
            coordinates += "%c" + std::to_string(index);
        }
        coordinates += "}";
        auto load = tma(
            "cp.async.bulk.tensor." + std::to_string(dimensions) +
                "d.shared::cta.global.tile.mbarrier::complete_tx::bytes",
            {"[%dst]", "[" + coordinates + "]", "[%bar]"});
        require(load.direction == hbfsim::ptx::TmaDirection::GlobalToShared &&
                    load.dimensions == dimensions &&
                    load.coordinates.size() == dimensions &&
                    load.completion == hbfsim::ptx::CompletionKind::Mbarrier,
                "typed tiled load differs");
        auto store = tma(
            "cp.async.bulk.tensor." + std::to_string(dimensions) +
                "d.global.shared::cta.tile.bulk_group",
            {"[" + coordinates + "]", "[%src]"});
        require(store.direction == hbfsim::ptx::TmaDirection::SharedToGlobal &&
                    store.completion == hbfsim::ptx::CompletionKind::BulkGroup,
                "typed tiled store differs");
    }

    auto multicast = tma(
        "cp.async.bulk.tensor.2d.tile::gather4.shared::cluster.global."
        "mbarrier::complete_tx::bytes.multicast::cluster.cta_group::2."
        "L2::cache_hint",
        {"[%dst]", "[%tm, {%x, %y0, %y1, %y2, %y3}]", "[%bar]",
         "%mask", "%policy"});
    require(multicast.mode == hbfsim::ptx::TensorMode::Gather4 &&
                multicast.multicast && multicast.cta_group == 2 &&
                multicast.cache_hint && multicast.multicast_mask == "%mask",
            "gather/multicast qualifiers differ");

    auto im2col = tma(
        "cp.async.bulk.tensor.5d.shared::cta.global.im2col."
        "mbarrier::complete_tx::bytes",
        {"[%dst]", "[%tm, {%a, %b, %c, %d, %e}]", "[%bar]",
         "{%ow, %oh, %od}"});
    require(im2col.mode == hbfsim::ptx::TensorMode::Im2col,
            "im2col mode differs");
    auto wide = tma(
        "cp.async.bulk.tensor.3d.im2col::w.shared::cluster.global."
        "mbarrier::complete_tx::bytes",
        {"[%dst]", "[%tm, {%a, %b, %c}]", "[%bar]", "{%halo, %offs}"});
    require(wide.mode == hbfsim::ptx::TensorMode::Im2colWide,
            "wide im2col mode differs");
    auto reduction = tma(
        "cp.reduce.async.bulk.tensor.2d.global.shared::cta.add.tile.bulk_group",
        {"[%tm, {%x, %y}]", "[%src]"});
    require(reduction.reduction == "add", "TMA reduction was not typed");

    using namespace hbfsim::ptx;
    auto barrier = parse_async_instruction(
        "mbarrier.arrive.expect_tx.release.cluster.b64",
        {"_", "[%bar]", "256"});
    require(barrier &&
                std::get<BarrierInstruction>(*barrier).op ==
                    BarrierOp::ArriveExpectTx &&
                std::get<BarrierInstruction>(*barrier).expected_bytes == 256,
            "expect_tx barrier differs");
    for (const auto& [opcode, expected] :
         std::vector<std::pair<std::string, BulkGroupOp>>{
             {"cp.async.bulk.commit_group", BulkGroupOp::Commit},
             {"cp.async.bulk.wait_group", BulkGroupOp::Wait},
             {"cp.async.bulk.wait_group.read", BulkGroupOp::WaitRead}}) {
        auto group = parse_async_instruction(
            opcode, expected == BulkGroupOp::Commit
                        ? std::vector<std::string>{}
                        : std::vector<std::string>{"2"});
        require(group && std::get<BulkGroupInstruction>(*group).op == expected,
                "bulk-group operation differs");
    }
    auto replace = parse_async_instruction(
        "tensormap.replace.tile.global_dim.global.b1024.b64",
        {"[%tm]", "2", "%value"});
    require(replace &&
                std::get<TensorMapInstruction>(*replace).ordinal == 2 &&
                std::get<TensorMapInstruction>(*replace).field == "global_dim",
            "TensorMap replace differs");
    auto release = parse_async_instruction(
        "fence.proxy.tensormap::generic.release.gpu", {});
    auto acquire = parse_async_instruction(
        "fence.proxy.tensormap::generic.acquire.gpu", {"[%tm]", "128"});
    auto async_fence = parse_async_instruction("fence.proxy.async.shared::cta", {});
    require(release && acquire && async_fence,
            "TensorMap/async fence was not typed");

    rejects("cp.async.bulk.tensor.6d.shared::cta.global.tile", {"[%d]"});
    rejects("cp.async.bulk.tensor.3d.tile::gather4.shared::cta.global",
            {"[%d]", "[%tm, {%x, %y, %z}]", "[%bar]"});
    rejects("cp.async.bulk.wait_group", {"8"});
    rejects("mbarrier.test_wait.shared.b64", {"%p", "[%bar]"});
    rejects("tensormap.replace.tile.global_dim.global.b1024.b64",
            {"[%tm]", "7", "%value"});

    const auto module = parse_module(R"ptx(
.version 9.0
.target sm_120
.address_size 64
.visible .entry kernel() {
  cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
      [%rd1], [%rd2, {%r1}], [%rd3];
  cp.async.bulk.commit_group;
  ret;
})ptx");
    require(module.function("kernel").instructions[0].async.has_value(),
            "PTX IR did not attach typed async operation");
    return 0;
}

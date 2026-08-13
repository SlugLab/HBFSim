#include "ptx_analysis.hpp"
#include "ptx_ir.hpp"

#include <cstdint>
#include <set>
#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

const char* kPtx = R"ptx(
.version 9.0
.target sm_120
.address_size 64

.visible .entry straight()
{
    .reg .b32 %r<16>;
    .reg .b64 %rd<8>;
    ld.global.u32 %r1, [%rd1];
    ld.global.u32 %r2, [%rd1+4];
    add.u32 %r3, %r1, 1;
    add.u32 %r4, %r1, %r2;
    st.global.u32 [%rd2], %r3;
    fence.release.gpu;
    st.global.u32 [%rd2+4], %r4;
    ret;
}

.visible .entry diamond()
{
    .reg .pred %p<2>;
    .reg .b32 %r<16>;
    .reg .b64 %rd<8>;
    ld.global.u32 %r1, [%rd1];
    @%p1 bra right;
left:
    add.u32 %r2, %r1, 1;
    bra join;
right:
    sub.u32 %r3, %r1, 1;
join:
    ret;
}

.visible .entry ambiguous()
{
    .reg .pred %p<2>;
    .reg .b32 %r<16>;
    .reg .b64 %rd<8>;
    @%p1 bra second;
first:
    ld.global.u32 %r1, [%rd1];
    bra join;
second:
    ld.global.u32 %r1, [%rd2];
join:
    add.u32 %r2, %r1, 1;
    ret;
}

.visible .entry predicated_definition()
{
    .reg .pred %p<2>;
    .reg .b32 %r<16>;
    .reg .b64 %rd<8>;
    mov.u32 %r1, 0;
    @%p1 ld.global.u32 %r1, [%rd1];
    add.u32 %r2, %r1, 1;
    ret;
}

.visible .entry loop_and_dead()
{
    .reg .pred %p<2>;
    .reg .b32 %r<16>;
    .reg .b64 %rd<8>;
    ld.global.u32 %r1, [%rd1];
loop:
    add.u32 %r2, %r1, 1;
    @%p1 bra loop;
    bra done;
dead:
    ld.global.u32 %r8, [%rd2];
done:
    ret;
}

.visible .entry overwritten()
{
    .reg .b32 %r<16>;
    .reg .b64 %rd<8>;
    ld.global.u32 %r1, [%rd1];
    mov.u32 %r1, 7;
    add.u32 %r2, %r1, 1;
    ret;
}
)ptx";

std::uint32_t id_of(const hbfsim::ptx::Function& function,
                    const std::string& opcode, std::size_t occurrence = 0)
{
    for (const auto& instruction : function.instructions) {
        if (instruction.opcode == opcode) {
            if (occurrence == 0) {
                return instruction.instruction_id;
            }
            --occurrence;
        }
    }
    throw std::runtime_error("missing opcode: " + opcode);
}

}  // namespace

int main()
{
    const auto module = hbfsim::ptx::parse_module(kPtx);

    const auto& straight = module.function("straight");
    const auto straight_plan = hbfsim::ptx::analyze_futures(straight);
    const auto load_a = id_of(straight, "ld.global.u32", 0);
    const auto load_b = id_of(straight, "ld.global.u32", 1);
    const auto add_a = id_of(straight, "add.u32", 0);
    const auto add_b = id_of(straight, "add.u32", 1);
    const auto store_a = id_of(straight, "st.global.u32", 0);
    const auto store_b = id_of(straight, "st.global.u32", 1);
    const auto fence = id_of(straight, "fence.release.gpu");
    const auto ret = id_of(straight, "ret");
    require(straight_plan.first_consumers.at(load_a) ==
                std::set<std::uint32_t>({add_a}),
            "straight load did not wait at its first consumer");
    require(straight_plan.first_consumers.at(load_b) ==
                std::set<std::uint32_t>({add_b}),
            "second load did not remain outstanding until use");
    require(straight_plan.drain_points.at(store_a) ==
                std::set<std::uint32_t>({fence}),
            "store was not drained by the release fence");
    require(straight_plan.drain_points.at(store_b) ==
                std::set<std::uint32_t>({ret}),
            "final store was not drained at return");
    require(straight_plan.maximum_live.thread_futures == 2 &&
                straight_plan.maximum_live.warp_futures == 64,
            "future liveness budget is wrong");
    require(straight_plan.exact_safe(), "straight flow was rejected");

    const auto& diamond = module.function("diamond");
    const auto diamond_plan = hbfsim::ptx::analyze_futures(diamond);
    const auto diamond_load = id_of(diamond, "ld.global.u32");
    require(diamond_plan.first_consumers.at(diamond_load) ==
                std::set<std::uint32_t>({id_of(diamond, "add.u32"),
                                         id_of(diamond, "sub.u32")}),
            "diamond did not retain one first consumer per path");
    require(diamond_plan.cfg.predecessors.at(3).size() == 2,
            "diamond join predecessors are wrong");

    const auto& ambiguous = module.function("ambiguous");
    const auto ambiguous_plan = hbfsim::ptx::analyze_futures(ambiguous);
    const auto ambiguous_use = id_of(ambiguous, "add.u32");
    require(!ambiguous_plan.exact_safe(), "ambiguous join was accepted");
    require(ambiguous_plan.reason(ambiguous_use) ==
                "ambiguous_future_definition",
            "ambiguous join has the wrong rejection reason");

    const auto& predicated = module.function("predicated_definition");
    const auto predicated_plan = hbfsim::ptx::analyze_futures(predicated);
    const auto predicated_use = id_of(predicated, "add.u32");
    require(predicated_plan.reason(predicated_use) ==
                "ambiguous_future_definition",
            "predicated definition did not preserve its false path");

    const auto& loop = module.function("loop_and_dead");
    const auto loop_plan = hbfsim::ptx::analyze_futures(loop);
    require(loop_plan.exact_safe(), "loop-carried ready state was rejected");
    require(loop_plan.first_consumers.at(id_of(loop, "ld.global.u32", 0)) ==
                std::set<std::uint32_t>({id_of(loop, "add.u32")}),
            "loop first consumer is unstable");
    require(!loop_plan.cfg.reachable.at(3), "dead block was marked reachable");
    require(loop_plan.first_consumers.count(
                id_of(loop, "ld.global.u32", 1)) == 0,
            "unreachable future affected the plan");

    const auto& overwritten = module.function("overwritten");
    const auto overwritten_plan = hbfsim::ptx::analyze_futures(overwritten);
    const auto overwritten_load = id_of(overwritten, "ld.global.u32");
    require(overwritten_plan.first_consumers.count(overwritten_load) == 0,
            "overwritten future was attached to a later register use");
    require(overwritten_plan.drain_points.at(overwritten_load) ==
                std::set<std::uint32_t>({id_of(overwritten, "ret")}),
            "unused future was not made terminal at exit");
    return 0;
}

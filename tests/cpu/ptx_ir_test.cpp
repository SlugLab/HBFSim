#include "ptx_ir.hpp"

#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

std::string fixture()
{
    std::ifstream input("tests/fixtures/ptx/async_cfg.ptx");
    require(input.good(), "unable to open async CFG fixture");
    return {std::istreambuf_iterator<char>(input), {}};
}

const hbfsim::ptx::Instruction& find_opcode(
    const hbfsim::ptx::Function& function, const std::string& opcode)
{
    for (const auto& instruction : function.instructions) {
        if (instruction.opcode == opcode) {
            return instruction;
        }
    }
    throw std::runtime_error("opcode was not parsed: " + opcode);
}

}  // namespace

int main()
{
    const auto module = hbfsim::ptx::parse_module(fixture());
    const auto& kernel = module.function("async_cfg");
    require(kernel.blocks.size() == 4, "wrong basic-block count");
    require(kernel.blocks.at(0).label == "async_cfg$entry",
            "entry block has no stable label");
    require(kernel.blocks.at(2).label == "load_path",
            "load label was not preserved");
    require(kernel.blocks.at(3).label == "join",
            "join label was not preserved");

    const auto& load = find_opcode(
        kernel, "ld.global.acquire.gpu.L2::128B.v2.u32");
    require(load.instruction_id != 0, "load has no stable instruction ID");
    require(load.location.line == 24 && load.location.column == 5,
            "load source location is wrong");
    require(load.predicate == "@%p1", "load predicate is wrong");
    require(load.defs == std::vector<std::string>({"%r4", "%r5"}),
            "vector load definitions are wrong");
    require(load.uses == std::vector<std::string>({"%p1", "%rd1"}),
            "load uses are wrong");
    require(load.memory.has_value(), "global load has no memory record");
    require(load.memory->kind == hbfsim::ptx::MemoryKind::Load,
            "global load has the wrong kind");
    require(load.memory->state_space == "global",
            "global load has the wrong state space");
    require(load.memory->signed_offset == -16 && load.memory->bytes == 8,
            "global vector load shape is wrong");
    require(load.memory->qualifiers ==
                std::vector<std::string>({"acquire", "gpu", "L2::128B",
                                          "v2", "u32"}),
            "load qualifiers are not ordered");

    const auto& atomic =
        find_opcode(kernel, "atom.global.acq_rel.gpu.add.u32");
    require(atomic.memory.has_value() &&
                atomic.memory->kind == hbfsim::ptx::MemoryKind::AtomicRmw,
            "atomic RMW was not typed");
    require(atomic.defs == std::vector<std::string>({"%r6"}),
            "atomic definition is wrong");
    require(atomic.uses == std::vector<std::string>({"%rd1", "%r4"}),
            "atomic uses are wrong");
    require(atomic.memory->signed_offset == 32 && atomic.memory->bytes == 4,
            "atomic address or width is wrong");

    const auto& store = find_opcode(
        kernel, "st.volatile.global.release.gpu.v2.u32");
    require(store.defs.empty(), "store unexpectedly defines a register");
    require(store.uses ==
                std::vector<std::string>({"%rd2", "%r4", "%r5"}),
            "store uses are wrong");
    require(store.memory.has_value() &&
                store.memory->kind == hbfsim::ptx::MemoryKind::Store &&
                store.memory->bytes == 8,
            "store memory record is wrong");

    const auto nvcc_offset = hbfsim::ptx::parse_module(R"PTX(
.version 9.0
.target sm_120
.address_size 64
.visible .entry nvcc_offset() {
    ld.global.u64 %rd1, [%rd2+-32];
    st.global.u64 [%rd2- -16], %rd1;
    ret;
}
)PTX");
    const auto& offset_kernel = nvcc_offset.function("nvcc_offset");
    require(offset_kernel.instructions.at(0).memory.has_value() &&
                offset_kernel.instructions.at(0).memory->signed_offset == -32,
            "nvcc plus-negative address offset was not normalized");
    require(offset_kernel.instructions.at(1).memory.has_value() &&
                offset_kernel.instructions.at(1).memory->signed_offset == 16,
            "nvcc minus-negative address offset was not normalized");

    const auto& branch = find_opcode(kernel, "bra");
    require(branch.predicate == "@%p1" &&
                branch.branch_targets == std::vector<std::string>({"load_path"}),
            "conditional branch metadata is wrong");

    const auto qualified = hbfsim::ptx::parse_module(R"PTX(
.version 9.0
.target sm_120
.address_size 64
.visible .entry qualified_branch() {
    @%p1 bra.uni done;
done:
    ret.uni;
}
)PTX");
    const auto& qualified_kernel = qualified.function("qualified_branch");
    require(qualified_kernel.blocks.size() == 2,
            "qualified branch did not terminate its basic block");
    const auto& qualified_branch = qualified_kernel.instructions.front();
    require(qualified_branch.opcode == "bra.uni" &&
                qualified_branch.branch_targets ==
                    std::vector<std::string>({"done"}),
            "qualified branch target metadata is wrong");

    const auto adjacent_labels = hbfsim::ptx::parse_module(R"PTX(
.version 9.0
.target sm_120
.address_size 64
.visible .entry adjacent_labels() {
    bra first;
first:
second:
    ret;
}
)PTX");
    const auto& adjacent_kernel = adjacent_labels.function("adjacent_labels");
    require(adjacent_kernel.blocks.size() == 3,
            "adjacent labels were collapsed into one basic block");
    require(adjacent_kernel.blocks.at(1).label == "first" &&
                adjacent_kernel.blocks.at(1).instructions.empty() &&
                adjacent_kernel.blocks.at(2).label == "second",
            "adjacent label aliases were not preserved");

    const auto inline_scope = hbfsim::ptx::parse_module(R"PTX(
.version 9.0
.target sm_120
.address_size 64
.visible .func helper() {
    { cvt.f32.f16 %f1, %rs1;}
    ret;
}
.visible .entry after_helper() {
    mov.u32 %r1, 7;
    ret;
}
)PTX");
    require(find_opcode(inline_scope.function("helper"), "cvt.f32.f16")
                    .defs == std::vector<std::string>({"%f1"}) &&
                find_opcode(inline_scope.function("after_helper"), "mov.u32")
                    .defs == std::vector<std::string>({"%r1"}),
            "NVCC one-line scoped instruction consumed the next function");

    bool missing_rejected = false;
    try {
        (void)module.function("missing");
    } catch (const std::out_of_range&) {
        missing_rejected = true;
    }
    require(missing_rejected, "missing function lookup did not fail");

    bool malformed_rejected = false;
    try {
        (void)hbfsim::ptx::parse_module(
            ".visible .entry broken() { ld.global.u32 %r1, [%rd1];");
    } catch (const hbfsim::ptx::ParseError&) {
        malformed_rejected = true;
    }
    require(malformed_rejected, "unterminated function was accepted");
    return 0;
}

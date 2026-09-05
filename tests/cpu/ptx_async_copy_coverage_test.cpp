// `cp.async` and the bulk tensor copy instructions read global memory without
// going through a register. Before this test, neither the rewrite pattern in
// src/ptxpass_hbf/ptx_memory_op.cpp nor the unsupported pattern in
// src/ptxpass_hbf/transform.cpp matched them, so an HBF address reached by one
// of them produced no entry of any kind: no modeled delay, and no
// unsupported-list entry either. The design goal on line 27 of
// docs/superpowers/specs/2026-08-09-hbfsim-hybrid-design.md is to fail closed
// whenever an HBF address could reach an uninstrumented or unsupported memory
// operation, which needs the instruction to be visible first.
//
// The point this test pins down is narrow: an asynchronous copy that names
// .global has to be counted, and the synchronisation instructions of the same
// family, which touch no memory, must not be.

#include "ptx_memory_op.hpp"
#include "transform.hpp"

#include <algorithm>
#include <cstdio>
#include <string>

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            std::printf("failed at line %d: %s\n", __LINE__, #condition);      \
            return __LINE__;                                                   \
        }                                                                      \
    } while (false)

namespace {

// Wraps one instruction in the smallest kernel the pass will walk.
std::string kernel_with(const std::string& instruction)
{
    return ".version 8.0\n"
           ".target sm_120\n"
           ".address_size 64\n"
           ".visible .entry probe(.param .u64 probe_param_0)\n"
           "{\n"
           "    .reg .b32 %r<8>;\n"
           "    .reg .b64 %rd<8>;\n"
           "    .reg .f32 %f<8>;\n"
           "    ld.param.u64 %rd1, [probe_param_0];\n" +
           std::string{"    "} + instruction + "\n" +
           "    ret;\n"
           "}\n";
}

hbfsim::ptx::TransformResult run(const std::string& instruction)
{
    hbfsim::ptx::TransformRequest request{};
    request.full_ptx = kernel_with(instruction);
    return hbfsim::ptx::transform_ptx(request);
}

// The kernel body needs a `ld.param` to load the pointer, and `ld.param`
// itself matches the unsupported pattern. Counting the absolute total would
// therefore report every instruction as unsupported, so each case is measured
// as the increment over the same kernel without the instruction under test.
std::uint64_t baseline_unsupported()
{
    static const auto value =
        run("ret;").coverage.unsupported_instructions;
    return value;
}

std::uint64_t baseline_rewritten()
{
    static const auto value = run("ret;").coverage.rewritten_instructions;
    return value;
}

bool counted_unsupported(const std::string& instruction)
{
    return run(instruction).coverage.unsupported_instructions >
           baseline_unsupported();
}

}  // namespace

int main()
{
    // .loc is a newline-terminated directive, not an unfinished instruction.
    // Debug line information must not hide the following HBF access.
    CHECK(counted_unsupported(
        ".loc 1 10 0\n    atom.global.add.u32 %r1, [%rd1], 1;"));
    CHECK(counted_unsupported(
        ".loc 1 10 0\n    cp.async.ca.shared.global [%r1], [%rd1], 4;"));
#if defined(HBFSIM_TEST_DEVICE_HELPER_PTX)
    {
        const auto result = run(
            ".loc 1 10 0\n    ld.global.u32 %r1, [%rd1];");
        CHECK(result.coverage.rewritten_instructions == 1);
    }
#endif

    // Reads global memory into shared memory. Must be visible.
    CHECK(counted_unsupported(
        "cp.async.ca.shared.global [%r1], [%rd1], 4;"));
    CHECK(counted_unsupported(
        "cp.async.cg.shared.global [%r1], [%rd1], 16;"));

    // Non-tensor bulk copy from global. Must be visible.
    CHECK(counted_unsupported(
        "cp.async.bulk.shared::cluster.global.mbarrier::complete_tx::bytes "
        "[%r1], [%rd1], %r2, [%r3];"));

    // Reduction form that reads global and is not a tensor copy. Must be
    // visible.
    CHECK(counted_unsupported(
        "cp.reduce.async.bulk.global.shared::cta.bulk_group.add.u32 "
        "[%rd1], [%r1], %r2;"));

    // The bulk TENSOR families are counted here too. Branch
    // feature/sm120-exact-stage1 models them in parse_tma, and an earlier
    // version of this test required them NOT to be counted so the two would
    // not collide on merge. That was wrong: hybrid has no parse_tma, so the
    // exclusion left them neither modeled nor reported, preserving the exact
    // hole this file exists to close. They stay counted until that branch
    // merges, and the merge commit removes them from both sides at once.
    CHECK(counted_unsupported(
        "cp.async.bulk.tensor.2d.shared::cluster.global.mbarrier::"
        "complete_tx::bytes [%r1], [tmap, {%r2,%r3}], [%r4];"));
    CHECK(counted_unsupported(
        "cp.reduce.async.bulk.tensor.2d.global.shared::cta.add.tile."
        "bulk_group [tmap, {%r2,%r3}], [%r1];"));
    CHECK(counted_unsupported(
        "cp.async.bulk.prefetch.tensor.2d.L2.global.tile "
        "[tmap, {%r2,%r3}];"));

    // Same family, but pure synchronisation: these touch no memory and must
    // not be reported as unsupported memory operations.
    CHECK(!counted_unsupported("cp.async.commit_group;"));
    CHECK(!counted_unsupported("cp.async.wait_group 0;"));
    CHECK(!counted_unsupported("cp.async.wait_all;"));
    CHECK(!counted_unsupported("cp.async.bulk.commit_group;"));
    CHECK(!counted_unsupported("cp.async.bulk.wait_group.read 0;"));

    // Predication and trailing comments do not change the memory operation.
    CHECK(counted_unsupported(
        "@%p1 cp.async.ca.shared.global [%r1], [%rd1], 4;"));
    CHECK(counted_unsupported(
        "@!%p1 cp.async.ca.shared.global [%r1], [%rd1], 4; // tail"));

    // The instructions the pass already handled must keep their old
    // classification: an ordinary global load is still rewritten and is not on
    // the unsupported list, and a shared load is still unsupported.
    // An ordinary global load must still be rewritten rather than counted
    // here. That case is not exercised in this file: rewriting makes
    // transform_ptx append the embedded device helper, which only exists in a
    // CUDA build, so the assertion lives in ptx_transform_test instead. Every
    // case in this file is chosen so that nothing is rewritten.
    {
        const auto result = run("ld.shared.u32 %r1, [%r2];");
        CHECK(result.coverage.rewritten_instructions == baseline_rewritten());
        CHECK(result.coverage.unsupported_instructions ==
              baseline_unsupported() + 1);
    }
    {
        const auto result = run("atom.global.add.u32 %r1, [%rd1], 1;");
        CHECK(result.coverage.rewritten_instructions == baseline_rewritten());
        CHECK(result.coverage.unsupported_instructions ==
              baseline_unsupported() + 1);
    }

    // An asynchronous copy that never names global memory is a shared-to-shared
    // move and is not an HBF access.
    CHECK(!counted_unsupported(
        "cp.async.bulk.shared::cluster.shared::cta [%r1], [%r2], %r3;"));

    // A statement split across physical lines has to be seen whole. Neither
    // half matches the pattern on its own, so scanning per line lets the
    // access through unreported; and if the same kernel also holds an ordinary
    // global load, the module is still marked instrumented and the launch
    // proceeds with an access nothing recorded.
    {
        hbfsim::ptx::TransformRequest request{};
        request.full_ptx =
            ".version 8.0\n.target sm_120\n.address_size 64\n"
            ".visible .entry probe(.param .u64 probe_param_0)\n"
            "{\n"
            "    .reg .b32 %r<8>;\n"
            "    .reg .b64 %rd<8>;\n"
            "    ld.param.u64 %rd1, [probe_param_0];\n"
            "    cp.async.bulk.tensor.2d.shared::cluster.global.mbarrier::"
            "complete_tx::bytes\n"
            "        [%r1], [tmap, {%r2,%r3}], [%r4];\n"
            "    ret;\n"
            "}\n";
        const auto split = hbfsim::ptx::transform_ptx(request);

        hbfsim::ptx::TransformRequest one_line{};
        one_line.full_ptx = request.full_ptx;
        const auto position = one_line.full_ptx.find("bytes\n");
        one_line.full_ptx.replace(position + 5, 10, " ");
        const auto joined = hbfsim::ptx::transform_ptx(one_line);

        // Written on one line or on two, the same statement must be counted
        // the same number of times.
        CHECK(split.coverage.unsupported_instructions ==
              joined.coverage.unsupported_instructions);
        CHECK(split.coverage.unsupported_instructions >
              baseline_unsupported());
    }

    // Strip comments one physical line at a time. If the first `//` in the
    // accumulated text were allowed to hide all later lines, both of these
    // statements would remain open forever and escape the unsupported list.
    for (const auto& commented_statement : {
             std::string{
                 "    cp.async.bulk.tensor.2d.shared::cluster.global."
                 "mbarrier::complete_tx::bytes // continue\n"
                 "        [%r1], [tmap, {%r2,%r3}], [%r4]; // tail\n"},
             std::string{
                 "    cp.async.bulk.tensor.2d.shared::cluster.global."
                 "mbarrier::complete_tx::bytes /* begin\n"
                 "        still a comment */\n"
                 "        [%r1], [tmap, {%r2,%r3}], [%r4];\n"},
         }) {
        hbfsim::ptx::TransformRequest request{};
        request.full_ptx =
            ".version 8.0\n.target sm_120\n.address_size 64\n"
            ".visible .entry probe(.param .u64 probe_param_0)\n"
            "{\n"
            "    .reg .b32 %r<8>;\n"
            "    .reg .b64 %rd<8>;\n"
            "    ld.param.u64 %rd1, [probe_param_0];\n" +
            commented_statement +
            "    ret;\n"
            "}\n";
        const auto result = hbfsim::ptx::transform_ptx(request);
        CHECK(result.coverage.unsupported_instructions ==
              baseline_unsupported() + 1);
    }

    // Declarations, labels, and braces terminate their own grammar units and
    // must not be glued to the instruction which follows them.
    {
        hbfsim::ptx::TransformRequest request{};
        request.full_ptx =
            ".version 8.0\n.target sm_120\n.address_size 64\n"
            ".visible .entry probe(.param .u64 probe_param_0)\n"
            "{\n"
            "    .reg .b32 %r<8>;\n"
            "    .reg .b64 %rd<8>;\n"
            "    ld.param.u64 %rd1, [probe_param_0];\n"
            "$L_probe:\n"
            "    {\n"
            "    cp.async.ca.shared.global [%r1], [%rd1], 4;\n"
            "    }\n"
            "    ret;\n"
            "}\n";
        const auto result = hbfsim::ptx::transform_ptx(request);
        CHECK(result.coverage.unsupported_instructions ==
              baseline_unsupported() + 1);
    }

#if defined(HBFSIM_TEST_DEVICE_HELPER_PTX)
    // Rewriting an ordinary load does not make a mixed kernel fully covered
    // when the same kernel contains an unsupported asynchronous copy.
    {
        hbfsim::ptx::TransformRequest request{};
        request.full_ptx =
            ".version 8.0\n.target sm_120\n.address_size 64\n"
            ".visible .entry probe(.param .u64 probe_param_0)\n"
            "{\n"
            "    .reg .b32 %r<8>;\n"
            "    .reg .b64 %rd<8>;\n"
            "    ld.param.u64 %rd1, [probe_param_0];\n"
            "    ld.global.u32 %r1, [%rd1];\n"
            "    cp.async.bulk.tensor.2d.shared::cluster.global."
            "mbarrier::complete_tx::bytes\n"
            "        [%r1], [tmap, {%r2,%r3}], [%r4];\n"
            "    ret;\n"
            "}\n";
        const auto result = hbfsim::ptx::transform_ptx(request);
        CHECK(result.coverage.rewritten_instructions == 1);
        CHECK(result.coverage.unsupported_instructions >
              baseline_unsupported());
    }
#endif

    // The recorded opcode has to name the instruction, so the coverage record
    // says which operation was refused.
    {
        const auto result = run("cp.async.ca.shared.global [%r1], [%rd1], 4;");
        const auto named = std::any_of(
            result.coverage.unsupported_opcodes.begin(),
            result.coverage.unsupported_opcodes.end(),
            [](const std::string& opcode) {
                return opcode.rfind("cp.async", 0) == 0;
            });
        CHECK(named);
    }

    return 0;
}

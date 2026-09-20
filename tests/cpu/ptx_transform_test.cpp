#include "ptx_memory_op.hpp"
#include "transform.hpp"

#include <cassert>
#include <cstdint>
#include <fstream>
#include <sstream>
#include <string>

#define HBFSIM_STRINGIFY_DETAIL(value) #value
#define HBFSIM_STRINGIFY(value) HBFSIM_STRINGIFY_DETAIL(value)

#if !defined(HBFSIM_TEST_DEVICE_PTX_ARCHITECTURE)
#define HBFSIM_TEST_DEVICE_PTX_ARCHITECTURE 120
#endif

#define CHECK(condition)                                                       \
    do {                                                                       \
        if (!(condition)) {                                                    \
            return __LINE__;                                                   \
        }                                                                      \
    } while (false)

namespace {

std::string configured_ptx(std::string ptx)
{
    const std::string default_target = ".target sm_120";
    const std::string configured_target =
        ".target sm_" +
        std::string{HBFSIM_STRINGIFY(HBFSIM_TEST_DEVICE_PTX_ARCHITECTURE)};
    if (const auto position = ptx.find(default_target);
        position != std::string::npos) {
        ptx.replace(position, default_target.size(), configured_target);
    }
    return ptx;
}

std::string read_fixture(const std::string& name)
{
    std::ifstream input("tests/fixtures/ptx/" + name);
    assert(input);
    return configured_ptx(std::string{
        std::istreambuf_iterator<char>{input},
        std::istreambuf_iterator<char>{},
    });
}

}  // namespace

int main()
{
    const auto op = hbfsim::ptx::parse_memory_op(
        "@%p1 ld.global.v2.u32 {%r4,%r5}, [%rd8+16];");
    assert(op.has_value());
    assert(op->predicate == "@%p1");
    assert(op->kind == hbfsim::ptx::AccessKind::Read);
    assert(op->bytes == 8);
    assert(op->base_register == "%rd8");
    assert(op->offset == 16);

    const auto store = hbfsim::ptx::parse_memory_op(
        "st.global.release.gpu.u64 [%rd2-0x20], %rd3; // payload");
    assert(store.has_value());
    assert(store->kind == hbfsim::ptx::AccessKind::Write);
    assert(store->bytes == 8);
    assert(store->offset == -32);

    const auto nc = hbfsim::ptx::parse_memory_op(
        "ld.global.nc.L2::128B.v4.b32 {%r0,%r1,%r2,%r3}, [%rd4];");
    assert(nc.has_value());
    assert(nc->bytes == 16);

    const auto volatile_load = hbfsim::ptx::parse_memory_op(
        "ld.volatile.global.u8 %rd7, [%rd8+4096];");
    assert(volatile_load.has_value());
    assert(volatile_load->kind == hbfsim::ptx::AccessKind::Read);
    assert(volatile_load->bytes == 1);
    assert(volatile_load->base_register == "%rd8");
    assert(volatile_load->offset == 4096);
    const auto volatile_negative = hbfsim::ptx::parse_memory_op(
        "ld.volatile.global.u8 %rd7, [%rd8+-32768];");
    assert(volatile_negative.has_value());
    assert(volatile_negative->offset == -32768);

    assert(!hbfsim::ptx::parse_memory_op("atom.global.add.u32 %r1, [%rd2], 1;"));
    assert(!hbfsim::ptx::parse_memory_op("ld.u32 %r1, [%rd2];"));
    assert(!hbfsim::ptx::parse_memory_op("ld.global.u32 %r1, [%r2+%r3];"));

    const std::string spoofed_helper = configured_ptx(R"ptx(.version 8.7
.target sm_120
.address_size 64
.visible .const .align 4 .u32 __hbfsim_device_helper_marker = 0x48424632;
.func (.param .align 8 .b8 result[16]) __hbfsim_resolve(
    .param .b64 address, .param .b32 bytes, .param .b32 operation)
{
    ret;
}
.func __hbfsim_fault(.param .b32 status)
{
    ret;
}
.visible .entry spoofed_kernel(.param .u64 ptr)
{
    .reg .b32 %r1;
    .reg .b64 %rd1;
    ld.param.u64 %rd1, [ptr];
    ld.global.u32 %r1, [%rd1];
    ret;
}
)ptx");
    bool spoof_rejected = false;
    try {
        (void)hbfsim::ptx::transform_ptx({
            .full_ptx = spoofed_helper,
            .to_patch_kernel = "spoofed_kernel",
        });
    } catch (const std::runtime_error&) {
        spoof_rejected = true;
    }
    CHECK(spoof_rejected);

#if defined(HBFSIM_TEST_DEVICE_HELPER_PTX)
    const auto result = hbfsim::ptx::transform_ptx({
        .full_ptx = read_fixture("supported.ptx"),
        .to_patch_kernel = "kernel",
    });
    assert(result.modified);
    assert(result.coverage.rewritten_instructions == 3);
    assert(result.output_ptx.find("__hbfsim_resolve") != std::string::npos);
    assert(result.output_ptx.find("__hbfsim_fault") != std::string::npos);
    assert(result.output_ptx.find("@!%p1 bra $L__hbfsim_skip_") !=
           std::string::npos);
    assert(result.output_ptx.find("[%hbfsim_addr_") != std::string::npos);

    // The unsupported scan lists inline asm because the transform cannot see
    // through it. The pattern used to be `asm\s*\(`, which does not match
    // `asm volatile (` -- the form almost everything actually writes. A
    // comment added alongside the per-access counting claimed the scan caught
    // inline asm "in its own right" and therefore that a semicolon inside an
    // asm string was harmless; an independent review measured that claim and
    // found it false. The pattern now accepts the volatile form, which makes
    // the comment true rather than the comment being softened to match a gap.
    {
        const auto inline_asm = configured_ptx(R"ptx(.version 8.7
.target sm_120
.address_size 64

.visible .entry asm_kernel()
{
    .reg .b32 %r<4>;
    asm volatile ("mov.u32 %0, %%laneid;" : "=r"(%r1));
    ret;
}
)ptx");
        const auto scanned = hbfsim::ptx::transform_ptx({
            .full_ptx = inline_asm,
            .to_patch_kernel = "asm_kernel",
        });
        CHECK(!scanned.modified);
        CHECK(scanned.coverage.unsupported_instructions >= 1);
    }

    // fail_open_global.ptx above cannot reach the case the fix is really for.
    // Every access in it is unparseable, so `modified` is false, and
    // plugin.cpp computes `instrumented = modified && relevant_unsupported
    // .empty()` -- false either way, fixed or not. An independent review built
    // the missing case and measured it: one rewritable access plus one that is
    // not. Before the fix that reported 1 rewritten and 1 unsupported, and the
    // single unsupported opcode was `ld.param.u64`, which plugin.cpp filters
    // out as irrelevant -- so `relevant_unsupported` was empty, `instrumented`
    // was true, and the second global access ran at native speed inside a
    // kernel reported as fully instrumented. After the fix `ld.global.u32`
    // joins the list, which is what makes `instrumented` false.
    const auto mixed = hbfsim::ptx::transform_ptx({
        .full_ptx = read_fixture("mixed_rewrite_and_fail.ptx"),
        .to_patch_kernel = "mixed_kernel",
    });
    CHECK(mixed.modified);
    CHECK(mixed.coverage.rewritten_instructions == 1);
    CHECK(mixed.coverage.unsupported_instructions == 1);
    bool mixed_reports_global = false;
    for (const auto& opcode : mixed.coverage.unsupported_opcodes) {
        if (opcode.starts_with("ld.global")) { mixed_reports_global = true; }
    }
    CHECK(mixed_reports_global);

    const std::string production_ptx = configured_ptx(R"ptx(.version 8.7
.target sm_120
.address_size 64
// __hbfsim_device_helper_marker is not a declaration.

.visible .entry production_kernel(
    .param .u64 production_ptr
)
{
    .reg .b32 %r1;
    .reg .b64 %rd1;
    ld.param.u64 %rd1, [production_ptr];
    ld.global.u32 %r1, [%rd1];
    ret;
}
)ptx");
    const auto self_contained = hbfsim::ptx::transform_ptx({
        .full_ptx = production_ptx,
        .to_patch_kernel = "production_kernel",
    });
    CHECK(self_contained.modified);
    const std::string marker_declaration =
        ".visible .const .align 4 .u32 __hbfsim_device_helper_marker";
    CHECK(self_contained.output_ptx.find(
              marker_declaration) != std::string::npos);
    CHECK(self_contained.output_ptx.find(
              ".visible .global .align 8 .u64 __hbfsim_control") !=
          std::string::npos);
    CHECK(self_contained.output_ptx.find(
              ".visible .func") != std::string::npos);
    CHECK(self_contained.output_ptx.find("__hbfsim_resolve(") !=
          std::string::npos);

    const auto helper_begin = self_contained.output_ptx.find(
        "// HBFSim embedded device helper");
    const auto helper_end = self_contained.output_ptx.find(
        ".visible .entry production_kernel");
    CHECK(helper_begin != std::string::npos);
    CHECK(helper_end != std::string::npos);
    auto commented_helper_spoof = production_ptx;
    const auto spoof_kernel = commented_helper_spoof.find(
        ".visible .entry production_kernel");
    CHECK(spoof_kernel != std::string::npos);
    const auto commented_helper =
        "/*\n" + self_contained.output_ptx.substr(
                       helper_begin, helper_end - helper_begin) +
        R"ptx(*/
.visible .const .align 4 .u32 __hbfsim_device_helper_marker = 0x48424632;
.func (.param .align 8 .b8 result[16]) __hbfsim_resolve(
    .param .b64 address, .param .b32 bytes, .param .b32 operation)
{
    ret;
}
.func __hbfsim_fault(.param .b32 status)
{
    ret;
}
)ptx";
    commented_helper_spoof.insert(spoof_kernel, commented_helper);
    bool commented_spoof_rejected = false;
    try {
        (void)hbfsim::ptx::transform_ptx({
            .full_ptx = commented_helper_spoof,
            .to_patch_kernel = "production_kernel",
        });
    } catch (const std::runtime_error&) {
        commented_spoof_rejected = true;
    }
    CHECK(commented_spoof_rejected);

    const auto twice = hbfsim::ptx::transform_ptx({
        .full_ptx = self_contained.output_ptx,
        .to_patch_kernel = "production_kernel",
        .trusted_existing_helper = true,
    });
    CHECK(twice.output_ptx.find(marker_declaration) ==
          twice.output_ptx.rfind(marker_declaration));
#endif

    const auto rejected = hbfsim::ptx::transform_ptx({
        .full_ptx = read_fixture("unsupported.ptx"),
        .to_patch_kernel = "unsupported_kernel",
    });
    assert(!rejected.modified);
    assert(rejected.coverage.unsupported_instructions == 5);

    const auto excluded = hbfsim::ptx::transform_ptx({
        .full_ptx = read_fixture("helper_exclusion.ptx"),
        .to_patch_kernel = "",
    });
    assert(!excluded.modified);
    assert(excluded.coverage.excluded_functions == 2);

    // A global access the rewriter could not consume must be reported, never
    // skipped in silence. Silence lets a kernel be marked instrumented while
    // some of its HBF accesses ran at native speed, which makes any coverage
    // number meaningless.
    //
    // The fixture holds three statements the rewriter cannot parse, carrying
    // four accesses between them: an address register not named %rd, a line
    // holding both a load and a store, and one statement split across physical
    // lines. Measured before the fix: 0 rewritten and 0 unsupported. Measured
    // after: 3 unsupported.
    //
    // Four, one per access, not three per statement. The scan splits an
    // accumulated statement on its semicolons before matching, so a line
    // carrying both a load and a store reports both. Counting per access is
    // what makes the coverage denominator mean something: a manifest that says
    // "one unsupported instruction" when two accesses were skipped understates
    // the gap by half.
    const auto fail_open = hbfsim::ptx::transform_ptx({
        .full_ptx = read_fixture("fail_open_global.ptx"),
        .to_patch_kernel = "fail_open_kernel",
    });
    CHECK(fail_open.coverage.unsupported_instructions == 4);
    CHECK(!fail_open.modified);

    return 0;
}

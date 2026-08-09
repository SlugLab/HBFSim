#include "ptx_memory_op.hpp"
#include "transform.hpp"

#include <cassert>
#include <cstdint>
#include <fstream>
#include <sstream>
#include <string>

namespace {

std::string read_fixture(const std::string& name)
{
    std::ifstream input("tests/fixtures/ptx/" + name);
    assert(input);
    return {std::istreambuf_iterator<char>(input), {}};
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

    assert(!hbfsim::ptx::parse_memory_op("atom.global.add.u32 %r1, [%rd2], 1;"));
    assert(!hbfsim::ptx::parse_memory_op("ld.u32 %r1, [%rd2];"));
    assert(!hbfsim::ptx::parse_memory_op("ld.global.u32 %r1, [%r2+%r3];"));

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

    return 0;
}

#include "tma_transform.hpp"
#include "transform.hpp"

#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

std::string fixture()
{
    std::ifstream input("tests/fixtures/ptx/tma_sm120.ptx");
    require(input.good(), "unable to open TMA fixture");
    return {std::istreambuf_iterator<char>(input), {}};
}

std::size_t find(const std::string& text, const std::string& needle)
{
    const auto result = text.find(needle);
    if (result == std::string::npos) {
        throw std::runtime_error("missing transformed text: " + needle);
    }
    return result;
}

std::size_t count(const std::string& text, const std::string& needle)
{
    std::size_t result = 0;
    for (std::size_t position = 0;
         (position = text.find(needle, position)) != std::string::npos;
         position += needle.size()) {
        ++result;
    }
    return result;
}

}  // namespace

int main()
{
    const auto ptx = fixture();
    const auto transformed = hbfsim::ptx::transform_tma(ptx, "tma_sm120");
    require(transformed.modified && transformed.rejection_reason.empty() &&
                transformed.rewritten_instructions == 3,
            "supported TMA flow was not transformed");
    const auto issue = find(transformed.output_ptx,
                            "// HBFSim TMA issue 7");
    const auto native = find(
        transformed.output_ptx,
        "cp.async.bulk.tensor.1d.shared::cta.global.tile");
    const auto native_wait = find(
        transformed.output_ptx,
        "mbarrier.test_wait.shared::cta.b64");
    const auto shadow_poll = find(
        transformed.output_ptx,
        "// HBFSim conjunctive TMA barrier poll 7");
    require(issue < native && native < native_wait &&
                native_wait < shadow_poll,
            "TMA issue/native/conjunctive wait ordering differs");
    require(transformed.output_ptx.find(
                "call.uni (%hbfsim_tma_7_return), __hbfsim_tma_issue") !=
                std::string::npos &&
                transformed.output_ptx.find(
                    "st.param.b32 [%hbfsim_tma_7_access], 0;") !=
                    std::string::npos &&
                transformed.output_ptx.find(
                    "st.param.b32 [%hbfsim_tma_7_coordinate_0], %r1;") !=
                    std::string::npos &&
                transformed.output_ptx.find(
                    "st.param.b32 [%hbfsim_tma_7_coordinate_4], 0;") !=
                    std::string::npos &&
                transformed.output_ptx.find(
                    "st.param.b64 [%hbfsim_tma_7_shared], %rd2;") !=
                    std::string::npos &&
                transformed.output_ptx.find(
                    "setp.lt.s64 %hbfsim_tma_7_software") !=
                    std::string::npos &&
                transformed.output_ptx.find(
                    "@%hbfsim_tma_7_software bra.uni $hbfsim_tma_7_native_done;") !=
                    std::string::npos &&
                transformed.output_ptx.find("$hbfsim_tma_7_native_done:") !=
                    std::string::npos &&
                transformed.output_ptx.find(
                    "__hbfsim_tma_barrier_poll, (%hbfsim_tma_poll_") !=
                    std::string::npos &&
                transformed.output_ptx.find(
                    "and.b32 %hbfsim_tma_poll_") !=
                    std::string::npos &&
                transformed.output_ptx.find("_software_flag") !=
                    std::string::npos &&
                transformed.output_ptx.find(
                    "or.pred %hbfsim_tma_poll_") !=
                    std::string::npos &&
                transformed.output_ptx.find(
                    "and.pred %p1, %hbfsim_tma_poll_") !=
                    std::string::npos &&
                transformed.output_ptx.find("@!%hbfsim_tma_7_valid trap;") !=
                    std::string::npos,
            "TMA generation/fail-closed/conjunctive helper ABI is absent");

    const std::string store = R"ptx(
.version 9.0
.target sm_120
.address_size 64
.visible .entry store() {
 .reg .b64 %rd<5>;
 .reg .b32 %r<4>;
 ld.param.u64 %rd1, [descriptor];
 fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
 @%p1 cp.async.bulk.tensor.1d.global.shared::cta.tile.bulk_group
     [%rd1, {%r1}], [%rd2];
 mov.u32 %r2, 9;
 cp.async.bulk.commit_group;
 cp.async.bulk.wait_group.read 0;
 cp.async.bulk.wait_group 0;
 ret;
})ptx";
    const auto store_transform = hbfsim::ptx::transform_tma(store, "store");
    require(store_transform.modified &&
                store_transform.output_ptx.find(
                    "// HBFSim TMA bulk-group read wait") !=
                    std::string::npos &&
                store_transform.output_ptx.find(
                    "// HBFSim TMA bulk-group full wait") !=
                    std::string::npos &&
                store_transform.output_ptx.find(
                    "call.uni __hbfsim_tma_commit_group") !=
                    std::string::npos &&
                store_transform.output_ptx.find(
                    "@!%p1 bra.uni $hbfsim_tma_") !=
                    std::string::npos &&
                store_transform.output_ptx.find("_predicate_false;") !=
                    std::string::npos,
            "TMA bulk-group read/full paths were not separated");

#if defined(HBFSIM_TEST_DEVICE_HELPER_PTX)
    const auto self_contained = hbfsim::ptx::transform_ptx({
        .full_ptx = ptx,
        .to_patch_kernel = "tma_sm120",
    });
    require(self_contained.modified &&
                self_contained.output_ptx.find(
                    ".visible .func  (.param .b64 func_retval0) "
                    "__hbfsim_tma_issue") != std::string::npos &&
                self_contained.output_ptx.find(
                    "__hbfsim_tma_barrier_poll") != std::string::npos,
            "self-contained transform omitted TMA device helpers");
#endif

    const std::string unknown = R"ptx(
.version 9.0
.target sm_120
.address_size 64
.visible .entry unknown() {
 .reg .b64 %rd<4>;
 .reg .b32 %r<2>;
 mbarrier.init.shared::cta.b64 [%rd3], 1;
 cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
     [%rd2], [%rd1, {%r1}], [%rd3];
 ret;
})ptx";
    const auto rejected = hbfsim::ptx::transform_tma(unknown, "unknown");
    require(!rejected.modified &&
                rejected.rejection_reason == "unknown_tensormap",
            "unknown runtime descriptor did not fail closed");

    const std::string updated = R"ptx(
.version 9.0
.target sm_120a
.address_size 64
.visible .entry updated(
 .param .u64 descriptor,
 .param .u64 replacement,
 .param .u64 destination,
 .param .u64 barrier) {
 .reg .b64 %rd<8>;
 .reg .b32 %r<3>;
 .reg .pred %p<2>;
 ld.param.u64 %rd1, [descriptor];
 ld.param.u64 %rd2, [replacement];
 ld.param.u64 %rd3, [destination];
 ld.param.u64 %rd4, [barrier];
 mov.u32 %r1, 0;
 fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
 tensormap.replace.tile.global_address.global.b1024.b64 [%rd1], %rd2;
 fence.proxy.tensormap::generic.release.gpu;
 fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
 mbarrier.init.shared::cta.b64 [%rd4], 1;
 cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
     [%rd3], [%rd1, {%r1}], [%rd4];
 mbarrier.arrive.shared::cta.b64 %rd5, [%rd4];
 mbarrier.test_wait.shared::cta.b64 %p1, [%rd4], %rd5;
 ret;
})ptx";
    const auto update_transform =
        hbfsim::ptx::transform_tma(updated, "updated");
    require(update_transform.modified &&
                update_transform.rejection_reason.empty() &&
                update_transform.output_ptx.find(
                    "__hbfsim_tensormap_replace_begin") !=
                    std::string::npos &&
                update_transform.output_ptx.find(
                    "__hbfsim_tensormap_replace_commit") !=
                    std::string::npos &&
                update_transform.output_ptx.find(
                    "__hbfsim_tensormap_acquire") !=
                    std::string::npos &&
                update_transform.output_ptx.find(
                    "@!%hbfsim_tmap_acquire_") != std::string::npos,
            "device TensorMap replace/acquire shadow publication is absent");

    const std::string im2col = R"ptx(
.version 9.0
.target sm_120
.address_size 64
.visible .entry im2col(
 .param .u64 descriptor,
 .param .u64 destination,
 .param .u64 barrier) {
 .reg .b64 %rd<5>;
 .reg .b32 %r<4>;
 .reg .u16 %rs<5>;
 .reg .pred %p<2>;
 ld.param.u64 %rd1, [descriptor];
 ld.param.u64 %rd2, [destination];
 ld.param.u64 %rd3, [barrier];
 fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
 mbarrier.init.shared::cta.b64 [%rd3], 1;
 cp.async.bulk.tensor.3d.shared::cluster.global.im2col.mbarrier::complete_tx::bytes.multicast::cluster
     [%rd2], [%rd1, {%r1, %r2, %r3}], [%rd3], {%rs1}, %rs2;
 mbarrier.arrive.shared::cta.b64 %rd4, [%rd3];
 mbarrier.test_wait.shared::cta.b64 %p1, [%rd3], %rd4;
 ret;
})ptx";
    const auto im2col_transform =
        hbfsim::ptx::transform_tma(im2col, "im2col");
    require(im2col_transform.modified &&
                im2col_transform.rejection_reason.empty() &&
                im2col_transform.output_ptx.find(
                    "cvt.u32.u16 %hbfsim_tma_6_mask32, %rs2;") !=
                    std::string::npos &&
                im2col_transform.output_ptx.find(
                    "cvt.u32.u16 %hbfsim_tma_6_offset_0_32, %rs1;") !=
                    std::string::npos &&
                im2col_transform.output_ptx.find(
                    "st.param.b32 [%hbfsim_tma_6_offset_0], "
                    "%hbfsim_tma_6_offset_0_32;") != std::string::npos &&
                im2col_transform.output_ptx.find(
                    "%hbfsim_tma_6_offset_2)") != std::string::npos,
            "dynamic im2col offsets/multicast mask were not widened into ABI");

    const std::string im2col_wide = R"ptx(
.version 9.0
.target sm_120
.address_size 64
.visible .entry im2col_wide(
 .param .u64 descriptor,
 .param .u64 destination,
 .param .u64 barrier) {
 .reg .b64 %rd<5>;
 .reg .b32 %r<4>;
 .reg .b16 %rs<3>;
 .reg .pred %p<2>;
 ld.param.u64 %rd1, [descriptor];
 ld.param.u64 %rd2, [destination];
 ld.param.u64 %rd3, [barrier];
 fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
 mbarrier.init.shared::cta.b64 [%rd3], 1;
 cp.async.bulk.tensor.3d.im2col::w.shared::cta.global.mbarrier::complete_tx::bytes
     [%rd2], [%rd1, {%r1, %r2, %r3}], [%rd3], {%rs1, %rs2};
 mbarrier.arrive.shared::cta.b64 %rd4, [%rd3];
 mbarrier.test_wait.shared::cta.b64 %p1, [%rd3], %rd4;
 ret;
})ptx";
    const auto im2col_wide_transform =
        hbfsim::ptx::transform_tma(im2col_wide, "im2col_wide");
    require(im2col_wide_transform.modified &&
                im2col_wide_transform.rejection_reason.empty() &&
                im2col_wide_transform.output_ptx.find(
                    "cp.async.bulk.tensor.3d.im2col::w") !=
                    std::string::npos &&
                im2col_wide_transform.output_ptx.find(
                    "_software bra.uni $hbfsim_tma_") !=
                    std::string::npos,
            "im2col::w native fallback/software bypass was not preserved");

    std::ifstream copy_input("tests/fixtures/ptx/tensormap_copy_sm120a.ptx");
    require(copy_input.good(), "unable to open TensorMap copy fixture");
    const std::string copy_ptx{std::istreambuf_iterator<char>(copy_input), {}};
    const auto copy_transform =
        hbfsim::ptx::transform_tma(copy_ptx, "tensormap_copy_sm120a");
    require(copy_transform.modified &&
                copy_transform.rejection_reason.empty() &&
                copy_transform.output_ptx.find(
                    "__hbfsim_tensormap_copy_begin") != std::string::npos &&
                copy_transform.output_ptx.find(
                    "__hbfsim_tensormap_copy_commit") != std::string::npos &&
                copy_transform.output_ptx.find("cvta.shared.u64 ") !=
                    std::string::npos &&
                copy_transform.output_ptx.find("_shared64, %r1;") !=
                    std::string::npos &&
                copy_transform.output_ptx.find(
                    "_source64, %hbfsim_tmap_copy_") != std::string::npos &&
                copy_transform.output_ptx.find(
                    "_address64, %hbfsim_tmap_commit_") != std::string::npos,
            "TensorMap copy shadow generation publication is absent");

    const std::string pending_groups = R"ptx(
.version 9.0
.target sm_120
.address_size 64
.visible .entry pending_groups(.param .u64 descriptor) {
 .reg .b64 %rd<4>;
 .reg .b32 %r<3>;
 ld.param.u64 %rd1, [descriptor];
 fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
 cp.async.bulk.tensor.1d.global.shared::cta.tile.bulk_group
     [%rd1, {%r1}], [%r2];
 cp.async.bulk.commit_group;
 cp.async.bulk.tensor.1d.global.shared::cta.tile.bulk_group
     [%rd1, {%r1}], [%r2];
 cp.async.bulk.commit_group;
 cp.async.bulk.wait_group 1;
 cp.async.bulk.wait_group 0;
 ret;
})ptx";
    const auto pending_transform =
        hbfsim::ptx::transform_tma(pending_groups, "pending_groups");
    require(pending_transform.modified &&
                pending_transform.rejection_reason.empty() &&
                count(pending_transform.output_ptx,
                      "// HBFSim TMA bulk-group full wait") == 2,
            "bulk wait pending limit did not select only retired groups");

    const std::string prefetch = R"ptx(
.version 9.0
.target sm_120
.address_size 64
.visible .entry prefetch(.param .u64 descriptor) {
 .reg .b64 %rd<3>;
 .reg .b32 %r<3>;
 ld.param.u64 %rd1, [descriptor];
 fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
 cp.async.bulk.prefetch.tensor.2d.L2.global.tile
     [%rd1, {%r1, %r2}];
 cp.async.bulk.commit_group;
 cp.async.bulk.wait_group 0;
 ret;
})ptx";
    const auto prefetch_transform =
        hbfsim::ptx::transform_tma(prefetch, "prefetch");
    require(prefetch_transform.modified &&
                prefetch_transform.rejection_reason.empty() &&
                prefetch_transform.output_ptx.find(
                    "st.param.b32 [%hbfsim_tma_3_direction], 2;") !=
                    std::string::npos &&
                prefetch_transform.output_ptx.find(
                    "st.param.b32 [%hbfsim_tma_3_cta_group], 1;") !=
                    std::string::npos &&
                count(prefetch_transform.output_ptx,
                      "// HBFSim TMA bulk-group full wait") == 1,
            "tensor prefetch was not tracked as a bulk async-group");

    const std::string cta_group = R"ptx(
.version 9.0
.target sm_120
.address_size 64
.visible .entry cta_group(.param .u64 descriptor) {
 .reg .b64 %rd<4>;
 .reg .b32 %r<3>;
 .reg .b16 %rs<2>;
 .reg .pred %p<2>;
 ld.param.u64 %rd1, [descriptor];
 fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
 mbarrier.init.shared::cta.b64 [%rd3], 1;
 cp.async.bulk.tensor.2d.shared::cluster.global.tile.mbarrier::complete_tx::bytes.multicast::cluster.cta_group::2
     [%rd2], [%rd1, {%r1, %r2}], [%rd3], %rs1;
 mbarrier.arrive.shared::cta.b64 %rd2, [%rd3];
 mbarrier.test_wait.shared::cta.b64 %p1, [%rd3], %rd2;
 ret;
})ptx";
    const auto cta_group_transform =
        hbfsim::ptx::transform_tma(cta_group, "cta_group");
    require(cta_group_transform.modified &&
                cta_group_transform.rejection_reason.empty() &&
                cta_group_transform.output_ptx.find(
                    "_cta_group], 2;") !=
                    std::string::npos &&
                cta_group_transform.output_ptx.find(
                    "cp.async.bulk.tensor.2d.shared::cluster.global.tile."
                    "mbarrier::complete_tx::bytes.multicast::cluster."
                    "cta_group::2") == std::string::npos &&
                cta_group_transform.output_ptx.find(
                    "_software trap;") !=
                    std::string::npos &&
                cta_group_transform.output_ptx.find(
                    "bra.uni $hbfsim_tma_") !=
                    std::string::npos,
            "CTA-group::2 was not forced through the software-only helper path");

    const std::string phase_reuse = R"ptx(
.version 9.0
.target sm_120
.address_size 64
.visible .entry phase_reuse(
 .param .u64 descriptor,
 .param .u64 destination,
 .param .u64 barrier) {
 .reg .b64 %rd<8>;
 .reg .b32 %r<3>;
 .reg .pred %p<3>;
 ld.param.u64 %rd1, [descriptor];
 ld.param.u64 %rd2, [destination];
 ld.param.u64 %rd3, [barrier];
 fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
 mbarrier.init.shared::cta.b64 [%rd3], 1;
 mbarrier.arrive.expect_tx.shared::cta.b64 %rd4, [%rd3], 256;
 cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
     [%rd2], [%rd1, {%r1}], [%rd3];
 mbarrier.test_wait.shared::cta.b64 %p1, [%rd3], %rd4;
 mbarrier.arrive.expect_tx.shared::cta.b64 %rd5, [%rd3], %r2;
 cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
     [%rd2], [%rd1, {%r1}], [%rd3];
 mbarrier.test_wait.shared::cta.b64 %p2, [%rd3], %rd5;
 ret;
})ptx";
    const auto phase_reuse_transform =
        hbfsim::ptx::transform_tma(phase_reuse, "phase_reuse");
    require(phase_reuse_transform.modified &&
                phase_reuse_transform.rejection_reason.empty() &&
                count(phase_reuse_transform.output_ptx,
                      "// HBFSim conjunctive TMA barrier poll") == 2,
            "software TMA did not compensate mbarrier tx-count before phase reuse");
#if defined(HBFSIM_TEST_DEVICE_HELPER_PTX)
    const auto phase_reuse_self_contained = hbfsim::ptx::transform_ptx({
        .full_ptx = phase_reuse,
        .to_patch_kernel = "phase_reuse",
    });
    const auto complete_tx = phase_reuse_self_contained.output_ptx.find(
        "mbarrier.complete_tx.relaxed.cta.shared::cta.b64");
    const auto publish_async = phase_reuse_self_contained.output_ptx.rfind(
        "fence.proxy.async.shared::cta;", complete_tx);
    require(complete_tx != std::string::npos &&
                publish_async != std::string::npos &&
                publish_async < complete_tx,
            "software TMA poll helper does not publish shared writes before "
            "retiring the hardware mbarrier tx-count");
    require(phase_reuse_self_contained.output_ptx.find(
                "canonical_tma_shared_address") != std::string::npos &&
                phase_reuse_self_contained.output_ptx.find(
                    "mapa.shared::cluster.u32") != std::string::npos,
            "software TMA helper does not canonicalize DSM addresses across "
            "CTA-rank windows");
#endif
    return 0;
}

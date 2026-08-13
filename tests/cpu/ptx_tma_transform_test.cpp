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

}  // namespace

int main()
{
    const auto ptx = fixture();
    const auto transformed = hbfsim::ptx::transform_tma(ptx, "tma_sm120");
    require(transformed.modified && transformed.rejection_reason.empty() &&
                transformed.rewritten_instructions == 2,
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
                    "and.pred %p1, %p1, %hbfsim_tma_poll_") !=
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
 cp.async.bulk.tensor.1d.global.shared::cta.tile.bulk_group
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
    return 0;
}

#include "async_object_analysis.hpp"
#include "ptx_ir.hpp"

#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) throw std::runtime_error(message);
}

hbfsim::ptx::AsyncObjectPlan analyze(const std::string& body,
                                     std::uint32_t budget = 64)
{
    const auto module = hbfsim::ptx::parse_module(
        ".version 9.0\n.target sm_120\n.address_size 64\n"
        ".visible .entry kernel() {\n" + body + "\nret;\n}\n");
    return hbfsim::ptx::analyze_async_objects(
        module.function("kernel"), {.maximum_live_objects = budget});
}

bool has_reason(const hbfsim::ptx::AsyncObjectPlan& plan,
                const std::string& reason)
{
    for (const auto& [id, value] : plan.rejection_reasons) {
        (void)id;
        if (value == reason) return true;
    }
    return false;
}

const std::string prefix = R"ptx(
.reg .b64 %rd<16>;
.reg .b32 %r<16>;
.reg .pred %p<4>;
ld.param.u64 %rd1, [descriptor];
fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
mbarrier.init.shared::cta.b64 [%rd3], 1;
)ptx";

}  // namespace

int main()
{
    auto valid = analyze(prefix + R"ptx(
mbarrier.arrive.expect_tx.shared::cta.b64 %rd4, [%rd3], 64;
cp.async.bulk.tensor.2d.shared::cta.global.tile.mbarrier::complete_tx::bytes
    [%rd2], [%rd1, {%r1, %r2}], [%rd3];
mbarrier.test_wait.shared::cta.b64 %p1, [%rd3], %rd4;
)ptx");
    require(valid.exact_safe() && valid.maximum_live_objects == 1 &&
                valid.tma_instruction_ids.size() == 1 &&
                valid.descriptor_generations.size() == 1,
            "valid TMA lifetime was rejected");

    auto unknown = analyze(R"ptx(
.reg .b64 %rd<8>;
.reg .b32 %r<4>;
mbarrier.init.shared::cta.b64 [%rd3], 1;
cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
    [%rd2], [%rd1, {%r1}], [%rd3];
mbarrier.test_wait.shared::cta.b64 %p1, [%rd3], %rd4;
)ptx");
    require(has_reason(unknown, "unknown_tensormap"),
            "unknown TensorMap was accepted");

    auto missing_fence = analyze(prefix + R"ptx(
tensormap.replace.tile.global_address.global.b1024.b64 [%rd1], %rd9;
cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
    [%rd2], [%rd1, {%r1}], [%rd3];
mbarrier.test_wait.shared::cta.b64 %p1, [%rd3], %rd4;
)ptx");
    require(has_reason(missing_fence, "tensormap_fence_missing"),
            "unfenced TensorMap replacement was accepted");

    auto stale = analyze(prefix + R"ptx(
mov.b64 %rd8, %rd1;
tensormap.replace.tile.global_address.global.b1024.b64 [%rd1], %rd9;
fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
    [%rd2], [%rd8, {%r1}], [%rd3];
mbarrier.test_wait.shared::cta.b64 %p1, [%rd3], %rd4;
)ptx");
    require(has_reason(stale, "stale_tensormap_generation"),
            "stale descriptor alias was accepted");

    auto unbalanced = analyze(R"ptx(
.reg .b64 %rd<8>;
.reg .b32 %r<4>;
ld.param.u64 %rd1, [descriptor];
fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
cp.async.bulk.tensor.1d.global.shared::cta.tile.bulk_group
    [%rd1, {%r1}], [%rd2];
cp.async.bulk.commit_group;
)ptx");
    require(has_reason(unbalanced, "bulk_group_unbalanced"),
            "unbalanced bulk group was accepted");

    auto balanced = analyze(R"ptx(
.reg .b64 %rd<8>;
.reg .b32 %r<4>;
ld.param.u64 %rd1, [descriptor];
fence.proxy.tensormap::generic.acquire.gpu [%rd1], 128;
cp.async.bulk.tensor.1d.global.shared::cta.tile.bulk_group
    [%rd1, {%r1}], [%rd2];
cp.async.bulk.commit_group;
cp.async.bulk.wait_group.read 0;
cp.async.bulk.wait_group 0;
)ptx");
    require(balanced.exact_safe(), "balanced bulk group was rejected");

    auto multicast = analyze(prefix + R"ptx(
cp.async.bulk.tensor.2d.shared::cluster.global.tile.mbarrier::complete_tx::bytes.multicast::cluster
    [%rd2], [%rd1, {%r1, %r2}], [%rd3], 0x15;
mbarrier.test_wait.shared::cta.b64 %p1, [%rd3], %rd4;
)ptx");
    require(multicast.exact_safe() &&
                multicast.multicast_targets.begin()->second ==
                    std::set<std::uint32_t>({0, 2, 4}),
            "multicast target keys differ");

    auto budget = analyze(prefix + R"ptx(
cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
    [%rd2], [%rd1, {%r1}], [%rd3];
)ptx", 0);
    require(has_reason(budget, "async_object_budget_exceeded"),
            "async object budget excess was accepted");

    auto phase = analyze(prefix + R"ptx(
cp.async.bulk.tensor.1d.shared::cta.global.tile.mbarrier::complete_tx::bytes
    [%rd2], [%rd1, {%r1}], [%rd3];
mbarrier.inval.shared::cta.b64 [%rd3];
)ptx");
    require(has_reason(phase, "ambiguous_mbarrier_phase"),
            "live barrier invalidation was accepted");
    return 0;
}

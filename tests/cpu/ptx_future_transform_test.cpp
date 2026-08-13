#include "future_transform.hpp"

#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>

namespace {

void require(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

std::string fixture()
{
    std::ifstream input("tests/fixtures/ptx/future_loads.ptx");
    require(input.good(), "unable to open future load fixture");
    return {std::istreambuf_iterator<char>(input), {}};
}

std::string store_fixture()
{
    std::ifstream input("tests/fixtures/ptx/future_stores.ptx");
    require(input.good(), "unable to open future store fixture");
    return {std::istreambuf_iterator<char>(input), {}};
}

std::size_t require_find(const std::string& text, const std::string& needle)
{
    const auto found = text.find(needle);
    if (found == std::string::npos) {
        throw std::runtime_error("missing transformed text: " + needle);
    }
    return found;
}

std::size_t count(const std::string& text, const std::string& needle)
{
    std::size_t result = 0;
    for (auto position = text.find(needle); position != std::string::npos;
         position = text.find(needle, position + needle.size())) {
        ++result;
    }
    return result;
}

}  // namespace

int main()
{
    const auto ptx = fixture();
    const auto result = hbfsim::ptx::transform_load_futures(
        ptx, "future_loads");
    require(result.modified && result.rejection_reason.empty(),
            "supported loads were not transformed");
    require(result.rewritten_futures == 3,
            "wrong number of load futures");
    require(count(result.output_ptx, "__hbfsim_future_issue") == 3,
            "each load must issue exactly once");

    const auto first_issue = require_find(
        result.output_ptx, "// HBFSim future issue 2");
    const auto independent = require_find(
        result.output_ptx, "add.u32 %r20, %r10, 1;");
    const auto first_wait = require_find(
        result.output_ptx, "// HBFSim future wait 2");
    const auto consumer = require_find(
        result.output_ptx, "add.u32 %r3, %r1, %r2;");
    require(first_issue < independent && independent < first_wait &&
                first_wait < consumer,
            "load wait was not delayed to the first consumer");
    require(result.output_ptx.find(
                "ld.global.v2.u32 {%r1, %r2}, "
                "[%hbfsim_f2_resolved];") != std::string::npos,
            "capacity vector materialization was not emitted");
    require(result.output_ptx.find("setp.eq.u32 %hbfsim_f2_deferred, "
                                   "%hbfsim_f2_state, 3;") !=
                std::string::npos &&
                result.output_ptx.find("mov.u32 %hbfsim_f2_state, 5;") !=
                    std::string::npos,
            "materialization is not idempotent");

    require(result.output_ptx.find(
                "@!%p1 bra $L__hbfsim_f5_predicate_false;") !=
                std::string::npos,
            "predicated false load path was not skipped");
    const auto predicated_issue = require_find(
        result.output_ptx, "// HBFSim future issue 5");
    const auto predicated_wait = require_find(
        result.output_ptx, "// HBFSim future wait 5");
    const auto predicated_consumer = require_find(
        result.output_ptx, "@%p1 add.u32 %r5, %r4, 1;");
    require(predicated_issue < predicated_wait &&
                predicated_wait < predicated_consumer,
            "predicated wait is not on its guarded consumer path");

    const auto acquire_issue = require_find(
        result.output_ptx, "// HBFSim future issue 8");
    const auto acquire_wait = require_find(
        result.output_ptx, "// HBFSim future wait 8");
    const auto after_acquire = require_find(
        result.output_ptx, "add.u32 %r22, %r12, 1;");
    require(acquire_issue < acquire_wait && acquire_wait < after_acquire,
            "acquire load escaped its architectural wait boundary");

    const auto diamond = hbfsim::ptx::transform_load_futures(
        ptx, "future_diamond");
    require(diamond.modified &&
                count(diamond.output_ptx, "// HBFSim future wait ") == 2,
            "diamond needs one first wait on each branch");

    const auto ambiguous = hbfsim::ptx::transform_load_futures(
        ptx, "future_ambiguous");
    require(!ambiguous.modified && ambiguous.output_ptx.empty() &&
                ambiguous.rejection_reason ==
                    "ambiguous_future_definition",
            "ambiguous future transform did not fail closed");

    const auto stores = hbfsim::ptx::transform_futures(
        store_fixture(), "future_stores");
    require(stores.modified && stores.rejection_reason.empty() &&
                stores.rewritten_futures == 4,
            "store/atomic futures were not transformed");
    const auto store_issue = require_find(
        stores.output_ptx, "// HBFSim future issue 3");
    const auto store_snapshot = require_find(
        stores.output_ptx,
        "mov.b32 %hbfsim_f3_snapshot_0, %r1;");
    const auto store_call = require_find(
        stores.output_ptx,
        "call (%hbfsim_issue_3_return), __hbfsim_future_issue");
    const auto source_redefinition = require_find(
        stores.output_ptx, "mov.u32 %r1, 9;");
    const auto fence_wait = require_find(
        stores.output_ptx, "// HBFSim future wait 3");
    const auto fence = require_find(
        stores.output_ptx, "fence.release.gpu;");
    require(store_issue < store_snapshot && store_snapshot < store_call &&
                store_call < source_redefinition &&
                source_redefinition < fence_wait && fence_wait < fence,
            "store snapshot or release-fence drain is misplaced");
    require(stores.output_ptx.find(
                "st.global.u32 [%hbfsim_f3_resolved], "
                "%hbfsim_f3_snapshot_0;") != std::string::npos,
            "deferred store does not use its issue-time snapshot");

    const auto atomic_issue = require_find(
        stores.output_ptx, "// HBFSim future issue 7");
    const auto atomic_operation = require_find(
        stores.output_ptx,
        "st.param.b32 [%hbfsim_issue_7_operation], 2;");
    const auto atomic_wait = require_find(
        stores.output_ptx, "// HBFSim future wait 7");
    const auto atomic_consumer = require_find(
        stores.output_ptx, "add.u32 %r3, %r2, 1;");
    require(atomic_issue <= atomic_operation &&
                atomic_operation < atomic_wait &&
                atomic_wait < atomic_consumer,
            "atomic is not one future waited at its result consumer");
    require(count(stores.output_ptx,
                  "atom.global.add.u32 %r2, "
                  "[%hbfsim_f7_resolved]") == 2 &&
                stores.output_ptx.find(
                    "@%hbfsim_f7_skip bra "
                    "$L__hbfsim_f7_skip_native;") != std::string::npos &&
                stores.output_ptx.find(
                    "@!%hbfsim_f7_deferred bra "
                    "$L__hbfsim_f7_wait_materialized_1;") !=
                    std::string::npos,
            "atomic resident/deferred executions are not mutually exclusive");

    require(stores.output_ptx.find(
                "@!%p1 bra $L__hbfsim_f10_predicate_false;") !=
                std::string::npos,
            "predicated store false path was not skipped");
    const auto final_return = stores.output_ptx.rfind("    ret;");
    require(final_return != std::string::npos &&
                stores.output_ptx.rfind("// HBFSim future wait 10",
                                        final_return) < final_return &&
                stores.output_ptx.rfind("// HBFSim future wait 13",
                                        final_return) < final_return,
            "return did not drain every remaining store future");

    const auto unsupported = hbfsim::ptx::transform_futures(
        store_fixture(), "unsupported_atomic");
    require(!unsupported.modified && unsupported.output_ptx.empty() &&
                unsupported.rejection_reason == "unsupported_atomic_type",
            "unsupported atomic type did not fail closed");
    return 0;
}

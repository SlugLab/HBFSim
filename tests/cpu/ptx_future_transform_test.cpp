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
    return 0;
}

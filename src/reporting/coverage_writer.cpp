#include "hbfsim/coverage.hpp"

#include <json.hpp>

#include <fstream>
#include <stdexcept>

namespace hbfsim {
namespace {

nlohmann::json to_json(const GateDecision& decision)
{
    return {
        {"allowed", decision.allowed},
        {"module_id", decision.module_id},
        {"kernel", decision.kernel},
        {"ptx_target", decision.ptx_target},
        {"cubin_only", decision.cubin_only},
        {"reason", decision.reason},
        {"operation", decision.operation},
        {"inspected_parameters", decision.inspected_parameters},
        {"parameter_index", decision.parameter_index},
        {"parameter_offset", decision.parameter_offset},
        {"address", decision.address},
    };
}

}  // namespace

CoverageWriter::CoverageWriter(std::filesystem::path path) : path_(std::move(path)) {}

void CoverageWriter::append(const GateDecision& decision)
{
    std::scoped_lock lock(mutex_);
    decisions_.push_back(decision);
    flush();
}

void CoverageWriter::flush() const
{
    nlohmann::json decisions = nlohmann::json::array();
    std::size_t unsafe_launches = 0;
    for (const auto& decision : decisions_) {
        decisions.push_back(to_json(decision));
        unsafe_launches += decision.allowed ? 0U : 1U;
    }
    const nlohmann::json report{
        {"unsafe_launches", unsafe_launches},
        {"decisions", std::move(decisions)},
    };

    std::ofstream output(path_, std::ios::trunc);
    if (!output) {
        throw std::runtime_error("unable to open coverage report: " + path_.string());
    }
    output << report.dump() << '\n';
    if (!output) {
        throw std::runtime_error("unable to write coverage report: " + path_.string());
    }
}

bool try_append_coverage(
    CoverageWriter& writer, const GateDecision& decision) noexcept
{
    try {
        writer.append(decision);
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

bool coverage_decision_permits_launch(
    CoverageWriter& writer, const GateDecision& decision) noexcept
{
    // Auditability is part of the launch policy: a launch is approved only
    // when its decision is safe and durably reportable.
    return try_append_coverage(writer, decision) && decision.allowed;
}

}  // namespace hbfsim

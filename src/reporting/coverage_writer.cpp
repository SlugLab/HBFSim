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
        {"module", decision.module},
        {"kernel", decision.kernel},
        {"reason", decision.reason},
        {"operation", decision.operation},
        {"inspected_parameters", decision.inspected_parameters},
        {"parameter_index", decision.parameter_index},
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

}  // namespace hbfsim

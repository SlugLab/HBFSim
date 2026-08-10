#include "hbfsim/coverage.hpp"

#include <algorithm>
#include <stdexcept>
#include <utility>

namespace hbfsim {

void CoverageGate::add_module(ModuleManifest manifest)
{
    if (manifest.name.empty()) {
        throw std::invalid_argument("coverage manifest name must not be empty");
    }
    std::unique_lock lock(mutex_);
    modules_.insert_or_assign(manifest.name, std::move(manifest));
}

void CoverageGate::add_range(std::uintptr_t begin, std::uintptr_t end)
{
    if (begin >= end) {
        throw std::invalid_argument("coverage range must be non-empty");
    }
    std::unique_lock lock(mutex_);
    ranges_.push_back({begin, end});
}

bool CoverageGate::has_ranges() const
{
    std::shared_lock lock(mutex_);
    return !ranges_.empty();
}

bool CoverageGate::contains(std::uintptr_t address) const
{
    return std::ranges::any_of(ranges_, [address](const AddressRange& range) {
        return address >= range.begin && address < range.end;
    });
}

GateDecision CoverageGate::check_launch(const KernelLaunch& launch) const
{
    std::shared_lock lock(mutex_);
    GateDecision decision{
        .allowed = true,
        .module = launch.module,
        .kernel = launch.kernel,
        .inspected_parameters = launch.arguments.size(),
    };

    const bool has_hbf_argument = std::ranges::any_of(
        launch.arguments, [this](std::uintptr_t value) { return contains(value); });
    if (!has_hbf_argument) {
        return decision;
    }

    auto module = modules_.find(launch.kernel);
    if (module == modules_.end()) {
        module = modules_.find(launch.module);
    }
    if (module == modules_.end() || !module->second.instrumented) {
        const auto hbf_argument = std::ranges::find_if(
            launch.arguments,
            [this](std::uintptr_t value) { return contains(value); });
        decision.parameter_index =
            static_cast<std::size_t>(hbf_argument - launch.arguments.begin());
        decision.address = *hbf_argument;
        decision.allowed = false;
        decision.reason = "uninstrumented_module";
        decision.operation = "opaque_pointer_access";
        return decision;
    }

    const auto& manifest = module->second;
    for (std::size_t parameter_index = 0;
         parameter_index < launch.arguments.size(); ++parameter_index) {
        if (!contains(launch.arguments[parameter_index])) {
            continue;
        }
        decision.parameter_index = parameter_index;
        decision.address = launch.arguments[parameter_index];

        const auto unsupported = std::ranges::find_if(
            manifest.unsupported_parameters,
            [parameter_index](const UnsupportedParameter& parameter) {
                return parameter.index == parameter_index;
            });
        if (unsupported != manifest.unsupported_parameters.end()) {
            decision.allowed = false;
            decision.reason = "unsupported_operation";
            decision.operation = unsupported->operation;
            return decision;
        }

        if (std::ranges::find(manifest.pointer_parameters, parameter_index) ==
            manifest.pointer_parameters.end()) {
            decision.allowed = false;
            decision.reason = "uninstrumented_pointer_parameter";
            decision.operation = "unrecognized_pointer_access";
            return decision;
        }
    }
    return decision;
}

}  // namespace hbfsim

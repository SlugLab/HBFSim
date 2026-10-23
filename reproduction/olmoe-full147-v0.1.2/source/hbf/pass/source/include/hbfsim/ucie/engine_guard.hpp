#pragma once

namespace hbfsim::ucie {
// MQSim's Simulator is process-global. Only real UCIe frontends take this
// lease; fake-media fixtures do not reset or own a Simulator.
class UcieEngineGuard {
public:
    UcieEngineGuard();
    ~UcieEngineGuard();
    UcieEngineGuard(const UcieEngineGuard&)=delete;
    UcieEngineGuard& operator=(const UcieEngineGuard&)=delete;
};
} // namespace hbfsim::ucie

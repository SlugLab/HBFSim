#include <hbfsim/ucie/engine_guard.hpp>

#include <atomic>
#include <stdexcept>

namespace hbfsim::ucie {
namespace { std::atomic<bool> engine_owned{false}; }
UcieEngineGuard::UcieEngineGuard()
{
    if (engine_owned.exchange(true))
        throw std::logic_error("MQSim is process-global: a UCIe frontend is already active");
}
UcieEngineGuard::~UcieEngineGuard() { engine_owned.store(false); }
} // namespace hbfsim::ucie

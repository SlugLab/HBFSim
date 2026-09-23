#include "hbfsim/strict_direct_policy.hpp"
#include <cassert>
#include <cstdio>

int main()
{
    using hbfsim::StrictDirectAction;
    constexpr auto caps = hbfsim::kStrictBridgeRequiredCapabilities;
    assert(hbfsim::strict_direct_action(caps, 0) == StrictDirectAction::Reject);
    assert(hbfsim::strict_direct_action(caps, 1) == StrictDirectAction::Original);
    assert(hbfsim::strict_direct_action(caps, 2) == StrictDirectAction::Patched);
    assert(hbfsim::strict_direct_action(caps, 3) == StrictDirectAction::Reject);
    assert(hbfsim::strict_direct_action(0, 1) == StrictDirectAction::Reject);
    assert(hbfsim::strict_direct_action(hbfsim::kStrictBridgeTriState, 1) ==
           StrictDirectAction::Reject);
    assert(hbfsim::strict_direct_action(
               hbfsim::kStrictBridgeRuntimeExactDriver, 2) ==
           StrictDirectAction::Reject);
    std::puts("PASS: strict direct gate 0/1/2 and missing capability");
}

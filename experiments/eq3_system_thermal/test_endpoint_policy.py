import os
from pathlib import Path
import subprocess
import sys
import unittest

from endpoint_policy import EndpointAwarePolicy
from read_rate_policy import EngineeringProfile, ReadRatePolicy, StackWindowFacts, WindowFacts


WINDOW = 20_000_000
BASELINE = 1000


def profile(stack):
    return EngineeringProfile(
        profile_id="fixed:" + stack, enabled=True,
        strategy="read_rate_feedback_thermal_guard_v1", window_ns=WINDOW,
        target_bytes_per_s=50_000, step_bytes=50, minimum_budget_bytes=100,
        maximum_budget_bytes=BASELINE, severe_budget_bytes=0, light_fraction=.5)


def facts(stack, state, budget, *, offered=0, delivered=0, backlog=0):
    row = StackWindowFacts(
        stack_id=stack, offered_bytes=offered, delivered_bytes=delivered,
        backlog_bytes=backlog, oldest_wait_ns=0, latency_p95_ns=None,
        censored_requests=0, gate_limited=False,
        backend_busy_fraction=0, resource_busy=False)
    return WindowFacts(0, WINDOW, state, (row,), {stack: budget},
                       guard_states={stack: state},
                       hysteresis_budget_bytes={stack: BASELINE // 2})


class EndpointPolicyTests(unittest.TestCase):
    def test_isolated_entrypoint_does_not_require_pythonpath(self):
        entry = Path(__file__).with_name("run_endpoint_guard_point.py")
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, str(entry), "--help"], cwd="/tmp", env=environment,
            text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--thermal-binary", result.stdout)

    def test_reproduces_legacy_hbm_half_cap_hold_without_local_demand(self):
        policy = ReadRatePolicy(profile("hbm0"))
        decision = policy.evaluate(facts("hbm0", "normal", BASELINE // 2))
        self.assertEqual(decision.stack_decisions[0].budget_bytes, BASELINE // 2)
        self.assertEqual(decision.stack_decisions[0].outcome, "INSUFFICIENT_DEMAND")

    def test_hbm_endpoint_light_caps_and_normal_restores_without_fake_demand(self):
        policy = EndpointAwarePolicy(profile("hbm0"))
        light = policy.evaluate(facts("hbm0", "light", BASELINE))
        self.assertEqual(light.stack_decisions[0].budget_bytes, BASELINE // 2)
        normal = policy.evaluate(facts("hbm0", "normal", BASELINE // 2))
        self.assertEqual(normal.stack_decisions[0].budget_bytes, BASELINE)
        self.assertEqual(normal.stack_decisions[0].reasons,
                         ("THERMAL_NORMAL_RESTORE_BASELINE",))
        self.assertIn("no HBM foreground delivery", normal.fact_semantics)

    def test_hbm_severe_shutdown_and_hbf_legacy_equivalence(self):
        hbm = EndpointAwarePolicy(profile("hbm0"))
        for state in ("severe", "shutdown"):
            self.assertEqual(hbm.evaluate(facts("hbm0", state, BASELINE)).
                             stack_decisions[0].budget_bytes, 0)
        left = EndpointAwarePolicy(profile("hbf0"))
        right = ReadRatePolicy(profile("hbf0"))
        observed = facts("hbf0", "normal", BASELINE, offered=2000,
                         delivered=900, backlog=1100)
        self.assertEqual(left.evaluate(observed), right.evaluate(observed))


if __name__ == "__main__":
    unittest.main()

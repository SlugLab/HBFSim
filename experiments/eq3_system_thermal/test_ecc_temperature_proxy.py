import math
import unittest

from ecc_temperature_proxy import (CLASSIFICATION, TemperatureProxyDomainError,
                                   TemperatureReadCostProxy)
from ecc_service_adapter import ReliabilityCausalService
from topology_service import default_config


def profile(p85):
    return {
        "classification": CLASSIFICATION,
        "recoverable_read_probability_at_85c": p85,
        "expected_extra_attempts_per_recoverable_read": 2.0,
        "activation_energy_ev": 1.04,
        "boltzmann_ev_per_k": 8.62e-5,
        "temperature_reference_k": 358.15,
        "temperature_domain_k": [300.0, 400.0],
        "ecc_decoder_headroom_over_fresh_media": 2.0,
    }


class TemperatureCostTests(unittest.TestCase):
    def test_p0_is_exact_null_effort(self):
        proxy = TemperatureReadCostProxy(profile(0), {"hbf0": 358.15})
        self.assertEqual(proxy.cost("hbf0", 0)["attempt_work_milli"], 1000)
        self.assertEqual(proxy.cost("hbf0", 0)["expected_retry_steps"], 0)

    def test_probability_is_finite_monotonic_and_hits_p85(self):
        proxy = TemperatureReadCostProxy(profile(.3), {"hbf0": 300.0})
        values = [proxy.probability(value) for value in (300, 330, 358.15, 380, 400)]
        self.assertTrue(all(math.isfinite(value) and 0 <= value < 1 for value in values))
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(proxy.probability(358.15), .3)
        with self.assertRaises(TemperatureProxyDomainError):
            proxy.probability(400.01)

    def test_observation_changes_future_cost_only(self):
        proxy = TemperatureReadCostProxy(profile(.1), {"hbf0": 300.0})
        before = proxy.cost("hbf0", 0)
        proxy.observe(0, 20_000_000, {"hbf0": 380.0})
        after = proxy.cost("hbf0", 20_000_000)
        self.assertGreater(after["expected_retry_steps"], before["expected_retry_steps"])
        self.assertEqual(proxy.snapshot()["states"]["hbf0"]["last_ns"], 20_000_000)

    def test_first_service_cost_is_frozen_after_hotter_observation(self):
        config = default_config("mixed_direct")
        proxy = TemperatureReadCostProxy(
            profile(.1), {stack: 300.0 for stack in config["fabric"]["hbf"]})
        service = ReliabilityCausalService(config, proxy)
        service.begin_window(0, 20_000_000,
                             {stack: 10**12 for stack in config["channels"]},
                             {stack: "normal" for stack in config["channels"]})
        service.submit_jobs([{"job_id": "r", "stack": "hbf0", "channel": "0",
                              "route": "direct", "operation": "read",
                              "bytes": 96_000_000, "arrival_ns": 0}])
        first = service.advance_to(500_000)
        frozen = first["job_progress"][0]["metadata"]["reliability_cost_proxy"]
        proxy.observe(0, 20_000_000,
                      {stack: 380.0 for stack in config["fabric"]["hbf"]})
        final = service.advance_to(20_000_000)
        self.assertEqual(final["job_progress"][0]["metadata"]["reliability_cost_proxy"],
                         frozen)
        self.assertGreater(proxy.cost("hbf0", 20_000_000)["expected_retry_steps"],
                           frozen["expected_retry_steps"])


if __name__ == "__main__":
    unittest.main()

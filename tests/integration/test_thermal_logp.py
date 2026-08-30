#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("fit_logp", ROOT / "scripts/thermal/fit_logp.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ThermalLogPTest(unittest.TestCase):
    def test_weighted_history_matches_paper_weights(self):
        result = MODULE.weighted_history([10.0, 20.0, 30.0], n=7)
        self.assertAlmostEqual(result[-1], (30.0 + 10.0 + 2.5) / 1.75)

    def test_log_domain_fit_recovers_tau(self):
        times = list(range(31))
        temperatures = [30.0 + 50.0 * (1.0 - __import__("math").exp(-t / 12.0)) for t in times]
        fit = MODULE.fit_heating(times, temperatures)
        self.assertAlmostEqual(fit["tau_seconds"], 12.0, delta=0.5)
        self.assertLess(fit["rmse_c"], 0.2)


if __name__ == "__main__":
    unittest.main()

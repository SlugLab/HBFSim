#!/usr/bin/env python3
"""Solver-free causal/conservation tests for the controlled fluid runner."""
import unittest

from fluid_service import FluidService
from run_controlled import BASELINE_STACK_BPS, WINDOW_NS, execute_loop


class FakeThermal:
    def __init__(self, states):
        self.states = iter(states)
        self.energy = 0.0

    def advance(self, start_ns, end_ns, component_energy_j):
        state = next(self.states)
        self.energy += sum(component_energy_j.values())
        temperature = {"normal": 320.0, "light": 355.0,
                       "severe": 365.0, "shutdown": 380.0}[state]
        return {
            "start_ns": start_ns, "end_ns": end_ns,
            "temperatures": {"hbf0": temperature},
            "stack_states": {"hbf0": state},
            "hysteresis_budget_bytes": {"hbf0": BASELINE_STACK_BPS * WINDOW_NS // 2_000_000_000},
            "energy_j": {"cumulative": {"total_input_j": self.energy}},
        }


def fixtures():
    channels = {str(index): f"hbf0.die{index}" for index in range(16)}
    capacities = {channel: 96_000_000_000 for channel in channels}
    offered = {"hbf0": {channel: 5_000_000_000 for channel in channels}}
    workload = {
        "schema_version": "eq3-rate-model-workload-v1",
        "metadata": {"mean_active_offered_Bps": 4_000_000_000_000},
        "windows": [
            {"start_ns": index * WINDOW_NS, "end_ns": (index + 1) * WINDOW_NS,
             "stack_channel_offered_bytes": offered,
             "total_offered_bytes": sum(sum(row.values()) for row in offered.values())}
            for index in range(3)
        ],
    }
    profile = {
        "schema_version": 1, "array_j_per_byte": 40e-12, "base_j_per_byte": 10e-12,
        "reference_read_Bps": 1_600_000_000_000,
        "provenance": "SCENARIO_ASSUMPTION_USER_CONFIRMED",
        "channel_map": {"hbf0": channels},
        "channel_capacity_Bps": {"hbf0": capacities},
    }
    normalized = {"components": ([{"id": "hbf0.base"}] +
                                  [{"id": value} for value in channels.values()])}
    scenario = {
        "schema_version": "eq3-rate-controlled-scenario-v1",
        "scenario_id": "FIXED_CAUSAL_TEST", "topology": "mixed_direct",
        "window_ns": WINDOW_NS,
        "target_read_Bps_per_stack": BASELINE_STACK_BPS * 4 // 5,
    }
    return profile, workload, scenario, normalized, channels, capacities


class ControlledRunnerTests(unittest.TestCase):
    def test_guard_action_applies_only_to_next_window_and_bytes_conserve(self):
        profile, workload, scenario, normalized, channels, capacities = fixtures()
        fluid = FluidService({"hbf0": list(channels)}, {"hbf0": capacities}, WINDOW_NS)
        result = execute_loop(
            profile=profile, workload=workload, scenario=scenario, normalized=normalized,
            strategy="guard_only", fluid=fluid,
            thermal=FakeThermal(["severe", "normal", "normal"]))
        baseline = BASELINE_STACK_BPS * WINDOW_NS // 1_000_000_000
        rates = result["records"]["rates"]
        controls = result["records"]["control"]
        self.assertEqual(rates[0]["budget_by_stack"]["hbf0"], baseline)
        self.assertEqual(controls[0]["next_budget_bytes"]["hbf0"], 0)
        self.assertEqual(rates[1]["budget_by_stack"]["hbf0"], 0)
        self.assertEqual(controls[1]["next_budget_bytes"]["hbf0"], baseline)
        self.assertEqual(rates[2]["budget_by_stack"]["hbf0"], baseline)
        self.assertEqual(result["summary"]["byte_conservation_error"], 0)
        self.assertEqual(result["semantics"]["backend_latency"], "UNKNOWN")
        self.assertFalse(result["semantics"]["mqsim_or_fabric_completion"])
        self.assertEqual(controls[0]["decision"]["fact_semantics"],
                         "MODELLED_FLUID_BYTE_DELIVERY_NOT_ACTUAL_FABRIC_DELIVERY")

    def test_all_strategies_use_the_same_severe_guard(self):
        for strategy in ("guard_only", "thermal_hysteresis_guard",
                         "read_rate_feedback_thermal_guard_v1"):
            profile, workload, scenario, normalized, channels, capacities = fixtures()
            workload["windows"] = workload["windows"][:1]
            fluid = FluidService({"hbf0": list(channels)}, {"hbf0": capacities}, WINDOW_NS)
            result = execute_loop(profile=profile, workload=workload, scenario=scenario,
                                  normalized=normalized, strategy=strategy, fluid=fluid,
                                  thermal=FakeThermal(["severe"]))
            self.assertEqual(result["records"]["control"][0]["next_budget_bytes"]["hbf0"], 0)

    def test_feedback_recovers_using_explicit_modelled_fluid_spare_capacity(self):
        profile, workload, scenario, normalized, channels, capacities = fixtures()
        fluid = FluidService({"hbf0": list(channels)}, {"hbf0": capacities}, WINDOW_NS)
        result = execute_loop(
            profile=profile, workload=workload, scenario=scenario, normalized=normalized,
            strategy="read_rate_feedback_thermal_guard_v1", fluid=fluid,
            thermal=FakeThermal(["severe", "normal", "normal"]))
        controls = result["records"]["control"]
        minimum = BASELINE_STACK_BPS * WINDOW_NS // 1_000_000_000 // 10
        step = BASELINE_STACK_BPS * WINDOW_NS // 1_000_000_000 // 20
        self.assertEqual(controls[0]["next_budget_bytes"]["hbf0"], 0)
        self.assertEqual(controls[1]["next_budget_bytes"]["hbf0"], minimum + step)
        self.assertEqual(controls[2]["next_budget_bytes"]["hbf0"], minimum + 2 * step)
        adapter = result["records"]["rates"][2]["policy_fact_adapter_by_stack"]["hbf0"]
        self.assertLess(adapter["modelled_fluid_capacity_utilization"], 1.0)
        self.assertFalse(adapter["modelled_fluid_capacity_saturated"])
        self.assertIsNotNone(adapter["fluid_byte_weighted_latency_p95_ns"])
        self.assertIn("MODELLED_FLUID", controls[1]["policy_fact_adapter_semantics"]["backend_busy_fraction"])

    def test_rejects_unfrozen_target_formula(self):
        profile, workload, scenario, normalized, channels, capacities = fixtures()
        scenario["target_read_Bps_per_stack"] -= 1
        fluid = FluidService({"hbf0": list(channels)}, {"hbf0": capacities}, WINDOW_NS)
        with self.assertRaisesRegex(ValueError, "scenario target"):
            execute_loop(profile=profile, workload=workload, scenario=scenario,
                         normalized=normalized, strategy="guard_only", fluid=fluid,
                         thermal=FakeThermal(["normal"] * 3))


if __name__ == "__main__":
    unittest.main()

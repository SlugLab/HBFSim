import io
import json
import unittest

from energy import engineering_energy_profile
from maintenance_inputs import add_maintenance
from run_maintenance_point import execute, WINDOW_NS
from topology_service import default_config, media_cost_from_service_rate
from reliability import DAY_NS


def normalized(service):
    components = [{"id": "gpu", "device_id": "gpu", "role": "compute"}]
    for stack in service["fabric"]["hbf"]:
        components.append({"id": stack + ".base", "device_id": stack, "role": "base"})
        for channel in sorted(service["channels"][stack], key=int):
            components.append({"id": f"{stack}.die{channel}", "device_id": stack,
                               "role": "array_die", "die_index": int(channel)})
    return {"components": components}


class FakeThermal:
    def __init__(self, normalized_data, service, *, temperature_k=300.0, guard_state="normal"):
        self.components = [row["id"] for row in normalized_data["components"]]
        self.stacks = sorted(service["channels"])
        self.total = 0.0
        self.temperature_k = temperature_k
        self.guard_state = guard_state
        self.limits = {"hbf": [353.15, 363.15, 378.15],
                       "hbm": [353.15, 363.15, 378.15],
                       "gpu": [363.15, 373.15, 383.15]}

    def advance(self, start, end, component_energy):
        self.total += sum(component_energy.values())
        entity = {component: {"hotspot_k": self.temperature_k + self.total * 1e-6}
                  for component in self.components}
        return {"entity_temperatures_k": entity,
                "temperatures": {**{stack: self.temperature_k + self.total * 1e-6
                                     for stack in self.stacks}, "gpu": self.temperature_k},
                "stack_states": {stack: self.guard_state for stack in self.stacks},
                "hysteresis_budget_bytes": {stack: 10**18 for stack in self.stacks},
                "energy_j": {"cumulative": {"total_input_j": self.total}}}


class SequenceThermal(FakeThermal):
    def __init__(self, normalized_data, service, states):
        super().__init__(normalized_data, service)
        self.sequence = iter(states)

    def advance(self, start, end, component_energy):
        result = super().advance(start, end, component_energy)
        state = next(self.sequence)
        result["stack_states"] = {stack: state for stack in self.stacks}
        return result


def config(mode):
    service = default_config("all_hbf_direct")
    service["operation_media_cost"]["program"] = media_cost_from_service_rate(
        96_000_000_000, 655_360_000)
    service["operation_media_cost"]["erase"] = media_cost_from_service_rate(
        96_000_000_000, 16 * 4096 * 256 * 1000)
    service["operation_cost_evidence"] = "FIXED_TEST_ENGINEERING_PROXY"
    energy = engineering_energy_profile()
    energy["erase_array_j_per_operation"] = 50e-6
    return {"point_id": "maintenance-fixed", "strategy": "guard_only",
            "service": service, "energy": energy,
            "workload": {"active_ns": WINDOW_NS, "per_stack_Bps": 1,
                         "pattern": "continuous", "channel_distribution": "uniform"},
            "recovery_ns": 2 * WINDOW_NS, "gpu_external_w": 0,
            "control_disabled": True,
            "maintenance": {"mode": mode, "ea_ev": 1.04,
                "refresh_trigger": "equivalent_age_or_wall",
                "initial_equivalent_age_ns": DAY_NS, "initial_wall_age_ns": 0,
                "initial_temperature_k": 300.0, "block_bytes": 4096 * 256,
                "pages_per_block": 256, "aged_blocks_per_stack": 16,
                "spares_per_channel": 1, "max_blocks_per_cohort": 1,
                "program_energy_j_per_byte": 0.05 * 100e-6 / 4096,
                "erase_energy_j_per_operation": 50e-6,
                "energy_evidence": {"program": "FIXED_PROXY", "erase": "FIXED_PROXY"}}}


class MaintenanceRunnerTests(unittest.TestCase):
    def test_frozen_bounded_input_geometry(self):
        base = config("disabled")
        base.pop("maintenance")
        frozen = add_maintenance(base, "shared")
        self.assertEqual(frozen["maintenance"]["aged_blocks_per_stack"] *
                         frozen["maintenance"]["block_bytes"], 4 * 1024**3)
        self.assertEqual(frozen["maintenance"]["spares_per_channel"], 16)
        self.assertEqual(frozen["maintenance_scope"]["spares_per_stack"], 256)

    def test_shared_and_ideal_complete_same_exact_extents(self):
        for mode in ("shared", "ideal_independent"):
            cfg = config(mode)
            norm = normalized(cfg["service"])
            sink = io.StringIO()
            summary, final = execute(cfg, norm, FakeThermal(norm, cfg["service"]), sink)
            rows = [json.loads(line) for line in sink.getvalue().splitlines()]
            self.assertEqual(len(rows), 3)
            self.assertEqual(summary["maintenance_mode"], mode)
            self.assertEqual(summary["offered_bytes"], 0)
            self.assertEqual(final["driver"]["terminal_summary"]["extent_count"], 8 * 16)
            self.assertEqual(final["driver"]["terminal_summary"]["status_counts"],
                             {"COMMITTED": 8 * 16})
            self.assertEqual(len(final["driver"]["physical_wear"]), 2 * 8 * 16)
            self.assertIsNotNone(rows[0]["independent_maintenance_service"]
                                 if mode == "ideal_independent" else rows[0]["service"])
            self.assertLess(len(json.dumps(rows[0]["maintenance_delta"])), 200_000)
            self.assertTrue(all(not row["maintenance_delta"]["events"]
                                or row["maintenance_delta"]["reliability_events"]
                                for row in rows))

    def test_disabled_is_fresh_baseline(self):
        cfg = config("disabled")
        norm = normalized(cfg["service"])
        sink = io.StringIO()
        summary, final = execute(cfg, norm, FakeThermal(norm, cfg["service"]), sink)
        self.assertIsNone(final)
        self.assertEqual(summary["maintenance_mode"], "disabled")
        rows = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertTrue(all(row["reliability"]["status"] ==
                            "FRESH_BASELINE_NO_MAINTENANCE" for row in rows))

    def test_maintenance_due_facts_reach_feedback_policy(self):
        cfg = config("shared")
        cfg["strategy"] = "read_rate_feedback_thermal_guard_v1"
        cfg["workload"]["per_stack_Bps"] = 10**12
        for row in cfg["service"]["fabric"]["hbf"].values():
            row["direct_link"]["bandwidth_bytes_per_s"] = 100_000_000_000
        norm = normalized(cfg["service"])
        sink = io.StringIO()
        execute(cfg, norm, FakeThermal(norm, cfg["service"]), sink)
        first = json.loads(sink.getvalue().splitlines()[0])
        for decision in first["control"]["decisions"].values():
            reasons = decision["stack_decisions"][0]["reasons"]
            self.assertIn("MAINTENANCE_DUE_VISIBLE", reasons)

    def test_ideal_independent_preserves_shutdown_admission(self):
        cfg = config("ideal_independent")
        cfg["control_disabled"] = False
        cfg["maintenance"]["initial_equivalent_age_ns"] = DAY_NS - WINDOW_NS
        cfg["maintenance"]["initial_temperature_k"] = 358.15
        norm = normalized(cfg["service"])
        sink = io.StringIO()
        _, final = execute(
            cfg, norm,
            FakeThermal(norm, cfg["service"], temperature_k=358.15,
                        guard_state="shutdown"), sink)
        rows = [json.loads(line) for line in sink.getvalue().splitlines()]
        blocked = rows[1]["independent_maintenance_service"]["blocked"]
        self.assertTrue(blocked)
        self.assertTrue(all("shutdown" in reason
                            for row in blocked for reason in row["reasons"]))
        self.assertEqual(final["driver"]["terminal_summary"]["operation_count"], 0)

    def test_hbm_shared_endpoint_recovers_full_cap_after_light(self):
        cfg = config("disabled")
        cfg["service"] = default_config("mixed_direct")
        cfg["control_disabled"] = False
        cfg["strategy"] = "read_rate_feedback_thermal_guard_v1"
        norm = normalized(cfg["service"])
        sink = io.StringIO()
        execute(cfg, norm, SequenceThermal(norm, cfg["service"],
                                           ["light", "normal", "normal"]), sink)
        rows = [json.loads(line) for line in sink.getvalue().splitlines()]
        hbm = next(stack for stack in cfg["service"]["channels"] if stack.startswith("hbm"))
        baseline = sum(cfg["service"]["channels"][hbm].values()) * WINDOW_NS // 10**9
        self.assertEqual(rows[0]["control"]["next_budgets"][hbm], baseline // 2)
        self.assertEqual(rows[1]["control"]["next_budgets"][hbm], baseline)


if __name__ == "__main__":
    unittest.main()

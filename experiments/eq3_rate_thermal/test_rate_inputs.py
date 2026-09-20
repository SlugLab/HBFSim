import copy
import math
import unittest

from rate_inputs import build_windows


def profile(**updates):
    value = {
        "schema_version": 1,
        "reference_read_Bps": 1.6e12,
        "array_j_per_byte": 40e-12,
        "base_j_per_byte": 10e-12,
        "provenance": "SCENARIO_ASSUMPTION_USER_CONFIRMED",
    }
    value.update(updates)
    return value


def normalized():
    def component(component_id, stack, role, die_index):
        return {
            "id": component_id,
            "device_id": stack,
            "physical_type": "HBF",
            "powered": True,
            "role": role,
            "die_index": die_index,
        }

    return {
        "components": [
            {"id": "gpu", "device_id": "gpu", "physical_type": "GPU", "powered": True},
            component("array-A", "stack-A", "array_die", 1),
            component("base-A", "stack-A", "base_die", -1),
            component("array-B0", "stack-B", "array_die", 0),
            component("array-A0", "stack-A", "array_die", 0),
            component("base-B", "stack-B", "base_die", -1),
            component("array-B2", "stack-B", "array_die", 2),
            component("array-B1", "stack-B", "array_die", 1),
        ]
    }


class RateInputsTests(unittest.TestCase):
    def test_ocp_grade2_aggregate_limit_applies_without_explicit_channel_rates(self):
        settings = profile(
            channel_map={"stack-A": {str(i): "array-A" for i in range(16)}},
            channel_capacity_Bps={"stack-A": {str(i): 96e9 for i in range(16)}})
        schedule = {"schema_version": 1, "end_ns": 20_000_000,
                    "segments": [{"start_ns": 0, "end_ns": 20_000_000,
                                  "read_Bps": {"stack-A": 1.536e12}}]}
        result = build_windows(settings, schedule, normalized())
        self.assertAlmostEqual(result["windows"][0]["stacks"]["stack-A"]["source_power_w"]["total"], 76.8)
        schedule["segments"][0]["read_Bps"]["stack-A"] = 1.6e12
        with self.assertRaisesRegex(ValueError, "aggregate channel capacity"):
            build_windows(settings, schedule, normalized())

    def test_discovers_geometry_and_full_rate_maps_to_80w(self):
        schedule = {
            "schema_version": 1,
            "end_ns": 20_000_000,
            "segments": [{"start_ns": 0, "end_ns": 20_000_000,
                          "read_Bps": {"stack-A": 1.6e12}}],
        }
        original = copy.deepcopy(schedule)
        result = build_windows(profile(), schedule, normalized())
        self.assertEqual(schedule, original)
        window = result["windows"][0]
        stack_a = window["stacks"]["stack-A"]
        self.assertAlmostEqual(stack_a["requested_bytes"], 32e9)
        self.assertEqual(stack_a["requested_bytes"], stack_a["modelled_bytes"])
        self.assertAlmostEqual(stack_a["source_energy_j"]["array"], 1.28)
        self.assertAlmostEqual(stack_a["source_energy_j"]["base"], 0.32)
        self.assertAlmostEqual(stack_a["source_power_w"]["total"], 80.0)
        self.assertAlmostEqual(window["component_energy_j"]["array-A"], 0.64)
        self.assertAlmostEqual(window["component_energy_j"]["array-A0"], 0.64)
        self.assertAlmostEqual(window["component_energy_j"]["base-A"], 0.32)
        self.assertEqual(window["stacks"]["stack-B"]["requested_bytes"], 0.0)
        self.assertEqual(result["metadata"]["idle_semantics"], "ZERO_READ_ONLY_NOT_ZERO_IDLE")
        self.assertEqual(len(result["metadata"]["entity_mapping"]["stack-B"]["array_dies"]), 3)

    def test_integrates_piecewise_segments_across_window_boundaries(self):
        schedule = {
            "schema_version": 1,
            "end_ns": 40_000_000,
            "segments": [
                {"start_ns": 0, "end_ns": 10_000_000, "read_Bps": {}},
                {"start_ns": 10_000_000, "end_ns": 30_000_000,
                 "read_Bps": {"stack-A": 0.8e12}},
                {"start_ns": 30_000_000, "end_ns": 40_000_000,
                 "read_Bps": {"stack-A": 1.6e12}},
            ],
        }
        result = build_windows(profile(), schedule, normalized())
        self.assertEqual(len(result["windows"]), 2)
        self.assertAlmostEqual(result["windows"][0]["stacks"]["stack-A"]["requested_bytes"], 8e9)
        self.assertAlmostEqual(result["windows"][1]["stacks"]["stack-A"]["requested_bytes"], 24e9)
        self.assertAlmostEqual(result["windows"][1]["stacks"]["stack-A"]["source_energy_j"]["total"], 1.2)

    def test_explicit_weights_are_applied_to_discovered_dies(self):
        p = profile(die_weights={"stack-A": {"array-A": 0.25, "array-A0": 0.75}})
        schedule = {"schema_version": 1, "end_ns": 20_000_000,
                    "segments": [{"start_ns": 0, "end_ns": 20_000_000,
                                  "read_Bps": {"stack-A": 1.6e12}}]}
        result = build_windows(p, schedule, normalized())
        energy = result["windows"][0]["component_energy_j"]
        self.assertAlmostEqual(energy["array-A"], 0.32)
        self.assertAlmostEqual(energy["array-A0"], 0.96)
        self.assertEqual(result["metadata"]["entity_mapping"]["stack-A"]["weight_mode"], "EXPLICIT")
        self.assertEqual(result["metadata"]["entity_mapping"]["stack-B"]["weight_mode"], "UNIFORM")

    def test_explicit_channels_change_die_locality_without_changing_total_energy(self):
        p = profile(
            channel_map={"stack-A": {"c0": "array-A0", "c1": "array-A0",
                                     "c2": "array-A", "c3": "array-A"}},
            channel_capacity_Bps={"stack-A": {"c0": 0.4e12, "c1": 0.4e12,
                                              "c2": 0.4e12, "c3": 0.4e12}},
        )
        concentrated = {"schema_version": 1, "end_ns": 20_000_000,
                        "segments": [{"start_ns": 0, "end_ns": 20_000_000,
                                      "read_Bps": {"stack-A": 0.4e12},
                                      "channel_read_Bps": {"stack-A": {"c0": 0.4e12}}}]}
        distributed = copy.deepcopy(concentrated)
        distributed["segments"][0]["channel_read_Bps"]["stack-A"] = {
            "c0": 0.1e12, "c1": 0.1e12, "c2": 0.1e12, "c3": 0.1e12,
        }
        one = build_windows(p, concentrated, normalized())["windows"][0]
        four = build_windows(p, distributed, normalized())["windows"][0]
        self.assertEqual(one["stacks"]["stack-A"]["active_channel_count"], 1)
        self.assertEqual(four["stacks"]["stack-A"]["active_channel_count"], 4)
        self.assertEqual(one["stacks"]["stack-A"]["source_energy_j"],
                         four["stacks"]["stack-A"]["source_energy_j"])
        self.assertAlmostEqual(one["component_energy_j"]["array-A0"], 0.32)
        self.assertAlmostEqual(one["component_energy_j"]["array-A"], 0.0)
        self.assertAlmostEqual(four["component_energy_j"]["array-A0"], 0.16)
        self.assertAlmostEqual(four["component_energy_j"]["array-A"], 0.16)

        too_fast = copy.deepcopy(concentrated)
        too_fast["segments"][0]["read_Bps"]["stack-A"] = 0.5e12
        too_fast["segments"][0]["channel_read_Bps"]["stack-A"]["c0"] = 0.5e12
        with self.assertRaisesRegex(ValueError, "channel capacity"):
            build_windows(p, too_fast, normalized())
        inconsistent = copy.deepcopy(concentrated)
        inconsistent["segments"][0]["read_Bps"]["stack-A"] = 0.3e12
        with self.assertRaisesRegex(ValueError, "disagree"):
            build_windows(p, inconsistent, normalized())
        bad_die = copy.deepcopy(p)
        bad_die["channel_map"]["stack-A"]["c0"] = "not-a-die"
        with self.assertRaisesRegex(ValueError, "unknown array die"):
            build_windows(bad_die, concentrated, normalized())

    def test_rejects_invalid_rates_timeline_profile_weights_and_entities(self):
        base_schedule = {"schema_version": 1, "end_ns": 20_000_000,
                         "segments": [{"start_ns": 0, "end_ns": 20_000_000,
                                       "read_Bps": {"stack-A": 1.0}}]}
        bad_rates = [-1.0, math.nan, math.inf, 1.6e12 + 1.0]
        for rate in bad_rates:
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                schedule = copy.deepcopy(base_schedule)
                schedule["segments"][0]["read_Bps"]["stack-A"] = rate
                build_windows(profile(), schedule, normalized())
        with self.assertRaisesRegex(ValueError, "unknown HBF"):
            schedule = copy.deepcopy(base_schedule)
            schedule["segments"][0]["read_Bps"] = {"unknown": 1.0}
            build_windows(profile(), schedule, normalized())
        with self.assertRaisesRegex(ValueError, "continuously cover"):
            schedule = copy.deepcopy(base_schedule)
            schedule["segments"][0]["start_ns"] = 1
            build_windows(profile(), schedule, normalized())
        with self.assertRaisesRegex(ValueError, "approved value"):
            build_windows(profile(array_j_per_byte=41e-12), base_schedule, normalized())
        with self.assertRaisesRegex(ValueError, "sum to 1"):
            p = profile(die_weights={"stack-A": {"array-A": 0.2, "array-A0": 0.7}})
            build_windows(p, base_schedule, normalized())
        with self.assertRaisesRegex(ValueError, "no powered base_die"):
            entities = normalized()
            entities["components"] = [x for x in entities["components"] if x.get("id") != "base-A"]
            build_windows(profile(), base_schedule, entities)


if __name__ == "__main__":
    unittest.main()

import copy
import json
import math
from pathlib import Path
import unittest

from eq3_layered_ir import ValidationError, normalize


ROOT = Path(__file__).resolve().parents[1]


def _profile(mode="mixed_direct", hbm_count=3):
    if mode == "all_hbf_direct":
        hbm_count = 0
    elif mode in {"relay", "dash"}:
        hbm_count = 4
    placements = [{
        "id": "gpu", "device": "GPU", "xy_um": [0, 100],
        "footprint_um": [100, 100], "array_die_count": 0,
    }]
    blocks = [{
        "id": "substrate", "device": "package", "parent_device_id": "package",
        "component_role": "package_layer", "die_index": -1,
        "xyz_um": [0, 0, 0], "size_um": [800, 200, 10],
        "material": "mat", "power_group": None, "powered": False,
    }, {
        "id": "gpu", "device": "GPU", "parent_device_id": "gpu",
        "component_role": "compute_die", "die_index": 0,
        "xyz_um": [0, 100, 10], "size_um": [100, 100, 10],
        "material": "mat", "power_group": "gpu", "powered": True,
    }]
    stack_ids = []
    for index in range(8):
        kind = "HBM4" if index < hbm_count else "HBF"
        identity = ("hbm" if kind == "HBM4" else "hbf") + str(index)
        stack_ids.append(identity)
        placements.append({
            "id": identity, "device": kind, "xy_um": [100 * index, 0],
            "footprint_um": [100, 100], "array_die_count": 1,
        })
        blocks.extend(({
            "id": identity + ".base", "device": kind, "parent_device_id": identity,
            "component_role": "base_die", "die_index": -1,
            "xyz_um": [100 * index, 0, 10], "size_um": [100, 100, 10],
            "material": "mat", "power_group": identity + ".base", "powered": True,
        }, {
            "id": identity + ".die0", "device": kind, "parent_device_id": identity,
            "component_role": "array_die", "die_index": 0,
            "xyz_um": [100 * index, 0, 20], "size_um": [100, 100, 10],
            "material": "mat", "power_group": identity + ".array", "powered": True,
        }))
    topology = {"kind": mode, "gpu_links": [["gpu", stack] for stack in stack_ids]}
    if mode == "all_hbf_direct":
        topology["external_fast_memory_profile"] = "gddr-retained-identity"
    if mode in {"relay", "dash"}:
        hbm = stack_ids[:4]
        hbf = stack_ids[4:]
        topology["pairs"] = [[a, b] for a, b in zip(hbm, hbf)]
    if mode == "relay":
        topology["custom_base_die_relay"] = True
    return {
        "profile_id": "small-generic",
        "units": {"geometry": "um"},
        "package_size_um": [800, 200, 100],
        "materials": {"mat": {
            "rho_kg_m3": 2, "cp_j_kg_k": 3, "k_w_m_k": [4, 5, 6],
            "source_id": "test", "evidence_kind": "FIXTURE",
        }},
        "placements": placements,
        "blocks": blocks,
        "background": {
            "z_interval_um": [10, 100], "xy_extent_um": [0, 0, 800, 200],
            "material": "mat", "rule": "fill complement; no overlap with blocks",
        },
        "boundaries": {
            "initial_temperature_k": 300,
            "top": {"ambient_k": 300, "h_w_m2_k": 10},
            "bottom": {"ambient_k": 300, "h_w_m2_k": 5},
            "sides": "adiabatic",
            "additional_area_contact_resistance_m2_k_W": 0,
        },
        "model_domain": {"temperature_k": [280, 420]},
        "data_topology": topology,
    }


def _per_component_power(profile, values=None):
    powered = sorted(block["id"] for block in profile["blocks"] if block.get("powered"))
    row = {identity: 0.0 for identity in powered}
    if values:
        row.update(values)
    return {
        "mode": "per_component", "power_unit": "W", "slot_s": 0.25,
        "traces": {"t": {"duration_s": 0.5, "slots_W": [row, dict(row)]}},
    }


def _group_power(profile, groups, row, *, allow_additive=False):
    return {
        "mode": "explicit_group_weights", "power_unit": "W",
        "groups": groups, "allow_additive": allow_additive,
        "traces": {"t": {"intervals": [{"start_s": 0, "end_s": 2, "power_w": row}]}},
    }


def _geometry_in(profile, unit):
    result = copy.deepcopy(profile)
    divisor = {"mm": 1000.0, "m": 1_000_000.0}[unit]
    result["units"]["geometry"] = unit
    result[f"package_size_{unit}"] = [v / divisor for v in result.pop("package_size_um")]
    for placement in result["placements"]:
        placement[f"xy_{unit}"] = [v / divisor for v in placement.pop("xy_um")]
        placement[f"footprint_{unit}"] = [v / divisor for v in placement.pop("footprint_um")]
    for block in result["blocks"]:
        block[f"xyz_{unit}"] = [v / divisor for v in block.pop("xyz_um")]
        block[f"size_{unit}"] = [v / divisor for v in block.pop("size_um")]
    background = result["background"]
    background[f"z_interval_{unit}"] = [v / divisor for v in background.pop("z_interval_um")]
    background[f"xy_extent_{unit}"] = [v / divisor for v in background.pop("xy_extent_um")]
    return result


class LayeredIrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate = json.loads((ROOT / "configs/eq3_thermal/research/candidate_profile.json").read_text())
        cls.calibration = json.loads((ROOT / "configs/eq3_thermal/research/calibration_power.json").read_text())

    def test_current_first_example_explicit_equal_input(self):
        ir = normalize(self.candidate, self.calibration, "development")
        self.assertEqual(255, len(ir["components"]))
        self.assertEqual("mixed_direct", ir["topology"]["mode"])
        self.assertTrue(ir["power"]["legacy_explicit_equal_weights"])
        first = ir["power"]["intervals"][0]["power_w"]
        self.assertAlmostEqual(51.2 / 12, first["hbm0.die0"])
        self.assertAlmostEqual(12.8, first["hbm2.base"])
        self.assertTrue(ir["gaps"])
        self.assertEqual(self.candidate["data_topology"], ir["data_graph"])

    def test_data_graph_is_deep_copied(self):
        profile = _profile()
        ir = normalize(profile, _per_component_power(profile), "t")
        ir["data_graph"]["gpu_links"][0][0] = "changed"
        self.assertEqual("gpu", profile["data_topology"]["gpu_links"][0][0])

    def test_data_graph_rejects_undeclared_endpoint(self):
        profile = _profile()
        profile["data_topology"]["gpu_links"].append(["gpu", "ghost-stack"])
        with self.assertRaisesRegex(ValidationError, "undeclared endpoint"):
            normalize(profile, _per_component_power(profile), "t")

    def test_missing_data_graph_is_nonblocking_thermal_gap(self):
        profile = _profile()
        profile["data_topology"].pop("gpu_links")
        ir = normalize(profile, _per_component_power(profile), "t")
        gap = next(g for g in ir["gaps"] if g["parameter_path"] == "data_topology.gpu_links")
        self.assertEqual("system_behavior", gap["stage"])
        self.assertIn("thermal-only", gap["reason"])

    def test_si_geometry_material_and_background(self):
        profile = _profile()
        ir = normalize(profile, _per_component_power(profile), "t")
        for actual, expected in zip(ir["package_size_m"], [0.0008, 0.0002, 0.0001]):
            self.assertAlmostEqual(expected, actual)
        for actual, expected in zip(ir["background"]["z_range_m"], [1e-5, 0.0001]):
            self.assertAlmostEqual(expected, actual)
        self.assertEqual([4.0, 5.0, 6.0], ir["materials"]["mat"]["k_xyz_w_m_k"])
        self.assertEqual(6.0, ir["materials"]["mat"]["cv_j_m3_k"])
        die = next(item for item in ir["components"] if item["id"] == "hbm0.die0")
        self.assertEqual("array_die", die["role"])
        self.assertAlmostEqual(1e-12, die["volume_m3"])

    def test_um_mm_and_m_inputs_are_equivalent(self):
        micrometre = _profile()
        expected = normalize(micrometre, _per_component_power(micrometre), "t")
        for unit in ("mm", "m"):
            with self.subTest(unit=unit):
                profile = _geometry_in(micrometre, unit)
                actual = normalize(profile, _per_component_power(profile), "t")
                for left, right in zip(expected["package_size_m"], actual["package_size_m"]):
                    self.assertAlmostEqual(left, right)
                for left, right in zip(expected["components"], actual["components"]):
                    for axis in range(3):
                        self.assertAlmostEqual(left["xyz_m"][axis], right["xyz_m"][axis])
                        self.assertAlmostEqual(left["size_m"][axis], right["size_m"][axis])

    def test_unit_suffix_mismatch_is_not_reinterpreted(self):
        profile = _profile()
        profile["units"]["geometry"] = "m"
        with self.assertRaisesRegex(ValidationError, "package_size_m"):
            normalize(profile, _per_component_power(profile), "t")

    def test_non_four_plus_four_and_nonuniform_per_component(self):
        profile = _profile(hbm_count=3)
        power = _per_component_power(profile, {"hbm0.die0": 7, "hbf3.die0": 1.25})
        ir = normalize(profile, power, "t")
        self.assertEqual((3, 5), (ir["topology"]["hbm_count"], ir["topology"]["hbf_count"]))
        self.assertEqual(7.0, ir["power"]["intervals"][0]["power_w"]["hbm0.die0"])
        self.assertEqual(1.25, ir["power"]["intervals"][0]["power_w"]["hbf3.die0"])

    def test_component_reordering_does_not_change_ir(self):
        profile = _profile()
        power = _per_component_power(profile)
        normal = normalize(profile, power, "t")
        profile["blocks"].reverse()
        reordered = normalize(profile, power, "t")
        self.assertEqual(normal["components"], reordered["components"])
        self.assertEqual(normal["sensors"], reordered["sensors"])

    def test_all_four_topology_modes(self):
        for mode in ("all_hbf_direct", "mixed_direct", "relay", "dash"):
            with self.subTest(mode=mode):
                profile = _profile(mode)
                ir = normalize(profile, _per_component_power(profile), "t")
                self.assertEqual(mode, ir["topology"]["mode"])
                if mode == "all_hbf_direct":
                    external = ir["topology"]["external_devices"]
                    self.assertEqual("GDDR", external[0]["physical_type"])
                    self.assertFalse(external[0]["package_geometry_modeled"])

    def test_relay_and_dash_require_complete_explicit_pairs(self):
        for mode in ("relay", "dash"):
            profile = _profile(mode)
            profile["data_topology"].pop("pairs")
            with self.subTest(mode=mode), self.assertRaisesRegex(ValidationError, "pairs"):
                normalize(profile, _per_component_power(profile), "t")

    def test_all_hbf_requires_external_gddr_identity_not_geometry(self):
        profile = _profile("all_hbf_direct")
        profile["data_topology"].pop("external_fast_memory_profile")
        with self.assertRaisesRegex(ValidationError, "GDDR identity"):
            normalize(profile, _per_component_power(profile), "t")

    def test_explicit_nonuniform_group_weights_and_energy(self):
        profile = _profile()
        powered = sorted(b["id"] for b in profile["blocks"] if b.get("powered"))
        selected = powered[:2]
        groups = {"nonuniform": {selected[0]: 0.25, selected[1]: 0.75}}
        for identity in powered[2:]:
            groups[identity] = {identity: 1.0}
        row = {name: 0 for name in groups}
        row["nonuniform"] = 8
        ir = normalize(profile, _group_power(profile, groups, row), "t")
        values = ir["power"]["intervals"][0]["power_w"]
        self.assertEqual(2.0, values[selected[0]])
        self.assertEqual(6.0, values[selected[1]])
        self.assertEqual(16.0, ir["power"]["total_energy_j"])
        self.assertEqual(4.0, ir["power"]["component_energy_j"][selected[0]])

    def test_volume_weighted_stack_sensor(self):
        profile = _profile()
        # Add a second, twice-thick array die without relying on a fixed die count.
        placement = next(p for p in profile["placements"] if p["id"] == "hbm0")
        placement["array_die_count"] = 2
        profile["blocks"].append({
            "id": "hbm0.die1", "device": "HBM4", "parent_device_id": "hbm0",
            "component_role": "array_die", "die_index": 1,
            "xyz_um": [0, 0, 30], "size_um": [100, 100, 20], "material": "mat",
            "power_group": "hbm0.array.1", "powered": True,
        })
        ir = normalize(profile, _per_component_power(profile), "t")
        sensor = next(s for s in ir["sensors"] if s["id"] == "stack:hbm0:array_mean")
        weights = {item["component_id"]: item["weight"] for item in sensor["weights"]}
        self.assertAlmostEqual(1 / 3, weights["hbm0.die0"])
        self.assertAlmostEqual(2 / 3, weights["hbm0.die1"])

    def test_stack_sensor_includes_base_and_array_even_if_base_unpowered(self):
        profile = _profile()
        base = next(block for block in profile["blocks"] if block["id"] == "hbm0.base")
        base["powered"] = False
        base["power_group"] = None
        ir = normalize(profile, _per_component_power(profile), "t")
        sensor = next(item for item in ir["sensors"] if item["id"] == "stack:hbm0:mean")
        self.assertEqual({"hbm0.base", "hbm0.die0"}, {w["component_id"] for w in sensor["weights"]})
        self.assertTrue(any(item["id"] == "component:hbm0.base:mean" for item in ir["sensors"]))

    def test_unpowered_array_die_still_has_component_and_stack_sensors(self):
        profile = _profile()
        die = next(block for block in profile["blocks"] if block["id"] == "hbm0.die0")
        die["powered"] = False
        die["power_group"] = None
        ir = normalize(profile, _per_component_power(profile), "t")
        self.assertTrue(any(item["id"] == "component:hbm0.die0:mean" for item in ir["sensors"]))
        stack = next(item for item in ir["sensors"] if item["id"] == "stack:hbm0:array_mean")
        self.assertEqual(["hbm0.die0"], [item["component_id"] for item in stack["weights"]])

    def test_powered_requires_boolean(self):
        profile = _profile()
        profile["blocks"][1]["powered"] = "false"
        with self.assertRaisesRegex(ValidationError, "JSON boolean"):
            normalize(profile, _per_component_power(profile), "t")

    def test_temperature_domain_is_required_and_ordered(self):
        profile = _profile()
        profile["model_domain"]["temperature_k"] = [400, 300]
        with self.assertRaisesRegex(ValidationError, "low < high"):
            normalize(profile, _per_component_power(profile), "t")
        profile = _profile()
        profile.pop("model_domain")
        with self.assertRaisesRegex(ValidationError, "model_domain"):
            normalize(profile, _per_component_power(profile), "t")

    def test_temperature_domain_and_provenance_are_preserved(self):
        profile = _profile()
        profile.update({"schema_version": "p1", "status": "TEST", "evidence_kind": "FIXTURE"})
        power = _per_component_power(profile)
        power.update({"schema_version": "w1", "status": "TEST", "evidence_kind": "SYNTHETIC"})
        ir = normalize(profile, power, "t")
        self.assertEqual([280.0, 420.0], ir["temperature_domain_k"])
        self.assertEqual("FIXTURE", ir["provenance"]["profile_evidence_kind"])
        self.assertEqual("SYNTHETIC", ir["provenance"]["power_evidence_kind"])

    def test_system_stage_rejects_and_carries_gaps(self):
        profile = _profile()
        with self.assertRaises(ValidationError) as caught:
            normalize(profile, _per_component_power(profile), "t", stage="system_behavior")
        self.assertTrue(caught.exception.gaps)
        self.assertTrue(all(gap["stage"] == "system_behavior" for gap in caught.exception.gaps))

    def test_closed_system_behavior_can_pass(self):
        profile = _profile()
        profile["data_topology"]["system_behavior"] = {
            "path_definition": "defined", "shared_resources": "defined",
            "arbitration": "defined", "latency": "defined", "phy_energy": "defined",
            "maintenance_rules": "defined",
        }
        ir = normalize(profile, _per_component_power(profile), "t", stage="system_behavior")
        self.assertEqual([], ir["gaps"])

    def test_unknown_topology_has_no_fallback(self):
        profile = _profile()
        profile["data_topology"]["kind"] = "typo"
        with self.assertRaisesRegex(ValidationError, "no fallback"):
            normalize(profile, _per_component_power(profile), "t")

    def test_unknown_identity_cannot_fallback_to_recognized_layout(self):
        profile = _profile("relay")
        profile["data_topology"]["kind"] = "not-a-topology"
        profile["data_topology"]["layout"] = "relay"
        with self.assertRaisesRegex(ValidationError, "not-a-topology"):
            normalize(profile, _per_component_power(profile), "t")

    def test_missing_identity_may_use_declared_layout(self):
        profile = _profile()
        profile["data_topology"] = {
            "layout": "direct",
            "gpu_links": [["gpu", placement["id"]] for placement in profile["placements"]
                          if placement["device"] in {"HBM4", "HBF"}],
        }
        ir = normalize(profile, _per_component_power(profile), "t")
        self.assertEqual("mixed_direct", ir["topology"]["mode"])

    def test_geometry_overlap_rejected(self):
        profile = _profile()
        block = next(b for b in profile["blocks"] if b["id"] == "hbf3.base")
        block["xyz_um"] = [0, 0, 10]
        with self.assertRaisesRegex(ValidationError, "overlap"):
            normalize(profile, _per_component_power(profile), "t")

    def test_invalid_fill_rule_rejected(self):
        profile = _profile()
        profile["background"]["rule"] = "paint everything"
        with self.assertRaisesRegex(ValidationError, "complement fill"):
            normalize(profile, _per_component_power(profile), "t")

    def test_missing_and_invalid_material_rejected(self):
        profile = _profile()
        profile["materials"]["mat"]["k_w_m_k"][2] = 0
        with self.assertRaisesRegex(ValidationError, "greater than zero"):
            normalize(profile, _per_component_power(profile), "t")
        profile = _profile()
        profile["blocks"][0]["material"] = "absent"
        with self.assertRaisesRegex(ValidationError, "unknown material"):
            normalize(profile, _per_component_power(profile), "t")

    def test_missing_base_rejected(self):
        profile = _profile()
        profile["blocks"] = [b for b in profile["blocks"] if b["id"] != "hbm0.base"]
        power = _per_component_power(profile)
        with self.assertRaisesRegex(ValidationError, "base_die"):
            normalize(profile, power, "t")

    def test_source_must_explicitly_include_zero_for_every_component(self):
        profile = _profile()
        power = _per_component_power(profile)
        power["traces"]["t"]["slots_W"][0].pop("gpu")
        with self.assertRaisesRegex(ValidationError, "explicitly specify every source"):
            normalize(profile, power, "t")

    def test_group_negative_and_nonunit_weights_rejected(self):
        profile = _profile()
        powered = sorted(b["id"] for b in profile["blocks"] if b.get("powered"))
        for weights, message in (({powered[0]: -1, powered[1]: 2}, "non-negative"),
                                 ({powered[0]: 0.4, powered[1]: 0.5}, "sum to one")):
            groups = {"g": weights}
            groups.update({identity: {identity: 1} for identity in powered[2:]})
            with self.subTest(message=message), self.assertRaisesRegex(ValidationError, message):
                normalize(profile, _group_power(profile, groups, {name: 0 for name in groups}), "t")

    def test_group_member_list_rejects_duplicate_identity(self):
        profile = _profile()
        powered = sorted(b["id"] for b in profile["blocks"] if b.get("powered"))
        duplicate = powered[0]
        groups = {
            "g": {"members": [
                {"component_id": duplicate, "weight": 0.5},
                {"component_id": duplicate, "weight": 0.5},
            ]}
        }
        groups.update({identity: {identity: 1} for identity in powered[1:]})
        with self.assertRaisesRegex(ValidationError, "duplicate member"):
            normalize(profile, _group_power(profile, groups, {name: 0 for name in groups}), "t")

    def test_allow_additive_requires_boolean(self):
        profile = _profile()
        power = _per_component_power(profile)
        power["allow_additive"] = "false"
        with self.assertRaisesRegex(ValidationError, "JSON boolean"):
            normalize(profile, power, "t")

    def test_vector_group_order_rejects_duplicate_source(self):
        profile = _profile()
        power = _per_component_power(profile)
        powered = sorted(b["id"] for b in profile["blocks"] if b.get("powered"))
        power["group_order"] = [powered[0], powered[0]]
        power["traces"]["t"]["slots_W"] = [[0, 0], [0, 0]]
        with self.assertRaisesRegex(ValidationError, "source identities must be unique"):
            normalize(profile, power, "t")

    def test_power_initial_temperature_must_match_profile(self):
        profile = _profile()
        power = _per_component_power(profile)
        power["initial_k"] = 301
        with self.assertRaisesRegex(ValidationError, "conflicts with authoritative"):
            normalize(profile, power, "t")
        power["initial_k"] = 300
        ir = normalize(profile, power, "t")
        self.assertEqual(300.0, ir["power"]["initial_temperature_k"])

    def test_duplicate_group_assignment_requires_explicit_additive(self):
        profile = _profile()
        powered = sorted(b["id"] for b in profile["blocks"] if b.get("powered"))
        groups = {"a": {powered[0]: 1}, "b": {powered[0]: 1}}
        groups.update({identity: {identity: 1} for identity in powered[1:]})
        with self.assertRaisesRegex(ValidationError, "allow_additive"):
            normalize(profile, _group_power(profile, groups, {name: 0 for name in groups}), "t")
        ir = normalize(
            profile,
            _group_power(profile, groups, {name: (1 if name in {"a", "b"} else 0) for name in groups}, allow_additive=True),
            "t",
        )
        self.assertEqual(2.0, ir["power"]["intervals"][0]["power_w"][powered[0]])

    def test_overlapping_time_intervals_rejected(self):
        profile = _profile()
        power = _per_component_power(profile)
        row = power["traces"]["t"]["slots_W"][0]
        power["traces"]["t"] = {"intervals": [
            {"start_s": 0, "end_s": 2, "power_w": row},
            {"start_s": 1, "end_s": 3, "power_w": row},
        ]}
        with self.assertRaisesRegex(ValidationError, "must not overlap"):
            normalize(profile, power, "t")

    def test_time_gap_is_not_silently_treated_as_zero(self):
        profile = _profile()
        power = _per_component_power(profile)
        row = power["traces"]["t"]["slots_W"][0]
        power["traces"]["t"] = {"intervals": [
            {"start_s": 0, "end_s": 1, "power_w": row},
            {"start_s": 2, "end_s": 3, "power_w": row},
        ]}
        with self.assertRaisesRegex(ValidationError, "implicit-zero gap"):
            normalize(profile, power, "t")

    def test_declared_topology_count_must_match_geometry(self):
        profile = _profile()
        profile["data_topology"]["hbm_count"] = 4
        with self.assertRaisesRegex(ValidationError, "geometry implies 3"):
            normalize(profile, _per_component_power(profile), "t")


if __name__ == "__main__":
    unittest.main()

import copy
import unittest
from pathlib import Path

from eq3_thermal_config import generate, load, validate_devices

ROOT = Path(__file__).resolve().parents[1] / "configs/eq3_thermal"


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.devices = load(ROOT / "devices.json")
        self.thermal = load(ROOT / "thermal_fixture.json")
        self.power = load(ROOT / "power_fixture.json")

    def gen(self, config):
        return generate(config, self.devices, self.thermal, self.power)

    def test_four_layouts_and_distinct_graphs(self):
        for path in (ROOT / "topologies").glob("*.json"):
            with self.subTest(path=path.name):
                config = load(path)
                model, events, graph = self.gen(config)
                self.assertEqual(len(graph["stacks"]), 8)
                self.assertIn("HBFSIM_EQ3_THERMAL_MODEL 1", model)
                self.assertIn("external_heat external", events)
                self.assertEqual(len(graph["thermal_nodes"]), len(set(graph["thermal_nodes"])))
                if config["layout"] == "direct":
                    self.assertTrue(all(e["to"] == "gpu" for e in graph["data_edges"]))
                if config["layout"] == "dual":
                    direct = next(e for e in graph["data_edges"] if e["from"] == "hbf0" and e["to"] == "gpu")
                    relay = next(e for e in graph["data_edges"] if e["from"] == "hbf0" and e["to"] != "gpu")
                    self.assertEqual(direct["shared"], relay["shared"][:2])
                    self.assertFalse(relay["dram_array_access"])
                    self.assertEqual(config["name_status"], "USER_CONFIRMED")

    def test_gddr_not_packaged_hbm(self):
        model, _, graph = self.gen(load(ROOT / "topologies/all_hbf_direct_8.json"))
        self.assertIn("node gddr gddr fast_memory gddr", model)
        self.assertEqual(sum(s["physical_kind"] == "HBF" for s in graph["stacks"]), 8)
        self.assertFalse(any(s["stack_id"] == "gddr" for s in graph["stacks"]))

    def test_arbitrary_stack_count_and_4plus4(self):
        config = load(ROOT / "topologies/mixed_direct_8.json")
        for hbm, hbf in ((4, 4), (1, 2), (1, 0)):
            config.update(hbm_count=hbm, hbf_count=hbf, stack_count=hbm+hbf)
            _, _, graph = self.gen(config)
            self.assertEqual(len(graph["stacks"]), hbm+hbf)

    def test_invalid_counts_and_pending_height(self):
        config = load(ROOT / "topologies/mixed_direct_8.json")
        config["stack_count"] = 9
        with self.assertRaises(ValueError): self.gen(config)
        config["stack_count"] = 8
        config["hbf_profile"] = "ocp_hbf_3072"
        with self.assertRaises(ValueError): self.gen(config)

    def test_capacity_and_bandwidth_guard(self):
        validate_devices(self.devices)
        bad = copy.deepcopy(self.devices)
        bad["profiles"]["sandisk_hbf_gen1_1600"]["capacity_bytes"] *= 2
        with self.assertRaises(ValueError): validate_devices(bad)
        bad = copy.deepcopy(self.devices)
        bad["profiles"]["micron_hbm4_36gb_12hi"]["operating_point"]["peak_bytes_s"] *= 2
        with self.assertRaises(ValueError): validate_devices(bad)
        bad = copy.deepcopy(self.devices)
        bad["profiles"]["ocp_hbf_0384"]["die_count"] = 16
        with self.assertRaises(ValueError): validate_devices(bad)

    def test_nonfinite_rejected(self):
        self.thermal["capacity_j_k"]["GPU"] = float("nan")
        with self.assertRaises(ValueError): self.gen(load(ROOT / "topologies/mixed_direct_8.json"))

    def test_zero_conductance_omits_edge(self):
        self.thermal["conductance_w_k"]["gpu_interposer"] = 0
        _, _, graph = self.gen(load(ROOT / "topologies/mixed_direct_8.json"))
        self.assertFalse(any(a == "gpu" and b == "interposer" for a, b, g in graph["thermal_edges"]))

    def test_activity_fields_and_resources_preserved(self):
        self.power.update(operation="read", origin="refresh")
        _, events, graph = self.gen(load(ROOT / "topologies/daisy_4hbm_4hbf.json"))
        self.assertIn("read refresh", events)
        self.assertIn("relay refresh", events)
        for edge in graph["data_edges"]:
            for resource in edge.get("shared", []) + ([edge["resource"]] if "resource" in edge else []):
                self.assertIn(resource, graph["shared_resources"])
        self.power["operation"] = "invented"
        with self.assertRaises(ValueError): self.gen(load(ROOT / "topologies/mixed_direct_8.json"))
        self.power["operation"] = "erase"
        with self.assertRaises(ValueError): self.gen(load(ROOT / "topologies/mixed_direct_8.json"))


if __name__ == "__main__":
    unittest.main()

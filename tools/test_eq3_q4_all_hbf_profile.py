import copy
import json
from pathlib import Path
import tempfile
import unittest

from eq3_layered_ir import normalize
from eq3_q4_all_hbf_profile import PAIRING, convert_profile, fixture_power, _write_new


ROOT = Path(__file__).resolve().parents[1]


class Q4AllHbfProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads(
            (ROOT / "configs/eq3_thermal/research/candidate_profile.json").read_text()
        )

    def test_complete_templates_translate_without_relabeling_hbm(self):
        q4 = convert_profile(self.source)
        placements = {row["id"]: row for row in q4["placements"]}
        self.assertEqual({"gpu", *(f"hbf{i}" for i in range(8))}, set(placements))
        self.assertFalse(any(block["id"].startswith("hbm") for block in q4["blocks"]))
        for old, template, new in PAIRING:
            self.assertEqual(16, placements[new]["array_die_count"])
            self.assertEqual(
                next(x for x in self.source["placements"] if x["id"] == old)["xy_um"],
                placements[new]["xy_um"],
            )
            source_blocks = {
                block["id"].replace(template, new, 1): block
                for block in self.source["blocks"]
                if block["id"].startswith(template + ".")
            }
            new_blocks = {
                block["id"]: block for block in q4["blocks"] if block["id"].startswith(new + ".")
            }
            self.assertEqual(set(source_blocks), set(new_blocks))
            self.assertEqual(list(range(16)), sorted(
                block["die_index"] for block in new_blocks.values() if ".die" in block["id"]
            ))
            for identity, cloned in new_blocks.items():
                original = source_blocks[identity]
                self.assertEqual(original["size_um"], cloned["size_um"])
                self.assertEqual(original["xyz_um"][2], cloned["xyz_um"][2])
                self.assertEqual(original["material"], cloned["material"])

    def test_package_physics_preserved_and_external_gddr_outside_domain(self):
        q4 = convert_profile(self.source)
        for key in ("package_size_um", "materials", "background", "boundaries", "model_domain"):
            self.assertEqual(self.source[key], q4[key])
        topology = q4["data_topology"]
        self.assertEqual("all_hbf_direct", topology["kind"])
        self.assertEqual(8, topology["hbf_count"])
        self.assertEqual(0, topology["hbm_count"])
        self.assertFalse(topology["external_fast_memory_profile"]["package_geometry_modeled"])
        self.assertEqual("UNAVAILABLE", q4["device_selection"]["gddr"]["package_temperature"])

    def test_normalizer_energy_and_sensor_contract(self):
        q4 = convert_profile(self.source)
        power = fixture_power(q4)
        ir = normalize(q4, power, "mapping_20ms")
        self.assertEqual("all_hbf_direct", ir["topology"]["mode"])
        self.assertEqual(8, ir["topology"]["hbf_count"])
        self.assertEqual(0, ir["topology"]["hbm_count"])
        self.assertEqual(287, len(ir["components"]))
        self.assertEqual(307, len(ir["sensors"]))
        self.assertEqual(137, sum(component["powered"] for component in ir["components"]))
        self.assertAlmostEqual(2.74, ir["power"]["total_energy_j"], places=12)
        external = next(x for x in ir["devices"] if x["id"] == "external_fast_memory")
        self.assertFalse(external["package_geometry_modeled"])

    def test_rejects_rotation_or_incomplete_template(self):
        rotated = copy.deepcopy(self.source)
        next(x for x in rotated["placements"] if x["id"] == "hbm0")["footprint_um"] = [16000, 12000]
        with self.assertRaisesRegex(ValueError, "orientation"):
            convert_profile(rotated)
        incomplete = copy.deepcopy(self.source)
        incomplete["blocks"] = [x for x in incomplete["blocks"] if x["id"] != "hbf0.die15"]
        with self.assertRaisesRegex(ValueError, "complete 35-block"):
            convert_profile(incomplete)

    def test_outputs_are_create_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out.json"
            _write_new(path, {"a": 1})
            with self.assertRaises(FileExistsError):
                _write_new(path, {"a": 2})


if __name__ == "__main__":
    unittest.main()

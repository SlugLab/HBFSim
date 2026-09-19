import json
import pathlib
import tempfile
import unittest

import eq3_reference


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "eq3_thermal" / "reference"


class ReferenceGeneratorTest(unittest.TestCase):
    def setUp(self):
        self.case = eq3_reference.load(CONFIG / "package_scenario.json")
        self.materials = eq3_reference.load(CONFIG / "materials.json")
        self.power = eq3_reference.load(CONFIG / "power_traces.json")

    def test_case_validates_and_covers_package(self):
        eq3_reference.validate_case(self.case, self.materials, self.power)

    def test_si_capacity_and_face_conductance_appear_in_model(self):
        text = eq3_reference.rc_model_text(self.case, self.materials)
        rho_cp = 2330.0 * 710.0
        expected_gpu = rho_cp * 0.02 * 0.02 * 0.0001
        gpu = next(line for line in text.splitlines() if line.startswith("node gpu "))
        self.assertAlmostEqual(float(gpu.split()[6]), expected_gpu)
        self.assertIn("edge gpu hbm0", text)
        self.assertIn("edge gpu hbf2", text)

    def test_reference_units_are_converted_to_micrometre_convention(self):
        text = eq3_reference.stack_text(self.case, self.materials, self.power, "fine", 0.05, "train")
        self.assertIn("thermal conductivity 0.00015", text)
        self.assertIn("volumetric heat capacity 1.6543e-12", text)
        self.assertIn("cell length 1000", text)
        self.assertIn("transient step 0.05, slot 0.5", text)

    def test_train_and_heldout_are_distinct_complete_traces(self):
        train = eq3_reference.trace_vectors(self.power, "train")
        held = eq3_reference.trace_vectors(self.power, "heldout")
        self.assertNotEqual(train, held)
        self.assertEqual(set(train), set(eq3_reference.SENSORS))
        self.assertEqual(len(held["gpu"]), 9)
        self.assertEqual(held["hbf2"][5], 32)

    def test_generate_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            out = pathlib.Path(temp) / "run"
            out.mkdir()
            args = type("Args", (), {"case":CONFIG/"package_scenario.json", "materials":CONFIG/"materials.json",
                                      "power":CONFIG/"power_traces.json", "trace":"train", "mesh":"coarse",
                                      "step_s":0.1, "output":out})()
            with self.assertRaises(FileExistsError):
                eq3_reference.generate(args)


if __name__ == "__main__":
    unittest.main()

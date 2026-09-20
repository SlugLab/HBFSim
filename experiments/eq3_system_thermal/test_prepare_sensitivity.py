import json
from pathlib import Path
import tempfile
import unittest

from prepare_sensitivity import prepare
from launch_sensitivity import domain_failure


def model(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir()
    for filename in ("model.txt", "normalized.json", "rc_grid.json", "rc_sensors.json"):
        (path / filename).write_text(filename + "\n")
    return path


class SensitivityPreparationTest(unittest.TestCase):
    def test_frozen_counts_consumers_and_shutdown_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mixed = model(root, "mixed")
            variants = []
            for name, ambient, resistance in (
                    ("a310", 310, 1.0), ("a320", 320, 1.0),
                    ("r05", 300, .5), ("r15", 300, 1.5), ("a320r15", 320, 1.5)):
                path = model(root, name)
                variants.append({"ambient_k": ambient, "external_resistance_scale": resistance,
                                 "coupling_mode": "full", "model_dir": str(path)})
            manifest = root / "models.json"
            manifest.write_text(json.dumps({"source_models": {
                "mixed_full_2mm": {"model_dir": str(mixed)}},
                "derived_models": variants}) + "\n")
            artifacts = root / "artifacts"; artifacts.mkdir()
            binary = artifacts / "thermal"; binary.write_text("fixture\n")
            output = root / "prepared"
            index = prepare(output, manifest, binary, artifacts)
            self.assertEqual(index["point_count"], 54)
            self.assertEqual(index["schema_version"], "eq3-system-sensitivity-index-v2")
            self.assertTrue(index["runner"].endswith("run_endpoint_guard_point.py"))
            self.assertEqual(index["status"], "PENDING_DEPENDENCIES_BASE_MATRIX")
            self.assertEqual(index["resources"]["sensitivity_output_gib"], 27)
            self.assertEqual(index["resources"]["parent_combined_output_gib"], 80)
            self.assertEqual(len({row["point_id"] for row in index["points"]}), 54)
            self.assertEqual(sum(row["topology"] == "mixed_direct" for row in index["points"]), 48)
            self.assertEqual(sum(row["topology"] == "relay" for row in index["points"]), 6)
            self.assertEqual(sum(row["execution_mode"] == "uncontrolled_first_constraint"
                                 for row in index["points"]), 18)
            relay = [row for row in index["points"] if row["topology"] == "relay"]
            self.assertTrue(all(row["mechanism_scope"] ==
                                "RELAY_HBM_ENDPOINT_ACTUAL_CONSUMER_EXTENSION" for row in relay))
            for row in index["points"]:
                config = json.loads(Path(row["config"]).read_text())
                self.assertEqual(config["thermal_limits_k"]["hbf"][2], 378.15)
                self.assertEqual(config["thermal_limits_k"]["hbm"][2], 378.15)
                self.assertEqual(config["thermal_limits_k"]["gpu"], [363.15, 373.15, 383.15])
                self.assertEqual(config["sensitivity"]["domain_max_k_unchanged"], 400)
                if row["execution_mode"] == "uncontrolled_first_constraint":
                    self.assertTrue(config["control_disabled"])
            deferred = json.loads((output / "DEFERRED_EA.json").read_text())
            self.assertFalse(deferred["runnable"])
            self.assertEqual(deferred["point_count_when_ready"], 6)
            self.assertEqual(deferred["ea_ev"], [1.01, 1.04, 1.08])

    def test_launcher_classifies_only_explicit_thermal_domain_failure(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td)
            process = output / "thermal-process"; process.mkdir()
            save = lambda path, value: path.write_text(json.dumps(value) + "\n")
            save(output / "FAILED.json", {"status": "FAILED"})
            transcript = process / "thermal-transcript.jsonl"
            save(transcript, {"response": {"type": "ERROR", "status": "NUMERICAL_FAILURE"}})
            self.assertFalse(domain_failure(output))
            transcript.write_text(json.dumps({"response": {
                "type": "ERROR", "status": "DOMAIN_FAILURE"}}) + "\n")
            self.assertTrue(domain_failure(output))


if __name__ == "__main__":
    unittest.main()

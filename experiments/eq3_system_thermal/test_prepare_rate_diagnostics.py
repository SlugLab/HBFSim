import json
from pathlib import Path
import tempfile
import unittest

from prepare_rate_diagnostics import RATE, STRATEGIES, TOPOLOGIES, digest, prepare
from prepare_stage import config as base_config


def model(root, name):
    path = root / name
    path.mkdir()
    for filename in ("model.txt", "normalized.json", "rc_grid.json", "rc_sensors.json"):
        (path / filename).write_text(filename + "\n")
    return path


class RateDiagnosticPreparationTests(unittest.TestCase):
    def test_nine_points_and_frozen_existing_base_comparisons(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mixed = model(root, "mixed")
            all_hbf = model(root, "all-hbf")
            no_cross = model(root, "no-cross")
            model_manifest = root / "models.json"
            model_manifest.write_text(json.dumps({
                "source_models": {
                    "mixed_full_2mm": {"model_dir": str(mixed)},
                    "all_hbf_full_2mm": {"model_dir": str(all_hbf)},
                },
                "derived_models": [{"ambient_k": 300, "external_resistance_scale": 1,
                    "coupling_mode": "no_cross_domain_lateral", "model_dir": str(no_cross)}],
            }) + "\n")
            base_inputs = root / "base-inputs"; base_inputs.mkdir()
            base_points = []
            identities = [(topology, RATE, strategy) for topology in TOPOLOGIES
                          for strategy in STRATEGIES]
            identities += [("all_hbf_direct", 768_000_000_000, strategy)
                           for strategy in STRATEGIES]
            for index, (topology, rate, strategy) in enumerate(identities):
                value = base_config(f"base-{index}", topology, rate, strategy, 20, 10)
                path = base_inputs / f"{index}.json"
                path.write_text(json.dumps(value) + "\n")
                base_points.append({"point_id": value["point_id"], "kind": "base",
                    "config": str(path), "config_sha256": digest(path),
                    "output": str(root / "base-points" / value["point_id"])})
            base_index = root / "RUN_INDEX.json"
            base_index.write_text(json.dumps({"points": base_points,
                                              "source_locks": {"fixture": "locked"}}) + "\n")
            artifacts = root / "artifacts"; artifacts.mkdir()
            binary = artifacts / "thermal"; binary.write_text("fixture\n")
            output = root / "prepared"
            result = prepare(output, model_manifest, base_index, binary, artifacts)

            self.assertEqual(result["point_count"], 9)
            self.assertEqual(result["schema_version"], "eq3-system-rate-diagnostics-index-v2")
            self.assertTrue(result["runner"].endswith("run_endpoint_guard_point.py"))
            self.assertEqual(result["resources"]["new_output_gib"], 4.5)
            self.assertEqual(result["resources"]["parent_accounting_gib"]
                             ["remaining_for_maintenance_causal"], 18.5)
            self.assertEqual(len(result["comparisons"]["nonuniform_vs_uniform"]), 8)
            self.assertEqual(len(result["comparisons"]["no_coupling_vs_full"]), 1)
            self.assertEqual(len(result["comparisons"]["same_total_offered_existing_base"]), 3)
            self.assertEqual(len(result["comparisons"]["same_per_stack_existing_base"]), 3)
            for row in result["points"]:
                value = json.loads(Path(row["config"]).read_text())
                self.assertEqual(value["strategy"], "read_rate_feedback_thermal_guard_v1")
                self.assertEqual(value["workload"]["active_ns"], 20_000_000_000)
                self.assertEqual(value["recovery_ns"], 10_000_000_000)
                self.assertEqual(value["workload"]["per_stack_Bps"], RATE)
            hot = [json.loads(Path(row["config"]).read_text()) for row in result["points"]
                   if row["diagnostic"].startswith("HOT_STACK")]
            self.assertEqual(len(hot), 4)
            self.assertTrue(all(row["workload"]["hot_stack"] == "hbf0" for row in hot))


if __name__ == "__main__":
    unittest.main()

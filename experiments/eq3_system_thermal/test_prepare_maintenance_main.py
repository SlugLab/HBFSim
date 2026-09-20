#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from maintenance_inputs import add_maintenance
from prepare_maintenance_main import prepare, STRATEGIES, TOPOLOGIES
from prepare_stage import config as base_config


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True,
                               allow_nan=False) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MaintenanceMainPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pilot_index = self.root / "PILOT_INDEX.json"
        self.analysis_root = self.root / "analysis"
        points = []
        for topology in TOPOLOGIES:
            point_id = f"pilot-{topology}"
            config = add_maintenance(base_config(
                point_id, topology, 1_536_000_000_000, STRATEGIES[2], 8, 4), "shared")
            config["point_id"] = point_id
            config_path = self.root / "pilot-inputs" / f"{point_id}.json"
            save(config_path, config)
            output = self.root / "pilot-points" / point_id
            save(output / "DONE.json", {"status": "COMPLETED"})
            analysis = {
                "analysis_status": "VALIDATED_COMPLETE_RECEIPTS", "runner": "maintenance",
                "identity": {"point_id": point_id},
                "checks": {"timeline": "PASS", "byte_conservation": "PASS",
                           "energy_to_thermal": "PASS",
                           "terminal_uniqueness": "EXACT_JOB_IDS"},
                "maintenance": {"terminal_operation_count": 4},
            }
            save(self.analysis_root / point_id / "analysis.json", analysis)
            save(self.analysis_root / point_id / "DONE.json", {"status": "COMPLETED"})
            points.append({
                "point_id": point_id, "topology": topology,
                "config": str(config_path), "config_sha256": digest(config_path),
                "output": str(output), "model_dir": f"model-{topology}",
                "thermal_binary": "thermal-service", "artifact_root": "artifacts",
            })
        save(self.pilot_index, {
            "points": points, "runner": "run_maintenance_point.py",
            "runtime_source_locks_sha256": {"runner": "a" * 64},
            "model_locks_sha256": {"model": {"normalized.json": "b" * 64}},
            "thermal_binary_sha256": "c" * 64,
        })
        analysis_hashes = {row["point_id"]: digest(
            self.analysis_root / row["point_id"] / "analysis.json") for row in points}
        self.review = self.root / "PILOT_REVIEW.json"
        save(self.review, {
            "status": "APPROVED_FOR_MAIN_INPUT_GENERATION",
            "pilot_index_sha256": digest(self.pilot_index),
            "analysis_sha256": analysis_hashes,
            "measured_resource_budget": {"point_wall_s": 600, "stage_wall_s": 7200,
                "point_output_gib": 2, "stage_output_gib": 40, "address_space_gib": 8,
                "cpu_threads": 1, "host_ram_reserve_gib": 32,
                "host_disk_reserve_gib": 100},
        })

    def tearDown(self):
        self.temp.cleanup()

    def test_generates_exact_reviewed_19_point_candidate(self):
        destination = self.root / "maintenance-main-v1"
        result = prepare(self.pilot_index, self.analysis_root, self.review, destination)
        self.assertEqual(result["point_count"], 19)
        points = [json.loads(Path(row["config"]).read_text()) for row in result["points"]]
        self.assertEqual(sum(row["maintenance"]["mode"] == "shared" for row in points), 16)
        self.assertEqual(sum(row["maintenance"]["mode"] == "ideal_independent"
                             for row in points), 3)
        self.assertEqual({row["maintenance"]["ea_ev"] for row in points}, {1.01, 1.04, 1.08})
        self.assertEqual(sum(row["maintenance"]["ea_ev"] != 1.04 for row in points), 4)
        self.assertTrue(all(row["workload"]["active_ns"] == 20_000_000_000
                            and row["recovery_ns"] == 10_000_000_000 for row in points))
        self.assertTrue(all(row["maintenance"]["aged_blocks_per_stack"] *
                            row["maintenance"]["block_bytes"] == 4 * 1024**3
                            for row in points))
        self.assertFalse((destination / "RUN_INDEX.json").exists())
        self.assertEqual(json.loads((destination / "CANDIDATE_INDEX.json").read_text())["status"],
                         "PILOT_REVIEW_PASSED_PREPARED_NOT_FROZEN_NOT_LAUNCHABLE")

    def test_requires_bound_review_and_all_completed_pilots(self):
        review = json.loads(self.review.read_text())
        review["analysis_sha256"]["pilot-dash"] = "0" * 64
        save(self.review, review)
        with self.assertRaisesRegex(ValueError, "not bound"):
            prepare(self.pilot_index, self.analysis_root, self.review,
                    self.root / "maintenance-main-v1")

    def test_refuses_pilot_stage_or_existing_destination(self):
        with self.assertRaisesRegex(ValueError, "maintenance-main-v1"):
            prepare(self.pilot_index, self.analysis_root, self.review,
                    self.root / "maintenance-v1")
        destination = self.root / "maintenance-main-v1"
        destination.mkdir()
        with self.assertRaises(FileExistsError):
            prepare(self.pilot_index, self.analysis_root, self.review, destination)


if __name__ == "__main__":
    unittest.main()

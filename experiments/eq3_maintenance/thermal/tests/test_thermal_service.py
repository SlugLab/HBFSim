#!/usr/bin/env python3
"""Fixed small-model tests for the isolated persistent thermal service."""
import argparse
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

BINARY = None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.model = root / "model.txt"
        self.grid = root / "rc_grid.json"
        self.sensors = root / "rc_sensors.json"
        self.model.write_text(
            "HBFSIM_EQ3_THERMAL_MODEL 1\n"
            "coupling on\n"
            "node n0 gpu compute a -1 2 300 0 0.5 300\n"
            "node n1 hbm fast_memory b 0 1 300 0 0.25 300\n"
            "edge n0 n1 1 component\n")
        cells = [
            {"index": 0, "id": "n0", "component": "a", "volume_m3": 2.0,
             "capacity_j_k": 2.0},
            {"index": 1, "id": "n1", "component": "b", "volume_m3": 1.0,
             "capacity_j_k": 1.0},
        ]
        self.grid.write_text(json.dumps({"shape": [2, 1, 1], "cells": cells,
                                         "component_cells": {"a": [0], "b": [1]}}))
        self.sensors.write_text(json.dumps([
            {"id": "component:a:hotspot", "reduction": "max", "cell_indices": [0]},
            {"id": "component:a:mean", "reduction": "weighted_mean",
             "cell_weights": [[0, 1.0]]},
            {"id": "component:b:hotspot", "reduction": "max", "cell_indices": [1]},
            {"id": "component:b:mean", "reduction": "weighted_mean",
             "cell_weights": [[1, 1.0]]},
        ]))

    def tearDown(self):
        self.temporary.cleanup()

    def command(self, maximum="400", model_hash=None):
        return [str(BINARY), "--model", str(self.model), "--grid", str(self.grid),
                "--sensors", str(self.sensors), "--model-sha256",
                model_hash or digest(self.model), "--grid-sha256", digest(self.grid),
                "--sensors-sha256", digest(self.sensors), "--step-ns", "20000000",
                "--min-k", "300", "--max-k", maximum]

    def run_service(self, text, **kwargs):
        return subprocess.run(self.command(**kwargs), input=text, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)

    def test_two_components_persistent_energy_and_observations(self):
        result = self.run_service(
            "ENERGY 0 20000000 a 2\n"
            "ENERGY 0 20000000 b 1\n"
            "ADVANCE 20000000\nQUIT\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["type"] for row in rows],
                         ["READY", "ENERGY_ACK", "ENERGY_ACK", "ADVANCE", "BYE"])
        self.assertEqual(rows[0]["nodes"], 2)
        self.assertEqual(rows[0]["entities"], 2)
        self.assertEqual(rows[0]["sensors"], 4)
        advanced = rows[3]
        self.assertEqual(advanced["factorization_count"], 1)
        self.assertEqual(len(advanced["entity_temperatures_k"]), 2)
        self.assertEqual(len(advanced["sensor_temperatures_k"]), 4)
        energy = advanced["energy_j"]["cumulative"]
        self.assertAlmostEqual(energy["activity_input_j"], 3.0, places=12)
        self.assertLess(abs(energy["energy_residual_j"]), 1e-10)
        self.assertGreater(advanced["temperature_range_k"][1], 300)
        self.assertIn("advance_seconds", advanced["timing"])
        self.assertIn("observation_seconds", advanced["timing"])
        self.assertIn("previous_stdout_write_seconds", advanced["timing"])

    def test_duplicate_energy_is_rejected_before_double_count(self):
        result = self.run_service(
            "ENERGY 0 20000000 a 1\nENERGY 0 20000000 a 1\n")
        self.assertEqual(result.returncode, 2)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[-1]["status"], "INPUT_OR_PROTOCOL_FAILURE")
        self.assertIn("double count", rows[-1]["reason"])

    def test_committed_past_and_partial_step_are_rejected(self):
        for invalid in ("ENERGY 0 10000000 a 1\n", "ADVANCE 10000000\n"):
            with self.subTest(invalid=invalid):
                result = self.run_service(invalid)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout.splitlines()[-1])["status"],
                                 "INPUT_OR_PROTOCOL_FAILURE")

    def test_domain_failure_preserves_last_valid_and_unknown_trial_energy(self):
        result = self.run_service("ENERGY 0 20000000 a 100\nADVANCE 20000000\n",
                                  maximum="300.01")
        self.assertEqual(result.returncode, 2)
        failure = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(failure["status"], "DOMAIN_FAILURE")
        self.assertEqual(failure["last_valid_time_ns"], 0)
        self.assertEqual(failure["trial_target_time_ns"], 20000000)
        self.assertEqual(failure["failed_trial_step_energy"]["status"],
                         "UNKNOWN_NOT_INTEGRATED")
        self.assertEqual(failure["completed_energy_j"]["activity_input_j"], 0)
        self.assertTrue(failure["offending_nodes"])

    def test_hash_mismatch_fails_closed(self):
        result = self.run_service("", model_hash="0" * 64)
        self.assertEqual(result.returncode, 2)
        failure = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(failure["status"], "INPUT_OR_PROTOCOL_FAILURE")
        self.assertIn("SHA-256 mismatch", failure["reason"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    arguments = parser.parse_args()
    BINARY = arguments.binary.resolve()
    unittest.main(argv=[__file__])

import csv
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "pilot_energy_replay", ROOT / "pilot_energy_replay.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReplayConversionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.grid = self.root / "grid.json"
        self.grid.write_text(json.dumps({
            "cells": [
                {"id": "n0", "volume_m3": 1.0},
                {"id": "n1", "volume_m3": 3.0},
                {"id": "n2", "volume_m3": 2.0},
            ],
            "component_cells": {"a": [0, 1], "b": [2]},
        }))

    def tearDown(self):
        self.temporary.cleanup()

    def ledger(self, rows):
        path = self.root / "energy.csv"
        fields = ["kind", "start_ns", "end_ns", "component_energy_j", "phase"]
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_conserves_windows_components_and_volume_weights(self):
        ledger = self.ledger([
            {"kind": "ACTIVITY", "start_ns": 0, "end_ns": 10,
             "component_energy_j": "", "phase": "OBSERVATION"},
            {"kind": "WINDOW_TOTAL", "start_ns": 0, "end_ns": 10,
             "component_energy_j": json.dumps({"a": 4.0, "b": 2.0}),
             "phase": "OBSERVATION"},
            {"kind": "WINDOW_TOTAL", "start_ns": 10, "end_ns": 20,
             "component_energy_j": json.dumps({"a": 1.0}), "phase": "DRAIN"},
        ])
        events, receipt = MODULE.convert(ledger, self.grid)
        self.assertEqual(receipt["window_count"], 2)
        self.assertEqual(receipt["event_count"], 3)
        self.assertEqual(receipt["source_total_energy_j"], 7.0)
        self.assertEqual(receipt["emitted_total_energy_j"], 7.0)
        self.assertEqual([x["phase"] for x in receipt["phase_windows"]],
                         ["OBSERVATION", "DRAIN"])
        first = events.splitlines()[1].split()
        assignments = dict(zip(first[12::2], map(float, first[13::2])))
        self.assertTrue(math.isclose(assignments["n0"], 1.0))
        self.assertTrue(math.isclose(assignments["n1"], 3.0))
        self.assertNotIn("ACTIVITY", events)

    def test_rejects_gap_overlap_or_duplicate_window(self):
        for second_start in (9, 11, 0):
            with self.subTest(second_start=second_start):
                ledger = self.ledger([
                    {"kind": "WINDOW_TOTAL", "start_ns": 0, "end_ns": 10,
                     "component_energy_j": "{}", "phase": "OBSERVATION"},
                    {"kind": "WINDOW_TOTAL", "start_ns": second_start, "end_ns": 20,
                     "component_energy_j": "{}", "phase": "DRAIN"},
                ])
                with self.assertRaisesRegex(ValueError, "ordered and contiguous"):
                    MODULE.convert(ledger, self.grid)

    def test_rejects_unknown_component(self):
        ledger = self.ledger([{
            "kind": "WINDOW_TOTAL", "start_ns": 0, "end_ns": 10,
            "component_energy_j": json.dumps({"unknown": 1.0}), "phase": "OBSERVATION",
        }])
        with self.assertRaisesRegex(ValueError, "absent from grid"):
            MODULE.convert(ledger, self.grid)

    def test_rejects_negative_or_nonfinite_energy(self):
        for value in (-1.0, float("nan"), float("inf")):
            with self.subTest(value=value):
                ledger = self.ledger([{
                    "kind": "WINDOW_TOTAL", "start_ns": 0, "end_ns": 10,
                    "component_energy_j": json.dumps({"a": value}), "phase": "OBSERVATION",
                }])
                with self.assertRaisesRegex(ValueError, "invalid energy"):
                    MODULE.convert(ledger, self.grid)


if __name__ == "__main__":
    unittest.main()

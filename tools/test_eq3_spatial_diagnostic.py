import csv
import tempfile
import unittest
from pathlib import Path

from eq3_spatial_diagnostic import analyze, write_result


SENSORS = ("component:hbf0.die0:mean", "component:hbf0.die0:hotspot")


def method():
    return {
        "schema_version": "eq3-acceptance-v2-method-v1",
        "temperature_unit": "K",
        "sensor_ids": list(SENSORS),
        "initial_time_s": 0.0,
        "initial_temperature_k": 300.0,
        "windows": [
            {"id": "full", "kind": "full", "start_s": 0.0, "end_s": 4.0},
            {"id": "heat", "kind": "excitation", "start_s": 0.0, "end_s": 2.0},
            {"id": "cool", "kind": "cooling", "start_s": 2.0, "end_s": 4.0},
        ],
        "hotspot_sensor_ids": [SENSORS[1]],
        "control_sensor_ids": [],
        "crossing_thresholds_k": [301.0],
        "crossing_min_time_s": 0.2,
        "crossing_fraction": 0.05,
    }


def write_csv(path, offset, times=(1.0, 2.0, 4.0), sensors=SENSORS):
    with path.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["time_s", "sensor_id", "temperature_k", "hotspot_cell_id"])
        for sensor in sensors:
            is_hotspot = sensor.endswith(":hotspot")
            for index, time_s in enumerate(times):
                base = (300.0, 302.0, 301.0)[index]
                temperature = base + offset * (index + 1) * (2 if is_hotspot else 1)
                cell = f"n{int(offset * 10)}_{index}" if is_hotspot else ""
                writer.writerow([time_s, sensor, temperature, cell])


class SpatialDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = [self.root / f"R0{index}.csv" for index in range(1, 4)]
        for path, offset in zip(self.paths, (0.0, 0.1, 0.3)):
            write_csv(path, offset)

    def tearDown(self):
        self.temporary.cleanup()

    def run_analysis(self):
        return analyze(*self.paths, method())

    def test_adjacent_pairs_windows_groups_and_exact_hotspot_cell(self):
        result = self.run_analysis()
        self.assertEqual(result["richardson_order"], "NOT_COMPUTED")
        self.assertEqual(len(result["pairs"]), 2)
        first = result["pairs"][0]
        self.assertEqual((first["reference_grid"], first["candidate_grid"]),
                         ("4mm", "2mm"))
        self.assertEqual({window["window_kind"] for window in first["windows"]},
                         {"full", "excitation", "cooling"})
        full = next(window for window in first["windows"]
                    if window["window_kind"] == "full")
        self.assertEqual(full["groups"]["mean"]["sensor_count"], 1)
        self.assertEqual(full["groups"]["hotspot"]["sensor_count"], 1)
        point = full["groups"]["hotspot"]["worst_registered_point"]
        self.assertEqual(point["sensor_id"], SENSORS[1])
        self.assertEqual(point["time_s"], 4.0)
        self.assertEqual(point["reference_hotspot_cell_id"], "n0_2")
        self.assertEqual(point["candidate_hotspot_cell_id"], "n1_2")
        self.assertAlmostEqual(point["abs_error_k"], 0.6)

    def test_excitation_and_cooling_are_scored_separately(self):
        windows = self.run_analysis()["pairs"][0]["windows"]
        heat = next(window for window in windows if window["window_kind"] == "excitation")
        cool = next(window for window in windows if window["window_kind"] == "cooling")
        heat_mae = heat["groups"]["mean"]["mean_sensor_time_weighted_mae_k"]
        cool_mae = cool["groups"]["mean"]["mean_sensor_time_weighted_mae_k"]
        self.assertNotEqual(heat_mae, cool_mae)

    def test_incomplete_r03_rejects_whole_analysis(self):
        write_csv(self.paths[2], 0.3, times=(1.0, 2.0))
        with self.assertRaisesRegex(ValueError, "R03 timestamp coverage differs"):
            self.run_analysis()

    def test_r03_sensor_coverage_mismatch_is_rejected(self):
        write_csv(self.paths[2], 0.3, sensors=(SENSORS[0],))
        with self.assertRaisesRegex(ValueError, "R03 sensor coverage differs"):
            self.run_analysis()

    def test_postprocessor_does_not_modify_raw_inputs(self):
        before = [path.read_bytes() for path in self.paths]
        self.run_analysis()
        self.assertEqual([path.read_bytes() for path in self.paths], before)

    def test_output_is_create_only(self):
        output = self.root / "result.json"
        output.write_text("preserve")
        with self.assertRaises(FileExistsError):
            write_result(output, self.run_analysis())
        self.assertEqual(output.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()

import csv
import json
import tempfile
import unittest
from pathlib import Path

from eq3_acceptance_v2 import analyze


SENSORS = ("mean", "grid_hotspot", "control")


def method(**updates):
    value = {
        "schema_version": "eq3-acceptance-v2-method-v1",
        "temperature_unit": "K",
        "sensor_ids": list(SENSORS),
        "initial_time_s": 0.0,
        "initial_temperature_k": 300.0,
        "windows": [
            {"id": "full", "kind": "full", "start_s": 0.0, "end_s": 4.0},
            {"id": "excited", "kind": "excitation", "start_s": 0.0, "end_s": 2.0},
            {"id": "cooling", "kind": "cooling", "start_s": 2.0, "end_s": 4.0},
        ],
        "hotspot_sensor_ids": ["grid_hotspot"],
        "control_sensor_ids": ["control"],
        "crossing_thresholds_k": [330.0],
        "crossing_min_time_s": 0.2,
        "crossing_fraction": 0.05,
    }
    value.update(updates)
    return value


def energy(residual=0.0):
    return {"total_input_energy_j": 10.0,
            "stored_energy_change_j": 4.0,
            "boundary_loss_j": 6.0 - residual}


def write_csv(path, values, times=(1.0, 2.0, 4.0), sensors=SENSORS):
    with path.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["time_s", "sensor_id", "temperature_k"])
        for sensor in sensors:
            sequence = values[sensor]
            for time_s, temperature in zip(times, sequence):
                writer.writerow([time_s, sensor, temperature])


class AcceptanceV2Tests(unittest.TestCase):
    def run_analysis(self, reference_values, candidate_values, selected_method=None,
                     selected_energy=None, reference_times=(1.0, 2.0, 4.0),
                     candidate_times=None, candidate_sensors=SENSORS):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            reference, candidate = root / "reference.csv", root / "candidate.csv"
            write_csv(reference, reference_values, reference_times)
            write_csv(candidate, candidate_values,
                      candidate_times or reference_times, candidate_sensors)
            return analyze(reference, candidate, selected_method or method(),
                           selected_energy or energy())

    @staticmethod
    def constant(value):
        return {sensor: [value, value, value] for sensor in SENSORS}

    def score(self, result, sensor="mean", window="excited"):
        return next(item for item in result["scores"]
                    if item["sensor_id"] == sensor and item["window_id"] == window)

    def test_constant_and_low_rise_v2_boundaries(self):
        reference = self.constant(300.0)
        candidate = self.constant(300.25)
        result = self.run_analysis(reference, candidate)
        score = self.score(result)
        self.assertEqual(score["reference_amplitude_k"], 0)
        self.assertAlmostEqual(score["v2_mae_limit_k"], .25)
        self.assertAlmostEqual(score["time_weighted_mae_k"], .1875)
        self.assertTrue(score["v2_pass"])

        reference = {sensor: [300.5, 301.0, 301.0] for sensor in SENSORS}
        candidate = {sensor: [300.79, 301.29, 301.29] for sensor in SENSORS}
        result = self.run_analysis(reference, candidate)
        score = self.score(result)
        self.assertEqual(score["reference_amplitude_k"], 1.0)
        self.assertEqual(score["v2_mae_limit_k"], .3)
        self.assertTrue(score["v2_pass"])

    def test_just_over_constant_limit_fails(self):
        reference = self.constant(300.0)
        candidate = self.constant(300.34)
        result = self.run_analysis(reference, candidate)
        self.assertFalse(self.score(result)["v2_pass"])
        self.assertEqual(result["status"], "NUMERICAL_FAIL")

    def test_nonuniform_time_weighting_and_legacy_arithmetic_are_distinct(self):
        reference = self.constant(300.0)
        candidate = {sensor: [300.0, 302.0, 302.0] for sensor in SENSORS}
        result = self.run_analysis(reference, candidate)
        score = self.score(result, window="full")
        self.assertAlmostEqual(score["time_weighted_mae_k"], 1.25)
        legacy = next(item for item in result["legacy_v1"]["sensors"]
                      if item["sensor_id"] == "mean")
        self.assertAlmostEqual(legacy["mae_k"], 4.0 / 3.0)
        self.assertEqual(legacy["mean_semantics"],
                         "sample_arithmetic_mean_without_synthetic_initial")

    def test_signed_error_zero_crossing_is_integrated_exactly(self):
        reference = self.constant(300.0)
        candidate = {sensor: [301.0, 299.0, 300.0] for sensor in SENSORS}
        result = self.run_analysis(reference, candidate)
        score = self.score(result, window="full")
        self.assertAlmostEqual(score["time_weighted_mae_k"], .5)

    def test_excitation_window_cannot_be_diluted_by_cooling(self):
        times = (.5, 1.0, 2.0, 4.0)
        reference = {sensor: [300.0] * 4 for sensor in SENSORS}
        candidate = {sensor: [301.2, 300.0, 300.0, 300.0] for sensor in SENSORS}
        result = self.run_analysis(reference, candidate, reference_times=times)
        full = self.score(result, window="full")
        excited = self.score(result, window="excited")
        self.assertLessEqual(full["time_weighted_mae_k"], full["v2_mae_limit_k"])
        self.assertGreater(excited["time_weighted_mae_k"], excited["v2_mae_limit_k"])

    def test_hotspot_and_control_keep_two_kelvin_maximum(self):
        times = (.01, .02, 2.0, 4.0)
        reference = {sensor: [300.0] * 4 for sensor in SENSORS}
        candidate = {sensor: [300.0] * 4 for sensor in SENSORS}
        candidate["grid_hotspot"] = [302.1, 300.0, 300.0, 300.0]
        candidate["control"] = [302.1, 300.0, 300.0, 300.0]
        result = self.run_analysis(reference, candidate, reference_times=times)
        self.assertLess(self.score(result, "grid_hotspot")["time_weighted_mae_k"], .25)
        self.assertFalse(self.score(result, "grid_hotspot")["v2_pass"])
        self.assertFalse(self.score(result, "control")["v2_pass"])

    def test_energy_limit_is_independent(self):
        result = self.run_analysis(self.constant(300), self.constant(300),
                                   selected_energy=energy(.02))
        self.assertFalse(result["energy"]["pass"])
        self.assertEqual(result["status"], "NUMERICAL_FAIL")

    def test_threshold_ambiguity_is_reported_without_forcing_numeric_fail(self):
        values = {sensor: [300.9, 301.1, 302.0] for sensor in SENSORS}
        selected = method(crossing_thresholds_k=[301.0])
        result = self.run_analysis(values, values, selected_method=selected)
        self.assertEqual(result["status"], "PASS_WITH_THRESHOLD_AMBIGUITY")
        crossing = self.score(result)["crossings"][0]
        self.assertEqual(crossing["status"], "THRESHOLD_AMBIGUOUS")
        self.assertEqual(crossing["legacy_v1_status"], "PASS")

    def test_missing_frame_sensor_and_unit_mutation_are_rejected(self):
        values = self.constant(300)
        with self.assertRaisesRegex(ValueError, "timestamp coverage differs"):
            self.run_analysis(values, values, candidate_times=(1.0, 2.1, 4.0))
        with self.assertRaisesRegex(ValueError, "sensor coverage differs"):
            self.run_analysis(values, values,
                              candidate_sensors=("mean", "grid_hotspot"))
        with self.assertRaisesRegex(ValueError, "preregistered sensor_ids"):
            self.run_analysis(values, values,
                              selected_method=method(sensor_ids=["mean", "grid_hotspot"]))
        with self.assertRaisesRegex(ValueError, "temperature_unit must be K"):
            self.run_analysis(values, values,
                              selected_method=method(temperature_unit="degC"))

    def test_all_three_window_kinds_are_required(self):
        selected = method(windows=[
            {"id": "full", "kind": "full", "start_s": 0, "end_s": 4},
            {"id": "excited", "kind": "excitation", "start_s": 0, "end_s": 2},
        ])
        with self.assertRaisesRegex(ValueError, "must be preregistered"):
            self.run_analysis(self.constant(300), self.constant(300),
                              selected_method=selected)


if __name__ == "__main__":
    unittest.main()

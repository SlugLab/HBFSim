import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

from eq3_sensor_time_canonicalize import canonicalize


FIELDS = ["time_s", "sensor_id", "temperature_k", "hotspot_cell_id"]


def write_csv(path, rows):
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open(newline="") as source:
        return list(csv.DictReader(source))


class SensorTimeCanonicalizeTest(unittest.TestCase):
    def test_binary_spelling_only_and_non_time_lossless(self):
        rows = [
            {"time_s": time_s, "sensor_id": sensor,
             "temperature_k": f"{300 + index / 10:.17g}",
             "hotspot_cell_id": f"{sensor}-cell-{index}"}
            for sensor, times in (
                ("a", ["0.1", "0.20000000000000001", "0.30000000000000004"]),
                ("b", ["0.10000000000000001", "0.2", "0.29999999999999999"]),
            )
            for index, time_s in enumerate(times, 1)
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.csv"
            output = Path(directory) / "canonical.csv"
            write_csv(source, rows)
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            receipt = canonicalize(source, output, 0.1, 0.3, 1e-12)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
            self.assertLessEqual(receipt["max_abs_time_adjustment_s"], 1e-14)
            actual = read_csv(output)
            self.assertEqual([row["time_s"] for row in actual],
                             ["0.1", "0.2", "0.3", "0.1", "0.2", "0.3"])
            names = [name for name in FIELDS if name != "time_s"]
            self.assertEqual([[row[name] for name in names] for row in actual],
                             [[row[name] for name in names] for row in rows])

    def test_rejects_real_shift_duplicate_missing_and_coverage_change(self):
        valid = [
            {"time_s": str(index / 10), "sensor_id": sensor,
             "temperature_k": "300", "hotspot_cell_id": sensor}
            for sensor in ("a", "b") for index in (1, 2, 3)
        ]
        cases = {
            "real shift": [dict(row, time_s="0.1001") if row["sensor_id"] == "a" and row["time_s"] == "0.1" else row for row in valid],
            "duplicate": [dict(row, time_s="0.1") if row["sensor_id"] == "a" and row["time_s"] == "0.2" else row for row in valid],
            "missing": [row for row in valid if not (row["sensor_id"] == "a" and row["time_s"] == "0.2")],
            "coverage change": [row for row in valid if not (row["sensor_id"] == "b" and row["time_s"] == "0.3")],
        }
        with tempfile.TemporaryDirectory() as directory:
            for label, rows in cases.items():
                with self.subTest(label=label):
                    source = Path(directory) / f"{label.replace(' ', '-')}.csv"
                    output = Path(directory) / f"{label.replace(' ', '-')}-out.csv"
                    write_csv(source, rows)
                    with self.assertRaises(ValueError):
                        canonicalize(source, output, 0.1, 0.3, 1e-12)


if __name__ == "__main__":
    unittest.main()

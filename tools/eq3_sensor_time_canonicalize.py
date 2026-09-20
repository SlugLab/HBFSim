"""Canonicalize only a sensor CSV's floating timestamp spelling on a fixed grid."""
import argparse
import csv
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def row_digest(rows):
    value = hashlib.sha256()
    for row in rows:
        value.update(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode())
        value.update(b"\n")
    return value.hexdigest()


def canonicalize(source_path, output_path, quantum_s, duration_s, tolerance_s):
    expected_count = round(duration_s / quantum_s)
    if not math.isclose(expected_count * quantum_s, duration_s, abs_tol=tolerance_s):
        raise ValueError("duration is not an integer number of timestamp quanta")
    counts = {}
    max_adjustment = 0.0
    non_time = []
    decimal_quantum = Decimal(str(quantum_s))
    with source_path.open(newline="") as source, output_path.open("x", newline="") as output:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "time_s" not in reader.fieldnames or "sensor_id" not in reader.fieldnames:
            raise ValueError("CSV must contain time_s and sensor_id")
        writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            sensor = row["sensor_id"]
            index = counts.get(sensor, 0) + 1
            if index > expected_count:
                raise ValueError(f"too many timestamps for {sensor}")
            actual = float(row["time_s"])
            expected_decimal = index * decimal_quantum
            expected = float(expected_decimal)
            adjustment = abs(actual - expected)
            if not math.isfinite(actual) or adjustment > tolerance_s:
                raise ValueError(f"timestamp is off the declared grid for {sensor} at row {index}")
            max_adjustment = max(max_adjustment, adjustment)
            counts[sensor] = index
            non_time.append([row[name] for name in reader.fieldnames if name != "time_s"])
            row["time_s"] = format(expected_decimal, "f")
            writer.writerow(row)
    if not counts or set(counts.values()) != {expected_count}:
        raise ValueError("sensor timestamp coverage is incomplete")
    return {"sensor_count": len(counts), "timestamps_per_sensor": expected_count,
            "max_abs_time_adjustment_s": max_adjustment,
            "non_time_rows_sha256": row_digest(non_time)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--quantum-s", type=float, required=True)
    parser.add_argument("--duration-s", type=float, required=True)
    parser.add_argument("--tolerance-s", type=float, default=1e-12)
    args = parser.parse_args()
    if args.quantum_s <= 0 or args.duration_s <= 0 or args.tolerance_s < 0:
        raise ValueError("quantum/duration must be positive and tolerance nonnegative")
    result = canonicalize(args.input, args.output, args.quantum_s,
                          args.duration_s, args.tolerance_s)
    result.update(schema_version="eq3-sensor-time-canonicalization-v1",
                  status="LOSSLESS_NON_TIME_FIELDS_TIMESTAMP_SPELLING_ONLY",
                  input_sha256=digest(args.input), output_sha256=digest(args.output),
                  quantum_s=args.quantum_s, duration_s=args.duration_s,
                  tolerance_s=args.tolerance_s)
    with args.receipt.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()

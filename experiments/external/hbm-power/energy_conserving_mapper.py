#!/usr/bin/env python3
"""Map a finite HBM-Power trace onto an explicit long-timescale schedule.

This is an energy-domain mapping, not command-trace repetition.  The finite
source trace is integrated once to obtain baseline and active average power.
Each macro interval then receives energy according to its resident and active
equivalent time.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import pathlib
import tempfile


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_file(path: pathlib.Path, label: str) -> pathlib.Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} is not a regular file: {resolved}")
    return resolved


def atomic_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def finite_fraction(text: str, field: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise ValueError(f"{field} must be finite and in [0, 1]")
    return value


def read_power_trace(path: pathlib.Path) -> tuple[list[str], list[tuple[int, list[float]]]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or reader.fieldnames[0] != "time_ns":
            raise ValueError("source trace must begin with time_ns")
        columns = reader.fieldnames[1:]
        if not columns or len(set(columns)) != len(columns):
            raise ValueError("source trace needs unique power columns")
        rows: list[tuple[int, list[float]]] = []
        for record in reader:
            time_ns = int(record["time_ns"])
            values = [float(record[column]) for column in columns]
            if time_ns < 0 or any(
                not math.isfinite(value) or value < 0.0 for value in values
            ):
                raise ValueError("source trace has invalid time or power")
            if rows and time_ns <= rows[-1][0]:
                raise ValueError("source trace times must strictly increase")
            rows.append((time_ns, values))
    if len(rows) < 2 or rows[0][0] != 0 or rows[-1][0] <= 0:
        raise ValueError("source trace must span a positive interval from zero")
    if any(value != 0.0 for value in rows[-1][1]):
        raise ValueError("source trace requires an explicit terminal zero sample")
    return columns, rows


def integrate_hold(
    rows: list[tuple[int, list[float]]]
) -> tuple[int, list[float], list[float]]:
    duration_ns = rows[-1][0]
    energy_j = [0.0] * len(rows[0][1])
    for (start, values), (end, _) in zip(rows, rows[1:]):
        width_s = (end - start) * 1.0e-9
        for index, value in enumerate(values):
            energy_j[index] += value * width_s
    average_w = [value / (duration_ns * 1.0e-9) for value in energy_j]
    return duration_ns, energy_j, average_w


def read_schedule(path: pathlib.Path) -> list[dict[str, object]]:
    required = [
        "start_ns", "end_ns", "phase", "resident_fraction", "activity_fraction"
    ]
    intervals: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != required:
            raise ValueError(f"schedule columns must be exactly {required}")
        for row in reader:
            start = int(row["start_ns"])
            end = int(row["end_ns"])
            resident = finite_fraction(row["resident_fraction"], "resident_fraction")
            active = finite_fraction(row["activity_fraction"], "activity_fraction")
            if not row["phase"] or end <= start:
                raise ValueError("schedule phase and duration must be positive")
            if active > resident:
                raise ValueError("activity_fraction cannot exceed resident_fraction")
            if intervals and start != intervals[-1]["end_ns"]:
                raise ValueError("schedule intervals must be contiguous")
            if not intervals and start != 0:
                raise ValueError("schedule must start at zero")
            intervals.append({
                "start_ns": start,
                "end_ns": end,
                "phase": row["phase"],
                "resident_fraction": resident,
                "activity_fraction": active,
            })
    if not intervals:
        raise ValueError("schedule is empty")
    return intervals


def close(left: float, right: float, tolerance: float = 1.0e-10) -> bool:
    return abs(left - right) <= tolerance * max(1.0, abs(left), abs(right))


def render_output(columns: list[str], rows: list[tuple[int, list[float]]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["time_ns", *columns])
    for time_ns, values in rows:
        writer.writerow([time_ns, *[f"{value:.17g}" for value in values]])
    return stream.getvalue()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-csv", type=pathlib.Path, required=True)
    parser.add_argument("--source-manifest", type=pathlib.Path, required=True)
    parser.add_argument("--schedule", type=pathlib.Path, required=True)
    parser.add_argument("--output-csv", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_csv = require_file(args.source_csv, "source CSV")
    source_manifest_path = require_file(args.source_manifest, "source manifest")
    schedule_path = require_file(args.schedule, "schedule")
    with source_manifest_path.open("r", encoding="utf-8") as stream:
        source_manifest = json.load(stream)
    source_sha = sha256_file(source_csv)
    if source_manifest["output"]["sha256"] != source_sha:
        raise ValueError("source CSV hash does not match source manifest")

    columns, source_rows = read_power_trace(source_csv)
    source_duration_ns, source_energy_j, source_average_w = integrate_hold(source_rows)
    stack_records = source_manifest["stacks"]
    if len(columns) != len(stack_records):
        raise ValueError("source trace/manifest stack count mismatch")

    baseline_w: list[float] = []
    active_w: list[float] = []
    for index, (average, record) in enumerate(zip(source_average_w, stack_records)):
        if record["index"] != index or record["duration_ns"] != source_duration_ns:
            raise ValueError("source manifest stack order/duration mismatch")
        baseline = float(record["baseline_power_w"])
        active = average - baseline
        if baseline < 0.0 or active < -1.0e-12:
            raise ValueError("source manifest yields negative baseline/active power")
        active = max(0.0, active)
        if not close(average, float(record["average_power_w"])):
            raise ValueError("integrated source average disagrees with manifest")
        if not close(active, float(record["active_power_w"])):
            raise ValueError("integrated source active power disagrees with manifest")
        baseline_w.append(baseline)
        active_w.append(active)

    intervals = read_schedule(schedule_path)
    output_rows: list[tuple[int, list[float]]] = []
    target_energy_j = [0.0] * len(columns)
    resident_equivalent_ns = 0.0
    active_equivalent_ns = 0.0
    for interval in intervals:
        start = int(interval["start_ns"])
        end = int(interval["end_ns"])
        width_ns = end - start
        resident = float(interval["resident_fraction"])
        active = float(interval["activity_fraction"])
        values = [
            base * resident + activity * active
            for base, activity in zip(baseline_w, active_w)
        ]
        output_rows.append((start, values))
        for index, value in enumerate(values):
            target_energy_j[index] += value * width_ns * 1.0e-9
        resident_equivalent_ns += resident * width_ns
        active_equivalent_ns += active * width_ns
    output_rows.append((int(intervals[-1]["end_ns"]), [0.0] * len(columns)))

    output_text = render_output(columns, output_rows)
    output_csv = args.output_csv.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    atomic_text(output_csv, output_text)
    _, output_energy_j, _ = integrate_hold(read_power_trace(output_csv)[1])
    errors = [
        abs(actual - target) / max(abs(target), 1.0e-30)
        for actual, target in zip(output_energy_j, target_energy_j)
    ]
    if any(error > 1.0e-12 for error in errors):
        raise RuntimeError("output trace violates energy conservation tolerance")

    manifest = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "ENERGY_CONSERVING_WORKLOAD_SCHEDULE_MAPPING",
        "claim_boundary": (
            "macro power/energy envelope only; no command-level repetition, "
            "no temporal-correlation preservation, and no local GDDR7 measurement"
        ),
        "equation": (
            "E_out_stack = P_baseline_stack * resident_equivalent_time + "
            "P_active_stack * active_equivalent_time"
        ),
        "source": {
            "csv": str(source_csv),
            "csv_sha256": source_sha,
            "manifest": str(source_manifest_path),
            "manifest_sha256": sha256_file(source_manifest_path),
            "technology": source_manifest["technology"],
            "duration_ns": source_duration_ns,
            "energy_j_per_stack": source_energy_j,
            "integrated_average_power_w_per_stack": source_average_w,
            "baseline_power_w_per_stack": baseline_w,
            "active_power_w_per_stack": active_w,
        },
        "schedule": {
            "path": str(schedule_path),
            "sha256": sha256_file(schedule_path),
            "horizon_ns": int(intervals[-1]["end_ns"]),
            "intervals": intervals,
            "resident_equivalent_ns": resident_equivalent_ns,
            "active_equivalent_ns": active_equivalent_ns,
        },
        "output": {
            "path": str(output_csv),
            "sha256": sha256_file(output_csv),
            "interpolation": "hold",
            "terminal_zero_sample": True,
            "energy_j_per_stack": output_energy_j,
            "target_energy_j_per_stack": target_energy_j,
            "relative_energy_error_per_stack": errors,
            "energy_conservation_tolerance": 1.0e-12,
        },
    }
    atomic_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": "PASS",
        "output_csv": str(output_csv),
        "manifest": str(manifest_path),
        "horizon_ns": int(intervals[-1]["end_ns"]),
        "max_relative_energy_error": max(errors),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"hbm-power-energy-mapper: {error}")

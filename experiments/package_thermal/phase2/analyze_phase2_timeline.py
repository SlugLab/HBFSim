#!/usr/bin/env python3
"""Fail-closed Phase-II timeline, stationarity, and closed-loop analysis."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean, median
from typing import Any


REQUIRED_COLUMNS = {
    "thermal_time_ns",
    "host_sample_time_ns",
    "P_accelerator",
    "P_hbf_total",
    "T_hbf_hotspot",
    "raw_policy",
    "effective_policy",
    "gate_open",
    "service_scale",
    "MQSim_events_this_bin",
    "MQSim_read_bytes",
    "MQSim_program_bytes",
    "submitted_requests",
    "admitted_requests",
    "completed_requests",
    "queue_depth",
}

REQUIRED_MANIFEST = {
    "schema_version",
    "experiment_id",
    "thermal_stage",
    "workload",
    "seed",
    "offered_byte_rate",
    "evidence_grid",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def numeric(value: str, field: str) -> float | None:
    if value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} contains a non-finite value")
    return result


def load_timeline(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        header = reader.fieldnames or []
        missing = sorted(REQUIRED_COLUMNS - set(header))
        if missing:
            raise ValueError(f"timeline lacks required columns: {missing}")
        rows: list[dict[str, Any]] = []
        numeric_columns = {
            name
            for name in header
            if name.startswith(("P_", "T_", "MQSim_"))
            or name.endswith(("_ns", "_requests", "_count", "_depth"))
            or name in {"gate_open", "service_scale", "requests_delayed"}
        }
        for line, raw in enumerate(reader, start=2):
            row: dict[str, Any] = dict(raw)
            for field in numeric_columns:
                row[field] = numeric(raw[field], f"{field} at line {line}")
            rows.append(row)
    if len(rows) < 3:
        raise ValueError("timeline needs at least three thermal steps")
    times = [int(row["thermal_time_ns"]) for row in rows]
    if any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("thermal_time_ns must be strictly increasing")
    return header, rows


def percentile(values: list[float], percentile_value: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * percentile_value / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def slope_per_second(rows: list[dict[str, Any]], field: str) -> float | None:
    points = [
        (float(row["thermal_time_ns"]) / 1.0e9, row[field])
        for row in rows
        if row[field] is not None
    ]
    if len(points) < 3:
        return None
    origin = points[0][0]
    x = [point[0] - origin for point in points]
    if x[-1] <= 0.0:
        return None
    y = [float(point[1]) for point in points]
    x_mean = fmean(x)
    y_mean = fmean(y)
    denominator = sum((value - x_mean) ** 2 for value in x)
    if denominator == 0.0:
        return None
    return sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x, y)
    ) / denominator


def dominant_tau_ns(
    rom_path: Path, override_ns: float | None
) -> tuple[float, str, str]:
    rom = load_json(rom_path)
    rom_hash = sha256(rom_path)
    if override_ns is not None:
        if not math.isfinite(override_ns) or override_ns < 0.0:
            raise ValueError("dominant tau override must be finite and non-negative")
        return override_ns, rom_hash, "validated_override"
    payload = rom.get("payload", rom)
    if not isinstance(payload, dict):
        raise ValueError("ROM payload must be a JSON object")
    count = int(payload["state_count"])
    flat = [float(value) for value in
            payload["A" if "A" in payload else "a"]]
    if len(flat) != count * count or any(not math.isfinite(value) for value in flat):
        raise ValueError("ROM A matrix has invalid dimensions or values")
    if any(value < -1.0e-12 for value in flat):
        raise ValueError(
            "ROM A has negative entries; pass --dominant-tau-ns from a validated eigensolver"
        )
    matrix = [max(0.0, value) for value in flat]
    vector = [1.0 / count] * count
    eigenvalue = 0.0
    for _ in range(20_000):
        product = [
            sum(matrix[row * count + column] * vector[column]
                for column in range(count))
            for row in range(count)
        ]
        norm = max(abs(value) for value in product)
        if norm == 0.0:
            return 0.0, rom_hash, "nonnegative_power_iteration"
        next_vector = [value / norm for value in product]
        denominator = sum(value * value for value in next_vector)
        rayleigh = sum(
            next_vector[row]
            * sum(matrix[row * count + column] * next_vector[column]
                  for column in range(count))
            for row in range(count)
        ) / denominator
        if max(abs(left - right) for left, right in zip(next_vector, vector)) < 1.0e-14:
            eigenvalue = rayleigh
            break
        vector = next_vector
        eigenvalue = rayleigh
    if not math.isfinite(eigenvalue) or eigenvalue < 0.0 or eigenvalue >= 1.0:
        raise ValueError("ROM is not strictly stable; dominant tau is undefined")
    if eigenvalue == 0.0:
        return 0.0, rom_hash, "nonnegative_power_iteration"
    step_ns = float(payload["sample_period_ns"])
    tau = -step_ns / math.log(eigenvalue)
    return tau, rom_hash, "nonnegative_power_iteration"


def window(rows: list[dict[str, Any]], start_ns: int, end_ns: int) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if start_ns <= int(row["thermal_time_ns"]) < end_ns
    ]


def covered_duration_ns(rows: list[dict[str, Any]]) -> int:
    if len(rows) < 2:
        return 0
    times = [int(row["thermal_time_ns"]) for row in rows]
    intervals = [right - left for left, right in zip(times, times[1:])]
    representative = int(median(intervals))
    return times[-1] - times[0] + representative


def event_rate(rows: list[dict[str, Any]]) -> float | None:
    duration = covered_duration_ns(rows)
    if duration <= 0:
        return None
    return sum(float(row["MQSim_events_this_bin"] or 0.0) for row in rows) / (
        duration / 1.0e9
    )


def mean_field(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if row[field] is not None]
    return fmean(values) if values else None


def relative_drop(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before <= 0.0:
        return None
    return (before - after) / before


def source_power_summary(
    header: list[str], rows: list[dict[str, Any]], timeline: Path
) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for field in header:
        if not field.startswith("P_"):
            continue
        values = [float(row[field]) for row in rows if row[field] is not None]
        sources[field] = (
            {
                "available": True,
                "samples": len(values),
                "mean_w": fmean(values),
                "median_w": median(values),
                "p95_w": percentile(values, 95.0),
                "maximum_w": max(values),
            }
            if values
            else {"available": False, "reason": "source is not separable"}
        )
    return {
        "schema_version": 1,
        "timeline_sha256": sha256(timeline),
        "sources": sources,
    }


def stationarity(
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    rom_path: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    tau_ns, rom_hash, tau_method = dominant_tau_ns(
        rom_path, args.dominant_tau_ns
    )
    warmup_ns = max(int(math.ceil(5.0 * tau_ns)), int(args.minimum_warmup_ns))
    start = int(rows[0]["thermal_time_ns"]) + warmup_ns
    analysis_rows = [row for row in rows if int(row["thermal_time_ns"]) >= start]
    duration_ns = covered_duration_ns(analysis_rows)
    bytes_served = sum(
        float(row["MQSim_read_bytes"] or 0.0)
        + float(row["MQSim_program_bytes"] or 0.0)
        for row in analysis_rows
    )
    served_rate = bytes_served / (duration_ns / 1.0e9) if duration_ns > 0 else None
    offered_rate = float(manifest["offered_byte_rate"])
    served_error = (
        abs(served_rate - offered_rate) / offered_rate
        if served_rate is not None and offered_rate > 0.0
        else None
    )
    queue_slope = slope_per_second(analysis_rows, "queue_depth")
    temperature_slope = slope_per_second(analysis_rows, "T_hbf_hotspot")
    terminal = any(row["effective_policy"] == "shutdown" for row in analysis_rows)
    mode_counts = {
        mode: sum(row["effective_policy"] == mode for row in analysis_rows)
        for mode in ("normal", "light", "severe", "shutdown")
    }
    mode_total = sum(mode_counts.values())
    mode_fractions = {
        mode: count / mode_total if mode_total else None
        for mode, count in mode_counts.items()
    }
    queue_values = [
        float(row["queue_depth"])
        for row in analysis_rows
        if row["queue_depth"] is not None
    ]
    hotspot_values = [
        float(row["T_hbf_hotspot"])
        for row in analysis_rows
        if row["T_hbf_hotspot"] is not None
    ]
    enough = duration_ns >= int(args.minimum_analysis_ns)
    stable = bool(
        enough
        and queue_slope is not None
        and abs(queue_slope) <= args.maximum_queue_slope
        and temperature_slope is not None
        and abs(temperature_slope) <= args.maximum_temperature_slope
        and served_error is not None
        and served_error <= args.maximum_served_rate_error
        and not terminal
    )
    return {
        "schema_version": 1,
        "rom_sha256": rom_hash,
        "dominant_tau_ns": tau_ns,
        "dominant_tau_method": tau_method,
        "required_warmup_ns": warmup_ns,
        "analysis_duration_ns": duration_ns,
        "offered_byte_rate": offered_rate,
        "served_physical_byte_rate": served_rate,
        "served_rate_relative_error": served_error,
        "queue_depth_slope_requests_per_s": queue_slope,
        "hotspot_slope_c_per_s": temperature_slope,
        "terminal_shutdown_observed": terminal,
        "thermal_state_fraction": mode_fractions,
        "queue_depth": {
            "mean": fmean(queue_values) if queue_values else None,
            "p95": percentile(queue_values, 95.0) if queue_values else None,
            "p99": percentile(queue_values, 99.0) if queue_values else None,
            "maximum": max(queue_values) if queue_values else None,
        },
        "hotspot_c": {
            "median": median(hotspot_values) if hotspot_values else None,
            "p95": percentile(hotspot_values, 95.0) if hotspot_values else None,
            "maximum": max(hotspot_values) if hotspot_values else None,
        },
        "stable": stable,
        "criteria": {
            "minimum_analysis_ns": int(args.minimum_analysis_ns),
            "maximum_queue_slope_requests_per_s": args.maximum_queue_slope,
            "maximum_hotspot_slope_c_per_s": args.maximum_temperature_slope,
            "maximum_served_rate_relative_error": args.maximum_served_rate_error,
            "evidence_class": "C",
        },
    }


def closed_loop(
    rows: list[dict[str, Any]], manifest: dict[str, Any], args: argparse.Namespace
) -> dict[str, Any]:
    transition_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row["effective_policy"] in {"severe", "shutdown"}
            and float(row["gate_open"] or 0.0) == 0.0
        ),
        None,
    )
    if manifest["thermal_stage"] != "active" or transition_index is None:
        return {
            "schema_version": 1,
            "verdict": "NOT_EVALUABLE",
            "reason": "active gate-closing transition was not observed",
        }
    transition_time = int(rows[transition_index]["thermal_time_ns"])
    recovery_index = next(
        (
            index
            for index in range(transition_index + 1, len(rows))
            if rows[index]["effective_policy"] not in {"severe", "shutdown"}
            and float(rows[index]["gate_open"] or 0.0) == 1.0
        ),
        None,
    )
    recovery_time = (
        int(rows[recovery_index]["thermal_time_ns"])
        if recovery_index is not None
        else None
    )
    causal_window_ns = int(args.causal_window_ns)
    if recovery_time is not None:
        causal_window_ns = min(
            causal_window_ns, max(1, recovery_time - transition_time)
        )
    before = window(
        rows, transition_time - causal_window_ns, transition_time
    )
    after = window(
        rows,
        transition_time + 1,
        transition_time + causal_window_ns + 1,
    )
    before_events = event_rate(before)
    after_events = event_rate(after)
    before_power = mean_field(before, "P_hbf_total")
    after_power = mean_field(after, "P_hbf_total")
    before_slope = slope_per_second(before, "T_hbf_hotspot")
    after_slope = slope_per_second(after, "T_hbf_hotspot")
    accelerator = [
        float(row["P_accelerator"])
        for row in before + after
        if row["P_accelerator"] is not None
    ]
    accelerator_mean = fmean(accelerator) if accelerator else 0.0
    accelerator_cv = (
        math.sqrt(
            fmean((value - accelerator_mean) ** 2 for value in accelerator)
        )
        / accelerator_mean
        if accelerator and accelerator_mean > 0.0
        else None
    )
    offered_continues = bool(
        recovery_index is not None
        and float(rows[-1]["submitted_requests"] or 0.0)
        > float(rows[recovery_index]["submitted_requests"] or 0.0)
    )
    event_drop = relative_drop(before_events, after_events)
    power_drop = relative_drop(before_power, after_power)
    slope_change = (
        before_slope - after_slope
        if before_slope is not None and after_slope is not None
        else None
    )
    checks = {
        "mqsim_activity_decreased": event_drop is not None
        and event_drop >= args.minimum_event_drop,
        "hbf_power_decreased": power_drop is not None
        and power_drop >= args.minimum_power_drop,
        "temperature_slope_decreased": slope_change is not None
        and slope_change >= args.minimum_slope_change,
        "accelerator_source_stationary": accelerator_cv is not None
        and accelerator_cv <= args.maximum_accelerator_cv,
        "recovery_observed": recovery_index is not None,
        "offered_workload_continued_after_recovery": offered_continues,
    }
    return {
        "schema_version": 1,
        "verdict": "GO" if all(checks.values()) else "FAIL",
        "transition_time_ns": transition_time,
        "recovery_time_ns": (
            recovery_time
        ),
        "pre_event_rate_per_s": before_events,
        "post_event_rate_per_s": after_events,
        "event_rate_relative_drop": event_drop,
        "pre_hbf_power_w": before_power,
        "post_hbf_power_w": after_power,
        "hbf_power_relative_drop": power_drop,
        "pre_hotspot_slope_c_per_s": before_slope,
        "post_hotspot_slope_c_per_s": after_slope,
        "hotspot_slope_change_c_per_s": slope_change,
        "accelerator_power_cv": accelerator_cv,
        "checks": checks,
        "criteria": {
            "requested_causal_window_ns": int(args.causal_window_ns),
            "causal_window_ns": causal_window_ns,
            "causal_window_bounded_by_recovery": (
                recovery_time is not None
                and causal_window_ns < int(args.causal_window_ns)
            ),
            "minimum_event_rate_relative_drop": args.minimum_event_drop,
            "minimum_hbf_power_relative_drop": args.minimum_power_drop,
            "minimum_hotspot_slope_change_c_per_s": args.minimum_slope_change,
            "maximum_accelerator_power_cv": args.maximum_accelerator_cv,
            "evidence_class": "C",
        },
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--experiment-manifest", type=Path, required=True)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-warmup-ns", type=int, default=0)
    parser.add_argument("--dominant-tau-ns", type=float)
    parser.add_argument("--minimum-analysis-ns", type=int, default=1_000_000_000)
    parser.add_argument("--maximum-queue-slope", type=float, default=0.01)
    parser.add_argument("--maximum-temperature-slope", type=float, default=0.01)
    parser.add_argument("--maximum-served-rate-error", type=float, default=0.05)
    parser.add_argument("--causal-window-ns", type=int, default=1_000_000_000)
    parser.add_argument("--minimum-event-drop", type=float, default=0.10)
    parser.add_argument("--minimum-power-drop", type=float, default=0.10)
    parser.add_argument("--minimum-slope-change", type=float, default=0.01)
    parser.add_argument("--maximum-accelerator-cv", type=float, default=0.02)
    args = parser.parse_args()

    manifest = load_json(args.experiment_manifest)
    missing_manifest = sorted(REQUIRED_MANIFEST - set(manifest))
    if missing_manifest:
        raise ValueError(f"experiment manifest lacks fields: {missing_manifest}")
    if float(manifest["offered_byte_rate"]) <= 0.0:
        raise ValueError("offered_byte_rate must be positive")
    header, rows = load_timeline(args.timeline)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary = source_power_summary(header, rows, args.timeline)
    summary["experiment_manifest_sha256"] = sha256(args.experiment_manifest)
    write_json(args.output_dir / "source-power-summary.json", summary)
    write_json(
        args.output_dir / "stationarity.json",
        stationarity(rows, manifest, args.rom, args),
    )
    write_json(
        args.output_dir / "closed-loop-analysis.json",
        closed_loop(rows, manifest, args),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

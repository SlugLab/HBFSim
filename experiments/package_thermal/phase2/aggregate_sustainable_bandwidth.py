#!/usr/bin/env python3
"""Aggregate repeat-aware Phase-II sustainable-bandwidth boundaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean, median, stdev
from typing import Any


class AggregateError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AggregateError(f"{path} must contain a JSON object")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise AggregateError("percentile needs a value")
    position = (len(ordered) - 1) * p / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def statistics(values: list[float]) -> dict[str, float | int]:
    if not values:
        raise AggregateError("statistics needs a value")
    half_width = (
        1.96 * stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0.0
    )
    mean = fmean(values)
    return {
        "count": len(values),
        "mean": mean,
        "median": median(values),
        "iqr": percentile(values, 75.0) - percentile(values, 25.0),
        "ci95_normal_lower": mean - half_width,
        "ci95_normal_upper": mean + half_width,
    }


def consistent(values: list[float], field: str) -> float:
    if not values:
        raise AggregateError(f"missing {field}")
    first = values[0]
    if any(value != first for value in values[1:]):
        raise AggregateError(f"inconsistent {field} within experiment group")
    return first


def run_record(stationarity_path: Path) -> dict[str, Any]:
    stationarity = load(stationarity_path)
    manifest_path = stationarity_path.parent / "experiment-manifest.json"
    if not manifest_path.is_file():
        raise AggregateError(f"missing manifest beside {stationarity_path}")
    manifest = load(manifest_path)
    required = {
        "experiment_id",
        "thermal_stage",
        "workload",
        "seed",
        "offered_byte_rate",
        "peak_byte_rate",
        "unthrottled_byte_rate",
        "stack_height",
        "evidence_grid",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise AggregateError(f"{manifest_path} lacks fields: {missing}")
    if float(stationarity["offered_byte_rate"]) != float(
        manifest["offered_byte_rate"]
    ):
        raise AggregateError(f"offered rate mismatch in {stationarity_path.parent}")
    request_path = stationarity_path.parent / "request-summary.json"
    request = load(request_path) if request_path.is_file() else None
    return {
        "directory": str(stationarity_path.parent.resolve()),
        "stationarity_sha256": digest(stationarity_path),
        "manifest_sha256": digest(manifest_path),
        "request_summary_sha256": digest(request_path) if request else None,
        "manifest": manifest,
        "stationarity": stationarity,
        "request": request,
    }


def average_dict(records: list[dict[str, Any]], path: tuple[str, ...]) -> dict[str, float | None]:
    keys: set[str] = set()
    for record in records:
        value: Any = record["stationarity"]
        for component in path:
            value = value.get(component, {}) if isinstance(value, dict) else {}
        if isinstance(value, dict):
            keys.update(value)
    result: dict[str, float | None] = {}
    for key in sorted(keys):
        values: list[float] = []
        for record in records:
            value: Any = record["stationarity"]
            for component in path:
                value = value.get(component, {}) if isinstance(value, dict) else {}
            item = value.get(key) if isinstance(value, dict) else None
            if item is not None:
                values.append(float(item))
        result[key] = fmean(values) if values else None
    return result


def latency_summary(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if any(record["request"] is None for record in records):
        return None
    result: dict[str, Any] = {}
    for field in ("p50_ns", "p95_ns", "p99_ns"):
        values = [
            float(record["request"]["latency_ns"][field]) for record in records
        ]
        result[field] = statistics(values)
    return result


def aggregate_group(
    key: tuple[str, int, str],
    records: list[dict[str, Any]],
    minimum_repeats: int,
) -> dict[str, Any]:
    workload, stack_height, stage = key
    peak = consistent(
        [float(record["manifest"]["peak_byte_rate"]) for record in records],
        "peak_byte_rate",
    )
    unthrottled = consistent(
        [float(record["manifest"]["unthrottled_byte_rate"]) for record in records],
        "unthrottled_byte_rate",
    )
    by_rate: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_rate[float(record["manifest"]["offered_byte_rate"])].append(record)
    loads: list[dict[str, Any]] = []
    accepted_rates: list[float] = []
    for offered_rate in sorted(by_rate):
        repeats = by_rate[offered_rate]
        seeds = [int(record["manifest"]["seed"]) for record in repeats]
        if len(set(seeds)) != len(seeds):
            raise AggregateError(
                f"duplicate seed for {workload}/{stack_height}/{stage}/{offered_rate}"
            )
        repeat_complete = len(repeats) >= minimum_repeats
        all_stable = repeat_complete and all(
            bool(record["stationarity"]["stable"]) for record in repeats
        )
        if all_stable:
            accepted_rates.append(offered_rate)
        served = [
            float(record["stationarity"]["served_physical_byte_rate"])
            for record in repeats
            if record["stationarity"]["served_physical_byte_rate"] is not None
        ]
        loads.append(
            {
                "offered_byte_rate": offered_rate,
                "repeat_count": len(repeats),
                "seeds": sorted(seeds),
                "all_repeats_stable": all_stable,
                "served_byte_rate": statistics(served) if served else None,
                "run_evidence": [
                    {
                        name: record[name]
                        for name in (
                            "directory",
                            "stationarity_sha256",
                            "manifest_sha256",
                            "request_summary_sha256",
                        )
                    }
                    for record in repeats
                ],
            }
        )
    if not accepted_rates:
        return {
            "workload": workload,
            "stack_height": stack_height,
            "thermal_stage": stage,
            "verdict": "FAIL_NO_STABLE_LOAD",
            "B_peak": peak,
            "B_unthrottled": unthrottled,
            "B_sustainable": None,
            "loads": loads,
        }
    highest = max(accepted_rates)
    rejected_higher = [
        item["offered_byte_rate"]
        for item in loads
        if item["offered_byte_rate"] > highest
        and not item["all_repeats_stable"]
        and item["repeat_count"] >= minimum_repeats
    ]
    rejected_lower = [
        item["offered_byte_rate"]
        for item in loads
        if item["offered_byte_rate"] < highest
        and not item["all_repeats_stable"]
        and item["repeat_count"] >= minimum_repeats
    ]
    selected_records = by_rate[highest]
    selected_served = [
        float(record["stationarity"]["served_physical_byte_rate"])
        for record in selected_records
    ]
    sustainable = median(selected_served)
    latency = latency_summary(selected_records)
    if rejected_lower:
        verdict = "FAIL_NON_MONOTONIC_STABILITY"
    elif not rejected_higher:
        verdict = "LOWER_BOUND_UNBRACKETED"
    elif latency is None:
        verdict = "INCOMPLETE_LATENCY"
    else:
        verdict = "GO"
    return {
        "workload": workload,
        "stack_height": stack_height,
        "thermal_stage": stage,
        "verdict": verdict,
        "B_peak": peak,
        "B_unthrottled": unthrottled,
        "B_sustainable": sustainable,
        "B_sustainable_over_B_peak": sustainable / peak,
        "boundary": {
            "highest_stable_offered_byte_rate": highest,
            "lowest_complete_unstable_rate_above": min(rejected_higher)
            if rejected_higher
            else None,
            "bracketed": bool(rejected_higher),
        },
        "served_byte_rate_at_boundary": statistics(selected_served),
        "thermal_state_fraction": average_dict(
            selected_records, ("thermal_state_fraction",)
        ),
        "queue_depth": average_dict(selected_records, ("queue_depth",)),
        "latency_across_repeats": latency,
        "loads": loads,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-repeats", type=int, default=3)
    args = parser.parse_args()
    if args.minimum_repeats < 3:
        raise AggregateError("minimum repeats cannot be below Phase-II requirement 3")
    paths = sorted(args.runs_root.rglob("stationarity.json"))
    if not paths:
        raise AggregateError("no stationarity.json files found")
    groups: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        record = run_record(path)
        manifest = record["manifest"]
        key = (
            str(manifest["workload"]),
            int(manifest["stack_height"]),
            str(manifest["thermal_stage"]),
        )
        groups[key].append(record)
    result = {
        "schema_version": 1,
        "minimum_independent_repeats": args.minimum_repeats,
        "groups": [
            aggregate_group(key, groups[key], args.minimum_repeats)
            for key in sorted(groups)
        ],
        "claim_rule": (
            "GO requires >=3 unique seeds at each boundary load, all repeats "
            "stable, a complete unstable load above, monotonic stability, and "
            "per-run latency summaries"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AggregateError as error:
        print(f"aggregate_sustainable_bandwidth.py: {error}", file=sys.stderr)
        raise SystemExit(2)

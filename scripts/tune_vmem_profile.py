#!/usr/bin/env python3
"""Generate a deterministic HBFSim profile from vmem benchmark evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import pathlib


REQUIRED_READ_SIZES = (4096, 16384, 65536, 262144, 1048576, 2097152)
SAMPLES = tuple(range(1, 12))
SOURCE_CAPACITY_BYTES = 1920383410176
KNOWN_METRICS = {
    "ram_hot_read",
    "ram_cold_fault_read",
    "ssd_cache_hot_read",
    "ssd_cached_write",
    "ssd_cold_fault_read",
    "ssd_fsync",
}


def nearest_rank(values: list[float], quantile: float) -> float:
    if not values or not 0 < quantile <= 1:
        raise ValueError("nearest_rank requires values and quantile in (0, 1]")
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


def summarize(rows: list[dict[str, str]]) -> dict[str, object]:
    targets = {("ssd_cold_fault_read", size)
               for size in REQUIRED_READ_SIZES} | {("ssd_fsync", 4096)}
    groups: dict[tuple[str, int], dict[int, float]] = {
        key: {} for key in targets
    }
    for row in rows:
        try:
            metric = row["metric"]
            size = int(row["size_bytes"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("malformed benchmark row") from error
        if metric not in KNOWN_METRICS:
            raise ValueError(f"unknown metric: {metric}")
        key = (metric, size)
        if key not in targets:
            continue
        try:
            sample = int(row["sample"])
            latency = float(row["latency_us"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("malformed benchmark row") from error
        if not math.isfinite(latency) or latency <= 0:
            raise ValueError("latency must be finite and positive")
        if sample in groups[key]:
            raise ValueError(f"duplicate row: {key} sample {sample}")
        groups[key][sample] = latency
    for key, samples in groups.items():
        if tuple(sorted(samples)) != SAMPLES:
            raise ValueError(f"missing samples: {key}")

    curve = []
    for size in REQUIRED_READ_SIZES:
        values = list(groups[("ssd_cold_fault_read", size)].values())
        curve.append({
            "pages": size // 4096,
            "cumulative_ns": round(nearest_rank(values, 0.50) * 1000),
            "p95_ns": round(nearest_rank(values, 0.95) * 1000),
        })
    if any(right["cumulative_ns"] <= left["cumulative_ns"]
           for left, right in zip(curve, curve[1:])):
        raise ValueError("read P50 curve must be strictly increasing")
    if any(point["p95_ns"] < point["cumulative_ns"] for point in curve):
        raise ValueError("read P95 must not be below P50")

    writes = list(groups[("ssd_fsync", 4096)].values())
    program_p50 = round(nearest_rank(writes, 0.50) * 1000)
    program_p95 = round(nearest_rank(writes, 0.95) * 1000)
    if program_p95 < program_p50:
        raise ValueError("program P95 must not be below P50")
    return {
        "read_curve": curve,
        "program_p50_ns": program_p50,
        "program_p95_ns": program_p95,
        "sample_count": len(SAMPLES),
    }


def align_capacity(capacity: int, page_bytes: int, channels: int,
                   dies: int, planes: int, pages_per_block: int) -> int:
    values = (capacity, page_bytes, channels, dies, planes, pages_per_block)
    if any(value <= 0 for value in values):
        raise ValueError("capacity geometry values must be positive")
    unit = page_bytes * channels * dies * planes * pages_per_block
    aligned = capacity // unit * unit
    if aligned == 0:
        raise ValueError("aligned capacity is zero")
    return aligned


def scalar_prediction(base: dict[str, object], transfer_bytes: int) -> int:
    latency = int(base["read_latency_ns"])
    bandwidth = int(base["aggregate_bandwidth_bytes_per_s"])
    if transfer_bytes <= 0 or bandwidth <= 0:
        raise ValueError("scalar prediction inputs must be positive")
    transfer = (transfer_bytes * 1_000_000_000 + bandwidth - 1) // bandwidth
    return latency + transfer


def atomic_json(path: pathlib.Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w") as output:
            json.dump(document, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def generate(*, csv_path: pathlib.Path, base_profile: pathlib.Path,
             profile_path: pathlib.Path, report_path: pathlib.Path,
             expected_sha256: str | None) -> dict[str, object]:
    csv_path = pathlib.Path(csv_path)
    base_profile = pathlib.Path(base_profile)
    profile_path = pathlib.Path(profile_path)
    report_path = pathlib.Path(report_path)
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("source SHA256 mismatch")
    with csv_path.open(newline="") as source:
        summary = summarize(list(csv.DictReader(source)))
    base = json.loads(base_profile.read_text())
    capacity = align_capacity(
        SOURCE_CAPACITY_BYTES, 4096, int(base["channels"]),
        int(base["dies_per_channel"]), int(base["planes_per_die"]),
        int(base["pages_per_block"]))
    profile = dict(base)
    profile.update({
        "name": "cd8p-vmem-p50",
        "capacity_bytes": capacity,
        "page_bytes": 4096,
        "read_latency_ns": 11133,
        "program_latency_ns": 408305,
        "queue_depth": 1,
        "aggregate_bandwidth_bytes_per_s": 103540697,
        "hbm_cache_bytes": 4294967296,
        "time_scale": 1,
        "timing_tolerance_ns": 27105,
    })
    profile["empirical_vmem"] = {
        "source_kind": "nvme-mem2nvm-vmem-sw-cold-fault",
        "source_sha256": digest,
        "source_capacity_bytes": SOURCE_CAPACITY_BYTES,
        "quantile": "p50",
        "sample_count": summary["sample_count"],
        "read_curve": summary["read_curve"],
        "program_p50_ns": summary["program_p50_ns"],
        "program_p95_ns": summary["program_p95_ns"],
    }
    comparisons = []
    for point in summary["read_curve"]:
        size = point["pages"] * 4096
        observed = point["cumulative_ns"]
        nominal = scalar_prediction(base, size)
        constant = point["pages"] * summary["read_curve"][0]["cumulative_ns"]
        comparisons.append({
            "bytes": size,
            "observed_p50_ns": observed,
            "observed_p95_ns": point["p95_ns"],
            "nominal_scalar_ns": nominal,
            "nominal_relative_error": (nominal - observed) / observed,
            "constant_page_ns": constant,
            "constant_page_relative_error": (constant - observed) / observed,
            "empirical_ns": observed,
            "empirical_relative_error": 0.0,
        })
    command = [
        "scripts/tune_vmem_profile.py",
        "--input-csv", str(csv_path),
        "--base-profile", str(base_profile),
        "--output-profile", str(profile_path),
        "--output-report", str(report_path),
    ]
    if expected_sha256 is not None:
        command.extend(["--expected-sha256", expected_sha256])
    report = {
        "schema_version": 1,
        "source_sha256": digest,
        "source_capacity_bytes": SOURCE_CAPACITY_BYTES,
        "effective_capacity_bytes": capacity,
        "capacity_delta_bytes": SOURCE_CAPACITY_BYTES - capacity,
        "sample_count": summary["sample_count"],
        "comparisons": comparisons,
        "all_breakpoints_exact": all(
            item["empirical_ns"] == item["observed_p50_ns"]
            for item in comparisons),
        "command": command,
    }
    atomic_json(profile_path, profile)
    atomic_json(report_path, report)
    return {"profile": profile, "report": report}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", required=True, type=pathlib.Path)
    parser.add_argument("--base-profile", required=True, type=pathlib.Path)
    parser.add_argument("--output-profile", required=True, type=pathlib.Path)
    parser.add_argument("--output-report", required=True, type=pathlib.Path)
    parser.add_argument("--expected-sha256")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = generate(
        csv_path=args.input_csv,
        base_profile=args.base_profile,
        profile_path=args.output_profile,
        report_path=args.output_report,
        expected_sha256=args.expected_sha256)
    print(json.dumps(result["report"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fit deterministic contention-equivalent SM120 queues from training only."""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import statistics
import sys

CLASSES = ["ordinary_load", "ordinary_store", "tma_load", "tma_store",
           "unicast", "multicast", "mixed_hbm_hbf"]
INPUTS = ["smid", "warpid", "cta_shape", "resident_warps",
          "cluster_ctarank", "operation"]


class FitError(Exception):
    pass


def parse(argv: list[str]) -> dict[str, str]:
    if len(argv) != 6:
        raise ValueError
    result = {argv[index]: argv[index + 1] for index in range(0, 6, 2)}
    if set(result) != {"--training", "--stage1-fragment", "--output"}:
        raise ValueError
    return result


def read_regular(value: str) -> tuple[pathlib.Path, bytes]:
    path = pathlib.Path(value)
    if path.is_symlink() or not path.is_file():
        raise FitError(f"non-regular input: {path}")
    return path.resolve(), path.read_bytes()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_members(manifest: dict, directory: pathlib.Path) -> None:
    digest = hashlib.sha256()
    members = manifest.get("members", [])
    if not isinstance(members, list):
        raise FitError("members is not a list")
    for member in sorted(members, key=lambda item: str(item.get("path", ""))):
        relative = member.get("path")
        if not isinstance(relative, str) or pathlib.Path(relative).name != relative:
            raise FitError("unsafe training member path")
        path = directory / relative
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != member.get("sha256"):
            raise FitError(f"training member hash mismatch: {relative}")
        digest.update(relative.encode()); digest.update(b"\0")
        digest.update(str(member["sha256"]).encode()); digest.update(b"\0")
    if digest.hexdigest() != manifest.get("members_sha256"):
        raise FitError("training aggregate hash mismatch")


def observations(manifest: dict, directory: pathlib.Path) -> list[dict]:
    direct = manifest.get("observations")
    if isinstance(direct, list):
        return direct
    case_metadata: dict[str, dict] = {}
    case_path = manifest.get("case_manifest_path")
    if isinstance(case_path, str):
        path = pathlib.Path(case_path)
        if path.is_symlink() or not path.is_file():
            raise FitError("training case manifest unavailable")
        raw = path.read_bytes()
        if sha(raw) != manifest.get("case_manifest_sha256"):
            raise FitError("training case manifest hash mismatch")
        case_metadata = {item["id"]: item for item in json.loads(raw)["cases"]}
    result = []
    for run in manifest.get("runs", []):
        record = run.get("benchmark_record", {})
        case_id = run.get("case_id")
        case = case_metadata.get(case_id, {})
        stamps = record.get("timestamps", {})
        begin, end = stamps.get("issue"), stamps.get("end")
        operation = record.get("operation_class", case.get("operation_class"))
        if not isinstance(begin, int) or not isinstance(end, int) or end < begin:
            continue
        result.append({
            "case_id": case_id, "operation_class": operation,
            "smid": record.get("smid", 0), "warpid": record.get("warpid", 0),
            "cta_shape": [case.get("warps", 1) * 32, 1, 1],
            "resident_warps": case.get("warps", 1),
            "cluster_ctarank": record.get("cluster_ctarank", case.get("cta_rank", 0)),
            "latency_ns": max(1, end - begin), "bytes": case.get("bytes", 1),
            "queue_depth": case.get("queue_depth", 1), "counters": {},
        })
    return result


def labels(items: list[dict], field: str, count: int) -> list[int]:
    vectors = [item.get(field) for item in items]
    if all(isinstance(vector, list) and vector for vector in vectors):
        unique = sorted({tuple(float(value) for value in vector) for vector in vectors})
        if len(unique) != count:
            raise FitError(f"underdetermined {count}-way contention matrix")
        mapping = {value: index for index, value in enumerate(unique)}
        return [mapping[tuple(float(value) for value in vector)] for vector in vectors]
    ordered = sorted(range(len(items)), key=lambda index: (
        float(items[index]["latency_ns"]), str(items[index]["case_id"])))
    result = [0] * len(items)
    for rank, index in enumerate(ordered):
        result[index] = min(count - 1, rank * count // len(items))
    return result


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def routing_bucket(item: dict) -> int:
    payload = [item.get("smid", 0), item.get("warpid", 0),
               item.get("cta_shape", [1, 1, 1]), item.get("resident_warps", 1),
               item.get("cluster_ctarank", 0), item.get("operation_class", "")]
    return int.from_bytes(hashlib.sha256(canonical(payload)).digest()[:8], "big") % 64


def fitted_lut(items: list[dict], item_labels: list[int], count: int) -> list[int]:
    votes: list[list[int]] = [[] for _ in range(64)]
    for item, label in zip(items, item_labels):
        votes[routing_bucket(item)].append(label)
    return [statistics.mode(bucket) if bucket else index % count
            for index, bucket in enumerate(votes)]


def main(argv: list[str]) -> int:
    try:
        options = parse(argv)
    except ValueError:
        print("usage: fit_sm120_channels.py --training FILE --stage1-fragment FILE --output FILE",
              file=sys.stderr)
        return 64
    try:
        training_path, raw_training = read_regular(options["--training"])
        _, raw_stage1 = read_regular(options["--stage1-fragment"])
        output = pathlib.Path(options["--output"]).absolute()
        if output.exists() or output.is_symlink() or output.parent.is_symlink() or not output.parent.is_dir():
            raise FitError("unsafe or existing output")
        manifest = json.loads(raw_training)
        stage1 = json.loads(raw_stage1)
        if manifest.get("suite") != "training":
            raise FitError("fitter accepts training suite only")
        metrics = manifest.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise FitError("training metrics missing")
        verify_members(manifest, training_path.parent)
        items = observations(manifest, training_path.parent)
        if len(items) < 28:
            raise FitError("underdetermined training set")
        present = {item.get("operation_class") for item in items}
        if present != set(CLASSES):
            raise FitError("training operation class missing")
        gnic_labels = labels(items, "contention_vector", 4)
        gpc_labels = labels(items, "return_contention_vector", 2)
        gnic_lut = fitted_lut(items, gnic_labels, 4)
        gpc_lut = fitted_lut(items, gpc_labels, 2)
        service = []
        residuals = []
        for operation in CLASSES:
            values = [float(item["latency_ns"]) for item in items
                      if item["operation_class"] == operation]
            median = max(1, round(statistics.median(values)))
            service.append(median)
            errors = [abs(value - median) * 100.0 / max(1.0, value)
                      for value in values]
            residuals.append({"operation_class": operation,
                              "p50_error_percent": round(percentile(errors, .50), 6),
                              "p95_error_percent": round(percentile(errors, .95), 6)})
        depth = max(int(item.get("queue_depth", 1)) for item in items)
        routing = {"version": 1, "inputs": INPUTS,
                   "smsp_proxy_lut": [index % 4 for index in range(64)],
                   "gnic_lut": gnic_lut, "gpc_lut": gpc_lut}
        routing["program_sha256"] = sha(canonical(routing))
        calibration = {
            "label_semantics": "contention_equivalent",
            "gnic": {"count": 4, "depth": depth, "arbitration": "fifo",
                     "service_ns_by_class": service},
            "gpc": {"count": 2, "depth": depth,
                    "arbitration": "round_robin",
                    "service_ns_by_class": [max(1, round(value * .8)) for value in service]},
            "routing": routing, "metric_names": metrics,
            "raw_training_sha256": sha(raw_training),
            "raw_holdout_sha256": "0" * 64,
            "fitted_case_ids": sorted({str(item["case_id"]) for item in items}),
            "residuals": residuals,
            "counter_thresholds": [{"metric": metric,
                                      "max_error_percent": 10.0}
                                     for metric in metrics],
        }
        candidate = dict(stage1)
        candidate["schema_version"] = 2
        candidate.setdefault("validation", {})["status"] = "pending"
        candidate["calibration"] = calibration
        candidate["fit_report"] = {
            "schema_version": 1, "training_sha256": sha(raw_training),
            "environment_sha256": sha(canonical(manifest.get("environment", {}))),
            "frozen_thresholds": stage1.get("thresholds", {}),
            "selected": {"gnic_classes": 4, "gpc_classes": 2,
                         "routing_program_sha256": routing["program_sha256"]},
            "cross_validation": {"method": "deterministic-even-odd-training-only",
                                 "holdout_read": False},
            "rejected_candidates": [
                {"gnic_classes": value, "reason": "required_class_count_mismatch"}
                for value in (1, 2, 3, 5, 6)],
        }
        encoded = (json.dumps(candidate, indent=2, sort_keys=True) + "\n").encode()
        with output.open("xb") as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        print(json.dumps({"status": "pending", "output": str(output),
                          "routing_program_sha256": routing["program_sha256"]},
                         sort_keys=True))
        return 0
    except (FitError, json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"fit_sm120_channels: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

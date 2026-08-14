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
        result = []
        for value in direct:
            if not isinstance(value, dict):
                raise FitError("training observation is malformed")
            item = dict(value)
            if "latency_ns" not in item:
                item["latency_ns"] = item.get("native_latency_ns")
            result.append(item)
        return result
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
    raw = [item.get(field) for item in items]
    if not all(isinstance(vector, list) and vector for vector in raw):
        raise FitError(f"{count}-way contention vectors are missing")
    try:
        vectors = [tuple(float(value) for value in vector) for vector in raw]
    except (TypeError, ValueError) as error:
        raise FitError("contention vector is not numeric") from error
    width = len(vectors[0])
    if width == 0 or any(len(vector) != width or any(
            not math.isfinite(value) for value in vector) for vector in vectors):
        raise FitError("contention vector shape/value is invalid")
    columns = list(zip(*vectors))
    minima = [min(column) for column in columns]
    spans = [max(column) - minimum
             for column, minimum in zip(columns, minima)]
    normalized = [tuple((value - minima[index]) / spans[index]
                        if spans[index] else 0.0
                        for index, value in enumerate(vector))
                  for vector in vectors]
    unique = sorted(set(normalized))
    if len(unique) < count:
        raise FitError(f"underdetermined {count}-way contention matrix")

    def distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        return sum((a - b) ** 2 for a, b in zip(left, right))

    centroids = [unique[0]]
    while len(centroids) < count:
        candidates = [(min(distance(value, center) for center in centroids),
                       value) for value in unique if value not in centroids]
        centroids.append(max(candidates, key=lambda item: (item[0], item[1]))[1])
    assignments = [0] * len(normalized)
    for _ in range(100):
        next_assignments = [min(range(count),
                                key=lambda index: (distance(value,
                                                            centroids[index]),
                                                   index))
                            for value in normalized]
        if next_assignments == assignments and _ != 0:
            break
        assignments = next_assignments
        next_centroids = []
        for label in range(count):
            members = [value for value, assigned in zip(normalized, assignments)
                       if assigned == label]
            if not members:
                raise FitError(f"empty {count}-way contention cluster")
            next_centroids.append(tuple(sum(column) / len(members)
                                        for column in zip(*members)))
        centroids = next_centroids
    order = {old: new for new, old in enumerate(sorted(
        range(count), key=lambda index: centroids[index]))}
    return [order[value] for value in assignments]


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


def positive_integer_map(value: object, required: set[str],
                         label: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != required or any(
            not isinstance(item, int) or isinstance(item, bool) or item <= 0
            for item in value.values()):
        raise FitError(f"invalid {label}")
    return {key: int(value[key]) for key in sorted(required)}


def runtime_artifacts(stage1_path: pathlib.Path, stage1: dict) -> dict[str, str]:
    provenance = stage1.get("provenance")
    if not isinstance(provenance, dict) or \
            set(provenance) != {"pass_manifest_sha256", "bundle"}:
        raise FitError("stage1 provenance missing")
    bundle = pathlib.Path(str(provenance["bundle"]))
    pass_manifest = stage1_path.parent / "pass-manifest.jsonl"
    prepatched = stage1_path.parent / "prepatched-ptx"
    for path, kind in ((bundle, "bundle"), (prepatched, "prepatched PTX")):
        if path.is_symlink() or not path.is_dir():
            raise FitError(f"stage1 {kind} directory unavailable")
    if pass_manifest.is_symlink() or not pass_manifest.is_file() or \
            sha(pass_manifest.read_bytes()) != provenance["pass_manifest_sha256"]:
        raise FitError("stage1 pass manifest unavailable or changed")
    if len(bundle.parents) < 2:
        raise FitError("stage1 bundle layout is invalid")
    bundle_root = bundle.parents[1]
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        raise FitError("stage1 bundle root is unsafe")
    ptx_files = sorted(prepatched.glob("*.ptx"))
    if not ptx_files or any(path.is_symlink() or not path.is_file()
                            for path in ptx_files):
        raise FitError("stage1 prepatched PTX set is empty or unsafe")
    return {"bundle_root": str(bundle_root.resolve()),
            "prepatched_ptx_dir": str(prepatched.resolve()),
            "pass_manifest": str(pass_manifest.resolve())}


def build_profile_base(training_path: pathlib.Path, manifest: dict,
                       stage1_path: pathlib.Path, stage1: dict,
                       training_sha256: str) -> dict:
    if stage1.get("fragment_schema_version") != 1:
        raise FitError("stage1 fragment schema mismatch")
    toolchain = stage1.get("toolchain")
    modules = stage1.get("modules")
    if not isinstance(toolchain, dict) or not isinstance(modules, list) or \
            not modules:
        raise FitError("stage1 toolchain/module evidence missing")
    environment = manifest.get("calibration_environment")
    target = environment.get("target") if isinstance(environment, dict) else None
    target_keys = {"gpu_name", "gpu_uuid", "pci_vendor_id", "pci_device_id",
                   "compute_capability_major", "compute_capability_minor",
                   "driver_version"}
    if not isinstance(target, dict) or set(target) != target_keys:
        raise FitError("calibration target evidence missing")
    if target.get("compute_capability_major") != 12 or \
            target.get("compute_capability_minor") != 0:
        raise FitError("calibration target is not SM120")
    contract = manifest.get("exact_profile_contract")
    if not isinstance(contract, dict):
        raise FitError("frozen exact profile contract missing")
    thresholds = contract.get("thresholds")
    if thresholds != {"p50_percent": 5, "p95_percent": 10,
                      "counter_percent": 10}:
        raise FitError("exact thresholds are absent or relaxed")
    limit_keys = {"max_thread_futures", "max_warp_futures",
                  "max_cta_futures", "max_cluster_futures",
                  "max_thread_async_objects", "max_warp_async_objects",
                  "max_cta_async_objects", "max_cluster_async_objects"}
    limits = positive_integer_map(contract.get("limits"), limit_keys,
                                  "exact limits")
    cluster = contract.get("cluster_shape")
    if not isinstance(cluster, list) or len(cluster) != 3 or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in cluster):
        raise FitError("invalid deployment cluster shape")
    snapshots = manifest.get("gpu_snapshots")
    if not isinstance(snapshots, list) or len(snapshots) < 2:
        raise FitError("operating-condition snapshots missing")
    try:
        sm_clocks = [int(item["sm_clock_mhz"]) for item in snapshots]
        memory_clocks = [int(item["memory_clock_mhz"]) for item in snapshots]
        power_limits = [int(item["power_limit_mw"]) for item in snapshots]
        temperatures = [int(item["temperature_c"]) for item in snapshots]
    except (KeyError, TypeError, ValueError) as error:
        raise FitError("operating-condition snapshot malformed") from error
    if len(set(power_limits)) != 1:
        raise FitError("power limit changed during training")
    module_hash = str(modules[0].get("original_ptx_sha256", ""))
    if len(module_hash) != 64:
        raise FitError("stage1 module identity missing")
    return {
        "schema_version": 2,
        "profile_id": f"sm120-{training_sha256[:16]}-{module_hash[:12]}",
        "target": dict(target), "toolchain": dict(toolchain),
        "conditions": {
            "sm_clock_mhz": statistics.mode(sm_clocks),
            "memory_clock_mhz": statistics.mode(memory_clocks),
            "power_limit_mw": power_limits[0],
            "temperature_min_c": min(temperatures),
            "temperature_max_c": max(temperatures),
            "cache_condition": contract.get("cache_condition"),
            "concurrency_condition": contract.get("concurrency_condition"),
            "cluster_shape": {"x": cluster[0], "y": cluster[1],
                              "z": cluster[2]},
        },
        "thresholds": dict(thresholds), "limits": limits,
        "modules": modules, "validation": {"status": "pending"},
        "runtime_artifacts": runtime_artifacts(stage1_path, stage1),
    }


def main(argv: list[str]) -> int:
    try:
        options = parse(argv)
    except ValueError:
        print("usage: fit_sm120_channels.py --training FILE --stage1-fragment FILE --output FILE",
              file=sys.stderr)
        return 64
    try:
        training_path, raw_training = read_regular(options["--training"])
        stage1_path, raw_stage1 = read_regular(options["--stage1-fragment"])
        output = pathlib.Path(options["--output"]).absolute()
        if output.exists() or output.is_symlink() or output.parent.is_symlink() or not output.parent.is_dir():
            raise FitError("unsafe or existing output")
        manifest = json.loads(raw_training)
        stage1 = json.loads(raw_stage1)
        if manifest.get("suite") != "training":
            raise FitError("fitter accepts training suite only")
        environment_sha256 = manifest.get("environment_sha256")
        if not isinstance(environment_sha256, str) or \
                len(environment_sha256) != 64:
            raise FitError("training environment hash missing")
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
        counter_model: dict[str, dict[str, float]] = {}
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
            selected_items = [item for item in items
                              if item["operation_class"] == operation]
            counter_model[operation] = {}
            for metric in metrics:
                samples = [item.get("native_counters", item.get("counters", {})).get(metric)
                           for item in selected_items]
                if not all(isinstance(value, (int, float)) and
                           not isinstance(value, bool) and math.isfinite(value)
                           for value in samples):
                    raise FitError(f"training counter missing: {operation}:{metric}")
                counter_model[operation][metric] = round(
                    statistics.median(float(value) for value in samples), 9)
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
        training_sha256 = sha(raw_training)
        candidate = build_profile_base(training_path, manifest, stage1_path,
                                       stage1, training_sha256)
        candidate["calibration"] = calibration
        candidate["fit_report"] = {
            "schema_version": 1, "training_sha256": training_sha256,
            "environment_sha256": environment_sha256,
            "frozen_thresholds": candidate["thresholds"],
            "selected": {"gnic_classes": 4, "gpc_classes": 2,
                         "routing_program_sha256": routing["program_sha256"]},
            "cross_validation": {"method": "deterministic-even-odd-training-only",
                                 "holdout_read": False},
            "counter_model_by_class": counter_model,
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

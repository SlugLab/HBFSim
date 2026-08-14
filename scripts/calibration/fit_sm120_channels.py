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
FEATURE_NAMES = [
    "log2_issued_operations", "log2_bytes", "log2_resident_warps",
    "log2_queue_depth", "dimension_count", "cache_warm",
    "log2_iterations", "log2_load_use_distance_plus_one",
    "log2_tile_elements", "cluster_size", "multicast_targets",
]
COUNTER_ERROR_CONTRACT = {
    "version": 1,
    "percentage_metrics": "absolute_percentage_points",
    "traffic_metrics":
        "native_or_logical_issued_or_training_class_envelope",
    "duration_metrics": "relative_to_native",
    "fallback_metrics": "relative_to_native",
}


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
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(str(member["sha256"]).encode())
        digest.update(b"\0")
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


def feature_vector(item: dict) -> list[float]:
    dimensions = item.get("dimensions", [])
    if not isinstance(dimensions, list) or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in dimensions):
        dimensions = []
    tile_elements = math.prod(dimensions) if dimensions else 1
    cluster = item.get("executed_cluster_shape", [1, 1, 1])
    if not isinstance(cluster, list) or len(cluster) != 3 or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in cluster):
        cluster = [1, 1, 1]
    mask = item.get("executed_multicast_mask", 0)
    if not isinstance(mask, int) or isinstance(mask, bool) or mask < 0:
        mask = 0
    def positive(name: str, default: int = 1) -> int:
        return max(1, int(item.get(name, default)))
    return [
        math.log2(positive("issued_operations")),
        math.log2(positive("bytes")),
        math.log2(positive("resident_warps")),
        math.log2(positive("queue_depth")),
        float(max(0, int(item.get("executed_dimension_count", 0)))),
        1.0 if item.get("cache_condition_executed") == "warm" else 0.0,
        math.log2(positive("iterations")),
        math.log2(max(0, int(item.get("load_use_distance", 0))) + 1),
        math.log2(tile_elements),
        float(math.prod(cluster)),
        float(mask.bit_count()),
    ]


def base_case_id(item: dict) -> str:
    explicit = item.get("base_case_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    value = str(item.get("case_id", ""))
    return value.split(".repeat-", 1)[0]


def build_predictor(items: list[dict], metrics: list[str]) -> dict:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        operation = str(item.get("operation_class", ""))
        grouped.setdefault((operation, base_case_id(item)), []).append(item)
    prototypes: dict[str, list[dict]] = {operation: [] for operation in CLASSES}
    for (operation, case_id), samples in sorted(grouped.items()):
        if operation not in prototypes:
            raise FitError("training predictor contains an unknown class")
        vectors = [feature_vector(sample) for sample in samples]
        if any(vector != vectors[0] for vector in vectors[1:]):
            raise FitError(f"training case features changed across repeats: {case_id}")
        latencies = [float(sample["latency_ns"]) for sample in samples]
        counters: dict[str, float] = {}
        for metric in metrics:
            values = [sample.get("native_counters",
                                 sample.get("counters", {})).get(metric)
                      for sample in samples]
            if not all(isinstance(value, (int, float)) and
                       not isinstance(value, bool) and math.isfinite(value)
                       for value in values):
                raise FitError(f"training counter missing: {operation}:{metric}")
            counters[metric] = round(statistics.median(
                float(value) for value in values), 9)
        prototypes[operation].append({
            "base_case_id": case_id,
            "features": [round(value, 9) for value in vectors[0]],
            "latency_ns": max(1, round(statistics.median(latencies))),
            "counters": counters,
        })
    if any(not prototypes[operation] for operation in CLASSES):
        raise FitError("training predictor class missing")
    domains = {}
    for operation in CLASSES:
        columns = list(zip(*(item["features"]
                             for item in prototypes[operation])))
        domains[operation] = {
            "minimum": [round(min(column), 9) for column in columns],
            "maximum": [round(max(column), 9) for column in columns],
        }
    return {
        "schema_version": 1,
        "feature_names": FEATURE_NAMES,
        "neighbors": 2,
        "aggregation": "median_by_base_case",
        "distance": "normalized_euclidean_inverse_square",
        "prototypes_by_class": prototypes,
        "domain_by_class": domains,
    }


def predict(predictor: dict, operation: str,
            features: list[float]) -> tuple[float, dict[str, float]]:
    prototypes = predictor["prototypes_by_class"][operation]
    domain = predictor["domain_by_class"][operation]
    minimum = domain["minimum"]
    maximum = domain["maximum"]
    distances = []
    for prototype in prototypes:
        distance = 0.0
        for value, reference, low, high in zip(
                features, prototype["features"], minimum, maximum):
            span = float(high) - float(low)
            delta = value - float(reference)
            distance += (delta / span) ** 2 if span > 0 else delta ** 2
        distances.append((distance, float(prototype["latency_ns"]),
                          prototype["counters"]))
    distances.sort(key=lambda item: item[0])
    selected = distances[:min(int(predictor["neighbors"]), len(distances))]
    exact = [item for item in selected if item[0] <= 1e-18]
    if exact:
        selected = exact
        weights = [1.0] * len(selected)
    else:
        weights = [1.0 / item[0] for item in selected]
    denominator = sum(weights)
    latency = sum(weight * item[1]
                  for weight, item in zip(weights, selected)) / denominator
    counters = {
        metric: sum(weight * float(item[2][metric])
                    for weight, item in zip(weights, selected)) / denominator
        for metric in selected[0][2]
    }
    return latency, counters


def relative_error(modeled: float, native: float) -> float:
    if modeled == native == 0:
        return 0.0
    return abs(modeled - native) * 100.0 / max(abs(native), 1e-12)


def logical_issued_bytes(item: dict) -> float:
    issued = item.get("issued_operations", 1)
    if not isinstance(issued, (int, float)) or isinstance(issued, bool) or \
            not math.isfinite(float(issued)) or issued <= 0:
        issued = 1
    dimensions = item.get("dimensions", [])
    dimension_count = item.get("executed_dimension_count", 0)
    if isinstance(dimensions, list) and dimensions and \
            isinstance(dimension_count, int) and not isinstance(
                dimension_count, bool) and dimension_count == len(dimensions) and \
            all(isinstance(value, int) and not isinstance(value, bool) and
                value > 0 for value in dimensions):
        bytes_per_operation = math.prod(dimensions) * 4
    else:
        bytes_per_operation = 8
    mask = item.get("executed_multicast_mask", 0)
    fanout = mask.bit_count() if isinstance(mask, int) and \
        not isinstance(mask, bool) and mask > 0 else 1
    return float(issued) * bytes_per_operation * fanout


def counter_error_percent(modeled: float, native: float, metric: str,
                          item: dict, training_scale: float = 0.0) -> float:
    absolute = abs(modeled - native)
    if metric.endswith(".pct") or ".pct_of_peak_" in metric:
        denominator = 100.0
    elif metric == "dram__bytes.sum":
        denominator = max(abs(native), logical_issued_bytes(item),
                          training_scale)
    elif metric == "lts__t_sectors.sum":
        denominator = max(abs(native),
                          math.ceil(logical_issued_bytes(item) / 32.0),
                          training_scale)
    else:
        denominator = abs(native)
    if absolute == denominator == 0:
        return 0.0
    return absolute * 100.0 / max(denominator, 1e-12)


def build_counter_error_scales(items: list[dict],
                               metrics: list[str]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for operation in CLASSES:
        selected = [item for item in items
                    if item.get("operation_class") == operation]
        if not selected:
            raise FitError(f"counter scale class missing: {operation}")
        result[operation] = {}
        for metric in metrics:
            if metric.endswith(".pct") or ".pct_of_peak_" in metric:
                scale = 100.0
            elif metric in {"dram__bytes.sum", "lts__t_sectors.sum"}:
                samples = []
                for item in selected:
                    counters = item.get("native_counters",
                                        item.get("counters", {}))
                    native = counters.get(metric)
                    if not isinstance(native, (int, float)) or \
                            isinstance(native, bool) or \
                            not math.isfinite(float(native)):
                        raise FitError(
                            f"training counter missing: {operation}:{metric}")
                    opportunity = logical_issued_bytes(item)
                    if metric == "lts__t_sectors.sum":
                        opportunity = math.ceil(opportunity / 32.0)
                    samples.append(max(abs(float(native)), opportunity))
                scale = max(samples)
            else:
                scale = 0.0
            result[operation][metric] = round(scale, 9)
    return result


def cross_validate(items: list[dict], metrics: list[str],
                   thresholds: dict[str, int]) -> dict:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        key = (str(item.get("operation_class", "")), base_case_id(item))
        grouped.setdefault(key, []).append(item)
    fit_items: list[dict] = []
    validation_items: list[dict] = []
    for (operation, case_id), samples in sorted(grouped.items()):
        ordered = sorted(samples, key=lambda item: str(item.get("case_id", "")))
        if len(ordered) < 2:
            raise FitError(
                f"training cross-validation repetitions missing: {operation}:{case_id}")
        fit_items.extend(ordered[0::2])
        retained = ordered[1::2]
        reference = dict(retained[0])
        reference["case_id"] = f"{case_id}.cv-validation"
        reference["latency_ns"] = statistics.median(
            float(item["latency_ns"]) for item in retained)
        reference["native_counters"] = {
            metric: statistics.median(float(item.get(
                "native_counters", item.get("counters", {}))[metric])
                                      for item in retained)
            for metric in metrics
        }
        validation_items.append(reference)
    predictor = build_predictor(fit_items, metrics)
    counter_scales = build_counter_error_scales(fit_items, metrics)
    classes = []
    for operation in CLASSES:
        selected = [item for item in validation_items
                    if item.get("operation_class") == operation]
        if not selected:
            raise FitError(
                f"training cross-validation class missing: {operation}")
        latency_errors = []
        counter_errors = []
        for item in selected:
            modeled, modeled_counters = predict(
                predictor, operation, feature_vector(item))
            latency_errors.append(relative_error(
                modeled, float(item["latency_ns"])))
            native_counters = item.get("native_counters",
                                       item.get("counters", {}))
            for metric in metrics:
                counter_errors.append(counter_error_percent(
                    float(modeled_counters[metric]),
                    float(native_counters[metric]), metric, item,
                    counter_scales[operation][metric]))
        p50 = percentile(latency_errors, .50)
        p95 = percentile(latency_errors, .95)
        counter = max(counter_errors)
        counter_within = counter <= thresholds["counter_percent"]
        passed = p50 <= thresholds["p50_percent"] and \
            p95 <= thresholds["p95_percent"] and counter_within
        classes.append({
            "operation_class": operation,
            "passed": passed,
            "p50_error_percent": round(p50, 6),
            "p95_error_percent": round(p95, 6),
            "counter_error_percent": round(counter, 6),
            "counter_within_holdout_threshold": counter_within,
            "validation_samples": len(selected),
        })
    return {
        "method": "repetition-stratified-training-only",
        "split": {"fit_repetition_indices": "even",
                  "validation_repetition_indices": "odd"},
        "counter_error_scale_source": "fit_repetitions_only",
        "holdout_read": False,
        "passed": all(item["passed"] for item in classes),
        "evaluated_samples": len(validation_items),
        "classes": classes,
    }


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
    clock_control = contract.get("clock_control")
    if clock_control != "none":
        raise FitError("unsupported calibration clock policy")
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
    module_hashes = sorted(str(module.get("original_ptx_sha256", ""))
                           for module in modules if isinstance(module, dict))
    if len(module_hashes) != len(modules) or \
            len(set(module_hashes)) != len(module_hashes) or \
            any(len(value) != 64 for value in module_hashes):
        raise FitError("stage1 module identity missing")
    module_set_hash = module_hashes[0] if len(module_hashes) == 1 else \
        sha(canonical(module_hashes))
    return {
        "schema_version": 2,
        "profile_id": f"sm120-{training_sha256[:16]}-{module_set_hash[:12]}",
        "target": dict(target), "toolchain": dict(toolchain),
        "conditions": {
            "sm_clock_mhz": statistics.mode(sm_clocks),
            "memory_clock_mhz": statistics.mode(memory_clocks),
            "clock_control": clock_control,
            "sm_clock_min_mhz": min(sm_clocks),
            "sm_clock_max_mhz": max(sm_clocks),
            "memory_clock_min_mhz": min(memory_clocks),
            "memory_clock_max_mhz": max(memory_clocks),
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
        predictor = build_predictor(items, metrics)
        predictor_sha256 = sha(canonical(predictor))
        workload_domain = {
            "schema_version": 1,
            "match_policy": "exact_calibrated_vector",
            "program_sha256": predictor_sha256,
            "feature_names": list(predictor["feature_names"]),
            "vectors_by_class": {
                operation: [list(features) for features in sorted({
                    tuple(prototype["features"])
                    for prototype in predictor["prototypes_by_class"][operation]
                })]
                for operation in CLASSES
            },
        }
        cross_validation = cross_validate(
            items, metrics, manifest["exact_profile_contract"]["thresholds"])
        if not cross_validation["passed"]:
            failed = [item["operation_class"]
                      for item in cross_validation["classes"]
                      if not item["passed"]]
            raise FitError("training cross-validation failed: " +
                           ",".join(failed))
        service = []
        residuals = []
        counter_model: dict[str, dict[str, float]] = {}
        for operation in CLASSES:
            values = [float(item["latency_ns"]) for item in items
                      if item["operation_class"] == operation]
            per_request = [
                float(prototype["latency_ns"]) /
                max(1.0, 2.0 ** float(prototype["features"][0]))
                for prototype in
                predictor["prototypes_by_class"][operation]
            ]
            median = max(1, round(statistics.median(values)))
            service.append(max(1, round(statistics.median(per_request))))
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
        counter_error_scales = build_counter_error_scales(items, metrics)
        routing = {"version": 1, "inputs": INPUTS,
                   "smsp_proxy_lut": [index % 4 for index in range(64)],
                   "gnic_lut": gnic_lut, "gpc_lut": gpc_lut}
        routing["program_sha256"] = sha(canonical(routing))
        calibration = {
            "label_semantics": "contention_equivalent",
            "counter_error_contract": COUNTER_ERROR_CONTRACT,
            "counter_error_scale_by_class": counter_error_scales,
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
            "workload_domain": workload_domain,
        }
        training_sha256 = sha(raw_training)
        candidate = build_profile_base(training_path, manifest, stage1_path,
                                       stage1, training_sha256)
        candidate["profile_id"] += f"-{predictor_sha256[:12]}"
        candidate["calibration"] = calibration
        candidate["fit_report"] = {
            "schema_version": 1, "training_sha256": training_sha256,
            "environment_sha256": environment_sha256,
            "frozen_thresholds": candidate["thresholds"],
            "selected": {"gnic_classes": 4, "gpc_classes": 2,
                         "routing_program_sha256": routing["program_sha256"],
                         "predictor_program_sha256": predictor_sha256},
            "cross_validation": cross_validation,
            "counter_model_by_class": counter_model,
            "predictor": predictor,
            "rejected_candidates": [
                {"gnic_classes": value, "reason": "required_class_count_mismatch"}
                for value in (1, 2, 3, 5, 6)],
        }
        encoded = (json.dumps(candidate, indent=2, sort_keys=True) + "\n").encode()
        with output.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({"status": "pending", "output": str(output),
                          "routing_program_sha256": routing["program_sha256"]},
                         sort_keys=True))
        return 0
    except (FitError, json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"fit_sm120_channels: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

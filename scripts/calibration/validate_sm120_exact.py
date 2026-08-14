#!/usr/bin/env python3
"""Independently validate frozen SM120 candidates against disjoint holdout."""

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


class ValidationFailure(Exception):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def parse(argv: list[str]) -> dict[str, str]:
    if len(argv) != 8:
        raise ValueError
    result = {argv[index]: argv[index + 1] for index in range(0, 8, 2)}
    required = {"--candidate", "--holdout", "--output-profile", "--report"}
    if set(result) != required:
        raise ValueError
    return result


def regular(path_text: str) -> tuple[pathlib.Path, bytes]:
    path = pathlib.Path(path_text)
    if path.is_symlink() or not path.is_file():
        raise ValidationFailure(f"unsafe input: {path}")
    return path.resolve(), path.read_bytes()


def new_output(path_text: str) -> pathlib.Path:
    path = pathlib.Path(path_text).absolute()
    if path.exists() or path.is_symlink() or path.parent.is_symlink() or not path.parent.is_dir():
        raise ValidationFailure(f"unsafe or existing output: {path}")
    return path


def write(path: pathlib.Path, value: object) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def verify_members(manifest: dict, directory: pathlib.Path) -> None:
    digest = hashlib.sha256()
    members = manifest.get("members")
    if not isinstance(members, list):
        raise ValidationFailure("holdout member list missing")
    for member in sorted(members, key=lambda item: str(item.get("path", ""))):
        relative = member.get("path")
        if not isinstance(relative, str) or pathlib.Path(relative).name != relative:
            raise ValidationFailure("unsafe holdout member path")
        path = directory / relative
        if path.is_symlink() or not path.is_file() or sha(path.read_bytes()) != member.get("sha256"):
            raise ValidationFailure(f"holdout member hash mismatch: {relative}")
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(str(member["sha256"]).encode())
        digest.update(b"\0")
    if digest.hexdigest() != manifest.get("members_sha256"):
        raise ValidationFailure("holdout aggregate hash mismatch")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1,
                       max(0, math.ceil(len(ordered) * fraction) - 1))]


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


def aggregate_observations(items: list[dict], metrics: list[str]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in items:
        grouped.setdefault((str(item.get("operation_class", "")),
                            base_case_id(item)), []).append(item)
    result = []
    for (operation, case_id), samples in sorted(grouped.items()):
        vectors = [feature_vector(sample) for sample in samples]
        if any(vector != vectors[0] for vector in vectors[1:]):
            raise ValidationFailure(
                f"holdout case features changed across repeats: {case_id}")
        latencies = [sample.get("native_latency_ns") for sample in samples]
        if not all(isinstance(value, (int, float)) and
                   not isinstance(value, bool) and value > 0 and
                   math.isfinite(float(value)) for value in latencies):
            raise ValidationFailure(f"holdout latency missing: {case_id}")
        counters = {}
        for metric in metrics:
            values = [sample.get("native_counters", {}).get(metric)
                      for sample in samples]
            if not all(isinstance(value, (int, float)) and
                       not isinstance(value, bool) and
                       math.isfinite(float(value)) for value in values):
                raise ValidationFailure(
                    f"holdout counter missing: {case_id}:{metric}")
            counters[metric] = statistics.median(float(value)
                                                  for value in values)
        result.append({
            "base_case_id": case_id,
            "operation_class": operation,
            "features": vectors[0],
            "issued_operations": samples[0].get("issued_operations", 1),
            "dimensions": samples[0].get("dimensions", []),
            "executed_dimension_count": samples[0].get(
                "executed_dimension_count", 0),
            "executed_multicast_mask": samples[0].get(
                "executed_multicast_mask", 0),
            "native_latency_ns": statistics.median(
                float(value) for value in latencies),
            "native_counters": counters,
            "byte_exact": all(sample.get("expected_sha256") ==
                              sample.get("observed_sha256")
                              for sample in samples),
        })
    return result


def predict(predictor: dict, operation: str,
            features: list[float]) -> tuple[float, dict[str, float], str]:
    if predictor.get("schema_version") != 1 or \
            predictor.get("feature_names") != FEATURE_NAMES or \
            predictor.get("aggregation") != "median_by_base_case" or \
            predictor.get("distance") != \
            "normalized_euclidean_inverse_square":
        return 0.0, {}, "predictor_schema_invalid"
    neighbors = predictor.get("neighbors")
    prototypes = predictor.get("prototypes_by_class", {}).get(operation)
    domain = predictor.get("domain_by_class", {}).get(operation)
    if not isinstance(neighbors, int) or isinstance(neighbors, bool) or \
            neighbors <= 0 or not isinstance(prototypes, list) or \
            not prototypes or not isinstance(domain, dict):
        return 0.0, {}, "predictor_class_missing"
    minimum = domain.get("minimum")
    maximum = domain.get("maximum")
    if not isinstance(minimum, list) or not isinstance(maximum, list) or \
            len(minimum) != len(FEATURE_NAMES) or \
            len(maximum) != len(FEATURE_NAMES):
        return 0.0, {}, "predictor_domain_invalid"
    distances = []
    for value, low, high in zip(features, minimum, maximum):
        if value < float(low) - 1e-9 or value > float(high) + 1e-9:
            return 0.0, {}, "feature_out_of_training_domain"
    for prototype in prototypes:
        vector = prototype.get("features")
        latency = prototype.get("latency_ns")
        counters = prototype.get("counters")
        if not isinstance(vector, list) or len(vector) != len(FEATURE_NAMES) or \
                not isinstance(latency, (int, float)) or latency <= 0 or \
                not isinstance(counters, dict):
            return 0.0, {}, "predictor_prototype_invalid"
        distance = 0.0
        for value, reference, low, high in zip(features, vector,
                                                minimum, maximum):
            span = float(high) - float(low)
            delta = value - float(reference)
            distance += (delta / span) ** 2 if span > 0 else delta ** 2
        distances.append((distance, float(latency), counters))
    distances.sort(key=lambda item: item[0])
    selected = distances[:min(neighbors, len(distances))]
    exact = [item for item in selected if item[0] <= 1e-18]
    if exact:
        selected = exact
        weights = [1.0] * len(selected)
    else:
        weights = [1.0 / item[0] for item in selected]
    denominator = sum(weights)
    latency = sum(weight * item[1]
                  for weight, item in zip(weights, selected)) / denominator
    metric_names = set(selected[0][2])
    if any(set(item[2]) != metric_names for item in selected):
        return 0.0, {}, "predictor_counter_set_invalid"
    counters = {
        metric: sum(weight * float(item[2][metric])
                    for weight, item in zip(weights, selected)) / denominator
        for metric in metric_names
    }
    return latency, counters, ""


def validate(candidate: dict, holdout: dict, raw_holdout: bytes,
             holdout_directory: pathlib.Path) -> tuple[dict, dict]:
    reasons: list[str] = []
    if candidate.get("schema_version") != 2:
        reasons.append("candidate_schema_not_two")
    validation = candidate.get("validation", {})
    if validation.get("status") != "pending":
        reasons.append("candidate_not_pending")
    calibration = candidate.get("calibration", {})
    fit = candidate.get("fit_report", {})
    thresholds = candidate.get("thresholds", {})
    if fit.get("frozen_thresholds") != thresholds:
        reasons.append("thresholds_changed_after_fit")
    cross_validation = fit.get("cross_validation", {})
    cross_validation_classes = cross_validation.get("classes")
    if cross_validation.get("method") != \
            "repetition-stratified-training-only" or \
            cross_validation.get("holdout_read") is not False or \
            cross_validation.get("passed") is not True or \
            not isinstance(cross_validation.get("evaluated_samples"), int) or \
            cross_validation.get("evaluated_samples", 0) <= 0 or \
            not isinstance(cross_validation_classes, list) or \
            {item.get("operation_class") for item in cross_validation_classes
             if isinstance(item, dict) and item.get("passed") is True} != \
            set(CLASSES):
        reasons.append("fitter_holdout_boundary_missing")
    routing_hash = calibration.get("routing", {}).get("program_sha256")
    if fit.get("selected", {}).get("routing_program_sha256") != routing_hash:
        reasons.append("routing_program_hash_mismatch")
    if fit.get("training_sha256") != calibration.get("raw_training_sha256"):
        reasons.append("training_hash_mismatch")
    if calibration.get("counter_error_contract") != COUNTER_ERROR_CONTRACT:
        reasons.append("counter_error_contract_invalid")
    if holdout.get("suite") != "holdout":
        reasons.append("not_holdout_suite")
    if holdout.get("environment_sha256") != fit.get("environment_sha256"):
        reasons.append("environment_mismatch")
    try:
        verify_members(holdout, holdout_directory)
    except ValidationFailure as error:
        reasons.append(str(error).replace(" ", "_"))
    observations = holdout.get("observations")
    if not isinstance(observations, list):
        observations = []
        reasons.append("holdout_observations_missing")
    fitted_ids = set(calibration.get("fitted_case_ids", []))
    holdout_ids = [item.get("case_id") for item in observations
                   if isinstance(item, dict)]
    if len(holdout_ids) != len(set(holdout_ids)):
        reasons.append("holdout_case_ids_not_unique")
    if fitted_ids & set(holdout_ids):
        reasons.append("training_holdout_case_overlap")
    metrics = calibration.get("metric_names", [])
    if not isinstance(metrics, list) or not metrics or any(
            not isinstance(metric, str) or not metric for metric in metrics):
        metrics = []
        reasons.append("metric_names_missing")
    counter_model = fit.get("counter_model_by_class")
    if not isinstance(counter_model, dict) or set(counter_model) != set(CLASSES):
        counter_model = {}
        reasons.append("counter_model_missing")
    predictor = fit.get("predictor")
    if not isinstance(predictor, dict):
        predictor = {}
        reasons.append("predictor_missing")
    predictor_hash = sha(canonical(predictor))
    if fit.get("selected", {}).get("predictor_program_sha256") != \
            predictor_hash:
        reasons.append("predictor_program_hash_mismatch")
    try:
        expected_domain = {
            "schema_version": 1,
            "match_policy": "exact_calibrated_vector",
            "program_sha256": predictor_hash,
            "feature_names": list(predictor["feature_names"]),
            "vectors_by_class": {
                operation: [list(features) for features in sorted({
                    tuple(prototype["features"])
                    for prototype in
                    predictor["prototypes_by_class"][operation]
                })]
                for operation in CLASSES
            },
        }
    except (KeyError, TypeError):
        expected_domain = None
    if calibration.get("workload_domain") != expected_domain:
        reasons.append("workload_domain_mismatch")
    gnic_service = calibration.get("gnic", {}).get("service_ns_by_class", [])
    gpc_service = calibration.get("gpc", {}).get("service_ns_by_class", [])
    if not isinstance(gnic_service, list) or not isinstance(gpc_service, list) or \
            len(gnic_service) != len(CLASSES) or \
            len(gpc_service) != len(CLASSES):
        reasons.append("latency_model_missing")
    threshold_map = {item.get("metric"): item.get("max_error_percent")
                     for item in calibration.get("counter_thresholds", [])
                     if isinstance(item, dict)}
    if set(metrics) != set(threshold_map):
        reasons.append("counter_threshold_set_mismatch")
    counter_scales = calibration.get("counter_error_scale_by_class")
    scales_valid = isinstance(counter_scales, dict) and \
        set(counter_scales) == set(CLASSES)
    if scales_valid:
        for operation in CLASSES:
            scales = counter_scales.get(operation)
            if not isinstance(scales, dict) or set(scales) != set(metrics):
                scales_valid = False
                break
            for metric, value in scales.items():
                if not isinstance(value, (int, float)) or \
                        isinstance(value, bool) or not math.isfinite(
                            float(value)) or value < 0:
                    scales_valid = False
                    break
                if (metric.endswith(".pct") or ".pct_of_peak_" in metric) and \
                        float(value) != 100.0:
                    scales_valid = False
                    break
                if metric in {"dram__bytes.sum", "lts__t_sectors.sum"} and \
                        value <= 0:
                    scales_valid = False
                    break
            if not scales_valid:
                break
    if not scales_valid:
        counter_scales = {}
        reasons.append("counter_error_scales_invalid")
    p50_limit = thresholds.get("p50_percent", -1)
    p95_limit = thresholds.get("p95_percent", -1)
    counter_limit = thresholds.get("counter_percent", -1)
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               for value in (p50_limit, p95_limit, counter_limit)) or \
            p50_limit > 5 or p95_limit > 10 or counter_limit > 10:
        reasons.append("threshold_limit_invalid")
    try:
        aggregated = aggregate_observations(
            [item for item in observations if isinstance(item, dict)], metrics)
    except ValidationFailure as error:
        aggregated = []
        reasons.append(str(error).replace(" ", "_"))
    class_results = []
    for operation in CLASSES:
        selected = [item for item in aggregated
                    if isinstance(item, dict) and
                    item.get("operation_class") == operation]
        class_reasons: list[str] = []
        latency_errors: list[float] = []
        counter_errors: list[float] = []
        if not selected:
            class_reasons.append("class_missing")
        for item in selected:
            if item.get("byte_exact") is not True:
                class_reasons.append("byte_mismatch")
            native = item.get("native_latency_ns")
            modeled, modeled_counters, prediction_error = predict(
                predictor, operation, item.get("features", []))
            if prediction_error:
                class_reasons.append(prediction_error)
            if not isinstance(native, (int, float)) or native <= 0 or \
                    not isinstance(modeled, (int, float)) or modeled <= 0:
                class_reasons.append("latency_missing")
            else:
                latency_errors.append(abs(modeled - native) * 100.0 / native)
            native_counters = item.get("native_counters", {})
            for metric in metrics:
                native_value = native_counters.get(metric)
                modeled_value = modeled_counters.get(metric)
                if not isinstance(native_value, (int, float)) or \
                        not isinstance(modeled_value, (int, float)):
                    class_reasons.append(f"counter_missing:{metric}")
                    continue
                error = counter_error_percent(
                    float(modeled_value), float(native_value), metric, item,
                    float(counter_scales.get(operation, {}).get(metric, 0.0)))
                counter_errors.append(error)
                if error > min(counter_limit, threshold_map.get(metric, -1)):
                    class_reasons.append(f"counter_threshold:{metric}")
        p50 = percentile(latency_errors, .50) if latency_errors else float("inf")
        p95 = percentile(latency_errors, .95) if latency_errors else float("inf")
        counter = max(counter_errors, default=float("inf"))
        if p50 > p50_limit:
            class_reasons.append("p50_threshold")
        if p95 > p95_limit:
            class_reasons.append("p95_threshold")
        passed = not class_reasons
        class_results.append({
            "operation_class": operation, "passed": passed,
            "p50_error_percent": round(p50, 6),
            "p95_error_percent": round(p95, 6),
            "counter_error_percent": round(counter, 6),
            "reasons": sorted(set(class_reasons)),
        })
    if any(not item["passed"] for item in class_results):
        reasons.append("per_class_validation_failed")
    report = {"schema_version": 1,
              "status": "failed" if reasons else "passed",
              "candidate_sha256": sha(canonical(candidate)),
              "holdout_sha256": sha(raw_holdout),
              "routing_program_sha256": routing_hash,
              "model_source": "frozen_training_candidate",
              "classes": class_results, "reasons": sorted(set(reasons))}
    if reasons:
        return {}, report
    profile = dict(candidate)
    profile.pop("fit_report", None)
    for item in class_results:
        item.pop("reasons", None)
    training_ids = sorted(fitted_ids)
    profile["calibration"]["raw_holdout_sha256"] = sha(raw_holdout)
    profile["calibration"]["residuals"] = [{
        "operation_class": item["operation_class"],
        "p50_error_percent": item["p50_error_percent"],
        "p95_error_percent": item["p95_error_percent"],
    } for item in class_results]
    profile["validation"] = {
        "status": "passed",
        "training": {"manifest_sha256": calibration["raw_training_sha256"],
                     "case_ids": training_ids},
        "holdout": {"manifest_sha256": sha(raw_holdout),
                    "case_ids": sorted(holdout_ids)},
        "classes": class_results,
    }
    return profile, report


def main(argv: list[str]) -> int:
    try:
        options = parse(argv)
    except ValueError:
        print("usage: validate_sm120_exact.py --candidate FILE --holdout FILE --output-profile FILE --report FILE",
              file=sys.stderr)
        return 64
    report_path: pathlib.Path | None = None
    try:
        _, candidate_raw = regular(options["--candidate"])
        holdout_path, holdout_raw = regular(options["--holdout"])
        output_path = new_output(options["--output-profile"])
        report_path = new_output(options["--report"])
        candidate = json.loads(candidate_raw)
        holdout = json.loads(holdout_raw)
        profile, report = validate(candidate, holdout, holdout_raw,
                                   holdout_path.parent)
        write(report_path, report)
        if not profile:
            print(json.dumps({"status": "failed", "report": str(report_path)},
                             sort_keys=True))
            return 2
        write(output_path, profile)
        print(json.dumps({"status": "passed", "profile": str(output_path),
                          "report": str(report_path)}, sort_keys=True))
        return 0
    except (ValidationFailure, json.JSONDecodeError, KeyError, TypeError,
            ValueError, OSError) as error:
        if report_path is not None and not report_path.exists():
            write(report_path, {"schema_version": 1, "status": "failed",
                                "classes": [], "reasons": [str(error)]})
        print(f"validate_sm120_exact: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

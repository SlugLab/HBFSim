#!/usr/bin/env python3
"""Independently validate frozen SM120 candidates against disjoint holdout."""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import sys

CLASSES = ["ordinary_load", "ordinary_store", "tma_load", "tma_store",
           "unicast", "multicast", "mixed_hbm_hbf"]


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
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())


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
        digest.update(relative.encode()); digest.update(b"\0")
        digest.update(str(member["sha256"]).encode()); digest.update(b"\0")
    if digest.hexdigest() != manifest.get("members_sha256"):
        raise ValidationFailure("holdout aggregate hash mismatch")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1,
                       max(0, math.ceil(len(ordered) * fraction) - 1))]


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
    if fit.get("cross_validation", {}).get("holdout_read") is not False:
        reasons.append("fitter_holdout_boundary_missing")
    routing_hash = calibration.get("routing", {}).get("program_sha256")
    if fit.get("selected", {}).get("routing_program_sha256") != routing_hash:
        reasons.append("routing_program_hash_mismatch")
    if fit.get("training_sha256") != calibration.get("raw_training_sha256"):
        reasons.append("training_hash_mismatch")
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
    counter_model = fit.get("counter_model_by_class")
    if not isinstance(counter_model, dict) or set(counter_model) != set(CLASSES):
        counter_model = {}
        reasons.append("counter_model_missing")
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
    p50_limit = thresholds.get("p50_percent", -1)
    p95_limit = thresholds.get("p95_percent", -1)
    counter_limit = thresholds.get("counter_percent", -1)
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               for value in (p50_limit, p95_limit, counter_limit)) or \
            p50_limit > 5 or p95_limit > 10 or counter_limit > 10:
        reasons.append("threshold_limit_invalid")
    class_results = []
    for operation_index, operation in enumerate(CLASSES):
        selected = [item for item in observations
                    if isinstance(item, dict) and
                    item.get("operation_class") == operation]
        class_reasons: list[str] = []
        latency_errors: list[float] = []
        counter_errors: list[float] = []
        if not selected:
            class_reasons.append("class_missing")
        for item in selected:
            if item.get("expected_sha256") != item.get("observed_sha256"):
                class_reasons.append("byte_mismatch")
            native = item.get("native_latency_ns")
            modeled = (max(float(gnic_service[operation_index]),
                           float(gpc_service[operation_index]))
                       if len(gnic_service) == len(CLASSES) and
                       len(gpc_service) == len(CLASSES) else None)
            if not isinstance(native, (int, float)) or native <= 0 or \
                    not isinstance(modeled, (int, float)) or modeled < 0:
                class_reasons.append("latency_missing")
            else:
                latency_errors.append(abs(modeled - native) * 100.0 / native)
            native_counters = item.get("native_counters", {})
            modeled_counters = counter_model.get(operation, {})
            for metric in metrics:
                native_value = native_counters.get(metric)
                modeled_value = modeled_counters.get(metric)
                if not isinstance(native_value, (int, float)) or \
                        not isinstance(modeled_value, (int, float)):
                    class_reasons.append(f"counter_missing:{metric}")
                    continue
                error = 0.0 if native_value == modeled_value == 0 else \
                    abs(modeled_value - native_value) * 100.0 / max(abs(native_value), 1e-12)
                counter_errors.append(error)
                if error > min(counter_limit, threshold_map.get(metric, -1)):
                    class_reasons.append(f"counter_threshold:{metric}")
        p50 = percentile(latency_errors, .50) if latency_errors else float("inf")
        p95 = percentile(latency_errors, .95) if latency_errors else float("inf")
        counter = max(counter_errors, default=float("inf"))
        if p50 > p50_limit: class_reasons.append("p50_threshold")
        if p95 > p95_limit: class_reasons.append("p95_threshold")
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

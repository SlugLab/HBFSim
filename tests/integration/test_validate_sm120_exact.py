#!/usr/bin/env python3
"""Independent holdout validator failure matrix."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts/calibration/validate_sm120_exact.py"
CLASSES = ["ordinary_load", "ordinary_store", "tma_load", "tma_store",
           "unicast", "multicast", "mixed_hbm_hbf"]
FEATURE_NAMES = [
    "log2_issued_operations", "log2_bytes", "log2_resident_warps",
    "log2_queue_depth", "dimension_count", "cache_warm",
    "log2_iterations", "log2_load_use_distance_plus_one",
    "log2_tile_elements", "cluster_size", "multicast_targets",
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(candidate: pathlib.Path, holdout: pathlib.Path, root: pathlib.Path,
        expected: int) -> tuple[pathlib.Path, pathlib.Path]:
    output = root / "passed-profile.json"
    report = root / "report.json"
    result = subprocess.run([
        sys.executable, str(VALIDATOR), "--candidate", str(candidate),
        "--holdout", str(holdout), "--output-profile", str(output),
        "--report", str(report)], text=True, capture_output=True,
        check=False, timeout=30)
    require(result.returncode == expected,
            f"expected {expected}, got {result.returncode}: {result.stderr}")
    require(report.is_file(), "validation report missing")
    require(output.exists() == (expected == 0), "passed profile publication mismatch")
    return output, report


def fixture(root: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    profile = json.loads((ROOT / "tests/fixtures/exact/sm120-stage4-valid.json").read_text())
    profile["validation"]["status"] = "pending"
    profile["validation"]["classes"] = []
    native_latencies = [1000 + index * 20 for index in range(len(CLASSES))]
    profile["calibration"]["gnic"]["service_ns_by_class"] = [
        value + 20 for value in native_latencies]
    profile["calibration"]["gpc"]["service_ns_by_class"] = [
        value for value in native_latencies]
    profile["calibration"]["metric_names"].append("dram__bytes.sum")
    profile["calibration"]["counter_thresholds"].append({
        "metric": "dram__bytes.sum", "max_error_percent": 10,
    })
    profile["calibration"]["counter_error_contract"] = {
        "version": 1,
        "percentage_metrics": "absolute_percentage_points",
        "traffic_metrics":
            "native_or_logical_issued_or_training_class_envelope",
        "duration_metrics": "relative_to_native",
        "fallback_metrics": "relative_to_native",
    }
    profile["calibration"]["counter_error_scale_by_class"] = {
        operation: {
            "lsu_active": 0.0,
            "tma_active": 0.0,
            "long_scoreboard": 0.0,
            "dram__bytes.sum": 1024.0,
        }
        for operation in CLASSES
    }
    feature = [7.0, 0.0, 0.0, 0.0, 0.0, 0.0,
               0.0, 0.0, 0.0, 1.0, 0.0]
    predictor = {
        "schema_version": 1,
        "feature_names": FEATURE_NAMES,
        "neighbors": 2,
        "aggregation": "median_by_base_case",
        "distance": "normalized_euclidean_inverse_square",
        "prototypes_by_class": {
            operation: [{
                "base_case_id": f"train-{operation}",
                "features": feature,
                "latency_ns": native_latencies[index] + 20,
                "counters": {"lsu_active": 104, "tma_active": 52,
                             "long_scoreboard": 26,
                             "dram__bytes.sum": 320},
            }]
            for index, operation in enumerate(CLASSES)
        },
        "domain_by_class": {
            operation: {"minimum": feature, "maximum": feature}
            for operation in CLASSES
        },
    }
    profile["fit_report"] = {
        "schema_version": 1,
        "training_sha256": profile["calibration"]["raw_training_sha256"],
        "environment_sha256": sha(b"environment-a"),
        "frozen_thresholds": profile["thresholds"],
        "selected": {"gnic_classes": 4, "gpc_classes": 2,
                     "routing_program_sha256":
                         profile["calibration"]["routing"]["program_sha256"],
                     "predictor_program_sha256": sha(json.dumps(
                         predictor, sort_keys=True,
                         separators=(",", ":")).encode())},
        "cross_validation": {
            "method": "repetition-stratified-training-only",
            "holdout_read": False,
            "passed": True,
            "evaluated_samples": 14,
            "classes": [
                {"operation_class": operation, "passed": True,
                 "p50_error_percent": 0.0, "p95_error_percent": 0.0,
                 "counter_error_percent": 0.0,
                 "counter_within_holdout_threshold": True,
                 "validation_samples": 2}
                for operation in CLASSES
            ],
        },
        "counter_model_by_class": {
            operation: {"lsu_active": 104, "tma_active": 52,
                        "long_scoreboard": 26, "dram__bytes.sum": 320}
            for operation in CLASSES
        },
        "predictor": predictor,
        "rejected_candidates": [{"reason": "class_count"}],
    }
    profile["calibration"]["workload_domain"] = {
        "schema_version": 1,
        "match_policy": "exact_calibrated_vector",
        "program_sha256": sha(json.dumps(
            predictor, sort_keys=True, separators=(",", ":")
        ).encode()),
        "feature_names": FEATURE_NAMES,
        "vectors_by_class": {
            operation: [feature] for operation in CLASSES
        },
    }
    candidate = root / "candidate.json"
    write(candidate, profile)
    observations = []
    for index, operation in enumerate(CLASSES):
        for sample in range(5):
            value = f"{operation}-{sample}".encode()
            digest = sha(value)
            observations.append({
                "case_id": f"holdout-{operation}-{sample}",
                "operation_class": operation,
                "expected_sha256": digest, "observed_sha256": digest,
                "native_latency_ns": 1000 + index * 20,
                "native_counters": {"lsu_active": 100, "tma_active": 50,
                                    "long_scoreboard": 25,
                                    "dram__bytes.sum": 256},
                "issued_operations": 128,
            })
    holdout_doc = {
        "schema_version": 1, "suite": "holdout",
        "environment_sha256": sha(b"environment-a"),
        "observations": observations, "members": [],
        "members_sha256": hashlib.sha256(b"").hexdigest(),
    }
    holdout = root / "holdout.json"
    write(holdout, holdout_doc)
    return candidate, holdout


def failure_mutation(base: pathlib.Path, mutate) -> None:
    case = base / mutate.__name__
    case.mkdir()
    candidate, holdout = fixture(case)
    candidate_doc = json.loads(candidate.read_text())
    holdout_doc = json.loads(holdout.read_text())
    mutate(candidate_doc, holdout_doc)
    write(candidate, candidate_doc)
    write(holdout, holdout_doc)
    output, report = run(candidate, holdout, case, 2)
    del output
    require(json.loads(report.read_text())["status"] == "failed",
            "failure report status missing")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hbfsim-validate-test-") as temporary:
        root = pathlib.Path(temporary)
        success = root / "success"
        success.mkdir()
        candidate, holdout = fixture(success)
        output, report = run(candidate, holdout, success, 0)
        profile = json.loads(output.read_text())
        require(profile["validation"]["status"] == "passed", "not passed")
        require("fit_report" not in profile, "fit-only material leaked")
        require(profile["calibration"]["workload_domain"] ==
                json.loads(candidate.read_text())["calibration"]
                ["workload_domain"], "workload domain was not persisted")
        schema = json.loads((ROOT / "configs/schema/"
                             "sm120-exact-profile.schema.json").read_text())
        jsonschema.validate(profile, schema)
        require(len(profile["validation"]["classes"]) == 7, "classes missing")
        require(json.loads(report.read_text())["status"] == "passed",
                "success report missing")

        def byte_mismatch(candidate, holdout):
            holdout["observations"][0]["observed_sha256"] = "f" * 64
        failure_mutation(root, byte_mismatch)

        def class_missing(candidate, holdout):
            holdout["observations"] = holdout["observations"][:-5]
        failure_mutation(root, class_missing)

        def class_p95(candidate, holdout):
            holdout["observations"][0]["native_latency_ns"] = 2000
        failure_mutation(root, class_p95)

        def counter_failure(candidate, holdout):
            holdout["observations"][0]["native_counters"]["lsu_active"] = 10
        failure_mutation(root, counter_failure)

        def threshold_changed(candidate, holdout):
            candidate["thresholds"]["p95_percent"] = 9
        failure_mutation(root, threshold_changed)

        def training_overlap(candidate, holdout):
            holdout["observations"][0]["case_id"] = candidate["calibration"]["fitted_case_ids"][0]
        failure_mutation(root, training_overlap)

        def environment_mismatch(candidate, holdout):
            holdout["environment_sha256"] = sha(b"environment-b")
        failure_mutation(root, environment_mismatch)

        def predictor_tampered(candidate, holdout):
            candidate["fit_report"]["predictor"]["neighbors"] = 1
        failure_mutation(root, predictor_tampered)

        def workload_domain_tampered(candidate, holdout):
            candidate["calibration"]["workload_domain"][
                "vectors_by_class"]["ordinary_load"][0][0] += 1
        failure_mutation(root, workload_domain_tampered)

        def counter_error_contract_tampered(candidate, holdout):
            candidate["calibration"]["counter_error_contract"]["version"] = 2
        failure_mutation(root, counter_error_contract_tampered)

        def counter_error_scale_tampered(candidate, holdout):
            candidate["calibration"]["counter_error_scale_by_class"][
                "ordinary_load"]["dram__bytes.sum"] = -1
        failure_mutation(root, counter_error_scale_tampered)

        def cross_validation_not_run(candidate, holdout):
            candidate["fit_report"]["cross_validation"] = {
                "method": "training-only", "holdout_read": False,
            }
        failure_mutation(root, cross_validation_not_run)

        scripts = list((ROOT / "scripts").rglob("*.py"))
        for script in scripts:
            if script == VALIDATOR:
                continue
            text = script.read_text()
            require('validation"]["status"] = "passed"' not in text and
                    "validation']['status'] = 'passed'" not in text,
                    f"non-validator can write passed: {script}")
    print(json.dumps({"status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

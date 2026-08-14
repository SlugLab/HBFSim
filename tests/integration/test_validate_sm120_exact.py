#!/usr/bin/env python3
"""Independent holdout validator failure matrix."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts/calibration/validate_sm120_exact.py"
CLASSES = ["ordinary_load", "ordinary_store", "tma_load", "tma_store",
           "unicast", "multicast", "mixed_hbm_hbf"]


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
    profile["fit_report"] = {
        "schema_version": 1,
        "training_sha256": profile["calibration"]["raw_training_sha256"],
        "environment_sha256": sha(b"environment-a"),
        "frozen_thresholds": profile["thresholds"],
        "selected": {"gnic_classes": 4, "gpc_classes": 2,
                     "routing_program_sha256":
                         profile["calibration"]["routing"]["program_sha256"]},
        "cross_validation": {"method": "training-only", "holdout_read": False},
        "counter_model_by_class": {
            operation: {"lsu_active": 104, "tma_active": 52,
                        "long_scoreboard": 26}
            for operation in CLASSES
        },
        "rejected_candidates": [{"reason": "class_count"}],
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
                                    "long_scoreboard": 25},
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
    write(candidate, candidate_doc); write(holdout, holdout_doc)
    output, report = run(candidate, holdout, case, 2)
    del output
    require(json.loads(report.read_text())["status"] == "failed",
            "failure report status missing")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hbfsim-validate-test-") as temporary:
        root = pathlib.Path(temporary)
        success = root / "success"; success.mkdir()
        candidate, holdout = fixture(success)
        output, report = run(candidate, holdout, success, 0)
        profile = json.loads(output.read_text())
        require(profile["validation"]["status"] == "passed", "not passed")
        require("fit_report" not in profile, "fit-only material leaked")
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

#!/usr/bin/env python3
"""Synthetic recovery and rejection tests for the training-only fitter."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
FITTER = ROOT / "scripts/calibration/fit_sm120_channels.py"
CLASSES = ["ordinary_load", "ordinary_store", "tma_load", "tma_store",
           "unicast", "multicast", "mixed_hbm_hbf"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(args: list[str], expected: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([sys.executable, str(FITTER), *args], text=True,
                            capture_output=True, check=False, timeout=30)
    require(result.returncode == expected,
            f"expected {expected}, got {result.returncode}: {result.stderr}")
    return result


def manifest() -> dict:
    observations = []
    for class_index, operation in enumerate(CLASSES):
        for sample in range(16):
            gnic = sample % 4
            gpc = (sample // 2) % 2
            observations.append({
                "case_id": f"train-{operation}-{sample}",
                "operation_class": operation,
                "smid": sample % 8, "warpid": sample,
                "cta_shape": [32 * (1 + sample % 4), 1, 1],
                "resident_warps": 1 + sample % 4,
                "cluster_ctarank": sample % 2,
                "latency_ns": 1000 + class_index * 50 + gnic * 100 + gpc * 20,
                "bytes": 256 * (1 + sample % 4), "queue_depth": 1 + sample % 8,
                "contention_vector": [1 if index == gnic else 0 for index in range(4)],
                "return_contention_vector": [1 if index == gpc else 0 for index in range(2)],
                "counters": {"lsu_active": 10 + class_index,
                             "tma_active": 5 + class_index,
                             "long_scoreboard": 2 + class_index},
            })
    return {"schema_version": 1, "suite": "training",
            "case_manifest_sha256": "a" * 64,
            "metrics": ["lsu_active", "tma_active", "long_scoreboard"],
            "observations": observations, "members": [],
            "members_sha256": hashlib.sha256(b"").hexdigest()}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hbfsim-fit-test-") as temporary:
        root = pathlib.Path(temporary)
        training = root / "training.json"
        stage1 = root / "stage1.json"
        output = root / "candidate.json"
        training.write_text(json.dumps(manifest(), sort_keys=True))
        stage1.write_text(json.dumps({"schema_version": 1,
                                      "profile_id": "synthetic-stage1",
                                      "validation": {"status": "pending"}},
                                     sort_keys=True))
        arguments = ["--training", str(training), "--stage1-fragment",
                     str(stage1), "--output", str(output)]
        run(arguments, 0)
        first = output.read_bytes()
        candidate = json.loads(first)
        calibration = candidate["calibration"]
        require(calibration["label_semantics"] == "contention_equivalent",
                "physical label semantics leaked")
        require(calibration["gnic"]["count"] == 4 and
                calibration["gpc"]["count"] == 2, "wrong queue counts")
        require(calibration["routing"]["inputs"] == [
            "smid", "warpid", "cta_shape", "resident_warps",
            "cluster_ctarank", "operation"], "wrong routing inputs")
        require(candidate["validation"]["status"] == "pending",
                "fitter wrote passed status")
        output.unlink()
        run(arguments, 0)
        require(output.read_bytes() == first, "fit is not deterministic")
        require(candidate["fit_report"]["selected"]["gnic_classes"] == 4,
                "four-way synthetic classes not recovered")
        require(candidate["fit_report"]["selected"]["gpc_classes"] == 2,
                "two-way synthetic classes not recovered")
        require(candidate["fit_report"]["rejected_candidates"],
                "rejected candidates absent")

        under = manifest()
        under["observations"] = under["observations"][:8]
        under_path = root / "under.json"
        under_path.write_text(json.dumps(under))
        run(["--training", str(under_path), "--stage1-fragment", str(stage1),
             "--output", str(root / "under-output.json")], 2)
        missing = manifest()
        missing["metrics"] = []
        missing_path = root / "missing.json"
        missing_path.write_text(json.dumps(missing))
        run(["--training", str(missing_path), "--stage1-fragment", str(stage1),
             "--output", str(root / "missing-output.json")], 2)
        run([*arguments, "--holdout", str(training)], 64)
    print(json.dumps({"status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

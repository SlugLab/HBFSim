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
    contract = {
        "cache_condition": "warm_l2",
        "concurrency_condition": "exclusive_process",
        "cluster_shape": [1, 1, 1],
        "thresholds": {"p50_percent": 5, "p95_percent": 10,
                       "counter_percent": 10},
        "limits": {"max_thread_futures": 16,
                   "max_warp_futures": 256,
                   "max_cta_futures": 2048,
                   "max_cluster_futures": 8192,
                   "max_thread_async_objects": 8,
                   "max_warp_async_objects": 128,
                   "max_cta_async_objects": 1024,
                   "max_cluster_async_objects": 4096},
    }
    calibration_environment = {
        "variables": {"CUDA_VISIBLE_DEVICES": "0"},
        "target": {"gpu_name": "synthetic-sm120", "gpu_uuid": "GPU-test",
                   "pci_vendor_id": 0x10DE, "pci_device_id": 0x2BB5,
                   "compute_capability_major": 12,
                   "compute_capability_minor": 0, "driver_version": 13020},
        "tool_versions": {"ncu": "2025.4.1.0", "nvcc": "release 13.0"},
        "exact_profile_contract": contract,
    }
    return {"schema_version": 1, "suite": "training",
            "case_manifest_sha256": "a" * 64,
            "metrics": ["lsu_active", "tma_active", "long_scoreboard"],
            "exact_profile_contract": contract,
            "calibration_environment": calibration_environment,
            "environment_sha256": hashlib.sha256(json.dumps(
                calibration_environment, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest(),
            "gpu_snapshots": [
                {"sm_clock_mhz": 1830, "memory_clock_mhz": 14001,
                 "power_limit_mw": 600000, "temperature_c": 44},
                {"sm_clock_mhz": 1830, "memory_clock_mhz": 14001,
                 "power_limit_mw": 600000, "temperature_c": 47},
            ],
            "observations": observations, "members": [],
            "members_sha256": hashlib.sha256(b"").hexdigest()}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hbfsim-fit-test-") as temporary:
        root = pathlib.Path(temporary)
        training = root / "training.json"
        stage1_root = root / "stage1"
        stage1_root.mkdir()
        stage1 = stage1_root / "profile-fragment.json"
        output = root / "candidate.json"
        training.write_text(json.dumps(manifest(), sort_keys=True))
        pass_manifest = stage1_root / "pass-manifest.jsonl"
        pass_manifest.write_text('{"schema_version":4}\n')
        prepatched = stage1_root / "prepatched-ptx"
        prepatched.mkdir()
        (prepatched / ("1" * 64 + ".ptx")).write_text("// synthetic\n")
        bundle_root = root / "bundles"
        bundle = bundle_root / ("1" * 64) / "sm_120"
        bundle.mkdir(parents=True)
        fixture = json.loads((ROOT /
            "tests/fixtures/exact/sm120-stage4-valid.json").read_text())
        module = fixture["modules"][0]
        module["original_ptx_sha256"] = "1" * 64
        stage1.write_text(json.dumps({
            "fragment_schema_version": 1,
            "toolchain": fixture["toolchain"], "modules": [module],
            "validation": {"status": "pending"},
            "provenance": {
                "pass_manifest_sha256": hashlib.sha256(
                    pass_manifest.read_bytes()).hexdigest(),
                "bundle": str(bundle),
            },
        }, sort_keys=True))
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
        require(set(candidate) == {
            "schema_version", "profile_id", "target", "toolchain",
            "conditions", "thresholds", "limits", "modules", "validation",
            "runtime_artifacts", "calibration", "fit_report",
        }, "candidate is not a complete exact-profile document")
        require(candidate["target"]["driver_version"] == 13020 and
                candidate["conditions"]["temperature_min_c"] == 44 and
                candidate["conditions"]["temperature_max_c"] == 47,
                "live target/condition evidence was not propagated")
        require(candidate["runtime_artifacts"] == {
            "bundle_root": str(bundle_root.resolve()),
            "prepatched_ptx_dir": str(prepatched.resolve()),
            "pass_manifest": str(pass_manifest.resolve()),
        }, "runtime artifact paths were not bound")
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

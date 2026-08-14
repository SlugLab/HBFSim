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
        for base in range(8):
            gnic = base % 4
            gpc = (base // 2) % 2
            for repetition in range(2):
                observations.append({
                    "case_id": f"train-{operation}-{base}.repeat-{repetition}",
                    "base_case_id": f"train-{operation}-{base}",
                    "operation_class": operation,
                    "smid": base % 8, "warpid": base,
                    "cta_shape": [32 * (1 + base % 4), 1, 1],
                    "resident_warps": 1 + base % 4,
                    "cluster_ctarank": base % 2,
                    "latency_ns": 1000 + class_index * 50 + gnic * 100 +
                    gpc * 20 + repetition,
                    "bytes": 256 * (1 + base % 4),
                    "queue_depth": 1 + base % 8,
                    "issued_operations": 128 * (1 + base % 4),
                    "iterations": 16 * (1 + base % 4),
                    "load_use_distance": base % 8,
                    "dimensions": [], "executed_dimension_count": 0,
                    "cache_condition_executed":
                        "warm" if base % 2 else "cold",
                    "executed_cluster_shape": [1, 1, 1],
                    "executed_multicast_mask": 0,
                    "contention_vector": [
                        1 if index == gnic else 0 for index in range(4)
                    ],
                    "return_contention_vector": [
                        1 if index == gpc else 0 for index in range(2)
                    ],
                    "counters": {"lsu_active": 10 + class_index,
                                 "tma_active": 5 + class_index,
                                 "long_scoreboard": 2 + class_index,
                                 "smsp__warp_issue_stalled_barrier_per_warp_active.pct":
                                     0.02 if repetition == 0 else 0.06,
                                 "dram__bytes.sum":
                                     256 if repetition == 0 else 320},
                })
    contract = {
        "cache_condition": "warm_l2",
        "concurrency_condition": "exclusive_process",
        "clock_control": "none",
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
            "metrics": [
                "lsu_active", "tma_active", "long_scoreboard",
                "smsp__warp_issue_stalled_barrier_per_warp_active.pct",
                "dram__bytes.sum",
            ],
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
                candidate["conditions"]["temperature_max_c"] == 47 and
                candidate["conditions"]["clock_control"] == "none" and
                candidate["conditions"]["sm_clock_min_mhz"] == 1830 and
                candidate["conditions"]["sm_clock_max_mhz"] == 1830 and
                candidate["conditions"]["memory_clock_min_mhz"] == 14001 and
                candidate["conditions"]["memory_clock_max_mhz"] == 14001,
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
        predictor = candidate["fit_report"]["predictor"]
        require(predictor["aggregation"] == "median_by_base_case" and
                predictor["neighbors"] == 2 and
                set(predictor["prototypes_by_class"]) == set(CLASSES),
                "training-only feature predictor is missing")
        require(len(candidate["fit_report"]["selected"]
                    ["predictor_program_sha256"]) == 64,
                "predictor program is not content-bound")
        domain = calibration["workload_domain"]
        require(domain["match_policy"] == "exact_calibrated_vector" and
                domain["program_sha256"] == candidate["fit_report"]
                ["selected"]["predictor_program_sha256"] and
                all(domain["vectors_by_class"][operation]
                    for operation in CLASSES),
                "runtime workload domain is missing or unbound")
        cross_validation = candidate["fit_report"]["cross_validation"]
        require(cross_validation["method"] ==
                "repetition-stratified-training-only" and
                cross_validation["holdout_read"] is False and
                cross_validation["passed"] is True and
                cross_validation["evaluated_samples"] == len(CLASSES) * 8 and
                len(cross_validation["classes"]) == len(CLASSES),
                "training-only cross-validation was not executed")
        require(all(item["passed"] for item in cross_validation["classes"]),
                "synthetic cross-validation unexpectedly failed")
        require(all(item["counter_within_holdout_threshold"]
                    for item in cross_validation["classes"]),
                "synthetic counter cross-validation unexpectedly failed")
        require(max(calibration["gnic"]["service_ns_by_class"]) < 20,
                "whole-kernel latency leaked into per-request service")
        require(calibration["counter_error_contract"] == {
            "version": 1,
            "percentage_metrics": "absolute_percentage_points",
            "traffic_metrics":
                "native_or_logical_issued_or_training_class_envelope",
            "duration_metrics": "relative_to_native",
            "fallback_metrics": "relative_to_native",
        }, "counter error normalization contract is missing")
        scales = calibration["counter_error_scale_by_class"]
        require(set(scales) == set(CLASSES) and all(
            set(scales[operation]) == set(calibration["metric_names"])
            for operation in CLASSES),
            "training-only counter error scales are incomplete")

        multi_root = root / "stage1-multi"
        multi_root.mkdir()
        multi_pass = multi_root / "pass-manifest.jsonl"
        multi_pass.write_text('{"manifest_schema_version":4}\n')
        multi_prepatched = multi_root / "prepatched-ptx"
        multi_prepatched.mkdir()
        (multi_prepatched / ("1" * 64 + ".ptx")).write_text("// first\n")
        (multi_prepatched / ("2" * 64 + ".ptx")).write_text("// second\n")
        second_module = dict(module)
        second_module["module_id"] = "ptx:sha256:" + "2" * 64
        second_module["original_ptx_sha256"] = "2" * 64
        second_module["transformed_ptx_sha256"] = "3" * 64
        second_module["cubin_sha256"] = "4" * 64
        second_module["sass_sha256"] = "5" * 64
        second_bundle = bundle_root / ("2" * 64) / "sm_120"
        second_bundle.mkdir(parents=True)
        multi_stage1 = multi_root / "profile-fragment.json"
        multi_stage1.write_text(json.dumps({
            "fragment_schema_version": 1,
            "toolchain": fixture["toolchain"],
            "modules": [module, second_module],
            "validation": {"status": "pending"},
            "provenance": {
                "pass_manifest_sha256": hashlib.sha256(
                    multi_pass.read_bytes()).hexdigest(),
                "bundle": str(bundle),
            },
        }, sort_keys=True))
        multi_output = root / "multi-candidate.json"
        run(["--training", str(training), "--stage1-fragment",
             str(multi_stage1), "--output", str(multi_output)], 0)
        multi_candidate = json.loads(multi_output.read_text())
        require(multi_candidate["profile_id"] != candidate["profile_id"],
                "profile identity ignored additional AOT modules")

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
        counter_drift = manifest()
        for observation in counter_drift["observations"]:
            if observation["case_id"].endswith(".repeat-1"):
                observation["counters"]["lsu_active"] *= 4
        counter_drift_path = root / "counter-drift.json"
        counter_drift_path.write_text(json.dumps(counter_drift))
        result = run([
            "--training", str(counter_drift_path),
            "--stage1-fragment", str(stage1),
            "--output", str(root / "counter-drift-output.json"),
        ], 2)
        require("training cross-validation failed" in result.stderr,
                "counter-only cross-validation drift was not rejected")
        run([*arguments, "--holdout", str(training)], 64)
    print(json.dumps({"status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

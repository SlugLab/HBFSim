#!/usr/bin/env python3

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile


CLASSES = (
    "ordinary_load", "ordinary_store", "tma_load", "tma_store",
    "unicast", "multicast", "mixed_hbm_hbf",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def make_fixture(root: pathlib.Path) -> dict[str, pathlib.Path]:
    original = b".version 9.0\n.target sm_120\n.address_size 64\n"
    original_hash = digest(original)
    identity = ", ".join(
        f"0x{original_hash[index:index + 2]}" for index in range(0, 64, 2)
    )
    transformed = original + (
        ".visible .const .align 8 .b8 __hbfsim_module_identity[32] = {"
        + identity + "};\n"
    ).encode()
    cubin = b"\x7fELF-HBFSIM-SM120"
    sass = b"// sm_120\n/*0000*/ EXIT;\n"
    module_id = "ptx:sha256:" + original_hash
    kernel = {
        "name": "kernel", "registers": 48,
        "spill_store_bytes": 16, "spill_load_bytes": 8,
        "static_shared_bytes": 1024,
        "max_dynamic_shared_bytes": 49152,
        "block_threads": 256, "occupancy_blocks_per_sm": 2,
    }
    toolchain = {
        "cuda_release": "13.0",
        "ptxas_version": "ptxas: release 13.0, V13.0.88",
        "nvdisasm_version": "nvdisasm: release 13.0, V13.0.85",
        "cuobjdump_version": "cuobjdump: release 13.0, V13.0.85",
    }
    hashes = {
        "original_ptx_sha256": digest(original),
        "transformed_ptx_sha256": digest(transformed),
        "cubin_sha256": digest(cubin),
        "sass_sha256": digest(sass),
    }
    bundle = root / "bundles" / original_hash / "sm_120"
    bundle.mkdir(parents=True)
    (bundle / "original.ptx").write_bytes(original)
    (bundle / "transformed.ptx").write_bytes(transformed)
    (bundle / "module.cubin").write_bytes(cubin)
    (bundle / "module.sass").write_bytes(sass)
    artifact = {
        "schema_version": 1, "module_id": module_id,
        "ptx_target": "sm_120", "toolchain": toolchain,
        "hashes": hashes, "kernels": [kernel],
    }
    write_json(bundle / "artifact.json", artifact)

    pass_manifest = root / "pass-manifest.jsonl"
    pass_manifest.write_text(json.dumps({
        "manifest_schema_version": 3,
        "module_id": module_id,
        "original_ptx_sha256": hashes["original_ptx_sha256"],
        "transformed_ptx_sha256": hashes["transformed_ptx_sha256"],
        "aot_required_for_exact": True,
        "kernel": "kernel", "ptx_target": "sm_120",
        "instrumented": True, "cubin_only": False,
        "async_transform_version": "sm120-future-v1",
        "ir_sha256": "6" * 64,
        "instruction_table": [{
            "instruction_id": 1, "source_line": 4, "bytes": 4,
            "opcode": "ld.global.u32", "memory_kind": "load",
        }],
        "maximum_live_futures": {
            "thread": 1, "warp": 32, "cta": 128, "cluster": 1024,
        },
        "ambiguities": [],
    }) + "\n")

    training = root / "training.json"
    holdout = root / "holdout.json"
    training.write_bytes(b'{"cases":["train-load","train-store"]}\n')
    holdout.write_bytes(b'{"cases":["holdout-load","holdout-tma"]}\n')
    profile = {
        "schema_version": 2, "profile_id": "sm120-e2e-test",
        "target": {
            "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
            "gpu_uuid": "GPU-11111111-2222-3333-4444-555555555555",
            "pci_vendor_id": 4318, "pci_device_id": 1234,
            "compute_capability_major": 12,
            "compute_capability_minor": 0, "driver_version": 13000,
        },
        "toolchain": {
            "cuda_version": toolchain["cuda_release"],
            "ptxas_version": toolchain["ptxas_version"],
            "nvdisasm_version": toolchain["nvdisasm_version"],
            "cuobjdump_version": toolchain["cuobjdump_version"],
            "ncu_version": "NVIDIA Nsight Compute CLI version 2025.4.1.0",
        },
        "conditions": {
            "sm_clock_mhz": 1830, "memory_clock_mhz": 14001,
            "clock_control": "none",
            "sm_clock_min_mhz": 1700, "sm_clock_max_mhz": 1900,
            "memory_clock_min_mhz": 13000,
            "memory_clock_max_mhz": 15000,
            "power_limit_mw": 600000, "temperature_min_c": 30,
            "temperature_max_c": 75, "cache_condition": "warm_l2",
            "concurrency_condition": "exclusive_process",
            "cluster_shape": {"x": 2, "y": 1, "z": 1},
        },
        "thresholds": {
            "p50_percent": 5.0, "p95_percent": 10.0,
            "counter_percent": 10.0,
        },
        "limits": {
            "max_thread_futures": 16, "max_warp_futures": 256,
            "max_cta_futures": 2048, "max_cluster_futures": 8192,
            "max_thread_async_objects": 8,
            "max_warp_async_objects": 128,
            "max_cta_async_objects": 1024,
            "max_cluster_async_objects": 4096,
        },
        "modules": [{
            "module_id": module_id, "ptx_target": "sm_120",
            **hashes, "kernels": [kernel],
        }],
        "validation": {
            "status": "passed",
            "training": {
                "manifest_sha256": digest(training.read_bytes()),
                "case_ids": ["train-load", "train-store"],
            },
            "holdout": {
                "manifest_sha256": digest(holdout.read_bytes()),
                "case_ids": ["holdout-load", "holdout-tma"],
            },
            "classes": [{
                "operation_class": name, "passed": True,
                "p50_error_percent": 2.0, "p95_error_percent": 4.0,
                "counter_error_percent": 5.0,
            } for name in CLASSES],
        },
        "calibration": {
            "label_semantics": "contention_equivalent",
            "gnic": {"count": 4, "depth": 8, "arbitration": "fifo",
                     "service_ns_by_class": [80, 90, 100, 110, 120, 130, 140]},
            "gpc": {"count": 2, "depth": 8,
                    "arbitration": "round_robin",
                    "service_ns_by_class": [60, 70, 80, 90, 100, 110, 120]},
            "routing": {
                "version": 1, "program_sha256": "8" * 64,
                "inputs": ["smid", "warpid", "cta_shape",
                           "resident_warps", "cluster_ctarank", "operation"],
                "smsp_proxy_lut": [0, 1, 2, 3],
                "gnic_lut": [0, 1, 2, 3], "gpc_lut": [0, 1],
            },
            "metric_names": ["lsu_active", "tma_active", "long_scoreboard"],
            "raw_training_sha256": digest(training.read_bytes()),
            "raw_holdout_sha256": digest(holdout.read_bytes()),
            "fitted_case_ids": ["train-load", "train-store"],
            "residuals": [{"operation_class": name,
                           "p50_error_percent": 2.0,
                           "p95_error_percent": 4.0} for name in CLASSES],
            "counter_thresholds": [
                {"metric": name, "max_error_percent": 10.0}
                for name in ("lsu_active", "tma_active", "long_scoreboard")],
            "counter_error_contract": {
                "version": 1,
                "percentage_metrics": "absolute_percentage_points",
                "traffic_metrics":
                    "native_or_logical_issued_or_training_class_envelope",
                "duration_metrics": "relative_to_native",
                "fallback_metrics": "relative_to_native",
            },
            "counter_error_scale_by_class": {
                operation: {
                    "lsu_active": 0.0,
                    "tma_active": 0.0,
                    "long_scoreboard": 0.0,
                }
                for operation in CLASSES
            },
            "workload_domain": {
                "schema_version": 1,
                "match_policy": "exact_calibrated_vector",
                "program_sha256": "9" * 64,
                "feature_names": [
                    "log2_issued_operations", "log2_bytes",
                    "log2_resident_warps", "log2_queue_depth",
                    "dimension_count", "cache_warm", "log2_iterations",
                    "log2_load_use_distance_plus_one",
                    "log2_tile_elements", "cluster_size",
                    "multicast_targets",
                ],
                "vectors_by_class": {
                    operation: [[7, 0, 0, 0, 0, 1, 0, 0, 0, 2, 0]]
                    for operation in CLASSES
                },
            },
        },
        "runtime_artifacts": {
            "bundle_root": str((root / "bundles").resolve()),
            "prepatched_ptx_dir": str(root.resolve()),
            "pass_manifest": str(pass_manifest.resolve()),
        },
    }
    profile_path = root / "profile.json"
    write_json(profile_path, profile)
    environment = {
        "gpu_name": profile["target"]["gpu_name"],
        "gpu_uuid": profile["target"]["gpu_uuid"],
        "pci_vendor_id": profile["target"]["pci_vendor_id"],
        "pci_device_id": profile["target"]["pci_device_id"],
        "compute_capability_major": 12, "compute_capability_minor": 0,
        "cuda_driver_version": profile["target"]["driver_version"],
        "pci_bus_id": "0000:01:00.0",
        "sm_clock_mhz": 1830, "memory_clock_mhz": 14001,
        "power_limit_mw": 600000, "temperature_c": 50,
        "current_process_is_exclusive": True,
        "captured_unix_ns": 1770000000000000000,
    }
    environment_path = root / "environment.json"
    write_json(environment_path, environment)
    contract = {
        "cache_condition": "warm_l2",
        "concurrency_condition": "exclusive_process",
        "cluster_shape": {"x": 2, "y": 1, "z": 1},
        "cache_condition_epoch": 9,
        "latest_relevant_mutation_epoch": 8,
        "operation_class": "ordinary_load",
        "issued_operations": 128,
        "bytes": 1,
        "resident_warps": 1,
        "queue_depth": 1,
        "dimension_count": 0,
        "iterations": 1,
        "load_use_distance": 0,
        "tile_elements": 1,
        "multicast_targets": 0,
    }
    contract_path = root / "run-contract.json"
    write_json(contract_path, contract)
    counter = root / "fake-cuda-launch-count"
    counter.write_text("0\n")
    return {
        "bundle": bundle, "manifest": pass_manifest,
        "profile": profile_path, "training": training,
        "holdout": holdout, "environment": environment_path,
        "contract": contract_path, "counter": counter,
    }


def run_checker(checker: pathlib.Path, gate: pathlib.Path,
                paths: dict[str, pathlib.Path],
                load_mode: str = "aot") -> tuple[subprocess.CompletedProcess[str], dict]:
    result = subprocess.run([
        sys.executable, str(checker),
        "--profile", str(paths["profile"]),
        "--bundle", str(paths["bundle"]),
        "--pass-manifest", str(paths["manifest"]),
        "--training-manifest", str(paths["training"]),
        "--holdout-manifest", str(paths["holdout"]),
        "--kernel", "kernel",
        "--run-contract", str(paths["contract"]),
        "--launch-gate", str(gate),
        "--environment-json", str(paths["environment"]),
        "--load-mode", load_mode,
    ], text=True, capture_output=True, check=False)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    require(len(lines) == 1,
            f"checker did not print exactly one JSON decision: {result.stdout!r}")
    return result, json.loads(lines[0])


def mutate_json(path: pathlib.Path, mutator) -> None:
    value = json.loads(path.read_text())
    mutator(value)
    write_json(path, value)


def main() -> int:
    repository = pathlib.Path(__file__).resolve().parents[2]
    checker = repository / "scripts/calibration/check_sm120_exact_admission.py"
    gate = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else (
        repository / "build-sm120-exact/libhbfsim_launch_gate.so"
    )
    require(checker.is_file(), f"admission command is missing: {checker}")
    require(gate.is_file(), f"launch gate is missing: {gate}")

    with tempfile.TemporaryDirectory(prefix="hbfsim-exact-admission-") as temp:
        baseline_root = pathlib.Path(temp) / "baseline"
        baseline_root.mkdir()
        baseline = make_fixture(baseline_root)
        result, decision = run_checker(checker, gate, baseline)
        require(result.returncode == 0,
                f"valid fixture rejected: {result.stderr}\n{decision}")
        require(decision["allowed"] is True and
                decision["requested_fidelity"] == "exact" and
                decision["admitted_fidelity"] == "calibrated_emulation" and
                decision["aot_verified"] is True and
                decision["aot_authorization_verified"] is True and
                decision["validation_passed"] is True and
                decision["post_run_validation_passed"] is False and
                decision["routing_program_sha256"] == "8" * 64 and
                decision["exact_rejection_reasons"] == [],
                f"valid fixture lacks exact proof: {decision}")
        require(decision["launch_attempted"] is False and
                decision["module_loaded"] is False,
                "dry-run claimed a module load or kernel launch")
        require(baseline["counter"].read_text() == "0\n",
                "admission dry-run launched CUDA")

        cases = [
            ("original PTX", lambda p: (p["bundle"] / "original.ptx").write_bytes(b"changed"),
             "original_ptx_sha256_mismatch", "aot"),
            ("transformed PTX", lambda p: (p["bundle"] / "transformed.ptx").write_bytes(b"changed"),
             "transformed_ptx_sha256_mismatch", "aot"),
            ("cubin", lambda p: (p["bundle"] / "module.cubin").write_bytes(b"changed"),
             "cubin_sha256_mismatch", "aot"),
            ("SASS", lambda p: (p["bundle"] / "module.sass").write_bytes(b"changed"),
             "sass_sha256_mismatch", "aot"),
            ("register count", lambda p: mutate_json(
                p["bundle"] / "artifact.json",
                lambda value: value["kernels"][0].__setitem__("registers", 49)),
             "register_count_mismatch", "aot"),
            ("tool version", lambda p: mutate_json(
                p["bundle"] / "artifact.json",
                lambda value: value["toolchain"].__setitem__(
                    "ptxas_version", "ptxas: release 13.0, V13.0.99")),
             "toolchain_mismatch", "aot"),
            ("GPU identity", lambda p: mutate_json(
                p["environment"],
                lambda value: value.__setitem__("gpu_uuid", "GPU-different")),
             "gpu_uuid_mismatch", "aot"),
            ("clock", lambda p: mutate_json(
                p["environment"],
                lambda value: value.__setitem__("sm_clock_mhz", 1901)),
             "sm_clock_mismatch", "aot"),
            ("temperature", lambda p: mutate_json(
                p["environment"],
                lambda value: value.__setitem__("temperature_c", 76)),
             "temperature_out_of_range", "aot"),
            ("cache epoch", lambda p: mutate_json(
                p["contract"],
                lambda value: value.__setitem__("cache_condition_epoch", 8)),
             "cache_condition_unproven", "aot"),
            ("workload domain", lambda p: mutate_json(
                p["contract"],
                lambda value: value.__setitem__("iterations", 2)),
             "workload_out_of_domain", "aot"),
            ("validation dataset", lambda p: p["holdout"].write_bytes(b"changed\n"),
             "holdout_manifest_sha256_mismatch", "aot"),
            ("pending validation", lambda p: mutate_json(
                p["profile"],
                lambda value: value["validation"].__setitem__("status", "pending")),
             "profile_not_validated", "aot"),
            ("JIT loading", lambda p: None,
             "aot_evidence_missing", "jit"),
        ]
        for index, (name, mutation, expected, load_mode) in enumerate(cases):
            case_root = pathlib.Path(temp) / f"case-{index}"
            shutil.copytree(baseline_root, case_root)
            paths = {
                key: case_root / value.relative_to(baseline_root)
                for key, value in baseline.items()
            }
            mutation(paths)
            result, decision = run_checker(checker, gate, paths, load_mode)
            require(result.returncode == 2,
                    f"{name} failure had wrong status: {result.returncode} "
                    f"{result.stderr}\n{decision}")
            require(decision["allowed"] is False and
                    decision["admitted_fidelity"] == "calibrated_emulation" and
                    expected in decision["exact_rejection_reasons"],
                    f"{name} did not fail closed with {expected}: {decision}")
            require(decision["launch_attempted"] is False and
                    paths["counter"].read_text() == "0\n",
                    f"{name} reached the fake CUDA launch")

        unsafe = pathlib.Path(temp) / "unsafe-profile.json"
        unsafe.symlink_to(baseline["profile"])
        command = [
            sys.executable, str(checker),
            "--profile", str(unsafe),
            "--bundle", str(baseline["bundle"]),
            "--pass-manifest", str(baseline["manifest"]),
            "--training-manifest", str(baseline["training"]),
            "--holdout-manifest", str(baseline["holdout"]),
            "--kernel", "kernel",
            "--run-contract", str(baseline["contract"]),
            "--launch-gate", str(gate),
            "--environment-json", str(baseline["environment"]),
        ]
        missing = subprocess.run(command, text=True, capture_output=True,
                                 check=False)
        require(missing.returncode == 66,
                "checker accepted an unsafe profile path")

        malformed_root = pathlib.Path(temp) / "malformed"
        shutil.copytree(baseline_root, malformed_root)
        malformed_paths = {
            key: malformed_root / value.relative_to(baseline_root)
            for key, value in baseline.items()
        }
        mutate_json(malformed_paths["profile"],
                    lambda value: value.__setitem__("unknown", True))
        malformed, malformed_decision = run_checker(
            checker, gate, malformed_paths)
        require(malformed.returncode == 64 and
                malformed_decision["reason"] == "admission_input_error",
                "malformed profile did not use exit status 64")

        runtime_command = [
            sys.executable, str(checker),
            "--profile", str(baseline["profile"]),
            "--bundle", str(baseline["bundle"]),
            "--pass-manifest", str(baseline["manifest"]),
            "--training-manifest", str(baseline["training"]),
            "--holdout-manifest", str(baseline["holdout"]),
            "--kernel", "kernel",
            "--run-contract", str(baseline["contract"]),
            "--launch-gate", str(baseline["profile"]),
        ]
        runtime = subprocess.run(runtime_command, text=True,
                                 capture_output=True, check=False)
        runtime_lines = [line for line in runtime.stdout.splitlines()
                         if line.strip()]
        require(runtime.returncode == 70 and len(runtime_lines) == 1 and
                json.loads(runtime_lines[0])["reason"] == "admission_input_error",
                "provider load failure did not use exit status 70")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

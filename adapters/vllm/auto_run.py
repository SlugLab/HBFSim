#!/usr/bin/env python3
"""Bounded discover, stage, probe-build, and fresh-launch workflow for vLLM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import auto_prepare_ptx


ENTRY_NAME = re.compile(r"^[.$A-Za-z_][.$A-Za-z0-9_]*$")
STRICT_DENIAL_CAPABILITY = "hbfsim_strict_denial_capabilities_v1"
STRICT_DENIAL_CAPABILITY_STOP_ON_FIRST = 1 << 0
STRICT_DENIAL_ENV = "HBFSIM_STRICT_STOP_ON_DENIAL_PATH"


class WorkflowError(RuntimeError):
    pass


@dataclass(frozen=True)
class Paths:
    root: pathlib.Path
    adapter: pathlib.Path
    runner: pathlib.Path
    wrapper: pathlib.Path
    stage_program: pathlib.Path
    baseline_report: pathlib.Path
    timing_report: pathlib.Path
    staging: pathlib.Path
    pass_manifest: pathlib.Path
    probe_source: pathlib.Path
    probe_object: pathlib.Path
    workflow_report: pathlib.Path


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _run(command: Sequence[str], *, env: dict[str, str], timeout: int,
         stdout_path: pathlib.Path, stderr_path: pathlib.Path) -> None:
    started = time.time()
    receipt_path = stdout_path.with_suffix(stdout_path.suffix + ".receipt.json")
    timed_out = False
    returncode: int | None = None
    caught: BaseException | None = None
    descendant_cleanup_required = False
    with stdout_path.open("w", encoding="utf-8") as stdout, \
            stderr_path.open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            list(command), env=env, text=True, stdout=stdout, stderr=stderr,
            start_new_session=True,
        )
        def terminate_process_group() -> bool:
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                return False
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return False
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    return True
                time.sleep(0.05)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return True
        try:
            returncode = process.wait(timeout=timeout)
        except BaseException as error:
            caught = error
            timed_out = isinstance(error, subprocess.TimeoutExpired)
            descendant_cleanup_required = terminate_process_group()
            try:
                returncode = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                returncode = process.poll()
        else:
            descendant_cleanup_required = terminate_process_group()
    _write_json(receipt_path, {
        "schema_version": 1, "command": list(command), "pid": process.pid,
        "timeout_seconds": timeout, "timed_out": timed_out,
        "returncode": returncode, "elapsed_seconds": time.time() - started,
        "interrupted_by": (type(caught).__name__ if caught is not None else None),
        "descendant_cleanup_required": descendant_cleanup_required,
        "stdout": str(stdout_path), "stderr": str(stderr_path),
    })
    if timed_out:
        raise WorkflowError(f"process exceeded {timeout}s: {command[0]}")
    if caught is not None:
        raise caught
    if returncode:
        raise WorkflowError(
            f"process exited {returncode} after "
            f"{time.time() - started:.3f}s: {command[0]}")


def _common_run_args(args: argparse.Namespace, report: pathlib.Path,
                     mode: str) -> list[str]:
    result = [
        "--mode", mode, "--model", args.model,
        "--report-dir", str(report),
        "--num-prompts", str(args.num_prompts),
        "--input-len", str(args.input_len),
        "--output-len", str(args.output_len),
        "--max-model-len", str(args.max_model_len),
        "--max-num-batched-tokens", str(args.max_num_batched_tokens),
        "--gpu-memory-utilization", str(args.gpu_memory_utilization),
        "--moe-backend", args.moe_backend,
        "--seed", str(args.seed),
        "--warmup-requests", str(args.warmup_requests),
        "--request-timeout-ns", str(args.request_timeout_ns),
    ]
    if args.tokenizer:
        result += ["--tokenizer", args.tokenizer]
    return result


def _timing_args(args: argparse.Namespace, report: pathlib.Path) -> list[str]:
    result = _common_run_args(args, report, "timing")
    result += [
        "--profile", args.profile,
        "--hbf-weight-selection", args.hbf_weight_selection,
        "--hbf-instrumentation-policy", args.hbf_instrumentation_policy,
        "--hbf-timing-model", args.hbf_timing_model,
    ]
    for pattern in args.hbf_include_pattern:
        result += ["--hbf-include-pattern", pattern]
    for pattern in args.hbf_exclude_pattern:
        result += ["--hbf-exclude-pattern", pattern]
    if args.hbf_accept_storage_closure:
        result.append("--hbf-accept-storage-closure")
    return result


def _published_entries(manifest: dict[str, Any]) -> list[str]:
    entries: set[str] = set()
    for variant in manifest.get("variants", []):
        if variant.get("status") not in {"READY", "PARTIAL_READY"}:
            continue
        if not variant.get("staged_path"):
            raise WorkflowError("published variant has no staged path")
        for result in variant.get("entry_results", []):
            if result.get("status") == "SUPPORTED_TRANSFORMED":
                name = result.get("kernel")
                if not isinstance(name, str) or not ENTRY_NAME.fullmatch(name):
                    raise WorkflowError(f"unsafe published PTX entry: {name!r}")
                entries.add(name)
    if not entries:
        raise WorkflowError("stage published no transformed PTX entries")
    return sorted(entries)


def _verify_completion(staging: pathlib.Path,
                       pass_manifest: pathlib.Path) -> dict[str, Any]:
    try:
        manifest = auto_prepare_ptx.verify_stage_completion(
            staging, pass_manifest)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise WorkflowError(f"PTX completion verification failed: {error}") from error
    variants = manifest.get("variants")
    if not isinstance(variants, list):
        raise WorkflowError("PTX stage manifest has no variant inventory")
    published_paths: set[pathlib.Path] = set()
    published_keys: set[tuple[str, str]] = set()
    for variant in variants:
        if variant.get("status") not in {"READY", "PARTIAL_READY"}:
            continue
        staged_path = pathlib.Path(variant.get("staged_path", "")).resolve()
        raw_sha = variant.get("raw_sha256")
        if (not isinstance(raw_sha, str) or len(raw_sha) != 64 or
                staged_path.name != f"{raw_sha}.ptx"):
            raise WorkflowError("published PTX path does not match raw identity")
        if variant.get("staged_sha256") != _sha256(staged_path):
            raise WorkflowError(f"published PTX digest mismatch: {staged_path}")
        published_paths.add(staged_path)
        module_id = f"ptx:sha256:{raw_sha}"
        for result in variant.get("entry_results", []):
            if result.get("status") == "SUPPORTED_TRANSFORMED":
                published_keys.add((module_id, result.get("kernel")))
    actual_ptx = {path.resolve() for path in staging.glob("*.ptx")}
    if actual_ptx != published_paths:
        raise WorkflowError("staging PTX file set differs from published variants")
    pass_keys: set[tuple[str, str]] = set()
    for number, line in enumerate(pass_manifest.read_text(
            encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        key = (row.get("module_id"), row.get("kernel"))
        if key in pass_keys:
            raise WorkflowError(f"duplicate pass manifest record on line {number}")
        if not row.get("instrumented") or int(
                row.get("rewritten_instructions", 0)) <= 0:
            raise WorkflowError(f"inactive pass manifest record on line {number}")
        pass_keys.add(key)
    if pass_keys != published_keys:
        raise WorkflowError("pass manifest entries differ from published PTX entries")
    return manifest


def _probe_source(entries: Sequence[str]) -> str:
    blocks = ["#define SEC(name) __attribute__((section(name), used))\n"]
    for index, entry in enumerate(entries):
        if not ENTRY_NAME.fullmatch(entry):
            raise WorkflowError(f"unsafe probe entry: {entry!r}")
        blocks.append(
            f'\nSEC("kprobe/{entry}")\n'
            f"int cuda__auto_{index}(void* context)\n{{\n"
            "    (void)context;\n    return 0;\n}\n"
        )
    blocks.append('\nchar LICENSE[] SEC("license") = "GPL";\n')
    return "".join(blocks)


def _build_probe(entries: Sequence[str], paths: Paths,
                 args: argparse.Namespace, env: dict[str, str]) -> None:
    paths.probe_source.write_text(_probe_source(entries), encoding="utf-8")
    _run(
        [args.clang, "-target", "bpf", "-O2", "-g", "-c",
         str(paths.probe_source), "-o", str(paths.probe_object)],
        env=env, timeout=args.build_timeout,
        stdout_path=paths.root / "probe-build.stdout",
        stderr_path=paths.root / "probe-build.stderr",
    )
    completed = subprocess.run(
        [args.objdump, "-h", str(paths.probe_object)],
        env=env, text=True, capture_output=True, timeout=args.build_timeout,
        check=False,
    )
    (paths.root / "probe-sections.txt").write_text(
        completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise WorkflowError("failed to inspect generated probe sections")
    missing = [entry for entry in entries
               if f"kprobe/{entry}" not in completed.stdout]
    if missing:
        raise WorkflowError(f"generated probe lacks sections: {missing}")


def _paths(report: pathlib.Path) -> Paths:
    adapter = pathlib.Path(__file__).resolve().parent
    root = adapter.parents[1]
    return Paths(
        root=report, adapter=adapter, runner=adapter / "run.py",
        wrapper=root / "scripts" / "run_with_bpftime.sh",
        stage_program=adapter / "auto_prepare_ptx.py",
        baseline_report=report / "baseline", timing_report=report / "timing",
        staging=report / "ptx-stage",
        pass_manifest=report / "ptx-stage" / "pass-manifests.jsonl",
        probe_source=report / "auto-probe.bpf.c",
        probe_object=report / "auto-probe.bpf.o",
        workflow_report=report / "workflow.json",
    )


def _check_disk(path: pathlib.Path, minimum_gib: float) -> None:
    free = shutil.disk_usage(path).free
    required = int(minimum_gib * (1 << 30))
    if free < required:
        raise WorkflowError(
            f"free disk below floor: {free} < {required} bytes at {path}")


def _native_environment(environment: dict[str, str]) -> dict[str, str]:
    native = dict(environment)
    original_preload = native.get("HBFSIM_TARGET_ORIGINAL_LD_PRELOAD")
    if original_preload is not None:
        if original_preload:
            native["LD_PRELOAD"] = original_preload
        else:
            native.pop("LD_PRELOAD", None)
    elif native.get("LD_PRELOAD"):
        keep = [item for item in native["LD_PRELOAD"].split(":") if not any(
            marker in item for marker in (
                "libbpftime-agent", "libbpftime-syscall-server",
                "libhbfsim_launch_gate"))]
        if keep:
            native["LD_PRELOAD"] = ":".join(keep)
        else:
            native.pop("LD_PRELOAD", None)
    for name in list(native):
        if (name.startswith("BPFTIME_") or
                name.startswith("HBFSIM_BPFTIME_") or
                name in {"HBFSIM_PRESTAGED_PASS_MANIFEST_PATH",
                         "HBFSIM_TARGET_ORIGINAL_LD_PRELOAD",
                         "HBFSIM_INSTRUMENTATION_POLICY",
                         STRICT_DENIAL_ENV,
                         "HBFSIM_VLLM_EXTENSION"}):
            native.pop(name, None)
    return native


def _query_capability(library: pathlib.Path, symbol: str,
                      environment: dict[str, str], timeout: int) -> int:
    program = (
        "import ctypes,sys; "
        "library=ctypes.CDLL(sys.argv[1], mode=ctypes.RTLD_LOCAL); "
        "function=getattr(library,sys.argv[2]); "
        "function.argtypes=[]; function.restype=ctypes.c_uint64; "
        "print(int(function()))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program, str(library), symbol],
        env=environment, text=True, capture_output=True, timeout=timeout,
        check=False,
    )
    if completed.returncode:
        raise WorkflowError(
            f"strict runtime capability query failed for {symbol}: "
            f"{completed.stderr.strip()}")
    try:
        return int(completed.stdout.strip(), 10)
    except ValueError as error:
        raise WorkflowError(
            f"strict runtime capability query returned invalid value for "
            f"{symbol}: {completed.stdout!r}") from error


def _strict_denial_summary(report: pathlib.Path,
                           denial_path: pathlib.Path,
                           process_receipt_path: pathlib.Path) -> dict[str, Any]:
    if not denial_path.is_file():
        raise WorkflowError("strict target failed without first-denial receipt")
    if not process_receipt_path.is_file():
        raise WorkflowError("strict target lacks process exit receipt")
    denial = json.loads(denial_path.read_text(encoding="utf-8"))
    process_receipt = json.loads(
        process_receipt_path.read_text(encoding="utf-8"))
    if not isinstance(denial, dict):
        raise WorkflowError("first-denial receipt is not a JSON object")
    if (denial.get("schema_version") != 1 or
            denial.get("event") != "strict_first_denial" or
            denial.get("exit_code") != 86 or
            process_receipt.get("returncode") != 86):
        raise WorkflowError("first-denial receipt does not match exit 86")
    required_strings = (
        "api", "domain", "reason", "kernel", "original_dso", "maps_path",
    )
    missing = [name for name in required_strings
               if not isinstance(denial.get(name), str) or
               not denial.get(name)]
    if missing:
        raise WorkflowError(
            f"first-denial receipt lacks required fields: {missing}")
    maps_path = pathlib.Path(denial["maps_path"])
    if not maps_path.is_absolute():
        maps_path = report / maps_path
    if not maps_path.is_file() or maps_path.stat().st_size <= 0:
        raise WorkflowError("first-denial maps receipt is missing or empty")
    fields = {
        name: denial.get(name) for name in (
            "api", "domain", "symbol_version", "reason", "module_id",
            "kernel", "original_function", "lookup_function",
            "launch_function", "original_dso", "lookup_dso",
        )
    }
    return {
        "status": "VALIDATED_FAILURE",
        "scientific_success": False,
        "exit_code": 86,
        "path": str(denial_path),
        "sha256": _sha256(denial_path),
        "maps_path": str(maps_path),
        "maps_sha256": _sha256(maps_path),
        "maps_bytes": maps_path.stat().st_size,
        "fields": fields,
    }


def _preflight_bundle(args: argparse.Namespace,
                      environment: dict[str, str]) -> dict[str, Any]:
    build = pathlib.Path(args.build_dir).resolve()
    bpftime = pathlib.Path(args.bpftime_build_dir).resolve()
    artifacts = {
        "ptx_pass": build / "libptxpass_hbf.so",
        "launch_gate": build / "libhbfsim_launch_gate.so",
        "attach_loader": build / "hbfsim_bpftime_attach_loader",
        "vllm_extension": build / "libhbfsim_vllm_extension.so",
        "daemon": build / "hbfsimd",
        "bpftime_agent": bpftime / "runtime/agent/libbpftime-agent.so",
        "bpftime_server": bpftime / "runtime/syscall-server/libbpftime-syscall-server.so",
        "bpftime_provenance": bpftime / "hbfsim-bpftime.provenance",
        "profile": pathlib.Path(args.profile).resolve(),
    }
    missing = [f"{name}={path}" for name, path in artifacts.items()
               if not path.is_file()]
    if missing:
        raise WorkflowError(f"runtime bundle is incomplete: {missing}")
    if not os.access(artifacts["attach_loader"], os.X_OK):
        raise WorkflowError("attach loader is not executable")
    capabilities: dict[str, bool] = {}
    capability_values: dict[str, int] = {}
    if args.hbf_instrumentation_policy == "strict":
        expected = {
            "launch_gate": "hbfsim_instrumentation_policy_capabilities_v1",
            "bpftime_agent": "bpftime_nv_strict_bridge_capabilities_v1",
            "strict_denial": STRICT_DENIAL_CAPABILITY,
        }
        for artifact, symbol in expected.items():
            artifact_name = "launch_gate" if artifact == "strict_denial" else artifact
            completed = subprocess.run(
                [args.nm, "-D", "--defined-only", str(artifacts[artifact_name])],
                env=environment, text=True, capture_output=True,
                timeout=args.resource_probe_timeout, check=False,
            )
            present = completed.returncode == 0 and symbol in completed.stdout
            capabilities[symbol] = present
            if not present:
                raise WorkflowError(
                    f"strict runtime capability export is missing: {symbol}")
        denial_capabilities = _query_capability(
            artifacts["launch_gate"], STRICT_DENIAL_CAPABILITY,
            environment, args.resource_probe_timeout)
        capability_values[STRICT_DENIAL_CAPABILITY] = denial_capabilities
        if not (denial_capabilities & STRICT_DENIAL_CAPABILITY_STOP_ON_FIRST):
            raise WorkflowError(
                "strict runtime lacks stop-on-first-denial capability bit 0")
    return {
        "artifacts": {name: {"path": str(path), "sha256": _sha256(path)}
                      for name, path in artifacts.items()},
        "strict_capability_exports": capabilities,
        "strict_capability_values": capability_values,
    }


def _resource_snapshot(args: argparse.Namespace,
                       environment: dict[str, str]) -> dict[str, Any]:
    meminfo: dict[str, int] = {}
    for line in pathlib.Path("/proc/meminfo").read_text(
            encoding="utf-8").splitlines():
        name, value = line.split(":", 1)
        meminfo[name] = int(value.strip().split()[0]) * 1024
    total_memory = meminfo["MemTotal"]
    available_memory = meminfo["MemAvailable"]
    required_memory = max(
        int(args.min_free_memory_gib * (1 << 30)),
        int(total_memory * 0.05),
    )
    if available_memory < required_memory:
        raise WorkflowError(
            f"available memory below floor: {available_memory} < {required_memory}")
    completed = subprocess.run(
        [args.nvidia_smi, "--query-gpu=index,uuid,memory.total,memory.free",
         "--format=csv,noheader,nounits"],
        env=environment, text=True, capture_output=True,
        timeout=args.resource_probe_timeout, check=False,
    )
    if completed.returncode:
        raise WorkflowError(
            f"GPU resource probe failed: {completed.stderr.strip()}")
    rows = [line.strip() for line in completed.stdout.splitlines()
            if line.strip()]
    visible = environment.get(
        "CUDA_VISIBLE_DEVICES", str(args.gpu_index)).split(",")[0].strip()
    parsed = [tuple(value.strip() for value in row.split(",")) for row in rows]
    matches = [row for row in parsed
               if row[0] == visible or row[1] == visible]
    if len(matches) != 1:
        raise WorkflowError(
            f"GPU resource probe cannot resolve selector {visible!r}")
    index_text, gpu_uuid, total_text, free_text = matches[0]
    total_mib, free_mib = int(total_text), int(free_text)
    used_mib = total_mib - free_mib
    reservation_mib = int(total_mib * args.gpu_memory_utilization)
    headroom_mib = max(1, int(total_mib * 0.05))
    if used_mib + reservation_mib + headroom_mib > total_mib:
        raise WorkflowError(
            "GPU memory admission failed: existing usage plus requested "
            "reservation and 5% headroom exceeds total")
    return {
        "memory_total_bytes": total_memory,
        "memory_available_bytes": available_memory,
        "memory_required_bytes": required_memory,
        "gpu_index": int(index_text), "gpu_uuid": gpu_uuid,
        "gpu_total_mib": total_mib,
        "gpu_free_mib": free_mib, "gpu_existing_used_mib": used_mib,
        "gpu_requested_reservation_mib": reservation_mib,
        "gpu_headroom_mib": headroom_mib,
    }


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise WorkflowError(f"required runtime report is missing: {path}")
    rows = [json.loads(line) for line in path.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise WorkflowError(f"runtime report has no records: {path}")
    return rows


def _load_success_result(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        raise WorkflowError(f"vLLM result is missing: {path}")
    result = json.loads(path.read_text(encoding="utf-8"))
    if (result.get("request_terminal_status") != "success" or
            result.get("scientific_status") != "COMPLETE"):
        raise WorkflowError(f"vLLM result is not complete success: {path}")
    if not result.get("output_token_ids_sha256") or "output_token_ids" not in result:
        raise WorkflowError(f"vLLM result lacks output identity: {path}")
    return result


def _validate_native_result(paths: Paths, args: argparse.Namespace) -> dict[str, Any]:
    result = _load_success_result(paths.timing_report / "result.json")
    return {"request_terminal_status": "success",
            "output_token_ids_sha256": result["output_token_ids_sha256"]}


def _validate_timing_results(paths: Paths,
                             args: argparse.Namespace) -> dict[str, Any]:
    baseline = _load_success_result(paths.baseline_report / "result.json")
    timing = _load_success_result(paths.timing_report / "result.json")
    scope = ("model", "num_prompts", "input_len", "output_len",
             "max_model_len", "max_num_batched_tokens", "seed",
             "warmup_requests", "prompt_token_ids")
    mismatched = [name for name in scope if baseline.get(name) != timing.get(name)]
    if mismatched:
        raise WorkflowError(f"baseline/timing scope mismatch: {mismatched}")
    if (baseline["output_token_ids_sha256"] != timing["output_token_ids_sha256"] or
            baseline["output_token_ids"] != timing["output_token_ids"]):
        raise WorkflowError("baseline/timing output token identity mismatch")
    bindings = _read_jsonl(paths.timing_report / "triton-bindings.jsonl")
    bound = sum(row.get("result") == "bound" for row in bindings)
    required_failed = sum(
        row.get("required") is True and row.get("result") != "bound"
        for row in bindings)
    reported_bound = timing.get("triton_exact_bindings")
    if bound <= 0 or reported_bound != bound or required_failed:
        raise WorkflowError(
            "exact Triton binding report is missing, inconsistent, or failed")
    bridge_summary: dict[str, Any] | None = None
    if args.hbf_instrumentation_policy == "strict":
        bridge = _read_jsonl(paths.root / "strict-bridge.jsonl")
        binding_by_original = {
            str(row.get("original_function")): row.get("kernel_name")
            for row in bindings if row.get("result") == "bound"
        }
        required_bridge = [row for row in bridge
                           if row.get("gate_decision") == 2]
        invalid_bridge = [
            row for row in bridge
            if row.get("schema_version") != 1 or
            row.get("selected_path") == "REJECTED" or
            row.get("cuda_result") != 0
        ]
        joined = 0
        for row in required_bridge:
            original = str(row.get("original_function"))
            valid = (
                row.get("schema_version") == 1 and
                row.get("exact_alias_found") is True and
                row.get("selected_path") == "PATCHED" and
                row.get("patched_function") is not None and
                row.get("selected_function") == row.get("patched_function") and
                row.get("selected_function") != row.get("original_function") and
                row.get("cuda_result") == 0
            )
            if not valid:
                if row not in invalid_bridge:
                    invalid_bridge.append(row)
                continue
            expected_kernel = binding_by_original.get(original)
            if expected_kernel is None:
                if row not in invalid_bridge:
                    invalid_bridge.append(row)
                continue
            if (row.get("kernel_name") is not None and
                    row.get("kernel_name") != expected_kernel):
                if row not in invalid_bridge:
                    invalid_bridge.append(row)
                continue
            joined += 1
        if not required_bridge or invalid_bridge or joined != len(required_bridge):
            raise WorkflowError(
                "strict bridge lacks complete PATCHED exact-alias execution receipts")
        bridge_summary = {
            "records": len(bridge),
            "instrumented_execution_records": len(required_bridge),
            "joined_to_exact_binding_records": joined,
            "evidence_scope": "aggregate_original_function_join",
        }
    coverage = _read_jsonl(paths.root / "coverage.jsonl")
    denied = sum(row.get("allowed") is not True for row in coverage)
    modeled = sum(row.get("modeled") is True for row in coverage)
    opaque = sum(
        row.get("opaque_unmodeled") is True or
        row.get("reason") == "opaque_unmodeled_timing"
        for row in coverage)
    requires = sum(
        row.get("requires_instrumented_execution") is True for row in coverage)
    no_direct_hit = sum(
        row.get("modeled") is False and
        row.get("requires_instrumented_execution") is True
        for row in coverage)
    unknown = sum(any(name not in row for name in (
        "allowed", "modeled", "requires_instrumented_execution",
        "opaque_unmodeled")) for row in coverage)
    if denied:
        raise WorkflowError(f"runtime coverage contains {denied} denied decisions")
    if args.hbf_instrumentation_policy == "strict" and (
            opaque or unknown or requires <= 0):
        raise WorkflowError(
            f"strict runtime coverage has requires={requires} "
            f"opaque={opaque} unknown={unknown}")
    coverage_status = (
        "PARTIAL_OBSERVED" if opaque else
        ("UNKNOWN_BOUNDARY" if unknown else
         ("STRICT_DECISION_REQUIRES_INSTRUMENTATION" if requires else
          "NO_INSTRUMENTED_EXECUTION_OBSERVED"))
    )
    return {
        "scope_match": True,
        "output_token_ids_sha256": timing["output_token_ids_sha256"],
        "exact_binding_records": len(bindings),
        "exact_bindings_bound": bound,
        "exact_bindings_required_failed": required_failed,
        "coverage_records": len(coverage), "modeled_records": modeled,
        "opaque_unmodeled_records": opaque, "unknown_boundary_records": unknown,
        "requires_instrumented_execution_records": requires,
        "required_no_direct_hit_records": no_direct_hit,
        "denied_records": denied,
        "coverage_status": coverage_status,
        "strict_bridge": bridge_summary,
        "dynamic_all_weights_claimed": False,
    }


def execute(args: argparse.Namespace, *,
            process_runner: Callable[..., None] = _run,
            probe_builder: Callable[..., None] = _build_probe,
            resource_checker: Callable[..., dict[str, Any]] = _resource_snapshot,
            timing_validator: Callable[..., dict[str, Any]] = _validate_timing_results,
            native_validator: Callable[..., dict[str, Any]] = _validate_native_result,
            bundle_checker: Callable[..., dict[str, Any]] = _preflight_bundle,
            ) -> dict[str, Any]:
    report = pathlib.Path(args.report_dir).resolve()
    if report.exists():
        raise WorkflowError(f"report directory already exists: {report}")
    report.mkdir(parents=True)
    paths = _paths(report)
    workflow: dict[str, Any] = {
        "schema_version": 1, "status": "RUNNING", "phase": "INITIALIZED",
        "selection": args.hbf_weight_selection,
        "instrumentation_policy": args.hbf_instrumentation_policy,
        "report_dir": str(report), "phases": [],
        "timeouts_seconds": {
            "baseline": args.baseline_timeout, "stage": args.stage_timeout,
            "module": args.module_timeout_seconds,
            "build": args.build_timeout, "target": args.target_timeout,
        },
    }
    _write_json(paths.workflow_report, workflow)
    env = _native_environment(os.environ.copy())
    cache = pathlib.Path(args.cache_root or (report / "cache")).resolve()
    try:
        if cache.exists() and any(cache.iterdir()):
            raise WorkflowError(f"cache root must be new or empty: {cache}")
        cache.mkdir(parents=True, exist_ok=True)
        env["HBFSIM_VLLM_CACHE"] = str(cache)
        _check_disk(report, args.min_free_disk_gib)
        if args.hbf_weight_selection == "off":
            workflow.setdefault("resource_snapshots", {})["native"] = \
                resource_checker(args, env)
            workflow["phase"] = "NATIVE"
            _write_json(paths.workflow_report, workflow)
            native_env = dict(env)
            command = [sys.executable, str(paths.runner)] + _common_run_args(
                args, paths.timing_report, "baseline")
            command += ["--hbf-weight-selection", "off"]
            process_runner(
                command, env=native_env, timeout=args.target_timeout,
                stdout_path=report / "native.stdout",
                stderr_path=report / "native.stderr",
            )
            workflow["runtime_validation"] = native_validator(paths, args)
            workflow["phases"].append({"name": "native", "status": "SUCCESS"})
            workflow.update(status="SUCCESS", phase="COMPLETE",
                            instrumentation_status="DISABLED_NATIVE")
            _write_json(paths.workflow_report, workflow)
            return workflow

        workflow["phase"] = "BUNDLE_PREFLIGHT"
        workflow["bundle_preflight"] = bundle_checker(args, env)
        workflow["phases"].append(
            {"name": "bundle_preflight", "status": "SUCCESS"})
        workflow["phase"] = "BASELINE_DISCOVERY"
        workflow.setdefault("resource_snapshots", {})["baseline"] = \
            resource_checker(args, env)
        _write_json(paths.workflow_report, workflow)
        baseline = [sys.executable, str(paths.runner)] + _common_run_args(
            args, paths.baseline_report, "baseline")
        process_runner(
            baseline, env=env,
            timeout=args.baseline_timeout,
            stdout_path=report / "baseline.stdout",
            stderr_path=report / "baseline.stderr",
        )
        workflow["phases"].append({"name": "baseline", "status": "SUCCESS"})

        _check_disk(report, args.min_free_disk_gib)
        workflow["phase"] = "PTX_STAGE"
        _write_json(paths.workflow_report, workflow)
        stage_command = [
            sys.executable, str(paths.stage_program),
            "--cache-root", str(cache / "triton"),
            "--staging-dir", str(paths.staging),
            "--pass-library", str(pathlib.Path(args.build_dir).resolve() /
                                  "libptxpass_hbf.so"),
            "--pass-manifest", str(paths.pass_manifest),
            "--policy", args.hbf_instrumentation_policy,
            "--module-timeout-seconds", str(args.module_timeout_seconds),
        ]
        for pattern in args.kernel_pattern:
            stage_command += ["--kernel-pattern", pattern]
        process_runner(
            stage_command, env=env, timeout=args.stage_timeout,
            stdout_path=report / "stage.stdout",
            stderr_path=report / "stage.stderr",
        )
        manifest = _verify_completion(paths.staging, paths.pass_manifest)
        expected = ("READY" if args.hbf_instrumentation_policy == "strict"
                    else "PARTIAL_READY")
        if (args.hbf_instrumentation_policy == "strict" and
                manifest.get("status") != expected):
            raise WorkflowError(
                f"strict PTX staging is {manifest.get('status')}; target not launched")
        if (args.hbf_instrumentation_policy == "partial" and
                manifest.get("status") not in {"READY", "PARTIAL_READY"}):
            raise WorkflowError(
                f"partial PTX staging is {manifest.get('status')}; target not launched")
        entries = _published_entries(manifest)
        uncovered_entries = [
            {"module_id": item.get("module_id"),
             "kernel": result.get("kernel"),
             "status": result.get("status"),
             "reason": result.get("reason")}
            for item in manifest.get("variants", [])
            for result in item.get("entry_results", [])
            if result.get("status") != "SUPPORTED_TRANSFORMED"
        ]
        workflow["ptx_stage"] = {
            "status": manifest.get("status"), "published_entries": entries,
            "manifest": str(paths.staging / "ptx-staging-manifest.json"),
            "uncovered_variants": sum(
                item.get("status") != "READY" for item in manifest.get("variants", [])),
            "uncovered_entries": uncovered_entries,
        }
        workflow["phases"].append({"name": "ptx_stage", "status": "SUCCESS"})

        workflow["phase"] = "PROBE_BUILD"
        _write_json(paths.workflow_report, workflow)
        probe_builder(entries, paths, args, env)
        workflow["phases"].append({"name": "probe_build", "status": "SUCCESS"})

        workflow["phase"] = "FRESH_TIMING_TARGET"
        workflow.setdefault("resource_snapshots", {})["timing"] = \
            resource_checker(args, env)
        _write_json(paths.workflow_report, workflow)
        timing_env = dict(env)
        timing_env.pop(STRICT_DENIAL_ENV, None)
        timing_env.update({
            "HBFSIM_BUILD_DIR": str(pathlib.Path(args.build_dir).resolve()),
            "HBFSIM_BPFTIME_BUILD_DIR": str(
                pathlib.Path(args.bpftime_build_dir).resolve()),
            "BPFTIME_CUDA_LATE_PTX_DIR": str(paths.staging),
            "BPFTIME_CUDA_LATE_PTX_PREPATCHED": "1",
            "HBFSIM_BPFTIME_PROBE": str(paths.probe_object),
            "HBFSIM_PRESTAGED_PASS_MANIFEST_PATH": str(paths.pass_manifest),
            "HBFSIM_COVERAGE_PATH": str(report / "coverage.jsonl"),
            "HBFSIM_PASS_MANIFEST_PATH": str(report / "pass-manifests.jsonl"),
            "HBFSIM_VLLM_EXTENSION": str(
                pathlib.Path(args.build_dir).resolve() /
                "libhbfsim_vllm_extension.so"),
            "HBFSIM_INSTRUMENTATION_POLICY": args.hbf_instrumentation_policy,
        })
        if args.hbf_instrumentation_policy == "strict":
            timing_env["HBFSIM_STRICT_BRIDGE_LOG_PATH"] = str(
                report / "strict-bridge.jsonl")
            first_denial_path = report / "first-denial.json"
            if first_denial_path.exists():
                raise WorkflowError(
                    "strict first-denial path exists before target launch")
            timing_env[STRICT_DENIAL_ENV] = str(first_denial_path)
            workflow["strict_first_denial"] = {
                "status": "ARMED", "path": str(first_denial_path),
                "path_was_absent_before_launch": True,
            }
            _write_json(paths.workflow_report, workflow)
        timing = [str(paths.wrapper), "--", sys.executable,
                  str(paths.runner)] + _timing_args(args, paths.timing_report)
        try:
            process_runner(
                timing, env=timing_env, timeout=args.target_timeout,
                stdout_path=report / "timing.stdout",
                stderr_path=report / "timing.stderr",
            )
        except BaseException:
            if args.hbf_instrumentation_policy == "strict":
                try:
                    workflow["strict_first_denial"] = _strict_denial_summary(
                        report, report / "first-denial.json",
                        report / "timing.stdout.receipt.json")
                except BaseException as denial_error:
                    workflow["strict_first_denial"] = {
                        "status": "INVALID_OR_ABSENT",
                        "scientific_success": False,
                        "path": str(report / "first-denial.json"),
                        "error": repr(denial_error),
                    }
                _write_json(paths.workflow_report, workflow)
            raise
        workflow["runtime_validation"] = timing_validator(paths, args)
        workflow["phases"].append(
            {"name": "fresh_timing_target", "status": "SUCCESS"})
        workflow.update(status="SUCCESS", phase="COMPLETE")
        _write_json(paths.workflow_report, workflow)
        return workflow
    except BaseException as error:
        workflow.update(status="FAILED", error=repr(error))
        workflow["failed_phase"] = workflow.get("phase")
        _write_json(paths.workflow_report, workflow)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--tokenizer")
    parser.add_argument("--profile")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--cache-root")
    parser.add_argument("--build-dir")
    parser.add_argument("--bpftime-build-dir")
    parser.add_argument("--hbf-weight-selection",
                        choices=("all", "include", "off"), default="all")
    parser.add_argument("--hbf-include-pattern", action="append", default=[])
    parser.add_argument("--hbf-exclude-pattern", action="append", default=[])
    parser.add_argument("--hbf-accept-storage-closure", action="store_true")
    parser.add_argument("--hbf-instrumentation-policy",
                        choices=("strict", "partial"), default="strict")
    parser.add_argument("--hbf-timing-model",
                        choices=("reference", "fast", "hybrid"), default="hybrid")
    parser.add_argument("--kernel-pattern", action="append", default=[])
    parser.add_argument("--num-prompts", type=int, default=4)
    parser.add_argument("--input-len", type=int, default=32)
    parser.add_argument("--output-len", type=int, default=32)
    parser.add_argument("--max-model-len", type=int, default=256)
    parser.add_argument("--max-num-batched-tokens", type=int, default=256)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument("--moe-backend", choices=("triton",), default="triton")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--warmup-requests", type=int, default=0)
    parser.add_argument("--request-timeout-ns", type=int, default=1_000_000_000)
    parser.add_argument("--baseline-timeout", type=int, default=900)
    parser.add_argument("--stage-timeout", type=int, default=600)
    parser.add_argument("--module-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--build-timeout", type=int, default=60)
    parser.add_argument("--target-timeout", type=int, default=14400)
    parser.add_argument("--min-free-disk-gib", type=float, default=10.0)
    parser.add_argument("--min-free-memory-gib", type=float, default=4.0)
    parser.add_argument("--resource-probe-timeout", type=int, default=10)
    parser.add_argument("--nvidia-smi", default="nvidia-smi")
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--nm", default="nm")
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--objdump", default="llvm-objdump")
    args = parser.parse_args(argv)
    if args.hbf_weight_selection == "include" and not args.hbf_include_pattern:
        parser.error("include selection requires --hbf-include-pattern")
    if args.hbf_weight_selection != "include" and args.hbf_include_pattern:
        parser.error("include patterns require selection=include")
    if args.hbf_weight_selection == "off" and (
            args.hbf_include_pattern or args.hbf_exclude_pattern or
            args.hbf_accept_storage_closure):
        parser.error("off selection cannot have binding filters")
    if args.hbf_weight_selection != "off" and not all(
            (args.profile, args.build_dir, args.bpftime_build_dir)):
        parser.error(
            "instrumented runs require --profile, --build-dir, and "
            "--bpftime-build-dir")
    if min(args.baseline_timeout, args.stage_timeout, args.build_timeout,
           args.target_timeout, args.module_timeout_seconds,
           args.resource_probe_timeout, args.request_timeout_ns) <= 0 or min(
               args.min_free_disk_gib, args.min_free_memory_gib) < 0:
        parser.error("timeouts must be positive and disk floor nonnegative")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = execute(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

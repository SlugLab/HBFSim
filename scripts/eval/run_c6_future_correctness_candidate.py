#!/usr/bin/env python3
"""Run one bounded C6 correctness process under the existing GPU guard.

This candidate captures four one-warp correctness cases. A successful capture
remains UNVALIDATED and does not close C6.3, overlap, G5, or native-completion
timing claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import signal
import stat
import sys

EVAL_DIR = pathlib.Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from resource_guard import ResourceBusy, ResourceGuard
from run_manifest import artifact_inventory, atomic_json, environment_snapshot, git_snapshot, now
from run_matrix import FailedRun, InterruptedRun, run_child

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHILD_TIMEOUT_SECONDS = 120


def regular_bytes(path: pathlib.Path) -> bytes:
    path = pathlib.Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("input must be a regular non-symlink file: " + str(path))
        return stream.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def interrupted_state(final: str, signals: dict) -> str:
    return "INTERRUPTED" if signals.get("signal") is not None else final


def finalize_attempt(out: pathlib.Path, manifest: dict, status: dict,
                     final: str, signals: dict, *,
                     inventory_fn=artifact_inventory,
                     atomic_fn=atomic_json) -> str:
    """Seal artifacts while a pending handled signal can still veto success."""
    final = interrupted_state(final, signals)
    try:
        files, rejected = inventory_fn(out)
        manifest["artifact_hashes"] = {
            name: sha256(regular_bytes(path)) for name, path in files.items()
        }
        manifest["rejected_artifacts"] = rejected
        if rejected:
            final = "INVALID_DIAGNOSTIC"
    except Exception as error:
        manifest["artifact_hashes"] = {}
        manifest["artifact_seal_error"] = str(error)
        status["error"] = "artifact sealing failed: " + str(error)
        final = "INVALID_DIAGNOSTIC"
    final = interrupted_state(final, signals)
    if signals.get("signal") is not None:
        status["signal"] = signals["signal"]
    try:
        atomic_fn(out / "manifest.json", manifest)
    except Exception as error:
        status["error"] = "manifest sealing failed: " + str(error)
        final = "INVALID_DIAGNOSTIC"
    final = interrupted_state(final, signals)
    if signals.get("signal") is not None:
        status["signal"] = signals["signal"]
    status.update(state=final, updated_at=now())
    atomic_fn(out / "status.json", status)
    # A signal handled during either saved-result write must replace a successful
    # state. Handlers remain installed until the caller finishes this function.
    after_write = interrupted_state(final, signals)
    if after_write != final:
        final = after_write
        status.update(state=final, updated_at=now(), signal=signals["signal"])
        atomic_fn(out / "status.json", status)
    return final


def execute(out: pathlib.Path, build: pathlib.Path, profile: pathlib.Path,
            ptx: pathlib.Path, gpu_uuid: str) -> dict:
    out = out.resolve()
    build = build.resolve()
    profile = profile.resolve()
    ptx = ptx.resolve()
    gold_root = (ROOT / "results/gold/timing-future-unit").resolve()
    if not out.is_relative_to(gold_root):
        raise ValueError("diagnostic output must be under timing-future-unit gold root")
    out.mkdir(parents=True, exist_ok=False)

    paths = {
        "binary": build / "benchmarks/cuda/c6_future_correctness",
        "ptx": ptx,
        "plugin": build / "libptxpass_hbf.so",
        "gate": build / "libhbfsim_launch_gate.so",
        "daemon": build / "hbfsimd",
        "profile": profile,
        "build_config": build / "CMakeCache.txt",
        "runner": pathlib.Path(__file__).resolve(),
        "benchmark_source": ROOT / "benchmarks/cuda/c6_future_correctness_candidate.cu",
        "future_abi": ROOT / "include/hbfsim/timing_future_abi.hpp",
        "device_helper": ROOT / "src/cuda_runtime/device/hbf_device.cu",
        "launch_gate": ROOT / "src/cuda_runtime/launch_gate.cpp",
    }
    frozen = {name: regular_bytes(path) for name, path in paths.items()}
    manifest = {
        "schema_version": 1,
        "created_at": now(),
        "state_contract": "UNVALIDATED",
        "resource_class": "GPU_EXCLUSIVE",
        "child_timeout_seconds": CHILD_TIMEOUT_SECONDS,
        "gpu_uuid": gpu_uuid,
        "git": git_snapshot(ROOT),
        "inputs": {
            name: {"path": str(paths[name]), "sha256": sha256(data)}
            for name, data in frozen.items()
        },
        "commands": {},
        "claims": {
            "c6_3_closed": False,
            "overlap_closed": False,
            "g5_closed": False,
            "native_completion_timing": False,
        },
    }
    status = {"state": "PLANNED_UNVALIDATED", "updated_at": now()}
    atomic_json(out / "manifest.json", manifest)
    atomic_json(out / "environment.json", environment_snapshot())
    atomic_json(out / "status.json", status)

    signals = {"signal": None}
    old_handlers = {}
    final = "INVALID_DIAGNOSTIC"

    def update(state: str, **fields) -> None:
        status.update(state=state, updated_at=now(), **fields)
        atomic_json(out / "status.json", status)

    def interrupted(number, _frame) -> None:
        signals["signal"] = number

    try:
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old_handlers[number] = signal.signal(number, interrupted)
        task = {"resource_class": "GPU_EXCLUSIVE", "gpu_uuid": gpu_uuid}
        with ResourceGuard(ROOT / "results", out, task) as guard:
            for name, path in paths.items():
                if sha256(regular_bytes(path)) != manifest["inputs"][name]["sha256"]:
                    raise ValueError("frozen input changed before launch: " + name)
            directory = out / "diagnostic"
            directory.mkdir()
            argv = [
                str(paths["binary"]),
                "--profile", str(paths["profile"]),
                "--plugin", str(paths["plugin"]),
                "--ptx", str(paths["ptx"]),
                "--output", str(directory / "raw.json"),
                "--report-dir", str(directory / "reports"),
            ]
            manifest["commands"]["diagnostic"] = argv
            atomic_json(out / "manifest.json", manifest)
            environment = {
                key: value for key, value in os.environ.items()
                if key not in ("LD_PRELOAD", "LD_AUDIT")
                and not key.startswith(("HBFSIM_", "BPFTIME_", "PTX_PASS_"))
            }
            environment.update(
                CUDA_VISIBLE_DEVICES=gpu_uuid,
                HBFSIM_DAEMON_PATH=str(paths["daemon"]),
                HBFSIM_PASS_MANIFEST_PATH=str(directory / "pass.jsonl"),
                LD_PRELOAD=str(paths["gate"]),
            )
            code = run_child(
                argv,
                environment,
                directory,
                "producer",
                guard,
                lambda child, phase: update(
                    "RUNNING_UNVALIDATED", child=child, phase=phase
                ),
                signals,
                CHILD_TIMEOUT_SECONDS,
                0.1,
            )
            if signals["signal"] is not None:
                raise InterruptedRun("signal received after diagnostic child")
            if code:
                raise FailedRun("C6 correctness diagnostic failed")
            raw = json.loads(regular_bytes(directory / "raw.json"))
            if raw.get("evidence") != "GPU_ACQUISITION":
                raise ValueError("diagnostic did not report physical acquisition")
            if raw.get("validation_status") != "UNVALIDATED":
                raise ValueError("diagnostic must retain UNVALIDATED status")
            if raw.get("scientific_claim") is not False or raw.get("g5_closed") is not False:
                raise ValueError("diagnostic emitted a prohibited scientific/gate claim")
            if len(raw.get("cases", [])) != 4:
                raise ValueError("diagnostic did not retain all four cases")
            guard.check("after-diagnostic")
            for name, path in paths.items():
                if sha256(regular_bytes(path)) != manifest["inputs"][name]["sha256"]:
                    raise ValueError("frozen input changed after launch: " + name)
            if signals["signal"] is not None:
                raise InterruptedRun("signal received during diagnostic validation")
            final = "CAPTURED_UNVALIDATED"
    except ResourceBusy as error:
        final = error.state
        status["error"] = str(error)
    except (InterruptedRun, KeyboardInterrupt) as error:
        final = "INTERRUPTED"
        status["error"] = str(error)
    except Exception as error:
        final = "INVALID_DIAGNOSTIC"
        status["error"] = str(error)
    finally:
        try:
            final = finalize_attempt(out, manifest, status, final, signals)
        finally:
            for number, handler in old_handlers.items():
                signal.signal(number, handler)
        # Catch a signal handled after the final post-write check but before
        # handler restoration. It still cannot leave a successful status.
        if signals["signal"] is not None and final != "INTERRUPTED":
            final = "INTERRUPTED"
            update(final, signal=signals["signal"])
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--build-dir", type=pathlib.Path)
    parser.add_argument("--profile", type=pathlib.Path)
    parser.add_argument("--ptx", type=pathlib.Path)
    parser.add_argument("--gpu-uuid")
    args = parser.parse_args()
    plan = {
        "resource_class": "GPU_EXCLUSIVE",
        "cases": ["same_dense", "distinct_dense", "same_sparse", "distinct_sparse"],
        "launch": {"grid": [1, 1, 1], "block": [32, 1, 1]},
        "child_timeout_seconds": CHILD_TIMEOUT_SECONDS,
        "validation_status": "UNVALIDATED",
    }
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    if not all((args.out, args.build_dir, args.profile, args.ptx, args.gpu_uuid)):
        parser.error("execution requires --out --build-dir --profile --ptx --gpu-uuid")
    result = execute(args.out, args.build_dir, args.profile, args.ptx, args.gpu_uuid)
    print(json.dumps(result, indent=2))
    return 0 if result["state"] == "CAPTURED_UNVALIDATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run one bounded same-process K1/W1/low per-chain ABBA diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import signal
import stat
import subprocess
import sys

EVAL_DIR = pathlib.Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

from resource_guard import ResourceBusy, ResourceGuard
from run_gpu_delay import validate_chain_diagnostic
from run_manifest import artifact_inventory, atomic_json, environment_snapshot, now
from run_matrix import FailedRun, InterruptedRun, run_child

ROOT = pathlib.Path(__file__).resolve().parents[2]
GPU_UUID = "GPU-f07ea2df-1b6f-9a02-b534-5090abf3c174"
SEQUENCE = ((0, "A_D0", 2), (500, "B_D500", 3),
            (500, "B_D500", 4), (0, "A_D0", 5))
PAIRS = ((0, 1), (3, 2))  # Each tuple is (zero launch, adjacent target launch).
SINGLE_BLOCK_GEOMETRY = {
    "diagnostic_blocks_requested": 1, "actual_grid_blocks": 1,
    "actual_chain_rows": 1, "actual_events_per_row": 8,
}
CHILD_TIMEOUT_SECONDS = 30


def regular_bytes(path: pathlib.Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("input must be a regular non-symlink file: " + str(path))
        return stream.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C"},
    )
    return result.stdout


def require_source_state(expected_head: str) -> None:
    if (len(expected_head) != 40 or
            any(char not in "0123456789abcdef" for char in expected_head)):
        raise ValueError("expected HEAD must be a lowercase commit hash")
    if git_output("rev-parse", "HEAD").strip() != expected_head:
        raise ValueError("HEAD differs from authorized ABBA source")
    if git_output("status", "--porcelain=v1", "--untracked-files=no"):
        raise ValueError("tracked worktree or index is dirty")


def _u64(value) -> bool:
    return type(value) is int and 0 <= value < 2**64


def interrupted_state(final: str, signals: dict) -> str:
    return "INTERRUPTED" if signals.get("signal") is not None else final


def finalize_attempt(out: pathlib.Path, manifest: dict, status: dict,
                     final: str, signals: dict, *,
                     inventory_fn=artifact_inventory,
                     atomic_fn=atomic_json) -> str:
    """Seal a bounded attempt while a handled signal can still veto success."""
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
    after_write = interrupted_state(final, signals)
    if after_write != final:
        final = after_write
        status.update(state=final, updated_at=now(), signal=signals["signal"])
        atomic_fn(out / "status.json", status)
    return final


def analyze_abba(raw: dict) -> dict:
    """Validate four physical launches and retain signed adjacent-pair deltas."""
    if (type(raw) is not dict or raw.get("schema_version") != 1 or
            raw.get("evidence") != "GPU_ACQUISITION" or
            raw.get("validation_status") != "UNVALIDATED" or
            raw.get("scientific_claim") is not False or
            raw.get("g2_gate_closed") is not False or
            raw.get("trace_mode") != "per_chain_abba" or
            raw.get("treatment") != "hbf_logical" or
            any(not _u64(raw.get(key)) or raw[key] != value
                for key, value in SINGLE_BLOCK_GEOMETRY.items()) or
            raw.get("warmup_delay_ns") != 500 or
            raw.get("sequence_delay_ns") != [0, 500, 500, 0] or
            raw.get("module_load_count") != 1 or
            raw.get("context_create_count") != 1):
        raise ValueError("ABBA acquisition header mismatch")
    if "pairs" in raw:
        raise ValueError("producer-supplied ABBA pairs are not accepted")
    identity = raw.get("shared_runtime_identity")
    identity_keys = {
        "module_handle", "context_handle", "input_address",
        "chain_output_address", "block_output_address", "storage_address",
        "config_symbol_address",
    }
    if (type(identity) is not dict or set(identity) != identity_keys or
            any(not _u64(value) or value == 0 for value in identity.values())):
        raise ValueError("invalid shared ABBA runtime identity")
    launches = raw.get("launches")
    if type(launches) is not list or len(launches) != 4:
        raise ValueError("ABBA acquisition must retain exactly four launches")
    by_launch = []
    reference_checksums = None
    for index, (launch, (delay, label, epoch)) in enumerate(zip(launches, SEQUENCE)):
        if (type(launch) is not dict or launch.get("launch_index") != index or
                launch.get("label") != label or
                launch.get("requested_delay_ns") != delay or
                launch.get("applied_delay_ns") != delay or
                launch.get("trace_mode") != "per_chain" or
                launch.get("treatment") != "hbf_logical" or
                launch.get("hops") != 1 or launch.get("warps") != 1 or
                launch.get("blocks") != 1 or
                launch.get("occupancy") != "low" or
                launch.get("validation") != "PASS" or
                launch.get("config_readback_before_launch_exact") is not True or
                launch.get("config_readback_after_launch_exact") is not True or
                launch.get("shared_runtime_identity") != identity):
            raise ValueError("ABBA launch order/config/identity mismatch")
        diagnostic = launch.get("chain_diagnostic")
        if (type(diagnostic) is not dict or
                diagnostic.get("delay_ns") != delay or
                diagnostic.get("launch_epoch") != epoch or
                diagnostic.get("grid_x") != 1 or
                diagnostic.get("row_count") != 1 or
                diagnostic.get("trace_capacity") != 8 or
                diagnostic.get("storage_address") != identity["storage_address"] or
                diagnostic.get("chain_output_address") !=
                    identity["chain_output_address"] or
                diagnostic.get("block_output_address") !=
                    identity["block_output_address"] or
                launch.get("input_base") != identity["input_address"]):
            raise ValueError("ABBA device config readback differs from shared identity")
        if not isinstance(launch.get("event_ns"), (int, float)) or isinstance(
                launch.get("event_ns"), bool) or not math.isfinite(launch["event_ns"]) or launch["event_ns"] <= 0:
            raise ValueError("invalid separate CUDA Event observation")
        validate_chain_diagnostic(launch, expected_epoch=epoch)
        chains = launch.get("chains")
        keyed = {(row["block"], row["warp"]): row for row in chains}
        if len(keyed) != len(chains):
            raise ValueError("duplicate ABBA chain identity")
        checksums = {key: row["checksum"] for key, row in keyed.items()}
        if reference_checksums is None:
            reference_checksums = checksums
        elif checksums != reference_checksums:
            raise ValueError("ABBA checksums changed between launches")
        waits = {wait["thread_id"]: wait for wait in launch["waits"]}
        if len(waits) != len(launch["waits"]):
            raise ValueError("duplicate ABBA wait identity")
        by_launch.append((keyed, waits))

    pairs = []
    for pair_index, (zero_index, target_index) in enumerate(PAIRS):
        zero_chains, zero_waits = by_launch[zero_index]
        target_chains, target_waits = by_launch[target_index]
        if set(zero_chains) != set(target_chains):
            raise ValueError("adjacent ABBA pair has unmatched chains")
        rows = []
        for identity_key in sorted(zero_chains):
            zero = zero_chains[identity_key]
            target = target_chains[identity_key]
            thread_id = (identity_key[0] * launches[zero_index]["warps"] +
                         identity_key[1]) * 32
            if thread_id not in zero_waits or thread_id not in target_waits:
                raise ValueError("adjacent ABBA pair has unmatched waits")
            zero_wait = zero_waits[thread_id]
            target_wait = target_waits[thread_id]
            chain_delta = ((target["end_ns"] - target["begin_ns"]) -
                           (zero["end_ns"] - zero["begin_ns"]))
            wait_delta = ((target_wait["wait_exit_ns"] -
                           target_wait["wait_enter_ns"]) -
                          (zero_wait["wait_exit_ns"] -
                           zero_wait["wait_enter_ns"]))
            rows.append({
                "block": identity_key[0], "warp": identity_key[1],
                "thread_id": thread_id,
                "signed_chain_delta_ns": chain_delta,
                "signed_wait_delta_ns": wait_delta,
            })
        pairs.append({
            "pair_index": pair_index,
            "zero_launch_index": zero_index,
            "target_launch_index": target_index,
            "signed_event_delta_ns": (launches[target_index]["event_ns"] -
                                      launches[zero_index]["event_ns"]),
            "per_chain": rows,
        })
    return {
        "schema_version": 1,
        "evidence": "GPU_ACQUISITION",
        "validation_status": "CAPTURED_UNVALIDATED",
        "scientific_claim": False,
        "g2_gate_closed": False,
        "scope": "same-process single-block K1/W1/low ABBA diagnostic; no G2 decision",
        "diagnostic_geometry": dict(SINGLE_BLOCK_GEOMETRY),
        "sequence_delay_ns": [0, 500, 500, 0],
        "pairs": pairs,
    }


def execute(out: pathlib.Path, build: pathlib.Path, profile: pathlib.Path,
            gpu_uuid: str, expected_head: str) -> dict:
    require_source_state(expected_head)
    if gpu_uuid != GPU_UUID:
        raise ValueError("ABBA diagnostic requires the reviewed GPU UUID")
    out = out.resolve()
    build = build.resolve()
    profile = profile.resolve()
    gold = (ROOT / "results/gold/known-delay").resolve()
    if not out.is_relative_to(gold):
        raise ValueError("ABBA output must remain under known-delay gold root")
    paths = {
        "binary": build / "benchmarks/cuda/hbf_dependent_delay",
        "plugin": build / "libptxpass_hbf.so",
        "ptx": build / "benchmarks/cuda/hbf_dependent_delay.ptx",
        "helper": build / "generated/hbf_device.ptx",
        "gate": build / "libhbfsim_launch_gate.so",
        "daemon": build / "hbfsimd",
        "build_config": build / "CMakeCache.txt",
        "profile": profile,
        "benchmark_source": ROOT / "benchmarks/cuda/hbf_dependent_delay.cu",
        "helper_source": ROOT / "src/cuda_runtime/device/hbf_device.cu",
        "helper_header": ROOT / "src/cuda_runtime/device/hbf_device.cuh",
        "runner": pathlib.Path(__file__).resolve(),
        "shared_validator": ROOT / "scripts/eval/run_gpu_delay.py",
    }
    frozen = {name: regular_bytes(path) for name, path in paths.items()}
    profile_value = json.loads(frozen["profile"])
    if (profile_value.get("time_scale") != 1 or
            any(type(profile_value.get(key)) is not int or
                profile_value[key] <= 0 for key in (
                    "read_latency_ns", "program_latency_ns",
                    "aggregate_bandwidth_bytes_per_s"))):
        raise ValueError("positive time-scale-1 profile required")
    out.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1, "created_at": now(),
        "state_contract": "CAPTURED_UNVALIDATED",
        "resource_class": "GPU_EXCLUSIVE", "gpu_uuid": gpu_uuid,
        "expected_head": expected_head,
        "condition": {"hops": 1, "warps": 1, "occupancy": "low",
                      "trace_mode": "per_chain_abba",
                      "diagnostic_blocks": 1,
                      "sequence_delay_ns": [0, 500, 500, 0]},
        "inputs": {name: {"path": str(paths[name]), "sha256": sha256(data),
                          "bytes": len(data)} for name, data in frozen.items()},
        "commands": {},
    }
    status = {"state": "PLANNED_UNVALIDATED", "updated_at": now()}
    atomic_json(out / "manifest.json", manifest)
    atomic_json(out / "environment.json", environment_snapshot())
    atomic_json(out / "status.json", status)
    signals = {"signal": None}
    old_handlers = {}

    def interrupted(number, _frame):
        signals["signal"] = number

    def update(state: str, **fields):
        status.update(state=state, updated_at=now(), **fields)
        atomic_json(out / "status.json", status)

    final = "INVALID_DIAGNOSTIC"
    try:
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old_handlers[number] = signal.signal(number, interrupted)
        task = {"resource_class": "GPU_EXCLUSIVE", "gpu_uuid": gpu_uuid}
        with ResourceGuard(ROOT / "results", out, task) as guard:
            require_source_state(expected_head)
            for name, path in paths.items():
                if sha256(regular_bytes(path)) != manifest["inputs"][name]["sha256"]:
                    raise ValueError("frozen ABBA input changed before launch: " + name)
            directory = out / "abba"
            directory.mkdir()
            argv = [
                str(paths["binary"]), "--treatment", "hbf_logical",
                "--delay-ns", "500", "--hops", "1", "--warps", "1",
                "--occupancy", "low", "--profile", str(paths["profile"]),
                "--plugin", str(paths["plugin"]), "--ptx", str(paths["ptx"]),
                "--output", str(directory / "raw.json"), "--report-dir",
                str(directory / "reports"), "--trace-mode", "per_chain_abba",
                "--diagnostic-blocks", "1",
            ]
            manifest["commands"]["abba"] = argv
            atomic_json(out / "manifest.json", manifest)
            environment = {
                key: value for key, value in os.environ.items()
                if key not in ("LD_PRELOAD", "LD_AUDIT") and
                not key.startswith(("HBFSIM_", "BPFTIME_", "PTX_PASS_"))
            }
            environment.update(
                CUDA_VISIBLE_DEVICES=gpu_uuid,
                HBFSIM_DAEMON_PATH=str(paths["daemon"]),
                HBFSIM_PASS_MANIFEST_PATH=str(directory / "pass.jsonl"),
                HBFSIM_COVERAGE_PATH=str(directory / "coverage.jsonl"),
                LD_PRELOAD=str(paths["gate"]),
            )
            code = run_child(
                argv, environment, directory, "producer", guard,
                lambda child, phase: update(
                    "RUNNING_UNVALIDATED", child=child, phase=phase),
                signals, CHILD_TIMEOUT_SECONDS, 0.1,
            )
            if signals["signal"] is not None:
                raise InterruptedRun("signal received after ABBA child")
            if code:
                raise FailedRun("same-process ABBA child failed")
            decisions = [
                json.loads(line) for line in
                regular_bytes(directory / "coverage.jsonl").splitlines()
                if line.strip()
            ]
            if (not decisions or
                    not all(decision.get("allowed") is True and
                            decision.get("modeled") is True
                            for decision in decisions)):
                raise ValueError("ABBA launch coverage gate failed")
            if not regular_bytes(directory / "pass.jsonl").strip():
                raise ValueError("ABBA transform pass manifest is empty")
            raw = json.loads(regular_bytes(directory / "raw.json"))
            analysis = analyze_abba(raw)
            atomic_json(out / "raw.analysis.json", analysis)
            guard.check("after-abba")
            require_source_state(expected_head)
            for name, path in paths.items():
                if sha256(regular_bytes(path)) != manifest["inputs"][name]["sha256"]:
                    raise ValueError("frozen ABBA input changed after launch: " + name)
            if signals["signal"] is not None:
                raise InterruptedRun("signal received during ABBA validation")
            final = "CAPTURED_UNVALIDATED"
    except ResourceBusy as error:
        final = error.state
        status["error"] = str(error)
    except (InterruptedRun, KeyboardInterrupt) as error:
        final = "INTERRUPTED"
        status["error"] = str(error)
    except Exception as error:
        status["error"] = str(error)
    finally:
        final = finalize_attempt(out, manifest, status, final, signals)
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--build-dir", type=pathlib.Path)
    parser.add_argument("--profile", type=pathlib.Path)
    parser.add_argument("--gpu-uuid", default=GPU_UUID)
    parser.add_argument("--expected-head")
    args = parser.parse_args()
    plan = {
        "resource_class": "GPU_EXCLUSIVE", "gpu_uuid": args.gpu_uuid,
        "condition": {"hops": 1, "warps": 1, "occupancy": "low",
                      "trace_mode": "per_chain_abba",
                      "diagnostic_blocks": 1},
        "warmup_delay_ns": 500, "sequence_delay_ns": [0, 500, 500, 0],
        "pairs": [{"zero": 0, "target": 1}, {"zero": 3, "target": 2}],
        "child_timeout_seconds": CHILD_TIMEOUT_SECONDS,
        "validation_status": "UNVALIDATED", "g2_gate_closed": False,
    }
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    if not all((args.out, args.build_dir, args.profile, args.expected_head)):
        parser.error("execution requires --out --build-dir --profile --expected-head")
    status = execute(args.out, args.build_dir, args.profile,
                     args.gpu_uuid, args.expected_head)
    print(json.dumps(status, indent=2))
    return 0 if status["state"] == "CAPTURED_UNVALIDATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

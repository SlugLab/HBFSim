#!/usr/bin/env python3
"""Acquire one bounded C6 future-delay timing slice.

Successful execution remains CAPTURED_UNVALIDATED. It cannot close C6.3,
C6.4, G5, overlap, native-completion timing, or SASS semantics.
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
LANES = 32
WARMUPS = 1
SAMPLES = 10
DELAY_NS = 20_000
WORK_COUNTS = (0, 4096)
ARMS = ("native", "future0", "futureD")
SELECTED_KERNEL = "c6_future_delay_future"
NATIVE_KERNEL = "c6_future_delay_native"
_SHA_KEYS = (
    "build_manifest",
    "disassembly_manifest",
    "nvdisasm",
    "cuobjdump",
)


def regular_bytes(path: pathlib.Path) -> bytes:
    path = pathlib.Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("input must be a regular non-symlink file: " + str(path))
        return stream.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exact_int(value, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(label + " must be an exact bounded integer")
    return value


def _hex(value, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(label + " must be a lowercase SHA256")
    return value


def load_binding(path: pathlib.Path) -> tuple[dict, dict[str, pathlib.Path]]:
    value = json.loads(regular_bytes(path))
    expected = {
        "schema_version",
        "selected_kernel",
        "native_kernel",
        "mapping_validation",
        "compiler",
        "original_ptx",
        "transformed_ptx",
        "cubin",
        *_SHA_KEYS,
    }
    if type(value) is not dict or set(value) != expected:
        raise ValueError("binding schema mismatch")
    if (
        _exact_int(value["schema_version"], "binding schema_version", 1) != 1
        or value["selected_kernel"] != SELECTED_KERNEL
        or value["native_kernel"] != NATIVE_KERNEL
        or value["mapping_validation"] != "NOT_PROVEN"
        or value["compiler"]
        != {"tool": "ptxas", "architecture": "sm_120", "optimization": "-O3"}
    ):
        raise ValueError("binding identity/compiler mismatch")
    paths: dict[str, pathlib.Path] = {}
    for name in ("original_ptx", "transformed_ptx", "cubin"):
        artifact = value[name]
        if type(artifact) is not dict or set(artifact) != {"path", "bytes", "sha256"}:
            raise ValueError(name + " binding schema mismatch")
        _exact_int(artifact["bytes"], name + " bytes", 1)
        _hex(artifact["sha256"], name + " sha256")
        if type(artifact["path"]) is not str or not artifact["path"]:
            raise ValueError(name + " path must be text")
        paths[name] = pathlib.Path(artifact["path"]).resolve()
        if not pathlib.Path(artifact["path"]).is_absolute():
            raise ValueError(name + " path must be absolute")
    for name in _SHA_KEYS:
        artifact = value[name]
        if type(artifact) is not dict or set(artifact) != {"path", "sha256"}:
            raise ValueError(name + " binding schema mismatch")
        _hex(artifact["sha256"], name + " sha256")
        if type(artifact["path"]) is not str or not artifact["path"]:
            raise ValueError(name + " path must be text")
        paths[name] = pathlib.Path(artifact["path"]).resolve()
        if not pathlib.Path(artifact["path"]).is_absolute():
            raise ValueError(name + " path must be absolute")
    return value, paths


def verify_binding_files(binding: dict, paths: dict[str, pathlib.Path]) -> dict[str, bytes]:
    frozen: dict[str, bytes] = {}
    for name, path in paths.items():
        data = regular_bytes(path)
        artifact = binding[name]
        if "bytes" in artifact and len(data) != artifact["bytes"]:
            raise ValueError(name + " byte length mismatch")
        if sha256(data) != artifact["sha256"]:
            raise ValueError(name + " SHA256 mismatch")
        frozen[name] = data
    return frozen


def derive_intervals(record: dict) -> dict:
    fields = (
        "helper_entry_ns",
        "arrival_ns",
        "helper_issue_exit_ns",
        "native_instruction_after_ns",
        "work_begin_ns",
        "work_end_ns",
        "wait_enter_ns",
        "wait_exit_ns",
        "consumer_after_ns",
    )
    values = {field: _exact_int(record.get(field), field) for field in fields}
    if list(values.values()) != sorted(values.values()):
        raise ValueError("future record timestamps are not ordered")
    return {
        "helper_pre_anchor_ns": values["arrival_ns"] - values["helper_entry_ns"],
        "issue_after_anchor_ns": values["helper_issue_exit_ns"] - values["arrival_ns"],
        "prework_from_anchor_ns": values["work_begin_ns"] - values["arrival_ns"],
        "work_ns": values["work_end_ns"] - values["work_begin_ns"],
        "prewait_gap_ns": values["wait_enter_ns"] - values["work_end_ns"],
        "wait_ns": values["wait_exit_ns"] - values["wait_enter_ns"],
        "consumer_gap_ns": values["consumer_after_ns"] - values["wait_exit_ns"],
    }


def classify_future_d(records: list[dict]) -> dict:
    if type(records) is not list or len(records) != LANES:
        raise ValueError("futureD launch must retain 32 records")
    intervals = [derive_intervals(record) for record in records]
    covered = [item["prework_from_anchor_ns"] >= DELAY_NS for item in intervals]
    state = (
        "NON_IDENTIFYING_ISSUE_OVERHEAD_COVERS_DELAY"
        if any(covered)
        else "IDENTIFYING_WINDOW_OBSERVED_UNVALIDATED"
    )
    return {
        "state": state,
        "delay_ns": DELAY_NS,
        "lanes_with_delay_expired_before_work": sum(covered),
        "intervals": intervals,
        "g5_scored": False,
        "overlap_claim": False,
    }


def validate_raw(raw: dict, expected_binding: dict) -> dict:
    if type(raw) is not dict:
        raise ValueError("raw diagnostic must be an object")
    for key, expected in {
        "schema_version": 1,
        "evidence": "GPU_ACQUISITION",
        "validation_status": "UNVALIDATED",
        "scientific_claim": False,
        "c6_3_closed": False,
        "c6_4_closed": False,
        "g5_closed": False,
        "overlap_closed": False,
        "native_completion_timing": False,
    }.items():
        observed = raw.get(key)
        if (type(expected) is bool and observed is not expected) or (
            type(expected) is int
            and (type(observed) is not int or observed != expected)
        ) or (type(expected) is str and observed != expected):
            raise ValueError("raw diagnostic claim mismatch: " + key)
    launches = raw.get("launches")
    if type(launches) is not list or len(launches) != len(ARMS) * len(WORK_COUNTS) * (
        WARMUPS + SAMPLES
    ):
        raise ValueError("raw diagnostic launch count mismatch")
    counts = {(arm, work): {"warmup": 0, "sample": 0} for arm in ARMS for work in WORK_COUNTS}
    future_d = []
    for launch in launches:
        if type(launch) is not dict or launch.get("validation") != "PASS":
            raise ValueError("raw launch is absent or invalid")
        arm = launch.get("arm")
        work = launch.get("work_count")
        key = (arm, work)
        if key not in counts:
            raise ValueError("unexpected arm/work cell")
        if type(launch.get("warmup")) is not bool:
            raise ValueError("warmup marker must be an exact boolean")
        kind = "warmup" if launch["warmup"] else "sample"
        counts[key][kind] += 1
        records = launch.get("records")
        if type(records) is not list or len(records) != LANES:
            raise ValueError("launch record count mismatch")
        if arm == "futureD" and not launch["warmup"]:
            future_d.append({"work_count": work, **classify_future_d(records)})
    if any(value != {"warmup": WARMUPS, "sample": SAMPLES} for value in counts.values()):
        raise ValueError("per-cell warmup/sample count mismatch")
    binding = raw.get("native_image_binding")
    expected_hashes = {
        "original_ptx_sha256": expected_binding["original_ptx"]["sha256"],
        "transformed_ptx_sha256": expected_binding["transformed_ptx"]["sha256"],
        "cubin_sha256": expected_binding["cubin"]["sha256"],
        "build_manifest_sha256": expected_binding["build_manifest"]["sha256"],
        "disassembly_manifest_sha256": expected_binding["disassembly_manifest"]["sha256"],
        "nvdisasm_sha256": expected_binding["nvdisasm"]["sha256"],
        "cuobjdump_sha256": expected_binding["cuobjdump"]["sha256"],
        "mapping_validation": "NOT_PROVEN",
        "same_retained_buffer": True,
    }
    if type(binding) is not dict or binding != expected_hashes:
        raise ValueError("raw native-image binding mismatch")
    return {
        "schema_version": 1,
        "status": "TIMING_INTERVALS_CAPTURED_UNVALIDATED",
        "future_d_samples": future_d,
        "g5_closed": False,
        "overlap_closed": False,
        "native_completion_timing": False,
        "warning": "Kwork is an operation count; W is measured from work timestamps",
    }


def interrupted_state(final: str, signals: dict) -> str:
    return "INTERRUPTED" if signals.get("signal") is not None else final


def finalize_attempt(out: pathlib.Path, manifest: dict, status: dict, final: str,
                     signals: dict, *, inventory_fn=artifact_inventory,
                     atomic_fn=atomic_json) -> str:
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
    after = interrupted_state(final, signals)
    if after != final:
        final = after
        status.update(state=final, updated_at=now(), signal=signals["signal"])
        atomic_fn(out / "status.json", status)
    return final


def execute(out: pathlib.Path, build: pathlib.Path, profile: pathlib.Path,
            binding_path: pathlib.Path, gpu_uuid: str) -> dict:
    out = out.resolve()
    build = build.resolve()
    profile = profile.resolve()
    binding_path = binding_path.resolve()
    gold_root = (ROOT / "results/gold/timing-future-unit").resolve()
    if not out.is_relative_to(gold_root):
        raise ValueError("output must be under the timing-future-unit gold root")
    out.mkdir(parents=True, exist_ok=False)
    binding, binding_paths = load_binding(binding_path)
    verified_binding = verify_binding_files(binding, binding_paths)
    paths = {
        "binary": build / "benchmarks/cuda/c6_future_delay_overlap",
        "plugin": build / "libptxpass_hbf.so",
        "gate": build / "libhbfsim_launch_gate.so",
        "daemon": build / "hbfsimd",
        "profile": profile,
        "binding": binding_path,
        "build_config": build / "CMakeCache.txt",
        "runner": pathlib.Path(__file__).resolve(),
        "benchmark_source": ROOT / "benchmarks/cuda/c6_future_delay_overlap.cu",
        "device_helper": ROOT / "src/cuda_runtime/device/hbf_device.cu",
        "future_abi": ROOT / "include/hbfsim/timing_future_abi.hpp",
    }
    paths.update({"binding_" + name: path for name, path in binding_paths.items()})
    frozen = {name: regular_bytes(path) for name, path in paths.items()}
    for name, data in verified_binding.items():
        if frozen["binding_" + name] != data:
            raise ValueError("binding artifact changed during initial freeze: " + name)
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
        "matrix": {
            "arms": list(ARMS),
            "work_counts": list(WORK_COUNTS),
            "delays_ns": [0, DELAY_NS],
            "warmups_per_cell": WARMUPS,
            "samples_per_cell": SAMPLES,
        },
        "claims": {
            "c6_3_closed": False,
            "c6_4_closed": False,
            "g5_closed": False,
            "overlap_closed": False,
            "native_completion_timing": False,
        },
        "commands": {},
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
            if git_snapshot(ROOT) != manifest["git"]:
                raise ValueError("git HEAD/clean snapshot changed before launch")
            for name, path in paths.items():
                if sha256(regular_bytes(path)) != manifest["inputs"][name]["sha256"]:
                    raise ValueError("frozen input changed before launch: " + name)
            directory = out / "diagnostic"
            directory.mkdir()
            argv = [
                str(paths["binary"]),
                "--profile", str(paths["profile"]),
                "--plugin", str(paths["plugin"]),
                "--ptx", str(paths["binding_original_ptx"]),
                "--transformed-ptx", str(paths["binding_transformed_ptx"]),
                "--cubin", str(paths["binding_cubin"]),
                "--binding", str(paths["binding"]),
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
                argv, environment, directory, "producer", guard,
                lambda child, phase: update(
                    "RUNNING_UNVALIDATED", child=child, phase=phase
                ),
                signals, CHILD_TIMEOUT_SECONDS, 0.1,
            )
            if signals["signal"] is not None:
                raise InterruptedRun("signal received after diagnostic child")
            if code:
                raise FailedRun("C6 future-delay diagnostic failed")
            raw = json.loads(regular_bytes(directory / "raw.json"))
            analysis = validate_raw(raw, binding)
            atomic_json(directory / "analysis.json", analysis)
            guard.check("after-diagnostic")
            if git_snapshot(ROOT) != manifest["git"]:
                raise ValueError("git HEAD/clean snapshot changed after launch")
            for name, path in paths.items():
                if sha256(regular_bytes(path)) != manifest["inputs"][name]["sha256"]:
                    raise ValueError("frozen input changed after launch: " + name)
            if signals["signal"] is not None:
                raise InterruptedRun("signal received during result sealing")
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
    parser.add_argument("--binding", type=pathlib.Path)
    parser.add_argument("--gpu-uuid")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({
            "resource_class": "GPU_EXCLUSIVE",
            "arms": ARMS,
            "work_counts": WORK_COUNTS,
            "warmups_per_cell": WARMUPS,
            "samples_per_cell": SAMPLES,
            "validation_status": "UNVALIDATED",
        }, indent=2))
        return 0
    if not all((args.out, args.build_dir, args.profile, args.binding, args.gpu_uuid)):
        parser.error("execution requires --out --build-dir --profile --binding --gpu-uuid")
    result = execute(
        args.out, args.build_dir, args.profile, args.binding, args.gpu_uuid
    )
    print(json.dumps(result, indent=2))
    return 0 if result["state"] == "CAPTURED_UNVALIDATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run one bounded C6 correctness process under the existing GPU guard.

This candidate captures executed overwrite, false overwrite and terminal exit cases. A successful capture
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
REVIEWED_CUBIN_BYTES = 90128
REVIEWED_CUBIN_SHA256 = "e4954d3fc1370a8cbf9302c823daa1f58028b09038162eb2c713c07ca3211f8b"
ORIGINAL_PTX_SHA256 = "eabc6abd294e65571f1ef5b52358a7279249348fdf7f9b1917df71ef729948d9"
REVIEWED_TRANSFORMED_PTX_BYTES = 169528
REVIEWED_TRANSFORMED_PTX_SHA256 = "65635db6979cd5d1d90a9aba49814437fa9b1e3834ff8d04153b52d458d4fa09"
REVIEWED_BUILD_MANIFEST_SHA256 = "aa06c52950c101df7f155e663603d5c0dfab9eba4a69e28d4df370c6f367416f"
REVIEWED_DISASSEMBLY_MANIFEST_SHA256 = "3cf03d5e00042857c5015c13e1e76541fe2670d8b2e5c0355aac3d3b71fc7cca"
REVIEWED_NVDISASM_SHA256 = "5a6bac301338087afbfbfeb9c1357ede2d81a1c352738deb85b83480a8ad5df4"
REVIEWED_CUOBJDUMP_SHA256 = "04637d85e56aa7e74d8ac6f17d5509c860f9962cca8802ca4a7a16a49caa76ae"
SELECTED_KERNEL = "c6_future_lifecycle_candidate"
LIFECYCLE_CASES = (("overwrite_executed", 0), ("overwrite_false_consume", 1),
                   ("unused_exit", 2))


def require_frozen_identities() -> None:
    hashes = (REVIEWED_CUBIN_SHA256, ORIGINAL_PTX_SHA256,
              REVIEWED_TRANSFORMED_PTX_SHA256, REVIEWED_BUILD_MANIFEST_SHA256,
              REVIEWED_DISASSEMBLY_MANIFEST_SHA256, REVIEWED_NVDISASM_SHA256,
              REVIEWED_CUOBJDUMP_SHA256)
    if (REVIEWED_CUBIN_BYTES <= 0 or REVIEWED_TRANSFORMED_PTX_BYTES <= 0 or
            any(len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
                for value in hashes)):
        raise ValueError("future-lifecycle native image identities are UNFROZEN")


def exit_sentinel(lane: int) -> int:
    return 0xc0dec000 ^ ((lane * 0x01020304) & 0xffffffff)


def expected_lane_output(lane: int, mode: int) -> int:
    if type(lane) is not int or not 0 <= lane < 32 or type(mode) is not int or mode not in (0, 1, 2):
        raise ValueError("invalid fixed lifecycle lane/mode")
    mask = 0xffffffff
    value = ((lane * 0x45d9f3b) & mask) ^ 0xa5a55a5a
    mixed = (((0x9e3779b9 ^ lane) * 0x0019660d) + 0x3c6ef35f) & mask
    rotated = ((mixed << 7) | (mixed >> 25)) & mask
    work = ((rotated ^ 0x85ebca6b) * 0x85ebca77) & mask
    if mode == 2:
        return exit_sentinel(lane)
    return ((0x9e3779b9 ^ lane) if mode == 0 else value) ^ work


def expected_sentinels(mode: int) -> list[int]:
    expected = [expected_lane_output(lane, mode) for lane in range(32)]
    return expected if mode == 2 else [value ^ 0xffffffff for value in expected]


def validate_lifecycle_observations(raw: dict) -> None:
    """Check three executed paths, independent outputs and terminal conservation."""
    def require(value: bool, message: str) -> None:
        if not value:
            raise ValueError(message)

    require(raw.get("kernel") == SELECTED_KERNEL and raw.get("seed") == 0x9e3779b9 and
            raw.get("launch") == {"grid": [1, 1, 1], "block": [32, 1, 1]} and
            raw.get("profile_page_bytes") == 4096, "lifecycle diagnostic configuration mismatch")
    instruction = raw.get("instruction_id")
    require(type(instruction) is int and 0 <= instruction < 0xffffffff,
            "invalid producer instruction identity")
    cases = raw.get("cases", [])
    require(len(cases) == 3, "diagnostic did not retain the three future-lifecycle cases")
    counts = dict.fromkeys(("issued", "model_ready", "consumed", "drained", "terminal_error",
                           "pending", "native_loads", "native_bytes", "rejected", "groups_issued",
                           "groups_completed", "trace_count", "trace_overflow", "next_reservation"), 0)
    counts["next_reservation"] = 1
    input_base = None
    for case, (name, mode) in zip(cases, LIFECYCLE_CASES):
        require(case.get("case") == name and case.get("mode") == mode and
                case.get("overwrite_executed") is (mode == 0) and
                case.get("output_store_executed") is (mode != 2) and
                case.get("terminal_kind") == ("CONSUMED" if mode == 1 else "DRAINED") and
                case.get("active_mask") == 0xffffffff and case.get("active_lanes") == 32 and
                case.get("stride_bytes") == 4 and case.get("expected_groups") == 1 and
                case.get("observed_unique_reservations") == 1 and case.get("validation") == "PASS",
                "lifecycle case/mask/producer group mismatch")
        expected = [expected_lane_output(lane, mode) for lane in range(32)]
        require(case.get("observed_outputs") == expected, "future-lifecycle output mismatch")
        require(case.get("sentinel_echo") == case.get("sentinel_outputs") ==
                expected_sentinels(mode), "lifecycle case sentinel mismatch")
        checksum = 0
        for value in expected:
            checksum = ((checksum * 131) ^ value) & 0xffffffffffffffff
        require(case.get("observed_checksum") == checksum, "lifecycle checksum mismatch")
        outputs = case.get("outputs", [])
        require(len(outputs) == 32 and all(
            item.get("lane") == lane and item.get("active") is True and
            item.get("overwrite_executed") is (mode == 0) and
            item.get("output_store_executed") is (mode != 2) and
            item.get("observed") == expected[lane] for lane, item in enumerate(outputs)),
            "lifecycle lane output records mismatch")
        before = dict(counts)
        for field, increment in (("issued", 32), ("model_ready", 32),
                                 ("consumed", 32 if mode == 1 else 0),
                                 ("drained", 0 if mode == 1 else 32),
                                 ("groups_issued", 1), ("groups_completed", 1),
                                 ("trace_count", 64), ("next_reservation", 1)):
            counts[field] += increment
        require(case.get("counters_before") == before and case.get("counters_after") == counts and
                case.get("counter_delta") == {key: counts[key] - before[key] for key in counts},
                "lifecycle producer/counter conservation failed")
        traces = case.get("raw_traces", [])
        require(traces == case.get("traces") and len(traces) == 64,
                "lifecycle trace conservation failed")
        window = case.get("trace_window", {})
        require(window.get("before_count") == before["trace_count"] and
                window.get("after_count") == counts["trace_count"] and
                window.get("copied_records") == 64 and window.get("truncated_or_anomalous") is False,
                "lifecycle trace window invalid")
        require(all(type(item.get("lane")) is int and 0 <= item["lane"] < 32 for item in traces),
                "trace attributed to an invalid producer lane")
        group_times = set()
        for lane in range(32):
            pair = [item for item in traces if item["lane"] == lane]
            require(len(pair) == 2 and [item.get("event") for item in pair] == [0, 5 if mode == 1 else 9] and
                    [item.get("status") for item in pair] == [0, 1],
                    "producer did not issue and terminate exactly once")
            issue, consumed = pair
            if input_base is None:
                input_base = issue.get("address")
                require(type(input_base) is int and input_base > 0 and input_base % 4096 == 0,
                        "input trace base is not a valid aligned address")
            for item in pair:
                require(item.get("address") == input_base + 4 * lane and item.get("bytes") == 4 and
                        item.get("group_mask") == 0xffffffff and item.get("instruction_id") == instruction and
                        item.get("reservation_id") == before["next_reservation"],
                        "lifecycle trace identity/group mismatch")
            require(issue.get("issue_ns") == consumed.get("issue_ns") and
                    issue.get("ready_ns") == consumed.get("ready_ns") and
                    issue["finish_ns"] >= issue["issue_ns"] and
                    consumed["finish_ns"] >= max(issue["finish_ns"], consumed["ready_ns"]),
                    "lifecycle trace timestamps are inconsistent")
            group_times.add((issue["issue_ns"], issue["ready_ns"]))
        require(len(group_times) == 1, "same-page lanes disagree on modeled readiness")
    require(raw.get("final_counters") == counts, "lifecycle final counters mismatch")


def regular_bytes(path: pathlib.Path) -> bytes:
    path = pathlib.Path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("input must be a regular non-symlink file: " + str(path))
        return stream.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_exact_bytes(data: bytes, *, expected_bytes: int,
                         expected_sha256: str, label: str) -> bytes:
    if len(data) != expected_bytes:
        raise ValueError(f"{label} length mismatch")
    if sha256(data) != expected_sha256:
        raise ValueError(f"{label} SHA256 mismatch")
    return data


def exact_regular_bytes(path: pathlib.Path, *, expected_bytes: int,
                        expected_sha256: str, label: str) -> bytes:
    return validate_exact_bytes(
        regular_bytes(path), expected_bytes=expected_bytes,
        expected_sha256=expected_sha256, label=label,
    )


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
            ptx: pathlib.Path, cubin: pathlib.Path, gpu_uuid: str) -> dict:
    require_frozen_identities()
    out = out.resolve()
    build = build.resolve()
    profile = profile.resolve()
    ptx = ptx.resolve()
    cubin = cubin.resolve()
    gold_root = (ROOT / "results/gold/timing-future-unit").resolve()
    if not out.is_relative_to(gold_root):
        raise ValueError("diagnostic output must be under timing-future-unit gold root")
    out.mkdir(parents=True, exist_ok=False)

    paths = {
        "binary": build / "benchmarks/cuda/c6_future_lifecycle_correctness",
        "ptx": ptx,
        "cubin": cubin,
        "plugin": build / "libptxpass_hbf.so",
        "gate": build / "libhbfsim_launch_gate.so",
        "daemon": build / "hbfsimd",
        "profile": profile,
        "build_config": build / "CMakeCache.txt",
        "runner": pathlib.Path(__file__).resolve(),
        "benchmark_source": ROOT / "benchmarks/cuda/c6_future_lifecycle_correctness.cu",
        "future_abi": ROOT / "include/hbfsim/timing_future_abi.hpp",
        "device_helper": ROOT / "src/cuda_runtime/device/hbf_device.cu",
        "launch_gate": ROOT / "src/cuda_runtime/launch_gate.cpp",
    }
    frozen = {
        name: regular_bytes(path) for name, path in paths.items()
        if name != "cubin"
    }
    frozen["cubin"] = exact_regular_bytes(
        paths["cubin"], expected_bytes=REVIEWED_CUBIN_BYTES,
        expected_sha256=REVIEWED_CUBIN_SHA256, label="reviewed cubin",
    )
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
        "native_image_binding": {
            "image_kind": "CUBIN",
            "image_bytes": REVIEWED_CUBIN_BYTES,
            "image_sha256": REVIEWED_CUBIN_SHA256,
            "source_ptx_bytes": REVIEWED_TRANSFORMED_PTX_BYTES,
            "source_ptx_sha256": REVIEWED_TRANSFORMED_PTX_SHA256,
            "original_ptx_sha256": ORIGINAL_PTX_SHA256,
            "selected_kernel": SELECTED_KERNEL,
            "compiler": {
                "tool": "ptxas",
                "architecture": "sm_120",
                "optimization": "-O3",
                "build_manifest_sha256": REVIEWED_BUILD_MANIFEST_SHA256,
            },
            "sass": {
                "mapping_validation": "NOT_PROVEN",
                "disassembly_manifest_sha256":
                    REVIEWED_DISASSEMBLY_MANIFEST_SHA256,
                "nvdisasm_sha256": REVIEWED_NVDISASM_SHA256,
                "cuobjdump_sha256": REVIEWED_CUOBJDUMP_SHA256,
            },
        },
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
                "--cubin", str(paths["cubin"]),
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
            validate_lifecycle_observations(raw)
            binding = raw.get("native_image_binding")
            expected = manifest["native_image_binding"]
            if not isinstance(binding, dict):
                raise ValueError("diagnostic omitted native image binding")
            for field in ("image_kind", "image_bytes", "image_sha256",
                          "source_ptx_bytes", "source_ptx_sha256",
                          "original_ptx_sha256", "selected_kernel"):
                if binding.get(field) != expected[field]:
                    raise ValueError("diagnostic native image binding mismatch: " + field)
            if (binding.get("compiler") != expected["compiler"] or
                    binding.get("sass") != expected["sass"]):
                raise ValueError("diagnostic build/SASS binding mismatch")
            if (binding.get("load_state") != "LOADED" or
                    binding.get("same_retained_buffer_passed_to_driver") is not True or
                    binding.get("driver_result") != 0):
                raise ValueError("diagnostic did not load the retained reviewed cubin")
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
    parser.add_argument("--cubin", type=pathlib.Path)
    parser.add_argument("--gpu-uuid")
    args = parser.parse_args()
    plan = {
        "resource_class": "GPU_EXCLUSIVE",
        "cases": [name for name, _mask in LIFECYCLE_CASES],
        "lifecycle_modes": [mode for _name, mode in LIFECYCLE_CASES],
        "artifact_identity_status": "UNFROZEN" if REVIEWED_CUBIN_BYTES == 0 else "FROZEN",
        "launch": {"grid": [1, 1, 1], "block": [32, 1, 1]},
        "child_timeout_seconds": CHILD_TIMEOUT_SECONDS,
        "validation_status": "UNVALIDATED",
        "native_image": {
            "kind": "CUBIN",
            "bytes": REVIEWED_CUBIN_BYTES,
            "sha256": REVIEWED_CUBIN_SHA256,
            "mapping_validation": "NOT_PROVEN",
        },
    }
    if not args.execute:
        print(json.dumps(plan, indent=2))
        return 0
    if not all((args.out, args.build_dir, args.profile, args.ptx,
                args.cubin, args.gpu_uuid)):
        parser.error(
            "execution requires --out --build-dir --profile --ptx --cubin --gpu-uuid"
        )
    result = execute(
        args.out, args.build_dir, args.profile, args.ptx, args.cubin, args.gpu_uuid
    )
    print(json.dumps(result, indent=2))
    return 0 if result["state"] == "CAPTURED_UNVALIDATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

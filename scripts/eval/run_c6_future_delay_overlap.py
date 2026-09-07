#!/usr/bin/env python3
"""Acquire two fixed-work, single-active-lane C6 slices under one guard.

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
WORKLOAD = "single_active_lane_fixed_work_v1"
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


def load_binding(path: pathlib.Path, fixed_work_count: int) -> tuple[dict, dict[str, pathlib.Path]]:
    value = json.loads(regular_bytes(path))
    expected = {
        "schema_version", "workload", "active_lane_mask", "fixed_work_count",
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
        _exact_int(value["schema_version"], "binding schema_version", 1) != 2
        or value["workload"] != WORKLOAD
        or _exact_int(value["active_lane_mask"], "active lane mask", 1) != 1
        or _exact_int(value["fixed_work_count"], "fixed work count") != fixed_work_count
        or fixed_work_count not in WORK_COUNTS
        or value["selected_kernel"] != SELECTED_KERNEL
        or value["native_kernel"] != NATIVE_KERNEL
        or value["mapping_validation"] != "NOT_PROVEN"
        or value["compiler"]
        != {"tool": "ptxas", "architecture": "sm_120", "optimization": "-O3"}
    ):
        raise ValueError("binding identity/compiler mismatch")
    return value, _binding_paths(value, ("original_ptx", "transformed_ptx", "cubin"))


def _binding_paths(value: dict, sized_names: tuple[str, ...]) -> dict[str, pathlib.Path]:
    paths: dict[str, pathlib.Path] = {}
    for name in sized_names:
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
    return paths


def load_native_binding(path: pathlib.Path, future_binding: dict) -> tuple[dict, dict[str, pathlib.Path]]:
    value = json.loads(regular_bytes(path))
    expected = {"schema_version", "workload", "active_lane_mask", "fixed_work_count", "selected_kernel", "transform_mode", "mapping_validation",
                "compiler", "original_ptx", "cubin", *_SHA_KEYS}
    if type(value) is not dict or set(value) != expected:
        raise ValueError("ordinary native binding schema mismatch")
    if (_exact_int(value["schema_version"], "native schema_version", 1) != 2
        or value["workload"] != WORKLOAD
        or _exact_int(value["active_lane_mask"], "native lane mask", 1) != 1
        or _exact_int(value["fixed_work_count"], "native fixed work count") != future_binding["fixed_work_count"]
        or value["selected_kernel"] != NATIVE_KERNEL
        or value["transform_mode"] != "native_untransformed"
        or value["mapping_validation"] != "NOT_PROVEN"
        or value["compiler"] != {"tool": "ptxas", "architecture": "sm_120", "optimization": "-O3"}):
        raise ValueError("ordinary native binding identity/compiler mismatch")
    paths = _binding_paths(value, ("original_ptx", "cubin"))
    for key in ("bytes", "sha256"):
        if value["original_ptx"][key] != future_binding["original_ptx"][key]:
            raise ValueError("ordinary native original PTX differs from future image source")
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
    interval = derive_intervals(records[0])
    expired = interval["prework_from_anchor_ns"] >= DELAY_NS
    return {"state": "NON_IDENTIFYING_PREWORK_COVERS_DELAY" if expired else "PENDING_WINDOW_PRESENT_UNVALIDATED",
            "active_lane": 0, "delay_ns": DELAY_NS, "expired_before_work": expired,
            "intervals": [interval], "g5_scored": False, "overlap_claim": False}


def expected_outputs(work_count: int) -> list[int]:
    result = []
    for lane in range(LANES):
        work = 0x9e3779b9 ^ ((lane * 0x9e3779b9 + 0x85ebca6b) & 0xffffffff)
        for _ in range(work_count):
            work = (work * 0x0019660d + 0x3c6ef35f + lane) & 0xffffffff
        result.append((0xa5a55a5a if lane == 0 else 0) ^ work ^ 0xd1b54a35)
    return result


def validate_accounting(counters, traces, future, address, instruction, active=None):
    expected = dict.fromkeys(("issued", "pending", "model_ready", "consumed", "drained", "terminal_error",
        "native_loads", "native_bytes", "rejected", "groups_issued", "groups_completed", "trace_count", "trace_overflow"), 0)
    expected["next_reservation"] = 2 if future else 1
    if future:
        expected.update(issued=1, model_ready=1, consumed=1, groups_issued=1, groups_completed=1, trace_count=2)
    if type(counters) is not dict or set(counters) != set(expected) or any(
        type(counters[key]) is not int or counters[key] != value for key, value in expected.items()
    ):
        raise ValueError("single-lane counter conservation mismatch")
    if type(traces) is not list or len(traces) != (2 if future else 0):
        raise ValueError("single-lane trace count mismatch")
    for index, trace in enumerate(traces):
        fixed = dict(lane=0, group_mask=1, bytes=4, reservation_id=1, address=address,
                     instruction_id=instruction, event=0 if index == 0 else 5, status=index)
        if type(trace) is not dict or any(type(trace.get(k)) is not int or trace[k] != v for k, v in fixed.items()):
            raise ValueError("single-lane trace identity mismatch")
        issue, ready, finish = (_exact_int(trace.get(k), k, 1) for k in ("issue_ns", "ready_ns", "finish_ns"))
        if ready < issue or finish < issue or (index and finish < ready):
            raise ValueError("single-lane trace timestamp order")
        if active is not None:
            lower, upper = ((active["arrival_ns"], active["helper_issue_exit_ns"]) if index == 0
                            else (active["wait_enter_ns"], active["wait_exit_ns"]))
            if issue != active["arrival_ns"] or ready != active["ready_ns"] or not lower <= finish <= upper:
                raise ValueError("single-lane trace/record join mismatch")
    if future and any(traces[0][k] != traces[1][k] for k in ("issue_ns", "ready_ns")):
        raise ValueError("issue/consume anchor mismatch")
    if future and traces[1]["finish_ns"] < traces[0]["finish_ns"]:
        raise ValueError("consume trace precedes issue trace")


def validate_launch(launch, work, ordinal, address, instruction, outputs):
    arm = ARMS[ordinal // (WARMUPS + SAMPLES)]
    within = ordinal % (WARMUPS + SAMPLES)
    expected = dict(arm=arm, work_count=work, delay_ns=DELAY_NS if arm == "futureD" else 0,
        warmup=within == 0, sample=max(0, within - 1), launch_epoch=101 + ordinal,
        validation="PASS", trace_copy_bounded=True)
    if type(launch) is not dict or any(type(launch.get(k)) is not type(v) or launch[k] != v for k, v in expected.items()):
        raise ValueError("fixed-work launch identity mismatch")
    if (launch.get("observed_outputs") != outputs or
        launch.get("sentinel_outputs") != [value ^ 0xffffffff for value in outputs] or
        any(type(value) is not int for name in ("observed_outputs", "sentinel_outputs") for value in launch[name])):
        raise ValueError("single-lane active/inactive output oracle mismatch")
    records = launch.get("records")
    if type(records) is not list or len(records) != LANES:
        raise ValueError("launch requires 32 defined lane records")
    for lane, record in enumerate(records):
        active = arm != "native" and lane == 0
        fixed = dict(lane=lane, launch_epoch=101 + ordinal, work_count=work, output_bits=outputs[lane],
            configured_delay_ns=expected["delay_ns"] if active else 0, reservation_id=1 if active else 0,
            valid_bits=1023 if active else 824, status=1)
        if type(record) is not dict or any(type(record.get(k)) is not int or record[k] != v for k, v in fixed.items()):
            raise ValueError("single-lane record identity/output mismatch")
        kernel = [_exact_int(record.get(k), k, 1) for k in ("native_instruction_after_ns", "work_begin_ns", "work_end_ns", "consumer_after_ns")]
        if kernel != sorted(kernel):
            raise ValueError("kernel timestamps not ordered")
        if active:
            derive_intervals(record)
            if record["ready_ns"] - record["arrival_ns"] != expected["delay_ns"] or record["wait_exit_ns"] < record["ready_ns"]:
                raise ValueError("modeled delay/consumer timing mismatch")
        elif any(type(record.get(k)) is not int or record[k] != 0 for k in ("helper_entry_ns", "arrival_ns",
                "helper_issue_exit_ns", "wait_enter_ns", "wait_exit_ns", "ready_ns")):
            raise ValueError("inactive lane unexpectedly entered future helper")
    validate_accounting(launch.get("counters"), launch.get("traces"), arm != "native", address, instruction,
                        records[0] if arm != "native" else None)
    return records


def validate_raw(raw: dict, expected_binding: dict, expected_native_binding: dict) -> dict:
    if type(raw) is not dict:
        raise ValueError("raw diagnostic must be an object")
    for key, expected in {
        "schema_version": 2,
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
    work = expected_binding["fixed_work_count"]
    for key, expected in {"workload": WORKLOAD, "active_lane_mask": 1, "fixed_work_count": work}.items():
        if type(raw.get(key)) is not type(expected) or raw[key] != expected:
            raise ValueError("raw fixed-work identity mismatch: " + key)
    markers = raw.get("fixed_work_markers")
    if (type(markers) is not dict or set(markers) != {"future", "native"} or
        any(type(v) is not int or v != work for v in markers.values())):
        raise ValueError("loaded fixed-work markers mismatch")
    launches = raw.get("launches")
    if type(launches) is not list or len(launches) != len(ARMS) * (WARMUPS + SAMPLES):
        raise ValueError("raw fixed-work launch count mismatch")
    instruction = _exact_int(raw.get("instruction_id"), "instruction id")
    address = _exact_int(raw.get("native_input", {}).get("future_address"), "future address", 1)
    future_d, active_work = [], {arm: [] for arm in ARMS}
    outputs = expected_outputs(work)
    for ordinal, launch in enumerate(launches):
        records = validate_launch(launch, work, ordinal, address, instruction, outputs)
        if not launch["warmup"]:
            active_work[launch["arm"]].append(records[0]["work_end_ns"] - records[0]["work_begin_ns"])
            if launch["arm"] == "futureD":
                future_d.append({"work_count": work, **classify_future_d(records)})
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
    native = raw.get("native_control_binding")
    expected_native = {
        **{name + "_sha256": expected_native_binding[name]["sha256"]
           for name in ("original_ptx", "cubin", *_SHA_KEYS)},
        "cubin_bytes": expected_native_binding["cubin"]["bytes"],
        "selected_kernel": NATIVE_KERNEL, "mapping_validation": "NOT_PROVEN",
        "same_retained_buffer": True, "load_result": 0,
        "future_requirements_lookup": 500,  # CUDA_ERROR_NOT_FOUND
        "future_requirements_absent": True, "distinct_modules": True,
    }
    if type(native) is not dict or set(native) != set(expected_native) or any(
        type(native[key]) is not type(value) or native[key] != value
        for key, value in expected_native.items()
    ):
        raise ValueError("raw ordinary native load binding mismatch")
    echoed = raw.get("native_input")
    fields = {"address", "bytes", "registered", "words", "future_words", "future_address",
              "future_registered_bytes", "contents_equal", "disjoint"}
    if type(echoed) is not dict or set(echoed) != fields:
        raise ValueError("native input readback schema mismatch")
    expected_words = [((lane * 0x45d9f3b) & 0xffffffff) ^ 0xa5a55a5a for lane in range(LANES)]
    if (echoed["registered"] is not False or echoed["contents_equal"] is not True
        or echoed["disjoint"] is not True or echoed["words"] != expected_words
        or echoed["future_words"] != expected_words
        or any(type(word) is not int for key in ("words", "future_words") for word in echoed[key])
        or _exact_int(echoed["bytes"], "native input bytes", 1) != 128
        or _exact_int(echoed["future_registered_bytes"], "future range bytes", 1) != 4096):
        raise ValueError("native input is registered or differs from fixed future input")
    address = _exact_int(echoed["address"], "native input address", 1)
    future_address = _exact_int(echoed["future_address"], "future input address", 1)
    if not (address + 128 <= future_address or future_address + 4096 <= address):
        raise ValueError("native input overlaps the registered future range")
    return {
        "schema_version": 2,
        "status": "TIMING_INTERVALS_CAPTURED_UNVALIDATED",
        "workload": WORKLOAD,
        "active_lane_mask": 1,
        "fixed_work_count": work,
        "active_work_ns": active_work,
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


def load_image_pairs(image_bindings):
    if (type(image_bindings) is not dict or set(image_bindings) != set(WORK_COUNTS)
        or any(type(key) is not int for key in image_bindings)):
        raise ValueError("both fixed K image pairs are required")
    pairs, paths, verified = {}, {}, {}
    for work in WORK_COUNTS:
        prefix = f"k{work}_"
        future_path, native_path = (pathlib.Path(path).resolve() for path in image_bindings[work])
        future_bytes, native_bytes = regular_bytes(future_path), regular_bytes(native_path)
        binding, future_paths = load_binding(future_path, work)
        native, native_paths = load_native_binding(native_path, binding)
        if json.loads(future_bytes) != binding or json.loads(native_bytes) != native:
            raise ValueError("binding changed during metadata parsing")
        if binding["original_ptx"]["sha256"] == binding["transformed_ptx"]["sha256"]:
            raise ValueError("same K original and transformed PTX must differ")
        if binding["cubin"]["sha256"] == native["cubin"]["sha256"]:
            raise ValueError("same K future and ordinary native cubin must differ")
        pairs[work] = (binding, native)
        paths[prefix + "binding"], paths[prefix + "native_binding"] = future_path, native_path
        verified[prefix + "binding"], verified[prefix + "native_binding"] = future_bytes, native_bytes
        for kind, value, references in (("binding_", binding, future_paths), ("native_binding_", native, native_paths)):
            frozen = verify_binding_files(value, references)
            paths.update({prefix + kind + name: path for name, path in references.items()})
            verified.update({prefix + kind + name: data for name, data in frozen.items()})
    for name in ("original_ptx", "transformed_ptx", "cubin"):
        if pairs[0][0][name]["sha256"] == pairs[4096][0][name]["sha256"]:
            raise ValueError("fixed K images must have separate " + name + " identities")
    if pairs[0][1]["cubin"]["sha256"] == pairs[4096][1]["cubin"]["sha256"]:
        raise ValueError("fixed K ordinary native images must differ")
    return pairs, paths, verified


def combine_analyses(analyses):
    separated = {arm: min(analyses[4096]["active_work_ns"][arm]) > max(analyses[0]["active_work_ns"][arm]) for arm in ARMS}
    expired = sum(sample["expired_before_work"] for value in analyses.values() for sample in value["future_d_samples"])
    state = ("NON_IDENTIFYING_PREWORK_COVERS_DELAY" if expired else
             "NON_IDENTIFYING_WORK_CONTROLS_NOT_SEPARATED" if not all(separated.values()) else
             "WINDOW_AND_WORK_CONTROLS_PRESENT_UNVALIDATED")
    return {"schema_version": 2, "status": state, "workload": WORKLOAD, "active_lane_mask": 1,
            "work_ranges_separated": separated, "futureD_samples_expired_before_work": expired,
            "total_launches": 68, "matrix_launches": 66, "discovery_launches": 2,
            "matrix_output_words": 2112, "issued_ready_consumed_each": 46, "trace_count": 92,
            "scientific_claim": False, "g5_closed": False, "overlap_closed": False,
            "c6_3_closed": False, "c6_4_closed": False, "native_completion_timing": False,
            "per_work_count": analyses}


def execute(out: pathlib.Path, build: pathlib.Path, profile: pathlib.Path,
            image_bindings: dict, gpu_uuid: str) -> dict:
    out, build, profile = out.resolve(), build.resolve(), profile.resolve()
    if not out.is_relative_to((ROOT / "results/gold/timing-future-unit").resolve()):
        raise ValueError("output must be under the timing-future-unit gold root")
    out.mkdir(parents=True, exist_ok=False)
    pairs, paths, verified = load_image_pairs(image_bindings)
    paths.update({"binary": build / "benchmarks/cuda/c6_future_delay_overlap",
        "plugin": build / "libptxpass_hbf.so", "gate": build / "libhbfsim_launch_gate.so",
        "daemon": build / "hbfsimd", "profile": profile, "build_config": build / "CMakeCache.txt",
        "runner": pathlib.Path(__file__).resolve(),
        "benchmark_source": ROOT / "benchmarks/cuda/c6_future_delay_overlap.cu",
        "benchmark_build_definition": ROOT / "benchmarks/cuda/CMakeLists.txt",
        "device_helper": ROOT / "src/cuda_runtime/device/hbf_device.cu",
        "future_abi": ROOT / "include/hbfsim/timing_future_abi.hpp"})
    frozen = {name: regular_bytes(path) for name, path in paths.items()}
    if any(frozen[name] != data for name, data in verified.items()):
        raise ValueError("bound image changed during initial freeze")
    manifest = {
        "schema_version": 2,
        "created_at": now(),
        "state_contract": "UNVALIDATED",
        "resource_class": "GPU_EXCLUSIVE",
        "child_timeout_seconds": CHILD_TIMEOUT_SECONDS,
        "sequential_children": 2,
        "outer_timeout_required_seconds": 300,
        "total_kernel_launches": 68,
        "active_lane_mask": 1,
        "workload": WORKLOAD,
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
            diagnostic = out / "diagnostic"
            diagnostic.mkdir()
            analyses = {}
            for work in WORK_COUNTS:
                if signals["signal"] is not None:
                    raise InterruptedRun("signal before next fixed-work child")
                if git_snapshot(ROOT) != manifest["git"]:
                    raise ValueError("git HEAD/clean snapshot changed between fixed-work children")
                for name, path in paths.items():
                    if sha256(regular_bytes(path)) != manifest["inputs"][name]["sha256"]:
                        raise ValueError("frozen input changed before fixed-work child: " + name)
                directory = diagnostic / f"k{work}"
                directory.mkdir()
                prefix = f"k{work}_"
                binding, native_binding = pairs[work]
                argv = [str(paths["binary"]), "--profile", str(paths["profile"]), "--plugin", str(paths["plugin"]),
                    "--ptx", str(paths[prefix + "binding_original_ptx"]),
                    "--transformed-ptx", str(paths[prefix + "binding_transformed_ptx"]),
                    "--cubin", str(paths[prefix + "binding_cubin"]),
                    "--binding", str(paths[prefix + "binding"]),
                    "--native-binding", str(paths[prefix + "native_binding"]),
                    "--fixed-work-count", str(work), "--output", str(directory / "raw.json"),
                    "--report-dir", str(directory / "reports")]
                manifest["commands"][f"k{work}"] = argv
                atomic_json(out / "manifest.json", manifest)
                environment = {key: value for key, value in os.environ.items()
                    if key not in ("LD_PRELOAD", "LD_AUDIT") and not key.startswith(("HBFSIM_", "BPFTIME_", "PTX_PASS_"))}
                environment.update(CUDA_VISIBLE_DEVICES=gpu_uuid, HBFSIM_DAEMON_PATH=str(paths["daemon"]),
                    HBFSIM_PASS_MANIFEST_PATH=str(directory / "pass.jsonl"), LD_PRELOAD=str(paths["gate"]))
                code = run_child(argv, environment, directory, "producer", guard,
                    lambda child, phase: update("RUNNING_UNVALIDATED", child=child, phase=phase, fixed_work_count=work),
                    signals, CHILD_TIMEOUT_SECONDS, 0.1)
                if signals["signal"] is not None:
                    raise InterruptedRun("signal received after fixed-work child")
                if code:
                    raise FailedRun("fixed-work child failed: " + str(work))
                raw = json.loads(regular_bytes(directory / "raw.json"))
                analyses[work] = validate_raw(raw, binding, native_binding)
                atomic_json(directory / "analysis.json", analyses[work])
                partial = json.loads(regular_bytes(directory / "raw.json.partial.json"))
                if (partial.get("capture_complete") is not True or partial.get("launches") != raw["launches"]
                    or partial.get("cleanup") != raw["cleanup"]):
                    raise ValueError("fixed-work journal/final mismatch")
                discovery = partial["instruction_discovery"]
                if discovery["config"] != "ALL_ZERO_DIAGNOSTIC_DISABLED":
                    raise ValueError("discovery config mismatch")
                validate_accounting(discovery["counters"], discovery["traces"], True,
                    raw["native_input"]["future_address"], raw["instruction_id"])
                guard.check("after-fixed-work-" + str(work))
            atomic_json(diagnostic / "analysis.json", combine_analyses(analyses))
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
    for work in WORK_COUNTS:
        parser.add_argument(f"--binding-k{work}", type=pathlib.Path)
        parser.add_argument(f"--native-binding-k{work}", type=pathlib.Path)
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
    image_bindings = {work: (getattr(args, f"binding_k{work}"), getattr(args, f"native_binding_k{work}")) for work in WORK_COUNTS}
    if not all((args.out, args.build_dir, args.profile, args.gpu_uuid)) or not all(path for pair in image_bindings.values() for path in pair):
        parser.error("execution requires out/build/profile/GPU and both fixed-K native/future bindings")
    result = execute(args.out, args.build_dir, args.profile, image_bindings, args.gpu_uuid)
    print(json.dumps(result, indent=2))
    return 0 if result["state"] == "CAPTURED_UNVALIDATED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

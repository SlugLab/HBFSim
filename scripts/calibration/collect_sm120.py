#!/usr/bin/env python3
"""Collect immutable SM120 calibration evidence without mutating GPU state."""

from __future__ import annotations

import hashlib
import csv
import ctypes
import io
import json
import os
import pathlib
import re
import signal
import shutil
import subprocess
import sys
import uuid

USAGE = "collect_sm120.py --suite training|holdout --cases FILE --benchmark EXE --ncu EXE --output-dir DIR"
NCU_VERSION = "2025.4.1.0"
CUDA_RELEASE = "release 13.0"
METRICS = (
    "sm__pipe_tma_cycles_active.avg.pct_of_peak_sustained_active",
    "smsp__inst_executed_pipe_lsu.avg.pct_of_peak_sustained_active",
    "smsp__warp_issue_stalled_long_scoreboard_per_warp_active.pct",
    "smsp__warp_issue_stalled_barrier_per_warp_active.pct",
    "smsp__warp_issue_stalled_membar_per_warp_active.pct",
    "lts__t_sectors.sum",
    "dram__bytes.sum",
    "gpu__time_duration.sum",
)
ENVIRONMENT_KEYS = (
    "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "LD_PRELOAD",
    "LD_LIBRARY_PATH", "PATH", "LANG", "LC_ALL",
)


class InputError(Exception):
    pass


class ToolError(Exception):
    pass


def interrupted(signum: int, frame: object) -> None:
    del frame
    raise ToolError(f"collection interrupted by signal {signum}")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True,
                      separators=(",", ":")).encode()


def cuda_driver_version() -> int:
    override = os.environ.get("HBFSIM_TEST_CUDA_DRIVER_VERSION")
    if override is not None:
        if not override.isdigit() or int(override) <= 0:
            raise ToolError("invalid test CUDA driver version")
        return int(override)
    try:
        driver = ctypes.CDLL("libcuda.so.1")
        value = ctypes.c_int()
        if driver.cuInit(0) != 0 or \
                driver.cuDriverGetVersion(ctypes.byref(value)) != 0 or \
                value.value <= 0:
            raise ToolError("CUDA driver version query failed")
        return int(value.value)
    except OSError as error:
        raise ToolError("CUDA driver library is unavailable") from error


def csv_rows(output: bytes) -> list[list[str]]:
    text = output.decode("utf-8", "replace")
    return [[field.strip() for field in row]
            for row in csv.reader(io.StringIO(text)) if row]


def selected_gpu_index(rows: list[list[str]]) -> int:
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "").split(",", 1)[0].strip()
    if visible.startswith("GPU-"):
        for position, row in enumerate(rows):
            if len(row) > 2 and row[2] == visible:
                return position
        raise ToolError("CUDA_VISIBLE_DEVICES UUID is not present")
    if visible and visible.isdigit():
        physical = int(visible)
        for position, row in enumerate(rows):
            if row and row[0].isdigit() and int(row[0]) == physical:
                return position
        raise ToolError("CUDA_VISIBLE_DEVICES index is not present")
    if len(rows) != 1:
        raise ToolError("collector requires one explicitly selected GPU")
    return 0


def gpu_snapshot(nvidia_smi: pathlib.Path) -> dict[str, object]:
    command = [
        str(nvidia_smi),
        "--query-gpu=index,name,uuid,pci.bus_id,pci.device_id,compute_cap,"
        "clocks.sm,clocks.mem,power.limit,temperature.gpu",
        "--format=csv,noheader,nounits",
    ]
    completed = run(command)
    if completed.returncode != 0:
        raise ToolError("nvidia-smi GPU snapshot failed")
    rows = csv_rows(completed.stdout)
    if not rows or any(len(row) != 10 for row in rows):
        raise ToolError("nvidia-smi GPU snapshot is malformed")
    row = rows[selected_gpu_index(rows)]
    try:
        capability = row[5].split(".")
        device_word = int(row[4], 16)
        return {
            "index": int(row[0]), "gpu_name": row[1], "gpu_uuid": row[2],
            "pci_bus_id": row[3], "pci_vendor_id": device_word & 0xFFFF,
            "pci_device_id": (device_word >> 16) & 0xFFFF,
            "compute_capability_major": int(capability[0]),
            "compute_capability_minor": int(capability[1]),
            "sm_clock_mhz": int(row[6]), "memory_clock_mhz": int(row[7]),
            "power_limit_mw": round(float(row[8]) * 1000),
            "temperature_c": int(row[9]),
        }
    except (ValueError, IndexError) as error:
        raise ToolError("nvidia-smi GPU snapshot values are malformed") from error


def competing_gpu_processes(nvidia_smi: pathlib.Path,
                            gpu_uuid: str) -> list[dict[str, object]]:
    completed = run([
        str(nvidia_smi),
        "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
        "--format=csv,noheader,nounits",
    ])
    if completed.returncode != 0:
        raise ToolError("nvidia-smi compute-process query failed")
    processes = []
    for row in csv_rows(completed.stdout):
        if len(row) != 4:
            raise ToolError("nvidia-smi compute-process result is malformed")
        if row[0] != gpu_uuid:
            continue
        try:
            processes.append({"gpu_uuid": row[0], "pid": int(row[1]),
                              "process_name": row[2],
                              "used_memory_mib": int(row[3])})
        except ValueError as error:
            raise ToolError("nvidia-smi compute-process values are malformed") from error
    return processes


def parse(argv: list[str]) -> dict[str, str]:
    if len(argv) != 10:
        raise ValueError(USAGE)
    result: dict[str, str] = {}
    allowed = {"--suite", "--cases", "--benchmark", "--ncu", "--output-dir"}
    for index in range(0, len(argv), 2):
        key, value = argv[index:index + 2]
        if key not in allowed or key in result or not value:
            raise ValueError(USAGE)
        result[key] = value
    if set(result) != allowed or result["--suite"] not in ("training", "holdout"):
        raise ValueError(USAGE)
    return result


def regular_input(value: str, executable: bool = False) -> pathlib.Path:
    path = pathlib.Path(value)
    try:
        status = path.lstat()
    except OSError as error:
        raise InputError(f"missing input: {path}") from error
    if path.is_symlink() or not path.is_file():
        raise InputError(f"input must be a regular non-symlink: {path}")
    if executable and not os.access(path, os.X_OK):
        raise InputError(f"input is not executable: {path}")
    del status
    return path.resolve()


def output_target(value: str) -> pathlib.Path:
    path = pathlib.Path(value).absolute()
    if path.exists() or path.is_symlink():
        raise InputError(f"output already exists: {path}")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise InputError(f"output parent must be a non-symlink directory: {parent}")
    return path


def durable_write(path: pathlib.Path, data: bytes) -> dict[str, object]:
    with path.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    return {"path": path.name, "bytes": len(data), "sha256": sha256(data)}


def run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(command, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=False, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ToolError(f"tool invocation failed: {command[0]}") from error


def version(executable: pathlib.Path, arguments: list[str]) -> str:
    completed = run([str(executable), *arguments])
    if completed.returncode != 0:
        raise ToolError(f"version command failed: {executable}")
    return completed.stdout.decode("utf-8", "replace").strip()


def expected_opcode_class(case: dict[str, object]) -> str:
    operation = str(case.get("operation_class", ""))
    dimensions = int(case.get("dimension_count", 0))
    if operation == "ordinary_load":
        return "ld.global.u64"
    if operation == "ordinary_store":
        return "st.global.u64"
    if operation == "mixed_hbm_hbf":
        return "ld.global.u64+st.global.u64"
    if operation == "tma_store":
        return f"tma_store_{dimensions}d"
    if operation == "unicast":
        return f"tma_load_{dimensions}d_unicast"
    if operation == "multicast":
        return f"tma_load_{dimensions}d_multicast"
    if operation == "tma_load":
        return f"tma_load_{dimensions}d"
    raise ToolError("benchmark case operation class is unsupported")


def parse_benchmark_record(stdout: bytes,
                           case: dict[str, object]) -> dict[str, object]:
    case_id = str(case.get("id", ""))
    for line in reversed(stdout.decode("utf-8", "replace").splitlines()):
        if not line.lstrip().startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("case_id") == case_id:
            if record.get("bit_exact") is not True or \
                    record.get("output_sha256") != record.get("expected_sha256"):
                raise ToolError(f"benchmark correctness failure: {case_id}")
            required_matches = {
                "executed_dimension_count": int(case.get("dimension_count", 0)),
                "executed_queue_depth": int(case.get("queue_depth", 1)),
                "executed_multicast_mask": int(case.get("multicast_mask", 0)),
                "executed_cluster_shape": case.get("cluster_shape", [1, 1, 1]),
                "cache_condition_executed": str(case.get("cache_condition", "")),
                "hardware_opcode_class": expected_opcode_class(case),
            }
            for field, expected in required_matches.items():
                if record.get(field) != expected:
                    raise ToolError(
                        f"benchmark execution evidence mismatch: {case_id}:{field}")
            issued = record.get("issued_operations")
            if not isinstance(issued, int) or isinstance(issued, bool) or issued <= 0:
                raise ToolError(
                    f"benchmark issued-operation evidence missing: {case_id}")
            if "proxy" in str(record.get("hardware_opcode_class", "")).lower():
                raise ToolError(f"benchmark proxy opcode is forbidden: {case_id}")
            if case.get("operation_class") == "multicast" and \
                    record.get("cluster_ctarank") != case.get("cta_rank"):
                raise ToolError(
                    f"benchmark multicast issuer rank mismatch: {case_id}")
            return record
    raise ToolError(f"benchmark JSON record missing: {case_id}")


def metric_number(text: str) -> float:
    value = text.strip().replace(",", "")
    if value.endswith("%"):
        value = value[:-1]
    try:
        result = float(value)
    except ValueError as error:
        raise ToolError(f"Nsight metric value is not numeric: {text}") from error
    if result < 0 or result != result or result in (float("inf"), float("-inf")):
        raise ToolError(f"Nsight metric value is invalid: {text}")
    return result


def parse_ncu_metrics(stdout: bytes) -> dict[str, float]:
    rows = csv_rows(stdout)
    header_index = next((index for index, row in enumerate(rows)
                         if "Metric Name" in row and "Metric Value" in row),
                        None)
    if header_index is None:
        raise ToolError("Nsight CSV header is missing")
    header = rows[header_index]
    name_index = header.index("Metric Name")
    value_index = header.index("Metric Value")
    values: dict[str, list[float]] = {metric: [] for metric in METRICS}
    for row in rows[header_index + 1:]:
        if len(row) <= max(name_index, value_index):
            continue
        name = row[name_index]
        if name in values:
            values[name].append(metric_number(row[value_index]))
    missing = [name for name, items in values.items() if not items]
    if missing:
        raise ToolError("Nsight metrics missing: " + ",".join(missing))
    return {name: sum(items) / len(items) for name, items in values.items()}


def observation(case: dict[str, object], repetition: int,
                record: dict[str, object], metrics: dict[str, float]) -> dict[str, object]:
    stamps = record.get("timestamps")
    if not isinstance(stamps, dict):
        raise ToolError("benchmark timestamps missing")
    try:
        timer_latency = max(1, int(stamps["end"]) - int(stamps["issue"]))
        native_latency = max(1, round(metrics["gpu__time_duration.sum"]))
        base_id = str(case["id"])
        return {
            "case_id": f"{base_id}.repeat-{repetition}",
            "base_case_id": base_id,
            "operation_class": str(case["operation_class"]),
            "expected_sha256": str(record["expected_sha256"]),
            "observed_sha256": str(record["output_sha256"]),
            "native_latency_ns": native_latency,
            "device_timer_latency_ns": timer_latency,
            "smid": int(record.get("smid", 0)),
            "warpid": int(record.get("warpid", 0)),
            "cta_shape": [int(case.get("warps", 1)) * 32, 1, 1],
            "resident_warps": int(case.get("warps", 1)),
            "cluster_ctarank": int(record.get("cluster_ctarank", 0)),
            "bytes": int(case.get("bytes", 1)),
            "queue_depth": int(case.get("queue_depth", 1)),
            "iterations": int(case.get("iterations", 1)),
            "load_use_distance": int(case.get("load_use_distance", 0)),
            "dimensions": [int(value) for value in
                           case.get("dimensions", [])],
            "hardware_opcode_class": str(record["hardware_opcode_class"]),
            "executed_dimension_count": int(record["executed_dimension_count"]),
            "executed_multicast_mask": int(record["executed_multicast_mask"]),
            "executed_cluster_shape": list(record["executed_cluster_shape"]),
            "issued_operations": int(record["issued_operations"]),
            "cache_condition_executed": str(record["cache_condition_executed"]),
            "native_counters": metrics,
            "contention_vector": [
                metrics["smsp__inst_executed_pipe_lsu.avg.pct_of_peak_sustained_active"],
                metrics["sm__pipe_tma_cycles_active.avg.pct_of_peak_sustained_active"],
                metrics["smsp__warp_issue_stalled_long_scoreboard_per_warp_active.pct"],
                metrics["lts__t_sectors.sum"],
            ],
            "return_contention_vector": [
                metrics["smsp__warp_issue_stalled_barrier_per_warp_active.pct"],
                metrics["smsp__warp_issue_stalled_membar_per_warp_active.pct"],
            ],
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ToolError("benchmark observation is malformed") from error


def main(argv: list[str]) -> int:
    try:
        options = parse(argv)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 64
    partial: pathlib.Path | None = None
    try:
        suite = options["--suite"]
        cases_path = regular_input(options["--cases"])
        benchmark = regular_input(options["--benchmark"], executable=True)
        ncu = regular_input(options["--ncu"], executable=True)
        nvidia_smi_raw = shutil.which("nvidia-smi")
        if nvidia_smi_raw is None:
            raise ToolError("nvidia-smi is unavailable")
        nvidia_smi = regular_input(nvidia_smi_raw, executable=True)
        output = output_target(options["--output-dir"])
        raw_cases = cases_path.read_bytes()
        try:
            cases = json.loads(raw_cases)
        except json.JSONDecodeError as error:
            raise InputError("case manifest is not JSON") from error
        if cases.get("manifest_schema_version") != 1 or cases.get("suite") != suite:
            raise InputError("case manifest suite/schema mismatch")
        warmup = cases.get("warmup")
        repetitions = cases.get("repetitions")
        if not isinstance(warmup, int) or warmup < 1 or \
                not isinstance(repetitions, int) or repetitions < 3:
            raise InputError("invalid warmup/repetition policy")
        exact_contract = cases.get("exact_profile_contract")
        if not isinstance(exact_contract, dict) or \
                exact_contract.get("concurrency_condition") != \
                "exclusive_process" or \
                exact_contract.get("clock_control") != "none":
            raise InputError("exact profile contract is missing or unsafe")
        case_items = cases.get("cases")
        if not isinstance(case_items, list) or not case_items:
            raise InputError("empty case manifest")
        ids: list[str] = []
        cases_by_id: dict[str, dict[str, object]] = {}
        for item in case_items:
            case_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9-]+", case_id) or case_id in ids:
                raise InputError("invalid or duplicate case id")
            ids.append(case_id)
            cases_by_id[case_id] = item

        ncu_text = version(ncu, ["--version"])
        if NCU_VERSION not in ncu_text:
            raise ToolError(f"Nsight Compute version must be {NCU_VERSION}")
        nvcc = regular_input(str(ncu.parent / "nvcc"), executable=True)
        nvcc_text = version(nvcc, ["--version"])
        if CUDA_RELEASE not in nvcc_text:
            raise ToolError("CUDA toolkit must be release 13.0")

        initial_snapshot = gpu_snapshot(nvidia_smi)
        if initial_snapshot["compute_capability_major"] != 12 or \
                initial_snapshot["compute_capability_minor"] != 0:
            raise ToolError("selected GPU is not compute capability 12.0")
        processes = competing_gpu_processes(
            nvidia_smi, str(initial_snapshot["gpu_uuid"]))
        if processes:
            summary = ", ".join(
                f"pid={item['pid']}:{item['process_name']}"
                for item in processes)
            raise ToolError(f"competing GPU work prevents exact collection: {summary}")
        driver_version = cuda_driver_version()
        target = {
            key: initial_snapshot[key] for key in (
                "gpu_name", "gpu_uuid", "pci_vendor_id", "pci_device_id",
                "compute_capability_major", "compute_capability_minor")
        }
        target["driver_version"] = driver_version
        captured_environment = {
            "variables": {key: os.environ.get(key, "")
                          for key in ENVIRONMENT_KEYS},
            "target": target,
            "tool_versions": {"ncu": ncu_text.split("Version ", 1)[-1],
                              "nvcc": nvcc_text},
            "exact_profile_contract": exact_contract,
        }
        environment_sha256 = sha256(canonical(captured_environment))

        partial = output.parent / f".{output.name}.partial-{uuid.uuid4().hex}"
        partial.mkdir(mode=0o700)
        members: list[dict[str, object]] = []
        runs: list[dict[str, object]] = []
        observations: list[dict[str, object]] = []
        commands: list[list[str]] = []
        snapshots: list[dict[str, object]] = [initial_snapshot]
        for case_id in ids:
            benchmark_command = [str(benchmark), "--cases", str(cases_path),
                                 "--case-id", case_id]
            for index in range(warmup):
                commands.append(benchmark_command)
                completed = run(benchmark_command)
                prefix = f"{case_id}.warmup-{index}"
                members.append(durable_write(partial / f"{prefix}.stdout.raw",
                                             completed.stdout))
                members.append(durable_write(partial / f"{prefix}.stderr.raw",
                                             completed.stderr))
                if completed.returncode != 0:
                    raise ToolError(f"warmup failed: {case_id}")
                parse_benchmark_record(completed.stdout, cases_by_id[case_id])
            for repetition in range(repetitions):
                command = [str(ncu), "--csv", "--page", "details",
                           "--replay-mode", "application",
                           "--app-replay-mode", "strict",
                           "--cache-control", "none",
                           "--clock-control",
                           str(exact_contract["clock_control"]), "--metrics",
                           ",".join(METRICS), "--target-processes", "all",
                           "--kernel-name-base", "function", "--kernel-name",
                           "regex:.*calibration_kernel.*", "--",
                           *benchmark_command]
                commands.append(command)
                completed = run(command)
                prefix = f"{case_id}.repeat-{repetition}"
                stdout_member = durable_write(partial / f"{prefix}.stdout.raw",
                                              completed.stdout)
                stderr_member = durable_write(partial / f"{prefix}.stderr.raw",
                                              completed.stderr)
                profile_output = completed.stdout + b"\n" + completed.stderr
                csv_lines = [line for line in profile_output.splitlines()
                             if not line.lstrip().startswith(b"{")]
                csv_data = b"\n".join(csv_lines) + (b"\n" if csv_lines else b"")
                csv_member = durable_write(partial / f"{prefix}.ncu.csv", csv_data)
                members.extend((stdout_member, stderr_member, csv_member))
                if completed.returncode != 0:
                    raise ToolError(f"Nsight collection failed: {case_id}")
                record = parse_benchmark_record(
                    profile_output, cases_by_id[case_id])
                metrics = parse_ncu_metrics(profile_output)
                snapshot = gpu_snapshot(nvidia_smi)
                if snapshot["gpu_uuid"] != initial_snapshot["gpu_uuid"] or \
                        snapshot["power_limit_mw"] != \
                        initial_snapshot["power_limit_mw"]:
                    raise ToolError("GPU identity or power limit changed during collection")
                snapshots.append(snapshot)
                runs.append({"case_id": case_id, "repetition": repetition,
                             "argv": command, "benchmark_record": record,
                             "stdout_sha256": stdout_member["sha256"],
                             "stderr_sha256": stderr_member["sha256"],
                             "csv_sha256": csv_member["sha256"]})
                observations.append(observation(
                    cases_by_id[case_id], repetition, record, metrics))
        aggregate = hashlib.sha256()
        for member in sorted(members, key=lambda item: str(item["path"])):
            aggregate.update(str(member["path"]).encode())
            aggregate.update(b"\0")
            aggregate.update(str(member["sha256"]).encode())
            aggregate.update(b"\0")
        manifest = {
            "schema_version": 1, "suite": suite,
            "case_manifest_path": str(cases_path),
            "case_manifest_sha256": sha256(raw_cases),
            "warmup": warmup, "repetitions": repetitions,
            "metrics": list(METRICS),
            "tools": {
                "ncu": {"path": str(ncu), "version": ncu_text.split("Version ", 1)[-1]},
                "nvcc": {"path": str(nvcc), "version": nvcc_text},
                "benchmark": {"path": str(benchmark),
                              "sha256": sha256(benchmark.read_bytes())},
            },
            "environment": {key: os.environ.get(key, "") for key in ENVIRONMENT_KEYS},
            "calibration_environment": captured_environment,
            "environment_sha256": environment_sha256,
            "exact_profile_contract": exact_contract,
            "gpu_processes": processes,
            "exclusive_process_observed": True,
            "gpu_snapshots": snapshots,
            "commands": commands, "runs": runs,
            "observations": observations, "members": members,
            "members_sha256": aggregate.hexdigest(),
            "gpu_state_mutation": False,
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        durable_write(partial / "manifest.json", manifest_bytes)
        directory_fd = os.open(partial, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.replace(partial, output)
        partial = None
        parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        print(json.dumps({"status": "passed", "output": str(output),
                          "members_sha256": manifest["members_sha256"]},
                         sort_keys=True))
        return 0
    except InputError as error:
        print(f"collect_sm120: {error}", file=sys.stderr)
        return 66
    except ToolError as error:
        print(f"collect_sm120: {error}", file=sys.stderr)
        return 70
    except Exception as error:
        print(f"collect_sm120: internal error: {error}", file=sys.stderr)
        return 70
    finally:
        if partial is not None and partial.exists() and partial.name.startswith(".") and \
                ".partial-" in partial.name:
            shutil.rmtree(partial)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    raise SystemExit(main(sys.argv[1:]))

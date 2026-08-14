#!/usr/bin/env python3
"""Collect immutable SM120 calibration evidence without mutating GPU state."""

from __future__ import annotations

import hashlib
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


def parse_benchmark_record(stdout: bytes, case_id: str) -> dict[str, object]:
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
            return record
    raise ToolError(f"benchmark JSON record missing: {case_id}")


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
        case_items = cases.get("cases")
        if not isinstance(case_items, list) or not case_items:
            raise InputError("empty case manifest")
        ids: list[str] = []
        for item in case_items:
            case_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(case_id, str) or not re.fullmatch(r"[a-z0-9-]+", case_id) or case_id in ids:
                raise InputError("invalid or duplicate case id")
            ids.append(case_id)

        ncu_text = version(ncu, ["--version"])
        if NCU_VERSION not in ncu_text:
            raise ToolError(f"Nsight Compute version must be {NCU_VERSION}")
        nvcc = regular_input(str(ncu.parent / "nvcc"), executable=True)
        nvcc_text = version(nvcc, ["--version"])
        if CUDA_RELEASE not in nvcc_text:
            raise ToolError("CUDA toolkit must be release 13.0")

        partial = output.parent / f".{output.name}.partial-{uuid.uuid4().hex}"
        partial.mkdir(mode=0o700)
        members: list[dict[str, object]] = []
        runs: list[dict[str, object]] = []
        commands: list[list[str]] = []
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
                parse_benchmark_record(completed.stdout, case_id)
            for repetition in range(repetitions):
                command = [str(ncu), "--csv", "--page", "raw", "--metrics",
                           ",".join(METRICS), "--target-processes", "all", "--",
                           *benchmark_command]
                commands.append(command)
                completed = run(command)
                prefix = f"{case_id}.repeat-{repetition}"
                stdout_member = durable_write(partial / f"{prefix}.stdout.raw",
                                              completed.stdout)
                stderr_member = durable_write(partial / f"{prefix}.stderr.raw",
                                              completed.stderr)
                csv_lines = [line for line in completed.stdout.splitlines()
                             if not line.lstrip().startswith(b"{")]
                csv_data = b"\n".join(csv_lines) + (b"\n" if csv_lines else b"")
                csv_member = durable_write(partial / f"{prefix}.ncu.csv", csv_data)
                members.extend((stdout_member, stderr_member, csv_member))
                if completed.returncode != 0:
                    raise ToolError(f"Nsight collection failed: {case_id}")
                record = parse_benchmark_record(completed.stdout, case_id)
                runs.append({"case_id": case_id, "repetition": repetition,
                             "argv": command, "benchmark_record": record,
                             "stdout_sha256": stdout_member["sha256"],
                             "stderr_sha256": stderr_member["sha256"],
                             "csv_sha256": csv_member["sha256"]})
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
            "commands": commands, "runs": runs, "members": members,
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

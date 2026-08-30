#!/usr/bin/env python3
"""Convert an external HBM-Power runner result into HBFSim power traces.

The adapter does not vendor or import HBM-Power.  It invokes a caller-provided
runner, records every input identity, and emits piecewise-constant per-stack
power with an accompanying provenance manifest.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass


POWER_RE = re.compile(r"Average power:\s+([0-9.eE+-]+)\s+mW")
DURATION_RE = re.compile(r"duration:\s+([0-9.eE+-]+)\s+ns")


@dataclass(frozen=True)
class RunnerResult:
    trace: pathlib.Path
    average_power_w: float
    active_power_w: float
    baseline_power_w: float
    duration_ns: int
    stdout_sha256: str
    command: list[str]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_file(path: pathlib.Path, label: str) -> pathlib.Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} is not a regular file: {resolved}")
    return resolved


def git_identity(path: pathlib.Path) -> dict[str, object]:
    try:
        root = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        remote = subprocess.run(
            ["git", "-C", root, "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return {
            "root": root,
            "commit": commit,
            "remote": remote,
            "dirty": bool(status.strip()),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"root": None, "commit": None, "remote": None, "dirty": None}


def invoke_runner(
    runner: pathlib.Path,
    organization: pathlib.Path,
    timing: pathlib.Path,
    power: pathlib.Path,
    trace: pathlib.Path,
    runner_args: list[str],
) -> tuple[float, int, str, list[str]]:
    command = [
        str(runner),
        str(organization),
        str(timing),
        str(power),
        str(trace),
        *runner_args,
    ]
    completed = subprocess.run(command, capture_output=True, text=True)
    combined = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise RuntimeError(
            f"HBM-Power runner failed for {trace} with code "
            f"{completed.returncode}:\n{combined[-4000:]}"
        )
    power_match = POWER_RE.search(combined)
    duration_match = DURATION_RE.search(combined)
    if not power_match or not duration_match:
        raise RuntimeError(
            f"HBM-Power runner output lacks power or duration for {trace}"
        )
    average_power_w = float(power_match.group(1)) / 1000.0
    duration_ns = math.ceil(float(duration_match.group(1)))
    if not math.isfinite(average_power_w) or average_power_w < 0.0:
        raise RuntimeError(f"invalid runner power for {trace}: {average_power_w}")
    if duration_ns <= 0:
        raise RuntimeError(f"invalid runner duration for {trace}: {duration_ns}")
    return average_power_w, duration_ns, sha256_bytes(combined.encode()), command


def atomic_text(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def render_csv(results: list[RunnerResult]) -> str:
    event_times = sorted({0, *(result.duration_ns for result in results)})
    rows: list[list[object]] = []
    for event_time in event_times:
        rows.append(
            [
                event_time,
                *[
                    result.average_power_w
                    if event_time < result.duration_ns
                    else 0.0
                    for result in results
                ],
            ]
        )
    buffer: list[str] = []
    # csv.writer requires a file-like object; a tiny adapter keeps newline rules
    # deterministic without a dependency.
    import io

    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        ["time_ns", *[f"hbm_stack_{index}_w" for index in range(len(results))]]
    )
    for row in rows:
        writer.writerow([row[0], *[f"{float(value):.9f}" for value in row[1:]]])
    buffer.append(stream.getvalue())
    return "".join(buffer)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=pathlib.Path, required=True)
    parser.add_argument("--organization", type=pathlib.Path, required=True)
    parser.add_argument("--timing", type=pathlib.Path, required=True)
    parser.add_argument("--power", type=pathlib.Path, required=True)
    parser.add_argument(
        "--stack-trace",
        type=pathlib.Path,
        action="append",
        required=True,
        help="one trace per HBM stack; repeat to emit multiple stacks",
    )
    parser.add_argument(
        "--baseline-trace",
        type=pathlib.Path,
        help="optional idle trace whose average is added to every stack",
    )
    parser.add_argument(
        "--runner-arg",
        action="append",
        default=[],
        help="extra runner argument, e.g. --runner-arg=--dq-rate=0.5",
    )
    parser.add_argument(
        "--baseline-runner-arg",
        action="append",
        default=[],
        help="extra argument used only for the optional baseline runner",
    )
    parser.add_argument(
        "--power-scale",
        type=float,
        default=1.0,
        help="explicit multiplier from runner scope to one stack",
    )
    parser.add_argument("--technology", required=True)
    parser.add_argument("--evidence-level", default="literature_bounded")
    parser.add_argument("--output-csv", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not math.isfinite(args.power_scale) or args.power_scale <= 0.0:
        raise ValueError("--power-scale must be finite and positive")
    runner = require_file(args.runner, "runner")
    organization = require_file(args.organization, "organization")
    timing = require_file(args.timing, "timing")
    power = require_file(args.power, "power")
    traces = [require_file(path, "stack trace") for path in args.stack_trace]
    baseline_trace = (
        require_file(args.baseline_trace, "baseline trace")
        if args.baseline_trace
        else None
    )

    baseline_w = 0.0
    baseline_record: dict[str, object] | None = None
    if baseline_trace is not None:
        baseline_args = args.baseline_runner_arg or args.runner_arg
        value, duration_ns, output_hash, command = invoke_runner(
            runner,
            organization,
            timing,
            power,
            baseline_trace,
            baseline_args,
        )
        baseline_w = value * args.power_scale
        baseline_record = {
            "trace": str(baseline_trace),
            "trace_sha256": sha256_file(baseline_trace),
            "runner_average_power_w": value,
            "scaled_power_w": baseline_w,
            "duration_ns": duration_ns,
            "runner_output_sha256": output_hash,
            "command": command,
        }

    results: list[RunnerResult] = []
    for trace in traces:
        active_w, duration_ns, output_hash, command = invoke_runner(
            runner, organization, timing, power, trace, args.runner_arg
        )
        scaled_active_w = active_w * args.power_scale
        results.append(
            RunnerResult(
                trace=trace,
                average_power_w=scaled_active_w + baseline_w,
                active_power_w=scaled_active_w,
                baseline_power_w=baseline_w,
                duration_ns=duration_ns,
                stdout_sha256=output_hash,
                command=command,
            )
        )

    csv_text = render_csv(results)
    output_csv = args.output_csv.expanduser().resolve()
    manifest_path = args.manifest.expanduser().resolve()
    atomic_text(output_csv, csv_text)

    manifest = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "adapter": {
            "path": str(pathlib.Path(__file__).resolve()),
            "sha256": sha256_file(pathlib.Path(__file__).resolve()),
        },
        "external_tool": git_identity(runner.parent),
        "runner": {"path": str(runner), "sha256": sha256_file(runner)},
        "technology": args.technology,
        "evidence_level": args.evidence_level,
        "scientific_scope": (
            "offline power-model projection; not an HBF specification and not "
            "a measurement of the available GDDR-equipped GPU"
        ),
        "power_scale_to_one_stack": args.power_scale,
        "runner_args": args.runner_arg,
        "baseline_runner_args": args.baseline_runner_arg,
        "inputs": {
            "organization": {
                "path": str(organization),
                "sha256": sha256_file(organization),
            },
            "timing": {"path": str(timing), "sha256": sha256_file(timing)},
            "power": {"path": str(power), "sha256": sha256_file(power)},
            "baseline": baseline_record,
        },
        "stacks": [
            {
                "index": index,
                "trace": str(result.trace),
                "trace_sha256": sha256_file(result.trace),
                "duration_ns": result.duration_ns,
                "active_power_w": result.active_power_w,
                "baseline_power_w": result.baseline_power_w,
                "average_power_w": result.average_power_w,
                "runner_output_sha256": result.stdout_sha256,
                "command": result.command,
            }
            for index, result in enumerate(results)
        ],
        "output": {
            "path": str(output_csv),
            "sha256": sha256_file(output_csv),
            "columns": [
                "time_ns",
                *[f"hbm_stack_{index}_w" for index in range(len(results))],
            ],
            "interpolation": "hold",
            "terminal_zero_sample": True,
        },
    }
    atomic_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "output_csv": str(output_csv),
                "manifest": str(manifest_path),
                "stacks": len(results),
                "powers_w": [result.average_power_w for result in results],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, OSError) as error:
        print(f"hbm_power_adapter: {error}", file=sys.stderr)
        raise SystemExit(2)

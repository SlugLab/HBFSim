#!/usr/bin/env python3
"""Run a resumable, manifest-first Phase-II sustainable-BW sweep."""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import shlex
import subprocess
from typing import Any


WORKLOADS = {
    "read_heavy": ("read_heavy", 0.9),
    "mixed": ("mixed", 0.5),
    "write_heavy": ("write_heavy", 0.1),
}


def positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return result


def comma_ints(value: str) -> list[int]:
    result = [positive_int(item.strip()) for item in value.split(",")]
    if len(set(result)) != len(result):
        raise argparse.ArgumentTypeError("values must be unique")
    return result


def comma_strings(value: str) -> list[str]:
    result = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(result) - set(WORKLOADS))
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown workloads: {unknown}")
    return result


def run(command: list[str], log_path: pathlib.Path) -> None:
    with log_path.open("a", encoding="utf-8") as log:
        log.write("$ " + shlex.join(command) + "\n")
        log.flush()
        subprocess.run(command, check=True, stdout=log,
                       stderr=subprocess.STDOUT)


def write_json(path: pathlib.Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=pathlib.Path, required=True)
    parser.add_argument("--manifest-tool", type=pathlib.Path, required=True)
    parser.add_argument("--analyzer", type=pathlib.Path, required=True)
    parser.add_argument("--repo", type=pathlib.Path, required=True)
    parser.add_argument("--device-profile", type=pathlib.Path, required=True)
    parser.add_argument("--package-profile", type=pathlib.Path, required=True)
    parser.add_argument("--rom", type=pathlib.Path, required=True)
    parser.add_argument("--evidence-grid", type=pathlib.Path, required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--stack-height", type=int, choices=(8, 16), required=True)
    parser.add_argument("--rates", type=comma_ints, required=True,
                        help="comma-separated offered byte rates")
    parser.add_argument("--seeds", type=comma_ints, required=True)
    parser.add_argument("--workloads", type=comma_strings,
                        default=list(WORKLOADS),
                        help="read_heavy,mixed,write_heavy")
    parser.add_argument("--duration-ns", type=positive_int, required=True)
    parser.add_argument("--peak-byte-rate", type=positive_int, required=True)
    parser.add_argument("--unthrottled-byte-rate", type=positive_int,
                        required=True)
    parser.add_argument("--request-bytes", type=positive_int, default=1048576)
    parser.add_argument("--queue-depth", type=positive_int, default=128)
    parser.add_argument("--dominant-tau-ns", type=float, required=True)
    parser.add_argument("--maximum-queue-slope", type=float, default=0.5)
    parser.add_argument("--maximum-temperature-slope", type=float, default=0.2)
    parser.add_argument("--maximum-served-rate-error", type=float, default=0.05)
    parser.add_argument("--minimum-analysis-ns", type=positive_int,
                        default=2_000_000_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    progress_path = args.output_root / "campaign-progress.json"
    progress: dict[str, Any] = {
        "schema_version": 1,
        "stack_height": args.stack_height,
        "rates": sorted(args.rates),
        "seeds": sorted(args.seeds),
        "workloads": args.workloads,
        "duration_ns": args.duration_ns,
        "peak_byte_rate": args.peak_byte_rate,
        "unthrottled_byte_rate": args.unthrottled_byte_rate,
        "dominant_tau_ns": args.dominant_tau_ns,
        "stationarity_criteria": {
            "maximum_queue_slope_requests_per_s": args.maximum_queue_slope,
            "maximum_hotspot_slope_c_per_s": args.maximum_temperature_slope,
            "maximum_served_rate_relative_error": args.maximum_served_rate_error,
            "minimum_analysis_ns": args.minimum_analysis_ns,
        },
        "completed": [],
        "skipped_existing": [],
        "failed": [],
    }
    write_json(progress_path, progress)

    for workload in args.workloads:
        runner_workload, read_ratio = WORKLOADS[workload]
        for rate in sorted(args.rates):
            for seed in sorted(args.seeds):
                run_id = (
                    f"bw-g1-{args.stack_height}hi-{workload}-"
                    f"rate{rate}-seed{seed}"
                )
                directory = (args.output_root / f"workload-{workload}" /
                             f"rate-{rate:012d}" / f"seed-{seed}")
                directory.mkdir(parents=True, exist_ok=True)
                required = [
                    directory / "experiment-manifest.json",
                    directory / "runner-result.json",
                    directory / "request-summary.json",
                    directory / "source-power-summary.json",
                    directory / "stationarity.json",
                    directory / "closed-loop-analysis.json",
                ]
                if all(path.is_file() for path in required):
                    progress["skipped_existing"].append(run_id)
                    write_json(progress_path, progress)
                    continue

                runner_command = [
                    str(args.runner),
                    "--device-profile", str(args.device_profile),
                    "--package-profile", str(args.package_profile),
                    "--model", str(args.rom),
                    "--output", str(directory),
                    "--duration-ns", str(args.duration_ns),
                    "--offered-byte-rate", str(rate),
                    "--peak-byte-rate", str(args.peak_byte_rate),
                    "--request-bytes", str(args.request_bytes),
                    "--queue-depth", str(args.queue_depth),
                    "--seed", str(seed),
                    "--arrival-mode", "periodic",
                    "--workload", runner_workload,
                    "--pattern", "random",
                ]
                manifest_command = [
                    "python3", str(args.manifest_tool),
                    "--output", str(directory / "experiment-manifest.json"),
                    "--repo", str(args.repo),
                    "--device-profile", str(args.device_profile),
                    "--package-profile", str(args.package_profile),
                    "--thermal-model", str(args.rom),
                    "--model-kind", "rom",
                    "--thermal-mode", "package_rc",
                    "--thermal-stage", "active",
                    "--thermal-clock", "model_time_replay",
                    "--three-d-ice-version", "4.0",
                    "--three-d-ice-commit",
                    "e0bb6850c5e446363e26936586d625270c87f224",
                    "--command", shlex.join(runner_command),
                    "--phase2",
                    "--experiment-id", run_id,
                    "--workload", workload,
                    "--seed", str(seed),
                    "--offered-byte-rate", str(rate),
                    "--arrival-mode", "periodic",
                    "--queue-depth", str(args.queue_depth),
                    "--read-ratio", str(read_ratio),
                    "--address-pattern", "random",
                    "--stack-height", str(args.stack_height),
                    "--peak-byte-rate", str(args.peak_byte_rate),
                    "--unthrottled-byte-rate", str(args.unthrottled_byte_rate),
                    "--evidence-grid", str(args.evidence_grid),
                ]
                analyzer_command = [
                    "python3", str(args.analyzer),
                    "--timeline", str(directory / "package-thermal-timeline.csv"),
                    "--experiment-manifest",
                    str(directory / "experiment-manifest.json"),
                    "--rom", str(args.rom),
                    "--output-dir", str(directory),
                    "--dominant-tau-ns", str(args.dominant_tau_ns),
                    "--minimum-analysis-ns", str(args.minimum_analysis_ns),
                    "--maximum-queue-slope", str(args.maximum_queue_slope),
                    "--maximum-temperature-slope",
                    str(args.maximum_temperature_slope),
                    "--maximum-served-rate-error",
                    str(args.maximum_served_rate_error),
                ]
                try:
                    log_path = directory / "run.log"
                    run(manifest_command, log_path)
                    run(runner_command, log_path)
                    run(analyzer_command, log_path)
                except (OSError, subprocess.CalledProcessError) as error:
                    progress["failed"].append({"run_id": run_id,
                                               "error": str(error)})
                    progress["last_update_utc"] = datetime.datetime.now(
                        datetime.timezone.utc).isoformat()
                    write_json(progress_path, progress)
                    raise
                progress["completed"].append(run_id)
                progress["last_update_utc"] = datetime.datetime.now(
                    datetime.timezone.utc).isoformat()
                write_json(progress_path, progress)
    progress["finished_utc"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    write_json(progress_path, progress)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

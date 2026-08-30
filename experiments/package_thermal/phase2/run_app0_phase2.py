#!/usr/bin/env python3
"""Run the Phase-II CUDA microbenchmark package-thermal stage matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import time


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_case(
    *,
    name: str,
    stage: str,
    source_root: pathlib.Path,
    build_dir: pathlib.Path,
    bpftime_build_dir: pathlib.Path,
    attach_loader: pathlib.Path,
    probe: pathlib.Path,
    inputs_root: pathlib.Path,
    output_root: pathlib.Path,
    iterations: int,
) -> dict:
    case_dir = output_root / name
    case_dir.mkdir(parents=True, exist_ok=False)
    input_name = "g1-8hi-app-read-only" if stage in {"off", "read_only"} else f"g1-8hi-app-{stage}"
    input_dir = inputs_root / input_name
    device = input_dir / "device-8hi.json"
    package = input_dir / (
        "package-8hi-read_only.json" if stage in {"off", "read_only"}
        else f"package-8hi-{stage}.json"
    )
    model = input_dir / "rom-8hi-runtime.json"
    result_path = case_dir / "run.json"
    command = [
        str(build_dir / "benchmarks/cuda/hbf_microbench"),
        "--pattern", "mixed_rw",
        "--mode", "baseline" if name == "baseline" else "timing",
        "--profile", str(device),
        "--report-dir", str(case_dir),
        "--backing-dir", "/tmp",
        "--bytes", str(8 * 1024 * 1024),
        "--iterations", str(iterations),
        "--seed", "21001",
        "--output", str(result_path),
    ]
    if stage != "off":
        command += [
            "--package-thermal-stage", stage,
            "--package-thermal-profile", str(package),
            "--package-thermal-model", str(model),
        ]
    environment = os.environ.copy()
    environment["HBFSIM_DAEMON_PATH"] = str(build_dir / "hbfsimd")
    if name != "baseline":
        environment.update({
            "HBFSIM_BUILD_DIR": str(build_dir),
            "HBFSIM_BPFTIME_BUILD_DIR": str(bpftime_build_dir),
            "HBFSIM_BPFTIME_LOADER": str(attach_loader),
            "HBFSIM_BPFTIME_PROBE": str(probe),
            "HBFSIM_COVERAGE_PATH": str(case_dir / "coverage.jsonl"),
            "HBFSIM_PASS_MANIFEST_PATH": str(case_dir / "pass-manifests.jsonl"),
        })
        command = [str(source_root / "scripts/run_with_bpftime.sh"), "--", *command]
    started = time.time_ns()
    completed = subprocess.run(
        command,
        cwd=source_root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
        check=False,
    )
    (case_dir / "stdout.txt").write_text(completed.stdout)
    (case_dir / "stderr.txt").write_text(completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(f"{name} failed with exit code {completed.returncode}: {completed.stderr[-2000:]}")
    result = json.loads(result_path.read_text())
    result.update({
        "case": name,
        "requested_package_thermal_stage": stage,
        "command": command,
        "started_unix_ns": started,
        "finished_unix_ns": time.time_ns(),
        "artifacts": {
            "device_profile": {"path": str(device), "sha256": sha256(device)},
            "executable": {
                "path": str(build_dir / "benchmarks/cuda/hbf_microbench"),
                "sha256": sha256(build_dir / "benchmarks/cuda/hbf_microbench"),
            },
        },
    })
    if stage != "off":
        result["artifacts"].update({
            "package_profile": {"path": str(package), "sha256": sha256(package)},
            "rom": {"path": str(model), "sha256": sha256(model)},
        })
    result_path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=pathlib.Path)
    parser.add_argument("--build-dir", required=True, type=pathlib.Path)
    parser.add_argument("--bpftime-build-dir", required=True, type=pathlib.Path)
    parser.add_argument("--attach-loader", required=True, type=pathlib.Path)
    parser.add_argument("--probe", required=True, type=pathlib.Path)
    parser.add_argument("--inputs-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--iterations", type=int, default=4096)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases = []
    for name, stage in (
        ("baseline", "off"),
        ("package-thermal-off", "off"),
        ("read-only", "read_only"),
        ("shadow", "shadow"),
        ("active", "active"),
    ):
        cases.append(run_case(
            name=name,
            stage=stage,
            source_root=args.source_root.resolve(),
            build_dir=args.build_dir.resolve(),
            bpftime_build_dir=args.bpftime_build_dir.resolve(),
            attach_loader=args.attach_loader.resolve(),
            probe=args.probe.resolve(),
            inputs_root=args.inputs_root.resolve(),
            output_root=args.output.resolve(),
            iterations=args.iterations,
        ))
    checksums = {case["checksum"] for case in cases}
    summary = {
        "schema_version": 1,
        "campaign": "APP-0 CUDA microbenchmark package-thermal stage matrix",
        "cases": cases,
        "correctness": {
            "all_checksums_match": len(checksums) == 1,
            "checksum": next(iter(checksums)) if len(checksums) == 1 else None,
        },
    }
    (args.output / "app0-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["correctness"]["all_checksums_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

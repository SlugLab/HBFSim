#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]


def parse_size(text: str) -> int:
    suffixes = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    value = text.strip().upper()
    multiplier = suffixes.get(value[-1:], 1)
    if multiplier != 1:
        value = value[:-1]
    parsed = int(value) * multiplier
    if parsed <= 0:
        raise argparse.ArgumentTypeError("size must be positive")
    return parsed


def derived_capacity_profile(source: pathlib.Path, destination: pathlib.Path,
                             logical_bytes: int, cache_bytes: int) -> None:
    profile = json.loads(source.read_text())
    if profile["capacity_bytes"] < logical_bytes:
        raise RuntimeError("named profile capacity is smaller than logical dataset")
    profile["name"] = f'{profile["name"]}-capacity-{cache_bytes}'
    profile["hbm_cache_bytes"] = cache_bytes
    # Correctness/capacity proof uses real MQSim routing without magnifying its
    # service time. Named-profile latency comparisons are separate matrix cases.
    profile["time_scale"] = 1
    destination.write_text(json.dumps(profile, indent=2) + "\n")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def coverage_summary(path: pathlib.Path) -> dict[str, int]:
    decisions = [json.loads(line) for line in path.read_text().splitlines()
                 if line.strip()]
    return {
        "decisions": len(decisions),
        "modeled_launches": sum(item.get("modeled") is True for item in decisions),
        "unsafe_launches": sum(item.get("allowed") is False for item in decisions),
    }


def run_case(binary: pathlib.Path, build: pathlib.Path, bpftime_build: pathlib.Path,
             profile: pathlib.Path, report_root: pathlib.Path, pattern: str,
             mode: str, logical_bytes: int, iterations: int,
             backing_dir: pathlib.Path) -> dict:
    case_dir = report_root / f"{pattern}-{mode}"
    case_dir.mkdir(parents=True)
    output = case_dir / "run.json"
    command = [str(binary), "--pattern", pattern, "--mode", mode,
               "--profile", str(profile), "--report-dir", str(case_dir),
               "--backing-dir", str(backing_dir), "--bytes", str(logical_bytes),
               "--iterations", str(iterations), "--seed", "0",
               "--output", str(output)]
    environment = os.environ.copy()
    environment["HBFSIM_DAEMON_PATH"] = str(build / "hbfsimd")
    coverage = case_dir / "coverage.jsonl"
    manifest = case_dir / "pass-manifests.jsonl"
    if mode != "baseline":
        environment.update({
            "HBFSIM_BUILD_DIR": str(build),
            "HBFSIM_BPFTIME_BUILD_DIR": str(bpftime_build),
            "HBFSIM_COVERAGE_PATH": str(coverage),
            "HBFSIM_PASS_MANIFEST_PATH": str(manifest),
            "HBFSIM_BPFTIME_PROBE": str(build / "microbench_probe.bpf.o"),
        })
        command = [str(ROOT / "scripts/run_with_bpftime.sh"), "--", *command]
    subprocess.run(command, cwd=ROOT, env=environment, check=True,
                   timeout=240)
    result = json.loads(output.read_text())
    result["cache_bytes"] = json.loads(profile.read_text())["hbm_cache_bytes"]
    result["profile_sha256"] = sha256(profile)
    result["executable_sha256"] = sha256(binary)
    result["command"] = command
    if mode != "baseline":
        result["coverage"] = coverage_summary(coverage)
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=pathlib.Path,
                        default=pathlib.Path(os.environ.get(
                            "HBFSIM_BUILD_DIR", ROOT / "build")))
    parser.add_argument("--bpftime-build-dir", type=pathlib.Path,
                        default=pathlib.Path(os.environ.get(
                            "HBFSIM_BPFTIME_BUILD_DIR",
                            ROOT / "build-bpftime-hbfsim")))
    parser.add_argument("--profile", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--over-vram", action="store_true")
    parser.add_argument("--logical-bytes", type=parse_size, default=8 * 1024**2)
    parser.add_argument("--cache-bytes", type=parse_size, default=64 * 1024**2)
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--backing-dir", type=pathlib.Path,
                        default=pathlib.Path("/mnt/disk2"))
    args = parser.parse_args()

    build = args.build_dir.resolve()
    bpftime_build = args.bpftime_build_dir.resolve()
    binary = build / "benchmarks/cuda/hbf_microbench"
    if not binary.is_file():
        raise RuntimeError(f"microbenchmark is not built: {binary}")
    if not args.backing_dir.is_dir() or not os.access(args.backing_dir, os.W_OK):
        args.backing_dir = pathlib.Path(tempfile.gettempdir())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report_root = args.output.parent / (args.output.stem + "-artifacts")
    report_root.mkdir(parents=True, exist_ok=True)
    capacity_profile = report_root / "capacity-profile.json"
    derived_capacity_profile(args.profile.resolve(), capacity_profile,
                             args.logical_bytes, args.cache_bytes)

    if args.over_vram:
        matrix = [("random", "capacity")]
        iterations = args.iterations or 128
    elif args.all:
        matrix = [(pattern, mode)
                  for pattern in ("sequential", "random", "strided",
                                  "pointer_chase", "mixed_rw")
                  for mode in ("baseline", "timing", "reference", "fast",
                               "hybrid", "capacity")]
        iterations = args.iterations or 256
    else:
        matrix = [("sequential", "baseline"), ("sequential", "fast")]
        iterations = args.iterations or 64

    cases = []
    for pattern, mode in matrix:
        selected_profile = capacity_profile if mode == "capacity" else args.profile.resolve()
        cases.append(run_case(binary, build, bpftime_build, selected_profile,
                              report_root, pattern, mode, args.logical_bytes,
                              iterations, args.backing_dir))
    summary = {
        "schema_version": 1,
        "gpu": subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader"], text=True, capture_output=True,
            check=True).stdout.strip(),
        "cases": cases,
    }
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

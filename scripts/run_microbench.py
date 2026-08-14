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
    exact = [item for item in decisions
             if item.get("admitted_fidelity") == "exact"]
    return {
        "decisions": len(decisions),
        "modeled_launches": sum(item.get("modeled") is True for item in decisions),
        "unsafe_launches": sum(item.get("allowed") is False for item in decisions),
        "exact_launches": len(exact),
        "post_run_exact_launches": sum(
            item.get("post_run_validation_passed") is True for item in exact),
        "future_faults": sum(item.get("future_faults", 0) for item in exact),
        "future_leaks": sum(item.get("future_leaked", 0) for item in exact),
        "tma_faults": sum(item.get("tma_faults", 0) for item in exact),
        "tma_leaks": sum(item.get("tma_leaked", 0) for item in exact),
        "tma_stale_generations": sum(
            item.get("tma_stale_generations", 0) for item in exact),
    }


def exact_runtime_artifacts(profile_path: pathlib.Path) -> tuple[dict, dict]:
    if profile_path.is_symlink() or not profile_path.is_file():
        raise RuntimeError("exact profile must be a regular non-symlink file")
    profile_path = profile_path.resolve()
    profile = json.loads(profile_path.read_text())
    if profile.get("schema_version") != 2 or \
            profile.get("validation", {}).get("status") != "passed":
        raise RuntimeError("exact profile must be schema v2 and independently passed")
    conditions = profile.get("conditions", {})
    cluster = conditions.get("cluster_shape", {})
    if conditions.get("cache_condition") != "warm_l2" or \
            conditions.get("concurrency_condition") != "exclusive_process" or \
            cluster != {"x": 1, "y": 1, "z": 1}:
        raise RuntimeError(
            "microbenchmark exact mode requires warm_l2, exclusive_process, "
            "and cluster 1x1x1")
    artifacts = profile.get("runtime_artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
            "bundle_root", "prepatched_ptx_dir", "pass_manifest"}:
        raise RuntimeError("exact profile runtime_artifacts are incomplete")
    resolved = {}
    for name, value in artifacts.items():
        path = pathlib.Path(value)
        if not path.is_absolute() or path.is_symlink():
            raise RuntimeError(f"unsafe exact runtime artifact: {name}")
        path = path.resolve()
        if name == "pass_manifest":
            valid = path.is_file()
        else:
            valid = path.is_dir()
        if not valid:
            raise RuntimeError(f"missing exact runtime artifact: {name}")
        resolved[name] = path
    return profile, resolved


def run_case(binary: pathlib.Path, build: pathlib.Path, bpftime_build: pathlib.Path,
             profile: pathlib.Path, report_root: pathlib.Path, pattern: str,
             mode: str, logical_bytes: int, iterations: int,
             backing_dir: pathlib.Path,
             exact_profile: pathlib.Path | None = None,
             exact_artifacts: dict[str, pathlib.Path] | None = None) -> dict:
    case_dir = report_root / f"{pattern}-{mode}"
    case_dir.mkdir(parents=True)
    output = case_dir / "run.json"
    command = [str(binary), "--pattern", pattern, "--mode", mode,
               "--profile", str(profile), "--report-dir", str(case_dir),
               "--backing-dir", str(backing_dir), "--bytes", str(logical_bytes),
               "--iterations", str(iterations), "--seed", "0",
               "--output", str(output)]
    if mode == "exact":
        if exact_profile is None or exact_artifacts is None:
            raise RuntimeError("internal error: exact case lacks artifacts")
        command.extend(["--exact-profile", str(exact_profile),
                        "--exact-cache", "warm_l2",
                        "--exact-cluster-x", "1",
                        "--exact-cluster-y", "1",
                        "--exact-cluster-z", "1"])
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
        wrapper = [str(ROOT / "scripts/run_with_bpftime.sh")]
        if mode == "exact":
            environment["HBFSIM_PRESTAGED_PASS_MANIFEST_PATH"] = str(
                exact_artifacts["pass_manifest"])
            wrapper.extend([
                "--exact-profile", str(exact_profile),
                "--exact-bundle-dir", str(exact_artifacts["bundle_root"]),
                "--prepatched-ptx-dir",
                str(exact_artifacts["prepatched_ptx_dir"]),
            ])
        command = [*wrapper, "--", *command]
    subprocess.run(command, cwd=ROOT, env=environment, check=True,
                   timeout=240)
    result = json.loads(output.read_text())
    result["cache_bytes"] = json.loads(profile.read_text())["hbm_cache_bytes"]
    result["profile_sha256"] = sha256(profile)
    result["executable_sha256"] = sha256(binary)
    result["command"] = command
    if mode != "baseline":
        result["coverage"] = coverage_summary(coverage)
        if mode == "exact":
            exact_coverage = result["coverage"]
            if exact_coverage["exact_launches"] == 0 or \
                    exact_coverage["post_run_exact_launches"] != \
                    exact_coverage["exact_launches"] or \
                    any(exact_coverage[name] != 0 for name in (
                        "unsafe_launches", "future_faults", "future_leaks",
                        "tma_faults", "tma_leaks", "tma_stale_generations")):
                raise RuntimeError("exact post-run coverage gate failed")
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
    parser.add_argument("--profile", type=pathlib.Path,
                        default=ROOT / "configs/profiles/nominal.json")
    parser.add_argument("--exact-profile", type=pathlib.Path)
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

    if args.exact_profile is not None and (args.all or args.over_vram):
        raise RuntimeError("--exact-profile cannot be combined with --all or --over-vram")

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

    exact_profile = None
    exact_artifacts = None
    if args.exact_profile is not None:
        _, exact_artifacts = exact_runtime_artifacts(args.exact_profile)
        exact_profile = args.exact_profile.resolve()
        matrix = [("sequential", "baseline"), ("sequential", "exact"),
                  ("mixed_rw", "baseline"), ("mixed_rw", "exact")]
        iterations = args.iterations or 64
    elif args.over_vram:
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
                              iterations, args.backing_dir, exact_profile,
                              exact_artifacts))
    summary = {
        "schema_version": 1,
        "gpu": subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader"], text=True, capture_output=True,
            check=True).stdout.strip(),
        "cases": cases,
    }
    if exact_profile is not None:
        summary["exact_profile_sha256"] = sha256(exact_profile)
        by_pattern = {(case["pattern"], case["mode"]): case
                      for case in cases}
        summary["exact_pairs_match"] = all(
            by_pattern[(pattern, "baseline")]["checksum"] ==
            by_pattern[(pattern, "exact")]["checksum"]
            for pattern in ("sequential", "mixed_rw"))
        if not summary["exact_pairs_match"]:
            raise RuntimeError("native and exact microbenchmark outputs differ")
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

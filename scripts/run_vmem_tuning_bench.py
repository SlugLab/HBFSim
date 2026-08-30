#!/usr/bin/env python3
"""Run exact CD8P-vmem timing breakpoints through automatic GPU rewriting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXPECTED_BREAKPOINTS = (1, 4, 16, 64, 256, 512)


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_curve(profile_path: pathlib.Path) -> dict[int, int]:
    profile = json.loads(profile_path.read_text())
    curve = profile.get("empirical_vmem", {}).get("read_curve", [])
    result = {
        int(point["pages"]): int(point["cumulative_ns"])
        for point in curve
    }
    if tuple(result) != EXPECTED_BREAKPOINTS:
        raise RuntimeError("profile does not contain the six CD8P breakpoints")
    return result


def coverage_summary(path: pathlib.Path) -> dict[str, int]:
    decisions = [
        json.loads(line) for line in path.read_text().splitlines()
        if line.strip()
    ]
    return {
        "decisions": len(decisions),
        "modeled_launches": sum(
            decision.get("modeled") is True for decision in decisions),
        "unsafe_launches": sum(
            decision.get("allowed") is False for decision in decisions),
    }


def benchmark_command(binary: pathlib.Path, profile: pathlib.Path,
                      case_dir: pathlib.Path, mode: str,
                      pages: int) -> list[str]:
    output = case_dir / f"{mode}.json"
    report_dir = case_dir / f"{mode}-reports"
    return [
        str(binary), "--mode", mode, "--pages", str(pages),
        "--profile", str(profile), "--report-dir", str(report_dir),
        "--output", str(output),
    ]


def make_manifest(build: pathlib.Path, bpftime_build: pathlib.Path,
                  profile: pathlib.Path, output: pathlib.Path,
                  curve: dict[int, int]) -> dict:
    binary = build / "benchmarks/cuda/hbf_vmem_tuning_bench"
    wrapper = ROOT / "scripts/run_with_bpftime.sh"
    artifact_root = output.parent / f"{output.stem}-artifacts"
    cases = []
    for pages in EXPECTED_BREAKPOINTS:
        case_dir = artifact_root / f"pages-{pages}"
        baseline = benchmark_command(
            binary, profile, case_dir, "baseline", pages)
        tuned = benchmark_command(binary, profile, case_dir, "tuned", pages)
        cases.append({
            "pages": pages,
            "expected_modeled_ns": curve[pages],
            "baseline_command": baseline,
            "tuned_command": [str(wrapper), "--", *tuned],
            "automatic_bpftime": True,
            "bpftime_build_dir": str(bpftime_build),
        })
    return {
        "schema_version": 1,
        "dry_run": True,
        "profile": str(profile),
        "probe": str(build / "vmem_tuning_probe.bpf.o"),
        "breakpoints": list(EXPECTED_BREAKPOINTS),
        "policies": {
            "exact_checksum": True,
            "modeled_total_equality": True,
            "zero_unsafe_launches": True,
        },
        "cases": cases,
    }


def run_case(case: dict, build: pathlib.Path, bpftime_build: pathlib.Path,
             profile: pathlib.Path, output: pathlib.Path) -> dict:
    pages = case["pages"]
    case_dir = output.parent / f"{output.stem}-artifacts/pages-{pages}"
    case_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["HBFSIM_DAEMON_PATH"] = str(build / "hbfsimd")

    subprocess.run(case["baseline_command"], cwd=ROOT, env=environment,
                   check=True, timeout=240)
    baseline = json.loads((case_dir / "baseline.json").read_text())

    coverage = case_dir / "coverage.jsonl"
    manifest = case_dir / "pass-manifests.jsonl"
    tuned_environment = dict(environment)
    tuned_environment.update({
        "HBFSIM_BUILD_DIR": str(build),
        "HBFSIM_BPFTIME_BUILD_DIR": str(bpftime_build),
        "HBFSIM_BPFTIME_PROBE": str(build / "vmem_tuning_probe.bpf.o"),
        "HBFSIM_COVERAGE_PATH": str(coverage),
        "HBFSIM_PASS_MANIFEST_PATH": str(manifest),
    })
    subprocess.run(case["tuned_command"], cwd=ROOT, env=tuned_environment,
                   check=True, timeout=240)
    tuned = json.loads((case_dir / "tuned.json").read_text())
    coverage_result = coverage_summary(coverage)

    failures = []
    if baseline["checksum"] != tuned["checksum"]:
        failures.append("baseline/tuned checksum mismatch")
    if baseline["checksum"] != baseline["expected_checksum"] or \
            tuned["checksum"] != tuned["expected_checksum"]:
        failures.append("GPU/CPU checksum mismatch")
    if tuned["modeled_ns"] != case["expected_modeled_ns"]:
        failures.append("modeled total differs from source P50")
    if tuned["requests"]["fast"] != pages:
        failures.append("fast request count differs from pages")
    if tuned["requests"]["reference"] != 0:
        failures.append("reference request count is nonzero")
    if coverage_result["modeled_launches"] != 1:
        failures.append("modeled launch count is not one")
    if coverage_result["unsafe_launches"] != 0:
        failures.append("unsafe launch count is nonzero")
    if failures:
        raise RuntimeError(
            f"pages={pages} validation failed: " + "; ".join(failures))
    return {
        "pages": pages,
        "expected_modeled_ns": case["expected_modeled_ns"],
        "baseline": baseline,
        "tuned": tuned,
        "coverage": coverage_result,
        "baseline_command": case["baseline_command"],
        "tuned_command": case["tuned_command"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--build-dir", type=pathlib.Path,
        default=pathlib.Path(os.environ.get(
            "HBFSIM_BUILD_DIR", ROOT / "build")))
    parser.add_argument(
        "--bpftime-build-dir", type=pathlib.Path,
        default=pathlib.Path(os.environ.get(
            "HBFSIM_BPFTIME_BUILD_DIR", ROOT / "build-bpftime-hbfsim")))
    parser.add_argument("--profile", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    build = args.build_dir.resolve()
    bpftime_build = args.bpftime_build_dir.resolve()
    profile = args.profile.resolve()
    output = args.output.resolve()
    curve = read_curve(profile)
    manifest = make_manifest(build, bpftime_build, profile, output, curve)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        output.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest, indent=2))
        return 0

    binary = build / "benchmarks/cuda/hbf_vmem_tuning_bench"
    if not binary.is_file():
        raise RuntimeError(f"benchmark is not built: {binary}")
    required = [
        build / "hbfsimd", build / "vmem_tuning_probe.bpf.o",
        build / "hbfsim_bpftime_attach_loader",
        build / "libhbfsim_launch_gate.so", build / "libptxpass_hbf.so",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError("required runtime artifacts are missing: " +
                           ", ".join(missing))

    cases = [
        run_case(case, build, bpftime_build, profile, output)
        for case in manifest["cases"]
    ]
    gpu = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,name,uuid,driver_version,memory.total",
         "--format=csv,noheader"], text=True, capture_output=True,
        check=True).stdout.strip()
    summary = {
        "schema_version": 1,
        "gpu": gpu,
        "profile": str(profile),
        "profile_sha256": sha256(profile),
        "breakpoints": list(EXPECTED_BREAKPOINTS),
        "policies": manifest["policies"],
        "cases": cases,
    }
    output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

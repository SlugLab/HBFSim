#!/usr/bin/env python3

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def gpu_is_sm120() -> bool:
    completed = subprocess.run(
        ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
        text=True, capture_output=True,
    )
    return completed.returncode == 0 and any(
        line.strip() == "12.0" for line in completed.stdout.splitlines()
    )


def capacity_profile(source: pathlib.Path, destination: pathlib.Path) -> None:
    profile = json.loads(source.read_text())
    profile.update({
        "name": "sm120-future-live",
        "hbm_cache_bytes": 32768,
        "time_scale": 1,
        "queue_depth": 8,
    })
    destination.write_text(json.dumps(profile, indent=2) + "\n")


def run_case(binary: pathlib.Path, environment: dict[str, str], run: pathlib.Path,
             profile: pathlib.Path, mode: str, backend: str) -> dict:
    name = f"{mode}-{backend}"
    output = run / f"{name}.json"
    command = [
        str(binary), "--mode", mode, "--backend", backend,
        "--profile", str(profile), "--report-dir", str(run / f"{name}-report"),
        "--backing-dir", str(run), "--output", str(output),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, env=environment, text=True,
        capture_output=True, timeout=90,
    )
    if completed.returncode == 77:
        raise RuntimeError("benchmark reported no CC 12.0 GPU after live precheck")
    require(completed.returncode == 0,
            f"{name} failed ({completed.returncode}): {completed.stderr.strip()}")
    result = json.loads(output.read_text())
    result["command"] = command
    return result


def cases_by_name(report: dict) -> dict[str, dict]:
    return {item["name"]: item for item in report["cases"]}


def validate(native: dict, synchronous: dict, future: dict,
             capacity: dict) -> dict:
    reports = [native, synchronous, future, capacity]
    expected_names = {
        "load", "store_fence", "atomic", "vector_branch_true",
        "vector_branch_false",
    }
    for report in reports:
        cases = cases_by_name(report)
        require(set(cases) == expected_names,
                f"case coverage differs in {report['mode']}/{report['backend']}")
        require(report["coverage"]["unsafe_launches"] == 0,
                "unsafe launch was observed")
        require(report["future_totals"]["leaked"] == 0 and
                report["future_totals"]["faults"] == 0,
                "future leak/fault was observed")

    native_cases = cases_by_name(native)
    for report in reports[1:]:
        for name, case in cases_by_name(report).items():
            require(case["output"] == native_cases[name]["output"],
                    f"byte result differs for {name} in "
                    f"{report['mode']}/{report['backend']}")

    sync_cases = cases_by_name(synchronous)
    future_cases = cases_by_name(future)
    capacity_cases = cases_by_name(capacity)
    modeled = ("load", "store_fence", "atomic", "vector_branch_true")
    overlap = {}
    for name in modeled:
        current = future_cases[name]
        stamps = current["timestamps"]
        require(stamps["issue"] < stamps["independent_end"] <
                stamps["dependency_wait_end"],
                f"future timestamps do not bracket independent work for {name}")
        require(current["futures"]["issued"] > 0 and
                current["futures"]["issued"] == current["futures"]["drained"],
                f"timing future conservation failed for {name}")
        require(capacity_cases[name]["futures"]["issued"] > 0 and
                capacity_cases[name]["futures"]["issued"] ==
                    capacity_cases[name]["futures"]["drained"],
                f"capacity future conservation failed for {name}")
        require(current["wait_tail_ns"] > sync_cases[name]["wait_tail_ns"],
                f"wait was not moved after independent work for {name}")
        future_overlap = stamps["independent_end"] - stamps["issue"]
        synchronous_overlap = 0
        require(future_overlap > synchronous_overlap,
                f"no issue/use overlap was measured for {name}")
        overlap[name] = {
            "future_overlap_ns": future_overlap,
            "synchronous_control_overlap_ns": synchronous_overlap,
            "future_wait_tail_ns": current["wait_tail_ns"],
            "synchronous_wait_tail_ns": sync_cases[name]["wait_tail_ns"],
        }

    false_case = future_cases["vector_branch_false"]
    require(false_case["futures"]["issued"] == 0 and
            false_case["futures"]["drained"] == 0,
            "predicated/branched false path created a future")
    require(capacity_cases["load"]["requests"]["submitted"] > 0 and
            capacity_cases["vector_branch_true"]["requests"]["submitted"] > 0,
            "capacity cold/warm accesses were not submitted")
    return {
        "schema_version": 1,
        "status": "passed",
        "bit_exact": True,
        "unsafe_launches": 0,
        "future_leaks": 0,
        "capacity_sequence": ["cold_load", "same_page_warm_operations"],
        "overlap": overlap,
        "reports": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    if not gpu_is_sm120():
        print("SM120 future live proof skipped: no CC 12.0 GPU", file=sys.stderr)
        return 77

    build = args.build_dir.resolve()
    binary = build / "benchmarks/cuda/sm120_future_bench"
    gate = build / "libhbfsim_launch_gate.so"
    daemon = build / "hbfsimd"
    require(binary.is_file() and gate.is_file() and daemon.is_file(),
            "Stage 2 live targets are not built")

    with tempfile.TemporaryDirectory(prefix="hbfsim-sm120-future-") as raw:
        run = pathlib.Path(raw)
        profile = run / "capacity-profile.json"
        capacity_profile(ROOT / "configs/profiles/nominal.json", profile)
        environment = os.environ.copy()
        system_driver = "/usr/lib/x86_64-linux-gnu"
        cuda_lib = "/usr/local/cuda-13.0/targets/x86_64-linux/lib"
        environment["LD_LIBRARY_PATH"] = ":".join(filter(None, (
            system_driver, cuda_lib, environment.get("LD_LIBRARY_PATH", ""))))
        environment["LD_PRELOAD"] = ":".join(filter(None, (
            str(gate), environment.get("LD_PRELOAD", ""))))
        environment["HBFSIM_DAEMON_PATH"] = str(daemon)
        environment["HBFSIM_PASS_MANIFEST_PATH"] = str(run / "manifest.jsonl")
        environment["HBFSIM_COVERAGE_PATH"] = str(run / "coverage.jsonl")

        resolved = subprocess.run(
            ["ldd", str(binary)], env=environment, text=True,
            capture_output=True, check=True,
        ).stdout
        require("libcuda.so.1 => /usr/lib/" in resolved,
                "live benchmark resolved the fake build-tree CUDA driver")

        native = run_case(binary, environment, run,
                          ROOT / "configs/profiles/nominal.json", "native", "hbm")
        synchronous = run_case(binary, environment, run,
                               ROOT / "configs/profiles/nominal.json",
                               "synchronous", "timing")
        future = run_case(binary, environment, run,
                          ROOT / "configs/profiles/nominal.json",
                          "future", "timing")
        capacity = run_case(binary, environment, run, profile,
                            "future", "capacity")
        summary = validate(native, synchronous, future, capacity)
        manifests = [json.loads(line) for line in
                     (run / "manifest.jsonl").read_text().splitlines() if line]
        require(any(item["async_transform_version"] == "legacy-sync-v1"
                    for item in manifests), "negative control was not legacy sync")
        require(any(item["async_transform_version"] == "sm120-future-v1"
                    for item in manifests), "future transform evidence is missing")
        summary["manifest_records"] = len(manifests)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3

import argparse
import json
import os
import pathlib
import statistics
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
SKIP = 77
DEFAULT_REQUIRED_FREE_MIB = 2048


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def gpu_inventory() -> list[dict[str, str]]:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,compute_cap,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        return []
    rows = []
    for line in completed.stdout.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) == 4:
            rows.append(dict(zip(("index", "cc", "total_mib", "free_mib"),
                                 fields, strict=True)))
    return rows


def compute_processes() -> str:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def write_profile(source: pathlib.Path, destination: pathlib.Path,
                  mode: str) -> None:
    profile = json.loads(source.read_text())
    thermal = profile["thermal_reliability"]
    temperature = 60.0 if mode == "normal" else 85.0
    profile["name"] = f"thermal-timing-live-{mode}"
    thermal["source_identity"] = f"declared-constant-{mode}"
    thermal["constant_gpu_c"] = temperature
    thermal["initial_hbf_junction_c"] = temperature
    destination.write_text(json.dumps(profile, indent=2) + "\n")


def run_trial(binary: pathlib.Path, environment: dict[str, str], run: pathlib.Path,
              profile: pathlib.Path, mode: str, trial: int) -> dict:
    stem = f"{mode}-{trial}"
    output = run / f"{stem}.json"
    report_dir = run / f"{stem}-report"
    command = [
        str(binary), "--mode", "future", "--backend", "timing",
        "--profile", str(profile), "--report-dir", str(report_dir),
        "--backing-dir", str(run), "--output", str(output),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, env=environment, text=True,
        capture_output=True, timeout=90,
    )
    require(completed.returncode == 0,
            f"{stem} failed ({completed.returncode}): "
            f"{completed.stderr.strip()}")
    result = json.loads(output.read_text())
    summary_path = report_dir / "thermal-reliability-summary.json"
    require(summary_path.is_file(), f"{stem} did not publish thermal summary")
    summary = json.loads(summary_path.read_text())
    require(summary["terminal_status"] == "clean",
            f"{stem} ended as {summary['terminal_status']}")
    require(summary["source"]["identity"] == f"declared-constant-{mode}",
            f"{stem} used the wrong declared temperature source")
    expected_ppm = 1_000_000 if mode == "normal" else 900_000
    require(summary["profile"]["light_service_ppm"] == 900_000,
            f"{stem} changed the Light service contract")
    residency_key = f"{mode}_residency_ns"
    require(summary["accounting"][residency_key] > 0,
            f"{stem} never resided in {mode}")
    load = next(case for case in result["cases"] if case["name"] == "load")
    require(load["futures"]["issued"] == load["futures"]["drained"] == 1,
            f"{stem} did not conserve its load future")
    return {
        "mode": mode,
        "trial": trial,
        "service_ppm": expected_ppm,
        "wait_tail_ns": load["wait_tail_ns"],
        "outputs": [case["output"] for case in result["cases"]],
        "summary": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=pathlib.Path, required=True)
    parser.add_argument("--gate", type=pathlib.Path, required=True)
    parser.add_argument("--daemon", type=pathlib.Path, required=True)
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()

    devices = [gpu for gpu in gpu_inventory() if gpu["cc"] == "12.0"]
    if not devices:
        print("SKIP: no CUDA compute-capability 12.0 device", file=sys.stderr)
        return SKIP
    selected = devices[0]
    required = int(os.environ.get("HBFSIM_LIVE_REQUIRE_FREE_MIB",
                                  DEFAULT_REQUIRED_FREE_MIB))
    free = int(selected["free_mib"])
    if free < required:
        print(
            f"SKIP: GPU {selected['index']} has {free} MiB free; "
            f"thermal live gate requires {required} MiB headroom; "
            f"compute processes: {compute_processes() or 'none'}",
            file=sys.stderr,
        )
        return SKIP

    require(args.binary.is_file(), f"live target is missing: {args.binary}")
    require(args.gate.is_file(), f"launch gate is missing: {args.gate}")
    require(args.daemon.is_file(), f"daemon is missing: {args.daemon}")
    require(args.profile.is_file(), f"profile is missing: {args.profile}")
    require(args.repetitions >= 3, "at least three repetitions are required")

    with tempfile.TemporaryDirectory(prefix="hbfsim-thermal-live-") as raw:
        run = pathlib.Path(raw)
        normal_profile = run / "normal.json"
        light_profile = run / "light.json"
        write_profile(args.profile, normal_profile, "normal")
        write_profile(args.profile, light_profile, "light")
        environment = os.environ.copy()
        environment["CUDA_VISIBLE_DEVICES"] = selected["index"]
        environment["LD_LIBRARY_PATH"] = ":".join(filter(None, (
            "/usr/lib/x86_64-linux-gnu",
            "/usr/local/cuda-13.0/targets/x86_64-linux/lib",
            environment.get("LD_LIBRARY_PATH", ""),
        )))
        environment["LD_PRELOAD"] = ":".join(filter(None, (
            str(args.gate.resolve()), environment.get("LD_PRELOAD", ""))))
        environment["HBFSIM_DAEMON_PATH"] = str(args.daemon.resolve())
        environment["HBFSIM_PASS_MANIFEST_PATH"] = str(run / "manifest.jsonl")
        environment["HBFSIM_COVERAGE_PATH"] = str(run / "coverage.jsonl")
        resolved = subprocess.run(
            ["ldd", str(args.binary.resolve())], env=environment, text=True,
            capture_output=True, check=True,
        ).stdout
        require("libcuda.so.1 => /usr/lib/" in resolved,
                "thermal live target resolved the fake CUDA driver")

        trials = []
        for trial in range(args.repetitions):
            trials.append(run_trial(args.binary.resolve(), environment, run,
                                    normal_profile, "normal", trial))
            trials.append(run_trial(args.binary.resolve(), environment, run,
                                    light_profile, "light", trial))

        baseline_outputs = trials[0]["outputs"]
        require(all(item["outputs"] == baseline_outputs for item in trials),
                "Normal and Light produced different output bytes")
        normal_ns = [item["wait_tail_ns"] for item in trials
                     if item["mode"] == "normal"]
        light_ns = [item["wait_tail_ns"] for item in trials
                    if item["mode"] == "light"]
        normal_median = statistics.median(normal_ns)
        light_median = statistics.median(light_ns)
        require(normal_median > 0, "Normal median latency is zero")
        ratio = light_median / normal_median
        require(ratio >= 1.10,
                f"Light/Normal median latency ratio {ratio:.6f} is below 1.10")

        summary = {
            "schema_version": 1,
            "status": "passed",
            "gpu": selected,
            "repetitions": args.repetitions,
            "normal_wait_tail_ns": normal_ns,
            "light_wait_tail_ns": light_ns,
            "normal_median_ns": normal_median,
            "light_median_ns": light_median,
            "light_normal_ratio": ratio,
            "output_bytes_identical": True,
            "outputs": baseline_outputs,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

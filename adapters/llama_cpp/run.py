#!/usr/bin/env python3
"""Run deterministic TinyLlama baseline/timing comparisons on a real GPU."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import time


def generated_text(stdout: str, prompt: str) -> str:
    lines = stdout.splitlines()
    marker = f"> {prompt}"
    for index, line in enumerate(lines):
        if line.strip() == marker:
            for candidate in lines[index + 1 :]:
                value = candidate.strip()
                if value and not value.startswith("["):
                    return value
    raise RuntimeError("llama-cli output did not contain generated text")


def run_once(args: argparse.Namespace, mode: str, destination: pathlib.Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["GGML_CUDA_PDL"] = "0"
    command = [
        str(args.llama_cli), "-m", str(args.model), "-ngl", "99",
        "-p", args.prompt, "-n", str(args.tokens), "-s", "0",
        "--temp", "0", "--single-turn", "--no-display-prompt",
        "--log-disable",
    ]
    config = None
    if mode == "timing":
        profile = json.loads(args.profile.read_text())
        delay_ns = args.delay_ns or int(profile["read_latency_ns"]) * int(profile["time_scale"])
        config = {
            "profile_path": str(args.profile.resolve()),
            "report_dir": str(destination.resolve()),
            "timing_model": args.timing_model,
            "probe_ptx": str((args.hbf_build / "hbfsim_llama_probe.ptx").resolve()),
            "probe_library": str((args.hbf_build / "libhbfsim_llama_probe.so").resolve()),
            "delay_ns": delay_ns,
            "range_mode": "timing",
        }
        config_path = destination / "llama-hbfsim.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        environment["LLAMA_HBFSIM_CONFIG"] = str(config_path)
        environment["HBFSIM_DAEMON_PATH"] = str(args.hbf_build / "hbfsimd")
        gate = str(args.hbf_build / "libhbfsim_launch_gate.so")
        environment["LD_PRELOAD"] = gate + (":" + environment["LD_PRELOAD"] if environment.get("LD_PRELOAD") else "")
        environment["HBFSIM_COVERAGE_PATH"] = str(destination / "coverage.jsonl")
    if args.dry_run:
        return {"mode": mode, "command": command, "config": config}
    start = time.perf_counter()
    completed = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=args.timeout)
    wall = time.perf_counter() - start
    (destination / "stdout.txt").write_text(completed.stdout)
    (destination / "stderr.txt").write_text(completed.stderr)
    if completed.returncode:
        raise RuntimeError(f"llama-cli {mode} failed with {completed.returncode}")
    injections = []
    trace = destination / "llama-injection.jsonl"
    if trace.exists():
        injections = [json.loads(line) for line in trace.read_text().splitlines() if line]
    return {
        "mode": mode,
        "wall_seconds": wall,
        "generated_text": generated_text(completed.stdout, args.prompt),
        "injection_count": len(injections),
        "injected_ns": sum(item["delay_ns"] for item in injections),
        "config": config,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("baseline", "timing", "compare"), default="compare")
    parser.add_argument("--llama-cli", type=pathlib.Path, required=True)
    parser.add_argument("--model", type=pathlib.Path, required=True)
    parser.add_argument("--hbf-build", type=pathlib.Path, required=True)
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--report-dir", type=pathlib.Path, required=True)
    parser.add_argument("--timing-model", choices=("reference", "fast", "hybrid"), default="hybrid")
    parser.add_argument("--delay-ns", type=int, default=0)
    parser.add_argument("--prompt", default="The fastest route to reliable systems is")
    parser.add_argument("--tokens", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.report_dir.mkdir(parents=True, exist_ok=True)
    modes = ("baseline", "timing") if args.mode == "compare" else (args.mode,)
    results = [run_once(args, mode, args.report_dir / mode) for mode in modes]
    manifest = {"schema_version": 1, "results": results}
    if len(results) == 2:
        if results[0]["generated_text"] != results[1]["generated_text"]:
            raise RuntimeError("baseline and timing semantic outputs differ")
        if not args.dry_run and results[1]["injection_count"] == 0:
            raise RuntimeError("timing run produced no GPU delay injections")
        manifest["exact_output_match"] = True
        if not args.dry_run:
            manifest["slowdown"] = results[1]["wall_seconds"] / results[0]["wall_seconds"]
    (args.report_dir / "summary.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

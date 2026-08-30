#!/usr/bin/env python3
"""Run the deterministic Qwen/vLLM Phase-II package-thermal stage matrix."""

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


def lines(path: pathlib.Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for item in path.read_text().splitlines() if item.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=pathlib.Path)
    parser.add_argument("--build-dir", required=True, type=pathlib.Path)
    parser.add_argument("--bpftime-build-dir", required=True, type=pathlib.Path)
    parser.add_argument("--attach-loader", required=True, type=pathlib.Path)
    parser.add_argument("--probe", required=True, type=pathlib.Path)
    parser.add_argument("--plugin-site", required=True, type=pathlib.Path)
    parser.add_argument("--python-bin", required=True, type=pathlib.Path)
    parser.add_argument("--model", required=True, type=pathlib.Path)
    parser.add_argument("--inputs-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--baseline-result", type=pathlib.Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    common = [
        "--model", str(args.model), "--num-prompts", "1",
        "--input-len", "8", "--output-len", "8",
        "--max-model-len", "64", "--max-num-batched-tokens", "64",
        "--gpu-memory-utilization", "0.85", "--seed", "43001",
        "--hbf-parameter-regex",
        r"layers\.0\..*w13_weight$",
        "--hbf-range-bytes", "16384", "--hbf-timing-model", "hybrid",
    ]
    environment = os.environ.copy()
    environment.update({
        "HBFSIM_BUILD_DIR": str(args.build_dir),
        "HBFSIM_BPFTIME_BUILD_DIR": str(args.bpftime_build_dir),
        "HBFSIM_BPFTIME_LOADER": str(args.attach_loader),
        "HBFSIM_BPFTIME_PROBE": str(args.probe),
        "HBFSIM_VLLM_EXTENSION": str(args.build_dir / "libhbfsim_vllm_extension.so"),
        "HBFSIM_DAEMON_PATH": str(args.build_dir / "hbfsimd"),
        "HBFSIM_VLLM_CACHE": "/dev/shm/hbfsim-vllm-live-cache",
        "HBFSIM_TRITON_PTX_STAGE": "/dev/shm/hbfsim-vllm-phase2-ptx-stage",
        "PYTHONPATH": str(args.plugin_site) + (
            ":" + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""
        ),
        "PATH": str(args.python_bin.parent) + ":" + environment.get("PATH", ""),
    })
    cases = []
    for name, stage in (
        ("baseline", "off"),
        ("package-thermal-off", "off"),
        ("read-only", "read_only"),
        ("shadow", "shadow"),
        ("active", "active"),
    ):
        if name == "baseline" and args.baseline_result is not None:
            result = json.loads(args.baseline_result.read_text())
            cases.append({
                "case": "baseline",
                "package_thermal_stage": "off",
                "output_token_ids": result["output_token_ids"],
                "generation_seconds": result["generation_seconds"],
                "output_tokens_per_second": result["output_tokens_per_second"],
                "load_seconds": result["load_seconds"],
                "coverage_decisions": 0,
                "pass_manifests": 0,
                "registration_sha256": None,
                "package_report_sha256": None,
                "reused_result": {
                    "path": str(args.baseline_result.resolve()),
                    "sha256": sha256(args.baseline_result),
                },
            })
            continue
        case = args.output / name
        case.mkdir()
        command = [
            str(args.python_bin), str(args.source_root / "adapters/vllm/run.py"),
            "--mode", "baseline" if name == "baseline" else "timing",
            "--report-dir", str(case), *common,
        ]
        if name != "baseline":
            input_name = "g1-8hi-app-read-only" if stage in {"off", "read_only"} else f"g1-8hi-app-{stage}"
            input_dir = args.inputs_root / input_name
            command += ["--profile", str(input_dir / "device-8hi.json")]
            if stage != "off":
                package = input_dir / (
                    "package-8hi-read_only.json" if stage == "read_only"
                    else f"package-8hi-{stage}.json"
                )
                model = input_dir / "rom-8hi-runtime.json"
                command += [
                    "--package-thermal-stage", stage,
                    "--package-thermal-profile", str(package),
                    "--package-thermal-model", str(model),
                ]
            command = [str(args.source_root / "adapters/vllm/run_timing.sh"), *command[4:]]
        case_env = environment.copy()
        case_env["HBFSIM_COVERAGE_PATH"] = str(case / "coverage.jsonl")
        case_env["HBFSIM_PASS_MANIFEST_PATH"] = str(case / "pass-manifests.jsonl")
        started = time.time_ns()
        completed = subprocess.run(
            command, cwd=args.source_root, env=case_env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=900, check=False,
        )
        (case / "stdout.txt").write_text(completed.stdout)
        (case / "stderr.txt").write_text(completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError(
                f"{name} failed with {completed.returncode}: {completed.stderr[-4000:]}"
            )
        result = json.loads((case / "result.json").read_text())
        item = {
            "case": name,
            "package_thermal_stage": stage,
            "output_token_ids": result["output_token_ids"],
            "generation_seconds": result["generation_seconds"],
            "output_tokens_per_second": result["output_tokens_per_second"],
            "load_seconds": result["load_seconds"],
            "coverage_decisions": lines(case / "coverage.jsonl"),
            "pass_manifests": lines(case / "pass-manifests.jsonl"),
            "registration_sha256": (
                sha256(case / "registration.json")
                if (case / "registration.json").is_file() else None
            ),
            "package_report_sha256": (
                sha256(case / "package-thermal.json")
                if (case / "package-thermal.json").is_file() else None
            ),
            "started_unix_ns": started,
            "finished_unix_ns": time.time_ns(),
            "command": command,
        }
        if (case / "package-thermal.json").is_file():
            thermal = json.loads((case / "package-thermal.json").read_text())
            item["thermal"] = {
                "stage": thermal["thermal_stage"],
                "steps": thermal["thermal_steps"],
                "max_hbf_temperature_c": thermal["max_hbf_temperature_c"],
                "blocked_requests": thermal["thermal_blocked_requests"],
                "light_transitions": thermal["light_transitions"],
                "severe_transitions": thermal["severe_transitions"],
            }
        cases.append(item)
    token_outputs = {json.dumps(case["output_token_ids"]) for case in cases}
    summary = {
        "schema_version": 1,
        "campaign": "APP-2 Qwen/vLLM package-thermal stage matrix",
        "cases": cases,
        "correctness": {"all_output_token_ids_match": len(token_outputs) == 1},
        "claim_limit": (
            "timing-backed selected fused-MoE range; capacity is unsupported "
            "and opaque kernels are not modeled"
        ),
    }
    (args.output / "app2-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["correctness"]["all_output_token_ids_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

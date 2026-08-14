#!/usr/bin/env python3
"""Run deterministic TinyLlama baseline/timing comparisons on a real GPU."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import time
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[2]


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


def exact_result_boundary(mode: str) -> dict[str, Any]:
    if mode != "exact":
        return {}
    return {
        "exact_scope": "one_shot_sideband_probe",
        "model_graph_fidelity": "native",
        "model_storage_registered": False,
    }


def dry_run_result(
    mode: str, command: list[str], config: dict[str, Any] | None
) -> dict[str, Any]:
    result = {"mode": mode, "command": command, "config": config}
    result.update(exact_result_boundary(mode))
    return result


def exact_runtime(profile_path: pathlib.Path) -> dict[str, Any]:
    if profile_path.is_symlink() or not profile_path.is_file():
        raise RuntimeError("exact profile must be a regular non-symlink file")
    profile = json.loads(profile_path.read_text())
    conditions = profile.get("conditions", {})
    if profile.get("schema_version") != 2 or \
            profile.get("validation", {}).get("status") != "passed":
        raise RuntimeError("exact profile must be schema v2 and independently passed")
    if conditions.get("cache_condition") != "warm_l2" or \
            conditions.get("concurrency_condition") != "exclusive_process" or \
            conditions.get("cluster_shape") != {"x": 1, "y": 1, "z": 1}:
        raise RuntimeError(
            "llama exact mode requires warm_l2, exclusive_process, and "
            "cluster 1x1x1"
        )
    artifacts = profile.get("runtime_artifacts", {})
    if set(artifacts) != {"bundle_root", "prepatched_ptx_dir", "pass_manifest"}:
        raise RuntimeError("exact profile runtime_artifacts are incomplete")
    for name, value in artifacts.items():
        path = pathlib.Path(value)
        if not path.is_absolute() or path.is_symlink() or \
                not (path.is_file() if name == "pass_manifest" else path.is_dir()):
            raise RuntimeError(f"unsafe or missing exact runtime artifact: {name}")
        artifacts[name] = str(path.resolve())
    return {"profile": str(profile_path.resolve()), "artifacts": artifacts}


def exact_coverage(path: pathlib.Path) -> dict[str, int]:
    decisions = [json.loads(line) for line in path.read_text().splitlines()
                 if line.strip()]
    final = [item for item in decisions
             if item.get("admitted_fidelity") == "exact" and
             item.get("post_run_validation_passed") is True]
    result = {
        "decisions": len(decisions),
        "exact_launches": len(final),
        "unsafe_launches": sum(item.get("allowed") is False
                               for item in decisions),
        "future_issued": sum(item.get("future_issued", 0) for item in final),
        "future_drained": sum(item.get("future_drained", 0) for item in final),
        "future_faults": sum(item.get("future_faults", 0) for item in final),
        "future_leaks": sum(item.get("future_leaked", 0) for item in final),
        "tma_faults": sum(item.get("tma_faults", 0) for item in final),
        "tma_leaks": sum(item.get("tma_leaked", 0) for item in final),
        "tma_stale_generations": sum(
            item.get("tma_stale_generations", 0) for item in final),
    }
    if result["exact_launches"] != 1 or \
            result["future_issued"] != 128 * 8 * 384 or \
            result["future_drained"] != result["future_issued"] or any(
                result[name] != 0 for name in (
                    "unsafe_launches", "future_faults", "future_leaks",
                    "tma_faults", "tma_leaks", "tma_stale_generations"
                )
            ):
        raise RuntimeError("llama exact post-run coverage gate failed")
    return result


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
    if mode in {"timing", "exact"}:
        profile = json.loads(args.profile.read_text())
        delay_ns = 0 if mode == "exact" else (
            args.delay_ns or int(profile["read_latency_ns"]) *
            int(profile["time_scale"])
        )
        config = {
            "profile_path": str(args.profile.resolve()),
            "report_dir": str(destination.resolve()),
            "timing_model": args.timing_model,
            "probe_ptx": str((args.hbf_build / "hbfsim_llama_probe.ptx").resolve()),
            "probe_library": str((args.hbf_build / "libhbfsim_llama_probe.so").resolve()),
            "delay_ns": delay_ns,
            "range_mode": "timing",
        }
        runtime = None
        if mode == "exact":
            runtime = exact_runtime(args.exact_profile)
            config.update({
                "exact_profile_path": runtime["profile"],
                "exact_cache_condition": "warm_l2",
                "exact_cluster_x": 1,
                "exact_cluster_y": 1,
                "exact_cluster_z": 1,
            })
        config_path = destination / "llama-hbfsim.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n")
        environment["LLAMA_HBFSIM_CONFIG"] = str(config_path)
        environment["HBFSIM_DAEMON_PATH"] = str(args.hbf_build / "hbfsimd")
        environment["HBFSIM_COVERAGE_PATH"] = str(destination / "coverage.jsonl")
        if mode == "timing":
            gate = str(args.hbf_build / "libhbfsim_launch_gate.so")
            environment["LD_PRELOAD"] = gate + (
                ":" + environment["LD_PRELOAD"]
                if environment.get("LD_PRELOAD") else ""
            )
        else:
            existing_preload = environment.get("LD_PRELOAD")
            environment["LD_PRELOAD"] = config["probe_library"]
            if existing_preload:
                environment["LD_PRELOAD"] += ":" + existing_preload
            environment.update({
                "HBFSIM_BUILD_DIR": str(args.hbf_build.resolve()),
                "HBFSIM_BPFTIME_BUILD_DIR": str(args.bpftime_build.resolve()),
                "HBFSIM_BPFTIME_PROBE": str(
                    (args.hbf_build / "llama_probe.bpf.o").resolve()
                ),
                "HBFSIM_PASS_MANIFEST_PATH": str(
                    destination / "pass-manifests.jsonl"
                ),
                "HBFSIM_PRESTAGED_PASS_MANIFEST_PATH":
                    runtime["artifacts"]["pass_manifest"],
            })
            command = [
                str(ROOT / "scripts/run_with_bpftime.sh"),
                "--exact-profile", runtime["profile"],
                "--exact-bundle-dir", runtime["artifacts"]["bundle_root"],
                "--prepatched-ptx-dir",
                runtime["artifacts"]["prepatched_ptx_dir"],
                "--", *command,
            ]
    if args.dry_run:
        return dry_run_result(mode, command, config)
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
    result = {
        "mode": mode,
        "wall_seconds": wall,
        "generated_text": generated_text(completed.stdout, args.prompt),
        "injection_count": len(injections),
        "injected_ns": sum(item["delay_ns"] for item in injections),
        "config": config,
    }
    result.update(exact_result_boundary(mode))
    if mode == "exact":
        if len(injections) != 1 or injections[0].get("bit_exact") is not True or \
                injections[0].get("output_count") != 128 or \
                injections[0].get("issued_operations") != 128 * 8 * 384:
            raise RuntimeError("llama exact probe lacks deterministic oracle evidence")
        result["exact_probe"] = injections[0]
        result["coverage"] = exact_coverage(destination / "coverage.jsonl")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("baseline", "timing", "compare", "exact", "compare-exact"),
        default="compare",
    )
    parser.add_argument("--llama-cli", type=pathlib.Path, required=True)
    parser.add_argument("--model", type=pathlib.Path, required=True)
    parser.add_argument("--hbf-build", type=pathlib.Path, required=True)
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--exact-profile", type=pathlib.Path)
    parser.add_argument(
        "--bpftime-build", type=pathlib.Path,
        default=pathlib.Path(os.environ.get(
            "HBFSIM_BPFTIME_BUILD_DIR", ROOT / "build-bpftime-hbfsim"
        )),
    )
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
    if args.mode in {"exact", "compare-exact"} and args.exact_profile is None:
        raise SystemExit("--exact-profile is required for exact modes")
    if args.mode not in {"exact", "compare-exact"} and \
            args.exact_profile is not None:
        raise SystemExit("--exact-profile requires an exact mode")
    args.report_dir.mkdir(parents=True, exist_ok=True)
    modes = ("baseline", "timing") if args.mode == "compare" else \
            ("baseline", "exact") if args.mode == "compare-exact" else \
            (args.mode,)
    results = [run_once(args, mode, args.report_dir / mode) for mode in modes]
    manifest = {"schema_version": 1, "results": results}
    if len(results) == 2:
        if results[0]["generated_text"] != results[1]["generated_text"]:
            raise RuntimeError("baseline and instrumented semantic outputs differ")
        if not args.dry_run and results[1]["injection_count"] == 0:
            raise RuntimeError("instrumented run produced no GPU probe launches")
        manifest["exact_output_match"] = True
        if not args.dry_run:
            manifest["slowdown"] = results[1]["wall_seconds"] / results[0]["wall_seconds"]
    (args.report_dir / "summary.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

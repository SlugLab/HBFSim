#!/usr/bin/env python3
"""Run the Phase-II TinyLlama/llama.cpp package-thermal stage matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import time


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_case(args: argparse.Namespace, name: str, stage: str) -> dict:
    case_dir = args.output / name
    input_name = "g1-8hi-app-read-only" if stage in {"off", "read_only"} else f"g1-8hi-app-{stage}"
    input_dir = args.inputs_root / input_name
    device = input_dir / "device-8hi.json"
    package = input_dir / (
        "package-8hi-read_only.json" if stage in {"off", "read_only"}
        else f"package-8hi-{stage}.json"
    )
    model = input_dir / "rom-8hi-runtime.json"
    command = [
        str(args.python), str(args.adapter_run),
        "--mode", "baseline" if name == "baseline" else "timing",
        "--llama-cli", str(args.llama_cli),
        "--model", str(args.model),
        "--hbf-build", str(args.hbf_build),
        "--profile", str(device),
        "--report-dir", str(case_dir),
        "--tokens", str(args.tokens),
        "--timeout", str(args.case_timeout),
        "--package-thermal-stage", stage,
    ]
    if stage != "off":
        command += [
            "--package-thermal-profile", str(package),
            "--package-thermal-model", str(model),
        ]
    started = time.time_ns()
    completed = subprocess.run(
        command,
        cwd=args.source_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=args.case_timeout + 60,
        check=False,
    )
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "orchestrator-stdout.txt").write_text(completed.stdout)
    (case_dir / "orchestrator-stderr.txt").write_text(completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{name} failed with exit code {completed.returncode}: {completed.stderr[-2000:]}"
        )
    summary = json.loads((case_dir / "summary.json").read_text())
    result = summary["results"][0]
    thermal_dir = case_dir / "timing"
    thermal_report = thermal_dir / "package-thermal.json"
    result.update({
        "case": name,
        "requested_package_thermal_stage": stage,
        "command": command,
        "started_unix_ns": started,
        "finished_unix_ns": time.time_ns(),
        "artifacts": {
            "device_profile": {"path": str(device), "sha256": sha256(device)},
            "llama_cli": {"path": str(args.llama_cli), "sha256": sha256(args.llama_cli)},
            "model": {"path": str(args.model), "sha256": sha256(args.model)},
        },
    })
    if stage != "off":
        result["artifacts"].update({
            "package_profile": {"path": str(package), "sha256": sha256(package)},
            "rom": {"path": str(model), "sha256": sha256(model)},
        })
        if not thermal_report.is_file():
            raise RuntimeError(f"{name} did not produce package-thermal.json")
        result["package_thermal"] = json.loads(thermal_report.read_text())
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=pathlib.Path, default=pathlib.Path("/usr/bin/python3"))
    parser.add_argument("--source-root", required=True, type=pathlib.Path)
    parser.add_argument("--adapter-run", required=True, type=pathlib.Path)
    parser.add_argument("--llama-cli", required=True, type=pathlib.Path)
    parser.add_argument("--model", required=True, type=pathlib.Path)
    parser.add_argument("--hbf-build", required=True, type=pathlib.Path)
    parser.add_argument("--inputs-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--tokens", type=int, default=8)
    parser.add_argument("--case-timeout", type=int, default=300)
    args = parser.parse_args()
    for field in ("source_root", "adapter_run", "llama_cli", "model", "hbf_build", "inputs_root", "output"):
        setattr(args, field, getattr(args, field).resolve())
    args.output.mkdir(parents=True, exist_ok=False)
    cases = [
        run_case(args, "baseline", "off"),
        run_case(args, "package-thermal-off", "off"),
        run_case(args, "read-only", "read_only"),
        run_case(args, "shadow", "shadow"),
        run_case(args, "active", "active"),
    ]
    outputs = {case["generated_text"] for case in cases}
    summary = {
        "schema_version": 1,
        "campaign": "APP-1 TinyLlama llama.cpp package-thermal stage matrix",
        "llama_cpp_commit": "7ba604f1cb61cd14898138e9abc0b4ff2601f180",
        "cases": cases,
        "correctness": {
            "all_generated_text_matches": len(outputs) == 1,
            "generated_text": next(iter(outputs)) if len(outputs) == 1 else None,
        },
    }
    (args.output / "app1-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0 if summary["correctness"]["all_generated_text_matches"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

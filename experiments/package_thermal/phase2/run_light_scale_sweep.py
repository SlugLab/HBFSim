#!/usr/bin/env python3
"""Run the preregistered 0.25/0.5/0.75 light-service-scale sweep."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=pathlib.Path)
    parser.add_argument("--runner", required=True, type=pathlib.Path)
    parser.add_argument("--base-device", required=True, type=pathlib.Path)
    parser.add_argument("--certified-rom", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    generator = args.source_root / "experiments/package_thermal/phase2/make_runtime_profile.py"
    cases = []
    for label, scale in (("025", 0.25), ("050", 0.50), ("075", 0.75)):
        case = args.output / f"light-scale-{label}"
        inputs = case / "inputs"
        subprocess.run([
            "python3", str(generator),
            "--base-device", str(args.base_device),
            "--certified-rom", str(args.certified_rom),
            "--output", str(inputs),
            "--height", "8", "--stage", "active",
            "--clock-mode", "model_time_replay",
            "--gpu-power-w", "30", "--hbm-power-w", "5",
            "--read-command-j", "0.01", "--program-command-j", "0.01",
            "--erase-command-j", "0.1", "--base-idle-w", "0.5",
            "--light-on-c", "40.5", "--light-off-c", "39",
            "--severe-on-c", "100", "--severe-off-c", "90",
            "--shutdown-on-c", "150", "--shutdown-off-c", "140",
            "--light-scale", str(scale),
            "--debounce-samples", "2", "--minimum-dwell-samples", "2",
        ], check=True)
        result_dir = case / "result"
        command = [
            str(args.runner),
            "--device-profile", str(inputs / "device-8hi.json"),
            "--package-profile", str(inputs / "package-8hi-active.json"),
            "--model", str(inputs / "rom-8hi-runtime.json"),
            "--output", str(result_dir),
            "--duration-ns", "4000000000",
            "--offered-byte-rate", "100000000",
            "--peak-byte-rate", "200000000",
            "--request-bytes", "1048576",
            "--queue-depth", "128", "--seed", "41001",
            "--arrival-mode", "periodic", "--workload", "read",
            "--pattern", "random",
        ]
        subprocess.run(command, check=True, timeout=300)
        runner_result = json.loads((result_dir / "runner-result.json").read_text())
        thermal = json.loads((result_dir / "package-thermal.json").read_text())
        cases.append({
            "light_scale": scale,
            "served_byte_rate": runner_result["served_byte_rate"],
            "requests": runner_result["requests"],
            "maximum_hbf_temperature_c": runner_result["maximum_hbf_temperature_c"],
            "time_normal_ns": thermal["time_normal_ns"],
            "time_light_ns": thermal["time_light_ns"],
            "light_transitions": thermal["light_transitions"],
            "severe_transitions": thermal["severe_transitions"],
            "package_profile_sha256": sha256(inputs / "package-8hi-active.json"),
            "runner_result_sha256": sha256(result_dir / "runner-result.json"),
            "command": command,
        })
    summary = {
        "schema_version": 1,
        "evidence_class": "C",
        "purpose": "scientific light-service-scale sensitivity",
        "constant_controls": {
            "duration_ns": 4_000_000_000,
            "offered_byte_rate": 100_000_000,
            "peak_byte_rate": 200_000_000,
            "request_bytes": 1_048_576,
            "seed": 41001,
            "light_on_c": 40.5,
            "light_off_c": 39.0,
            "severe_on_c": 100.0,
        },
        "cases": cases,
    }
    (args.output / "light-scale-sweep-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

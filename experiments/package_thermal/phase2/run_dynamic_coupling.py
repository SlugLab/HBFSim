#!/usr/bin/env python3
"""Run CL-2 GPU-step and CL-3 HBM-Power-informed coupling experiments."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
import subprocess


HBM_POWER_COMMIT = "4642c61f856cbd9d5d3f37d56214e5dbfa663703"


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hbm_power(
    runner: pathlib.Path,
    organization: pathlib.Path,
    timing: pathlib.Path,
    power: pathlib.Path,
    trace: pathlib.Path,
    output: pathlib.Path,
    rates: tuple[str, str, str],
) -> float:
    command = [str(runner), str(organization), str(timing), str(power), str(trace),
               f"--dq-rate={rates[0]}", f"--tsv-rate={rates[1]}",
               f"--bg-rate={rates[2]}"]
    completed = subprocess.run(command, text=True, capture_output=True,
                               check=True, timeout=300)
    output.write_text(completed.stdout)
    found = re.search(r"Average power:\s+([0-9.]+)\s+mW", completed.stdout)
    if found is None:
        raise RuntimeError("HBM-Power output did not contain average power")
    # The official Figure-16 trace exercises one of 32 pseudo-channels; the
    # artifact's published adapter uses the same explicit one-stack scale.
    return float(found.group(1)) / 1000.0 * 32.0


def set_provider(profile: dict, source: str, samples: list[tuple[int, float]],
                 evidence_class: str, locator: str, note: str) -> None:
    provider = {
        "kind": "synthetic",
        "interpolation": "hold",
        "samples": [{"relative_time_ns": time_ns, "watts": watts}
                    for time_ns, watts in samples],
        "provenance": {
            "class": evidence_class,
            "source": source,
            "locator": locator,
            "note": note,
        },
    }
    if locator == "gpu low-high-low step":
        profile["gpu_provider"] = provider
    else:
        profile["near_memory"]["power_sources"][0]["provider"] = provider


def run_case(
    *, name: str, source_root: pathlib.Path, generator: pathlib.Path,
    runner: pathlib.Path, base_device: pathlib.Path, certified_rom: pathlib.Path,
    output: pathlib.Path, gpu_samples: list[tuple[int, float]],
    hbm_samples: list[tuple[int, float]], hbm_class: str, hbm_locator: str,
    hbm_note: str,
) -> dict:
    case = output / name
    inputs = case / "inputs"
    subprocess.run([
        "python3", str(generator), "--base-device", str(base_device),
        "--certified-rom", str(certified_rom), "--output", str(inputs),
        "--height", "16", "--stage", "read_only",
        "--clock-mode", "model_time_replay", "--gpu-power-w", "30",
        "--hbm-power-w", "5", "--read-command-j", "0.001",
        "--program-command-j", "0.001", "--erase-command-j", "0.01",
        "--base-idle-w", "0.5",
    ], check=True)
    package_path = inputs / "package-16hi-read_only.json"
    profile = json.loads(package_path.read_text())
    set_provider(profile, "gpu", gpu_samples, "C", "gpu low-high-low step",
                 "controlled coupling actuator; not a device power specification")
    set_provider(profile, "hbm.s0", hbm_samples, hbm_class, hbm_locator, hbm_note)
    package_path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n")
    result = case / "result"
    command = [
        str(runner), "--device-profile", str(inputs / "device-16hi.json"),
        "--package-profile", str(package_path), "--model",
        str(inputs / "rom-16hi-runtime.json"), "--output", str(result),
        "--duration-ns", "6000000000", "--offered-byte-rate", "50000000",
        "--peak-byte-rate", "200000000", "--request-bytes", "1048576",
        "--queue-depth", "128", "--seed", "42001", "--arrival-mode",
        "periodic", "--workload", "read", "--pattern", "random",
    ]
    subprocess.run(command, cwd=source_root, check=True, timeout=300)
    rows = list(csv.DictReader((result / "package-thermal-timeline.csv").open()))

    def mean_window(field: str, begin: int, end: int) -> float:
        values = [float(row[field]) for row in rows
                  if begin <= int(row["thermal_time_ns"]) <= end]
        if not values:
            raise RuntimeError(f"empty timeline window for {name}: {begin}-{end}")
        return sum(values) / len(values)

    runner_result = json.loads((result / "runner-result.json").read_text())
    return {
        "case": name,
        "served_byte_rate": runner_result["served_byte_rate"],
        "requests": runner_result["requests"],
        "maximum_hbf_temperature_c": runner_result["maximum_hbf_temperature_c"],
        "hbf_hotspot_window_mean_c": {
            "pre_0p8_1p0s": mean_window("T_hbf_hotspot", 800_000_000, 1_000_000_000),
            "high_end_3p8_4p0s": mean_window("T_hbf_hotspot", 3_800_000_000, 4_000_000_000),
            "post_5p8_6p0s": mean_window("T_hbf_hotspot", 5_800_000_000, 6_000_000_000),
        },
        "package_profile_sha256": sha256(package_path),
        "runner_result_sha256": sha256(result / "runner-result.json"),
        "timeline_sha256": sha256(result / "package-thermal-timeline.csv"),
        "command": command,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=pathlib.Path)
    parser.add_argument("--runner", required=True, type=pathlib.Path)
    parser.add_argument("--base-device", required=True, type=pathlib.Path)
    parser.add_argument("--certified-rom", required=True, type=pathlib.Path)
    parser.add_argument("--hbm-power-root", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    hbm = args.hbm_power_root
    figure = hbm / "sources/figure16"
    trace_root = figure / "traces"
    generated = args.output / "hbm-power"
    generated.mkdir()
    read_trace = trace_root / "hbm3_random_read_4rpa.csv"
    write_trace = generated / "hbm3-derived-random-write-4rpa.csv"
    write_trace.write_text(read_trace.read_text().replace(",RD,", ",WR,"))
    hbm_runner = hbm / "sources/drampower/build/bin/HBM3_runner"
    organization = figure / "configs/HBM3_16Gb_8hi_organization.json"
    timing = figure / "configs/HBM3_6400Mbps_timing.json"
    power = figure / "configs/HBM3_6400_power_datapattern.json"
    idle_w = hbm_power(hbm_runner, organization, timing, power,
                       trace_root / "hbm3_baseline_nop_ref.csv",
                       generated / "idle-runner.txt", ("0", "0", "0"))
    read_w = hbm_power(hbm_runner, organization, timing, power, read_trace,
                       generated / "read-runner.txt", ("0.5", "0.5", "0.5"))
    write_w = hbm_power(hbm_runner, organization, timing, power, write_trace,
                        generated / "write-runner.txt", ("0.5", "0.5", "0.5"))
    generator = args.source_root / "experiments/package_thermal/phase2/make_runtime_profile.py"
    constant_gpu = [(0, 30.0)]
    constant_hbf_note = "one stack; fixed HBF workload is identical in all cases"
    cases = []
    cases.append(run_case(
        name="cl2-gpu-step", source_root=args.source_root, generator=generator,
        runner=args.runner, base_device=args.base_device,
        certified_rom=args.certified_rom, output=args.output,
        gpu_samples=[(0, 30.0), (1_000_000_000, 300.0),
                     (4_000_000_000, 30.0)],
        hbm_samples=[(0, 5.0)], hbm_class="C", hbm_locator="fixed HBM control",
        hbm_note=constant_hbf_note))
    hbm_note = ("CMU-SAFARI HBM-Power Figure-16 model; one active pseudo-channel "
                "scaled by 32 to one HBM3E stack; not a measurement")
    for name, watts, locator in (
        ("cl3-hbm-idle", idle_w, "official HBM3E baseline trace"),
        ("cl3-hbm-read-heavy", read_w, "official HBM3E random-read trace"),
        ("cl3-hbm-write-heavy-derived", write_w,
         "derived HBM3E trace: official random-read commands RD replaced by WR"),
    ):
        cases.append(run_case(
            name=name, source_root=args.source_root, generator=generator,
            runner=args.runner, base_device=args.base_device,
            certified_rom=args.certified_rom, output=args.output,
            gpu_samples=constant_gpu, hbm_samples=[(0, watts)], hbm_class="L",
            hbm_locator=locator, hbm_note=hbm_note))
    summary = {
        "schema_version": 1,
        "hbm_power": {
            "commit": HBM_POWER_COMMIT,
            "runner_sha256": sha256(hbm_runner),
            "organization_sha256": sha256(organization),
            "timing_sha256": sha256(timing),
            "power_sha256": sha256(power),
            "official_read_trace_sha256": sha256(read_trace),
            "derived_write_trace_sha256": sha256(write_trace),
            "scale_to_one_stack": 32.0,
            "idle_w": idle_w, "read_heavy_w": read_w,
            "write_heavy_derived_w": write_w,
        },
        "controls": {
            "height": 16, "duration_ns": 6_000_000_000,
            "fixed_hbf_offered_byte_rate": 50_000_000,
            "fixed_gpu_w_for_cl3": 30.0, "seed": 42001,
            "thermal_stage": "read_only",
        },
        "cases": cases,
    }
    (args.output / "dynamic-coupling-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

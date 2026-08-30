#!/usr/bin/env python3
"""Safely extrapolate calibrated thermal responses to named overheat scenarios."""

import argparse
import json
import math
import pathlib


def response(component: dict, multiplier: float, duration: int) -> list[dict]:
    ambient = component["ambient_c"]
    rise = (component["steady_state_c"] - ambient) * multiplier
    tau = component["tau_seconds"]
    return [{"elapsed_s": second,
             "temperature_c": ambient + rise * (1.0 - math.exp(-second / tau))}
            for second in range(duration + 1)]


def crossing(trace: list[dict], threshold: float | None):
    if threshold is None:
        return None
    return next((item["elapsed_s"] for item in trace if item["temperature_c"] >= threshold), None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration", type=pathlib.Path, required=True)
    parser.add_argument("--scenarios", type=pathlib.Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    calibration = json.loads(args.calibration.read_text())
    profiles = json.loads(args.scenarios.read_text())["profiles"]
    if args.profile not in profiles:
        raise SystemExit(f"unknown thermal profile {args.profile!r}")
    scenario = profiles[args.profile]
    duration = int(scenario["duration_seconds"])
    gpu = response(calibration["gpu"], scenario["gpu_heat_multiplier"], duration)
    ssd = response(calibration["ssd"], scenario["ssd_heat_multiplier"], duration)
    slope = calibration["gpu"]["performance"]["tflops_per_c"]
    cold = calibration["gpu"]["performance"]["cold_tflops"]
    for item in gpu:
        item["predicted_tflops"] = cold + slope * (item["temperature_c"] - calibration["gpu"]["ambient_c"])
    result = {
        "schema_version": 1, "profile": args.profile,
        "virtual_only": True,
        "note": "Threshold behavior is extrapolated; no hardware was driven to warning temperature.",
        "gpu": {"trace": gpu, "threshold_c": scenario.get("gpu_threshold_c"),
                "threshold_crossing_s": crossing(gpu, scenario.get("gpu_threshold_c"))},
        "ssd": {"trace": ssd, "threshold_c": scenario.get("ssd_threshold_c"),
                "threshold_crossing_s": crossing(ssd, scenario.get("ssd_threshold_c"))},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"profile": args.profile,
                      "gpu_threshold_crossing_s": result["gpu"]["threshold_crossing_s"],
                      "ssd_threshold_crossing_s": result["ssd"]["threshold_crossing_s"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

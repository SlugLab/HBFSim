#!/usr/bin/env python3
"""Fit log-domain first-order thermal responses and sampled performance loss."""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics


def load_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def weighted_history(values: list[float], n: int = 7) -> list[float]:
    result = []
    for index in range(len(values)):
        window = values[max(0, index - n + 1): index + 1][::-1]
        weights = [0.5**offset for offset in range(len(window))]
        result.append(sum(v * w for v, w in zip(window, weights)) / sum(weights))
    return result


def fit_heating(times: list[float], temperatures: list[float]) -> dict:
    ambient = temperatures[0]
    best = None
    for tau_tenths in range(5, 3001):
        tau = tau_tenths / 10.0
        basis = [1.0 - math.exp(-(t - times[0]) / tau) for t in times]
        denominator = sum(value * value for value in basis)
        if denominator == 0:
            continue
        rise = sum((temp - ambient) * value for temp, value in zip(temperatures, basis)) / denominator
        predicted = [ambient + rise * value for value in basis]
        log_rmse = math.sqrt(sum((math.log1p(max(0.0, p - ambient)) - math.log1p(max(0.0, t - ambient)))**2
                                 for p, t in zip(predicted, temperatures)) / len(times))
        candidate = (log_rmse, tau, rise, predicted)
        if best is None or candidate[0] < best[0]:
            best = candidate
    log_rmse, tau, rise, predicted = best
    rmse = math.sqrt(sum((p - t) ** 2 for p, t in zip(predicted, temperatures)) / len(times))
    return {"ambient_c": ambient, "steady_state_c": ambient + rise, "tau_seconds": tau,
            "log_rmse": log_rmse, "rmse_c": rmse, "observed_peak_c": max(temperatures)}


def performance_fit(telemetry: list[dict], performance: list[dict]) -> dict:
    pairs = []
    for item in performance:
        nearest = min(telemetry, key=lambda sample: abs(sample["timestamp_s"] - item["timestamp_s"]))
        pairs.append((float(nearest["gpu"]["temperature_c"]), float(item["window_tflops"])))
    if len(pairs) < 2:
        return {"samples": len(pairs)}
    mean_t = statistics.fmean(t for t, _ in pairs)
    mean_p = statistics.fmean(p for _, p in pairs)
    denominator = sum((t - mean_t) ** 2 for t, _ in pairs)
    slope = sum((t - mean_t) * (p - mean_p) for t, p in pairs) / denominator if denominator else 0.0
    return {"samples": len(pairs), "tflops_per_c": slope, "cold_tflops": pairs[0][1],
            "hot_tflops": pairs[-1][1], "change_pct": 100.0 * (pairs[-1][1] / pairs[0][1] - 1.0),
            "checksum_exact": len({item["checksum"] for item in performance}) == 1}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--name", default="gpu-cd8p-logp")
    args = parser.parse_args()
    telemetry = load_jsonl(args.input / "telemetry.jsonl")
    heat = [item for item in telemetry if item["phase"] == "heat"]
    gpu_temperatures = weighted_history([float(item["gpu"]["temperature_c"]) for item in heat])
    ssd_temperatures = weighted_history([float(item["ssd"]["temperature_c"]) for item in heat])
    times = [float(item["elapsed_s"]) for item in heat]
    performance = load_jsonl(args.input / "gpu-performance.jsonl")
    fio = json.loads((args.input / "fio.json").read_text())["jobs"][0]["read"]
    profile = {
        "schema_version": 1, "name": args.name,
        "method": {"response": "first_order_rc", "objective": "log1p_temperature_rise_rmse",
                   "paper_weighted_history": {"n": 7, "alpha_k": "1/2^k"}},
        "gpu": {**fit_heating(times, gpu_temperatures), "performance": performance_fit(telemetry, performance)},
        "ssd": {**fit_heating(times, ssd_temperatures),
                "read_bandwidth_bytes_per_s": fio["bw_bytes"], "read_iops": fio["iops"],
                "mean_completion_latency_ns": fio["clat_ns"]["mean"]},
        "safety": json.loads((args.input / "safety.json").read_text()),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2) + "\n")
    print(json.dumps(profile, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

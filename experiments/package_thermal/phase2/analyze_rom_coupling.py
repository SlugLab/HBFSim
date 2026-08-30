#!/usr/bin/env python3
"""Derive Phase-II resistance matrices, source coupling, and SH-1/SH-2."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
from typing import Any

import numpy as np


AMBIENT_C = 30.0
HBF_TOTAL_W = 53.72
BASELINE_PER_DIE_W = HBF_TOTAL_W / 8.0


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rom(path: pathlib.Path) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact = json.loads(path.read_text(encoding="utf-8"))
    payload = artifact.get("payload", artifact)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid ROM payload: {path}")
    return artifact, payload


def matrices(payload: dict[str, Any]) -> tuple[np.ndarray, ...]:
    states = int(payload["state_count"])
    inputs = len(payload["input_names"])
    outputs = len(payload["output_names"])
    return (
        np.asarray(payload["a"], dtype=float).reshape(states, states),
        np.asarray(payload["b"], dtype=float).reshape(states, inputs),
        np.asarray(payload["bias"], dtype=float),
        np.asarray(payload["c"], dtype=float).reshape(outputs, states),
        np.asarray(payload["d"], dtype=float).reshape(outputs, inputs),
        np.asarray(payload["offset"], dtype=float),
    )


def first_crossing(values: list[float], threshold: float,
                   period_ns: int) -> int | None:
    return next((index * period_ns for index, value in enumerate(values, 1)
                 if value >= threshold), None)


def analyze_one(path: pathlib.Path, height: int) -> dict[str, Any]:
    artifact, payload = read_rom(path)
    names = list(payload["input_names"])
    output_names = list(payload["output_names"])
    if names != output_names:
        raise ValueError("resistance analysis requires aligned ROM I/O names")
    expected = ["gpu", "hbm", "hbf.base"] + [
        f"hbf.s0.l{index}" for index in range(height)
    ]
    if names != expected:
        raise ValueError(f"unexpected {height}Hi node order")
    a, b, bias, c, d, offset = matrices(payload)
    identity = np.eye(a.shape[0])

    def steady(power: np.ndarray) -> np.ndarray:
        state = np.linalg.solve(identity - a, b @ power + bias)
        return c @ state + d @ power + offset

    zero = np.zeros(len(names))
    baseline = steady(zero)
    resistance = np.empty((len(names), len(names)))
    for source in range(len(names)):
        power = zero.copy()
        power[source] = 1.0
        resistance[source, :] = steady(power) - baseline
    hbf_indices = [index for index, name in enumerate(names)
                   if name == "hbf.base" or name.startswith("hbf.s")]

    def scenario(power: np.ndarray) -> dict[str, Any]:
        temperatures = steady(power)
        hotspot_index = max(hbf_indices, key=lambda index: temperatures[index])
        return {
            "power_w": dict(zip(names, power.tolist())),
            "hbf_total_w": float(sum(
                power[index] for index in hbf_indices
                if names[index].startswith("hbf.s"))),
            "hbf_hotspot_c": float(temperatures[hotspot_index]),
            "hbf_hotspot_node": names[hotspot_index],
            "hbf_hotspot_rise_c": float(
                temperatures[hotspot_index] - AMBIENT_C),
            "temperatures_c": dict(zip(names, temperatures.tolist())),
        }

    equal_total = zero.copy()
    equal_total[3:] = HBF_TOTAL_W / height
    equal_per_die = zero.copy()
    equal_per_die[3:] = BASELINE_PER_DIE_W
    sh1 = scenario(equal_total)
    sh2 = scenario(equal_per_die)

    source_to_hbf = []
    for source, name in enumerate(names):
        responses = resistance[source, hbf_indices]
        hotspot_offset = int(np.argmax(responses))
        source_to_hbf.append({
            "source": name,
            "maximum_hbf_rise_k_per_w": float(responses[hotspot_offset]),
            "maximum_hbf_node": names[hbf_indices[hotspot_offset]],
            "mean_hbf_rise_k_per_w": float(np.mean(responses)),
        })

    coupling_arms = []
    for arm, gpu_w, hbm_w in (
        ("runtime_controlled", 30.0, 5.0),
        ("literature_projected_upper", 300.0, 95.0),
    ):
        hbf_only = equal_total.copy()
        combined = equal_total.copy()
        combined[0] = gpu_w
        combined[1] = hbm_w
        hbf_result = scenario(hbf_only)
        combined_result = scenario(combined)
        external_delta = (combined_result["hbf_hotspot_c"] -
                          hbf_result["hbf_hotspot_c"])

        def transient(power: np.ndarray, maximum_steps: int = 5000
                      ) -> list[float]:
            state = np.zeros(a.shape[0])
            hotspots = []
            for _ in range(maximum_steps):
                state = a @ state + b @ power + bias
                temperatures = c @ state + d @ power + offset
                hotspots.append(float(np.max(temperatures[hbf_indices])))
            return hotspots

        hbf_transient = transient(hbf_only)
        combined_transient = transient(combined)
        thresholds = {}
        for threshold in (42.0, 80.0, 90.0, 100.0):
            without = first_crossing(
                hbf_transient, threshold, int(payload["sample_period_ns"]))
            with_external = first_crossing(
                combined_transient, threshold,
                int(payload["sample_period_ns"]))
            thresholds[f"{threshold:g}c"] = {
                "hbf_only_crossing_ns": without,
                "with_external_crossing_ns": with_external,
                "external_shift_ns": (
                    with_external - without
                    if without is not None and with_external is not None
                    else None
                ),
                "classification_changed": (without is None) !=
                (with_external is None),
            }
        coupling_arms.append({
            "arm": arm,
            "gpu_w": gpu_w,
            "hbm_w": hbm_w,
            "hbf_only": hbf_result,
            "with_external": combined_result,
            "external_hotspot_delta_c": external_delta,
            "external_fraction_of_combined_hotspot_rise": (
                external_delta / combined_result["hbf_hotspot_rise_c"]
                if combined_result["hbf_hotspot_rise_c"] != 0.0 else None
            ),
            "threshold_crossing": thresholds,
        })

    return {
        "height": height,
        "rom_path": str(path.resolve()),
        "rom_sha256": sha256(path),
        "model_id": payload["model_id"],
        "solver_identity": payload["solver_identity"],
        "sample_period_ns": payload["sample_period_ns"],
        "ambient_c": AMBIENT_C,
        "node_names": names,
        "resistance_definition": "R[source][output] = steady DeltaT_output / 1 W_source",
        "thermal_resistance_k_per_w": resistance.tolist(),
        "source_to_hbf_hotspot": source_to_hbf,
        "SH-1_equal_total_hbf_power": sh1,
        "SH-2_equal_per_die_power": sh2,
        "external_coupling": coupling_arms,
        "evidence_class": "C",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom-8hi", type=pathlib.Path, required=True)
    parser.add_argument("--rom-16hi", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    results = [analyze_one(args.rom_8hi, 8), analyze_one(args.rom_16hi, 16)]
    by_height = {item["height"]: item for item in results}
    comparison = {}
    for arm in ("SH-1_equal_total_hbf_power", "SH-2_equal_per_die_power"):
        left = by_height[8][arm]
        right = by_height[16][arm]
        comparison[arm] = {
            "8hi_hotspot_c": left["hbf_hotspot_c"],
            "16hi_hotspot_c": right["hbf_hotspot_c"],
            "16hi_minus_8hi_c": right["hbf_hotspot_c"] - left["hbf_hotspot_c"],
            "16hi_over_8hi_hotspot_rise": (
                right["hbf_hotspot_rise_c"] / left["hbf_hotspot_rise_c"]
                if left["hbf_hotspot_rise_c"] != 0.0 else None
            ),
        }
    output = {
        "schema_version": 1,
        "method": "exact steady solve and direct ROM transient",
        "heights": results,
        "height_comparison": comparison,
        "interpretation": {
            "SH-1": "equal total HBF power isolates geometry/path effects",
            "SH-2": "equal per-die power includes stack-scale total-power effects",
            "external_coupling": "projection conditional on certified ROM geometry and stated source powers",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

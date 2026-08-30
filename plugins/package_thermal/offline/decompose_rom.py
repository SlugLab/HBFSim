#!/usr/bin/env python3
"""Decompose linear-ROM HBF temperature rise by GPU/HBM/HBF power source."""

from __future__ import annotations

import argparse
import math
import pathlib
import sys

from _common import (OfflineError, finite_number, load_dataset, load_json,
                     require_keys, rom_payload_sha256, sha256_file, write_json)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run GPU-only/HBM-only/HBF-only linear ROM decomposition")
    parser.add_argument("--model", type=pathlib.Path, required=True)
    parser.add_argument("--dataset", type=pathlib.Path, required=True,
                        help="golden dataset whose power_w frames are decomposed")
    parser.add_argument("--initial-temperature-c", type=float, default=30.0)
    parser.add_argument("--max-superposition-error-c", type=float,
                        default=1.0e-8)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def load_model(path: pathlib.Path) -> dict:
    envelope = load_json(path)
    require_keys(envelope, {"schema_version", "payload", "payload_sha256"},
                 "ROM envelope")
    if (envelope["schema_version"] not in {1, 2} or
            not isinstance(envelope["payload"], dict)):
        raise OfflineError("unsupported ROM envelope")
    payload = envelope["payload"]
    expected = rom_payload_sha256(payload, envelope["schema_version"])
    if envelope["payload_sha256"] != expected:
        raise OfflineError("ROM payload SHA-256 mismatch")
    required = {"model_id", "evidence_label", "sample_period_ns",
                "input_names", "output_names", "state_count", "a", "b",
                "bias", "c", "d", "offset", "solver_identity",
                "geometry_sha256", "training_split", "held_out_split",
                "held_out_max_error_c", "held_out_p95_error_c"}
    require_keys(payload, required, "ROM payload")
    n = payload["state_count"]
    inputs = payload["input_names"]
    outputs = payload["output_names"]
    if (not isinstance(n, int) or isinstance(n, bool) or n <= 0 or n > 256 or
            not isinstance(inputs, list) or not inputs or
            not isinstance(outputs, list) or not outputs):
        raise OfflineError("ROM dimensions are invalid")
    dimensions = {"a": n * n, "b": n * len(inputs), "bias": n,
                  "c": len(outputs) * n, "d": len(outputs) * len(inputs),
                  "offset": len(outputs)}
    for field, size in dimensions.items():
        values = payload[field]
        if not isinstance(values, list) or len(values) != size:
            raise OfflineError(f"ROM {field} dimension mismatch")
        payload[field] = [finite_number(item, f"ROM {field}") for item in values]
    return payload


def advance(payload: dict, powers: list[list[float]], initial: float) -> list[list[float]]:
    n = payload["state_count"]
    m = len(payload["input_names"])
    o = len(payload["output_names"])
    state = [initial] * n
    result = []
    for power in powers:
        output = []
        for row in range(o):
            value = payload["offset"][row]
            value += sum(payload["c"][row * n + column] * state[column]
                         for column in range(n))
            value += sum(payload["d"][row * m + column] * power[column]
                         for column in range(m))
            output.append(value)
        next_state = []
        for row in range(n):
            value = payload["bias"][row]
            value += sum(payload["a"][row * n + column] * state[column]
                         for column in range(n))
            value += sum(payload["b"][row * m + column] * power[column]
                         for column in range(m))
            next_state.append(value)
        if (not all(math.isfinite(item) for item in next_state) or
                not all(math.isfinite(item) and -273.15 <= item <= 1000.0
                        for item in output)):
            raise OfflineError("ROM decomposition produced an invalid state")
        result.append(output)
        state = next_state
    return result


def source_group(name: str) -> str | None:
    lowered = name.lower()
    if lowered == "gpu" or lowered.startswith("gpu."):
        return "gpu"
    if lowered == "hbm" or lowered.startswith("hbm."):
        return "hbm"
    if lowered == "hbf" or lowered.startswith("hbf."):
        return "hbf_self"
    return None


def main() -> int:
    args = arguments()
    initial = finite_number(args.initial_temperature_c, "initial temperature")
    tolerance = finite_number(args.max_superposition_error_c,
                              "superposition tolerance", nonnegative=True)
    if initial < -273.15:
        raise OfflineError("initial temperature is below absolute zero")
    payload = load_model(args.model)
    dataset = load_dataset(args.dataset)
    if (dataset["input_names"] != payload["input_names"] or
            dataset["sample_period_ns"] != payload["sample_period_ns"]):
        raise OfflineError("dataset power ordering/period does not match ROM")
    hbf_outputs = [index for index, name in enumerate(payload["output_names"])
                   if name.lower() == "hbf" or name.lower().startswith("hbf.")]
    if not hbf_outputs:
        raise OfflineError("ROM exposes no HBF output nodes")
    powers = [sample["power_w"] for sample in dataset["samples"]]
    groups = {"gpu": [], "hbm": [], "hbf_self": []}
    for frame in powers:
        split = {name: [0.0] * len(frame) for name in groups}
        for index, value in enumerate(frame):
            group = source_group(payload["input_names"][index])
            if group is None:
                if value != 0.0:
                    raise OfflineError(
                        f"unclassified source has nonzero power: "
                        f"{payload['input_names'][index]}")
            else:
                split[group][index] = value
        for group in groups:
            groups[group].append(split[group])

    zeros = [[0.0] * len(payload["input_names"]) for _ in powers]
    baseline = advance(payload, zeros, initial)
    full = advance(payload, powers, initial)
    isolated = {name: advance(payload, frames, initial)
                for name, frames in groups.items()}
    contributions = {name: [[isolated[name][sample][node] -
                             baseline[sample][node]
                             for node in range(len(payload["output_names"]))]
                            for sample in range(len(powers))]
                     for name in groups}
    maximum_error = 0.0
    for sample in range(len(powers)):
        for node in range(len(payload["output_names"])):
            expected = full[sample][node] - baseline[sample][node]
            actual = sum(contributions[group][sample][node] for group in groups)
            maximum_error = max(maximum_error, abs(expected - actual))

    hotspot_sample, hotspot_node = max(
        ((sample, node) for sample in range(len(powers)) for node in hbf_outputs),
        key=lambda item: full[item[0]][item[1]])
    timestamps = [sample["time_ns"] for sample in dataset["samples"]]
    hotspot_contributions = {
        group: contributions[group][hotspot_sample][hotspot_node]
        for group in groups}
    maximum_by_source = {
        group: max(contributions[group][sample][node]
                   for sample in range(len(powers)) for node in hbf_outputs)
        for group in groups}
    accepted = maximum_error <= tolerance
    write_json(args.output, {
        "schema_version": 1,
        "model_sha256": sha256_file(args.model),
        "dataset_sha256": sha256_file(args.dataset),
        "trace_id": dataset["trace_id"],
        "evidence_label": "model_based_projection",
        "initial_temperature_c": initial,
        "full_hotspot": {
            "node": payload["output_names"][hotspot_node],
            "time_ns": timestamps[hotspot_sample],
            "temperature_c": full[hotspot_sample][hotspot_node],
            "zero_input_baseline_c": baseline[hotspot_sample][hotspot_node],
            "coupled_rise_c": (full[hotspot_sample][hotspot_node] -
                               baseline[hotspot_sample][hotspot_node]),
        },
        "contribution_delta_c_at_full_hotspot": hotspot_contributions,
        "contribution_sum_c_at_full_hotspot": sum(hotspot_contributions.values()),
        "maximum_hbf_delta_c_by_source": maximum_by_source,
        "maximum_superposition_error_c": maximum_error,
        "acceptance_tolerance_c": tolerance,
        "accepted": accepted,
        "warning": "linear ROM projection; not measured HBF temperature",
    })
    if not accepted:
        raise OfflineError(
            f"decomposition superposition error {maximum_error} exceeds {tolerance}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfflineError as error:
        print(f"decompose_rom.py: {error}", file=sys.stderr)
        raise SystemExit(2)

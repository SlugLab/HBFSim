#!/usr/bin/env python3
"""Fit a deterministic, stable, discrete temperature-state ROM."""

from __future__ import annotations

import argparse
import math
import pathlib
import sys

from _common import (EVIDENCE_LABELS, OfflineError, canonical_json,
                     load_dataset, percentile, select_evidence_label,
                     sha256_bytes, sha256_file, write_json)


REQUIRED_HELD_OUT = {"square_wave", "burst", "mixed_gpu_hbm_hbf",
                     "write_heavy_hbf", "read_heavy_hbf"}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit x[k+1]=A*x[k]+B*p[k]+bias from golden traces")
    parser.add_argument("--training", type=pathlib.Path, action="append",
                        required=True)
    parser.add_argument("--held-out", type=pathlib.Path, action="append",
                        required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--geometry-sha256", required=True)
    parser.add_argument("--solver-identity", required=True)
    parser.add_argument(
        "--evidence-label", choices=EVIDENCE_LABELS,
        help="optional evidence downgrade; defaults to the weakest input label")
    parser.add_argument("--ridge", type=float, default=1.0e-9)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def solve(matrix: list[list[float]], right: list[float]) -> list[float]:
    size = len(right)
    augmented = [matrix[row][:] + [right[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1.0e-18:
            raise OfflineError("ROM regression is rank deficient")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [item / scale for item in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor == 0.0:
                continue
            augmented[row] = [left - factor * right_value
                              for left, right_value in
                              zip(augmented[row], augmented[column])]
    return [augmented[row][-1] for row in range(size)]


def certified_spectral_radius_bound(values: list[list[float]]) -> float:
    """Mirror the runtime's fail-closed normalized-squaring bound."""
    dimension = len(values)
    matrix = [row[:] for row in values]
    log_scale = 0.0
    exponent = 1
    best = math.inf
    for _ in range(12):
        norm = max(sum(abs(item) for item in row) for row in matrix)
        if norm == 0.0:
            return 0.0
        if not math.isfinite(norm):
            raise OfflineError("ROM stability bound overflowed")
        log_scale += math.log(norm)
        matrix = [[item / norm for item in row] for row in matrix]
        best = min(best, math.exp(log_scale / exponent))
        matrix = [[sum(matrix[row][inner] * matrix[inner][column]
                              for inner in range(dimension))
                   for column in range(dimension)]
                  for row in range(dimension)]
        log_scale *= 2.0
        exponent *= 2
    if not math.isfinite(best):
        raise OfflineError("ROM stability bound is non-finite")
    return best * (1.0 + 64.0 * sys.float_info.epsilon)


def preserve_equilibrium(original_a: list[list[float]],
                         original_b: list[list[float]],
                         original_bias: list[float],
                         stabilized_a: list[list[float]]) -> tuple[list[list[float]], list[float]]:
    states = len(original_a)
    inputs = len(original_b[0])
    original_left = [
        [(1.0 if row == column else 0.0) - original_a[row][column]
         for column in range(states)]
        for row in range(states)
    ]
    stabilized_left = [
        [(1.0 if row == column else 0.0) - stabilized_a[row][column]
         for column in range(states)]
        for row in range(states)
    ]
    equilibrium_bias = solve(original_left, original_bias)
    equilibrium_inputs = [
        solve(original_left, [original_b[row][input_] for row in range(states)])
        for input_ in range(inputs)
    ]
    if not all(math.isfinite(value) for value in [
            *equilibrium_bias,
            *(item for column in equilibrium_inputs for item in column),
    ]):
        raise OfflineError("ROM constant-input equilibrium is non-finite")
    stabilized_bias = [
        sum(stabilized_left[row][column] * equilibrium_bias[column]
            for column in range(states))
        for row in range(states)
    ]
    stabilized_b = [
        [sum(stabilized_left[row][state] * equilibrium_inputs[input_][state]
             for state in range(states))
         for input_ in range(inputs)]
        for row in range(states)
    ]
    return stabilized_b, stabilized_bias


def fit(training: list[dict], ridge: float) -> tuple[list[list[float]], list[list[float]], list[float]]:
    states = len(training[0]["output_names"])
    inputs = len(training[0]["input_names"])
    reference = [
        sum(trace["samples"][0]["temperature_c"][state]
            for trace in training) / len(training)
        for state in range(states)
    ]
    columns = states + inputs + 1
    gram = [[0.0] * columns for _ in range(columns)]
    rhs = [[0.0] * columns for _ in range(states)]
    transitions = 0
    for trace in training:
        for current, following in zip(trace["samples"], trace["samples"][1:]):
            row = [*[value - reference[index] for index, value in
                     enumerate(current["temperature_c"])],
                   *current["power_w"], 1.0]
            target = [value - reference[index] for index, value in
                      enumerate(following["temperature_c"])]
            transitions += 1
            for left in range(columns):
                for right in range(columns):
                    gram[left][right] += row[left] * row[right]
                for output in range(states):
                    rhs[output][left] += row[left] * target[output]
    if transitions < columns:
        raise OfflineError("not enough independent training transitions")
    for index in range(columns - 1):
        gram[index][index] += ridge
    coefficients = [solve(gram, rhs[output]) for output in range(states)]
    a = [row[:states] for row in coefficients]
    b = [row[states:states + inputs] for row in coefficients]
    centered_bias = [row[-1] for row in coefficients]
    stability_bound = certified_spectral_radius_bound(a)
    if stability_bound >= 0.999:
        original_a = [row[:] for row in a]
        factor = 0.98 / stability_bound
        a = [[item * factor for item in row] for row in a]
        b, centered_bias = preserve_equilibrium(
            original_a, b, centered_bias, a)
    bias = [
        reference[row] + centered_bias[row] -
        sum(a[row][column] * reference[column] for column in range(states))
        for row in range(states)
    ]
    return a, b, bias


def predict(trace: dict, a: list[list[float]], b: list[list[float]],
            bias: list[float]) -> list[list[float]]:
    state = trace["samples"][0]["temperature_c"][:]
    result = [state[:]]
    for sample in trace["samples"][:-1]:
        power = sample["power_w"]
        state = [bias[row] + sum(a[row][column] * state[column]
                                 for column in range(len(state))) +
                 sum(b[row][column] * power[column]
                     for column in range(len(power)))
                 for row in range(len(state))]
        result.append(state)
    return result


def main() -> int:
    args = arguments()
    if args.ridge < 0.0:
        raise OfflineError("--ridge must be non-negative")
    if len(args.geometry_sha256) != 64:
        raise OfflineError("--geometry-sha256 must contain 64 hex characters")
    training = [load_dataset(path) for path in args.training]
    held = [load_dataset(path) for path in args.held_out]
    reference = training[0]
    for trace in [*training, *held]:
        if (trace["input_names"] != reference["input_names"] or
                trace["output_names"] != reference["output_names"] or
                trace["sample_period_ns"] != reference["sample_period_ns"]):
            raise OfflineError("all ROM datasets must use identical names and sampling")
    training_hashes = {sha256_file(path) for path in args.training}
    held_hashes = {sha256_file(path) for path in args.held_out}
    if training_hashes & held_hashes:
        raise OfflineError("training and held-out datasets overlap")
    kinds = {trace["trace_kind"] for trace in held}
    missing = REQUIRED_HELD_OUT - kinds
    if missing:
        raise OfflineError(f"held-out set lacks required trace kinds: {sorted(missing)}")
    evidence_label = select_evidence_label(
        args.evidence_label,
        [trace["provenance"]["evidence_label"]
         for trace in [*training, *held]],
        "ROM output",
    )
    a, b, bias = fit(training, args.ridge)
    errors = []
    for trace in held:
        prediction = predict(trace, a, b, bias)
        for actual, predicted in zip(trace["samples"], prediction):
            errors.extend(abs(left - right) for left, right in
                          zip(actual["temperature_c"], predicted))
    states = len(reference["output_names"])
    inputs = len(reference["input_names"])
    payload = {
        "model_id": args.model_id,
        "evidence_label": evidence_label,
        "sample_period_ns": reference["sample_period_ns"],
        "input_names": reference["input_names"],
        "output_names": reference["output_names"],
        "state_count": states,
        "a": [item for row in a for item in row],
        "b": [item for row in b for item in row],
        "bias": bias,
        "c": [1.0 if row == column else 0.0
              for row in range(states) for column in range(states)],
        "d": [0.0] * (states * inputs),
        "offset": [0.0] * states,
        "solver_identity": args.solver_identity,
        "geometry_sha256": args.geometry_sha256,
        "training_split": ",".join(sorted(training_hashes)),
        "held_out_split": ",".join(sorted(held_hashes)),
        "held_out_max_error_c": max(errors),
        "held_out_p95_error_c": percentile(errors, 0.95),
    }
    write_json(args.output,
               {"schema_version": 1, "payload": payload,
                "payload_sha256": sha256_bytes(canonical_json(payload).encode())})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfflineError as error:
        print(f"fit_reduced_model.py: {error}", file=sys.stderr)
        raise SystemExit(2)

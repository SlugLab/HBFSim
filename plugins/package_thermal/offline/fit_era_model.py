#!/usr/bin/env python3
"""Fit a stable grouped ERA ROM from 3D-ICE unit-step responses."""

from __future__ import annotations

import argparse
import math
import pathlib
import sys

try:
    import numpy as np
except ImportError as error:  # pragma: no cover - environment diagnostic
    raise SystemExit("fit_era_model.py requires NumPy for offline SVD") from error

from _common import (EVIDENCE_LABELS, OfflineError, canonical_json,
                     load_dataset, percentile, select_evidence_label,
                     rom_payload_sha256, sha256_file, write_json)


REQUIRED_HELD_OUT = {"square_wave", "burst", "mixed_gpu_hbm_hbf",
                     "write_heavy_hbf", "read_heavy_hbf"}
MAXIMUM_ROM_STATES = 256


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit a grouped eigensystem-realization thermal ROM")
    parser.add_argument("--training", type=pathlib.Path, action="append",
                        required=True)
    parser.add_argument("--held-out", type=pathlib.Path, action="append",
                        required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--geometry-sha256", required=True)
    parser.add_argument("--solver-identity", required=True)
    parser.add_argument("--evidence-label", choices=EVIDENCE_LABELS)
    parser.add_argument("--external-blocks", type=int, default=512)
    parser.add_argument("--external-rank", type=int, default=192)
    parser.add_argument("--hbf-blocks", type=int, default=64)
    parser.add_argument("--hbf-rank", type=int, default=32)
    parser.add_argument("--singular-relative-floor", type=float,
                        default=1.0e-12)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def certified_spectral_radius_bound(values: np.ndarray) -> float:
    matrix = values.copy()
    log_scale = 0.0
    exponent = 1
    best = math.inf
    for _ in range(12):
        norm = float(np.max(np.sum(np.abs(matrix), axis=1)))
        if norm == 0.0:
            return 0.0
        if not math.isfinite(norm):
            raise OfflineError("ROM stability bound overflowed")
        log_scale += math.log(norm)
        matrix /= norm
        best = min(best, math.exp(log_scale / exponent))
        matrix = matrix @ matrix
        log_scale *= 2.0
        exponent *= 2
    return best * (1.0 + 64.0 * sys.float_info.epsilon)


def unit_markov(training: list[dict]) -> tuple[np.ndarray, float]:
    inputs = len(training[0]["input_names"])
    outputs = len(training[0]["output_names"])
    responses: list[np.ndarray | None] = [None] * inputs
    ambient_value: float | None = None
    for trace in training:
        if trace["trace_kind"] != "unit_step":
            raise OfflineError("ERA training data must contain only unit steps")
        powers = np.asarray([sample["power_w"] for sample in trace["samples"]],
                            dtype=float)
        temperatures = np.asarray(
            [sample["temperature_c"] for sample in trace["samples"]],
            dtype=float)
        active_samples = np.flatnonzero(np.max(np.abs(powers), axis=1) > 0.0)
        if active_samples.size == 0:
            raise OfflineError("unit-step trace contains no nonzero input")
        start = int(active_samples[0])
        active_inputs = np.flatnonzero(np.abs(powers[start]) > 0.0)
        if active_inputs.size != 1:
            raise OfflineError("unit-step trace must activate exactly one source")
        source = int(active_inputs[0])
        amplitude = float(powers[start, source])
        if amplitude <= 0.0 or responses[source] is not None:
            raise OfflineError("unit-step source mapping is invalid or duplicated")
        if (np.any(powers[:start] != 0.0) or
                np.any(powers[start:, source] != amplitude) or
                np.any(np.delete(powers[start:], source, axis=1) != 0.0)):
            raise OfflineError("unit-step power waveform is not zero-to-constant")
        ambient = temperatures[start]
        if float(np.max(ambient) - np.min(ambient)) > 1.0e-9:
            raise OfflineError("ERA requires a uniform initial temperature")
        if ambient_value is None:
            ambient_value = float(ambient[0])
        elif abs(ambient_value - float(ambient[0])) > 1.0e-9:
            raise OfflineError("unit-step traces use different ambient temperatures")
        responses[source] = (temperatures[start:] - ambient) / amplitude
    if any(response is None for response in responses):
        raise OfflineError("ERA training lacks one or more unit input responses")
    length = min(response.shape[0] for response in responses
                 if response is not None)
    step = np.stack([response[:length] for response in responses
                     if response is not None], axis=2)
    if step.shape[1:] != (outputs, inputs):
        raise OfflineError("unit-step response dimensions are inconsistent")
    markov = np.empty_like(step)
    markov[0] = step[0]
    markov[1:] = step[1:] - step[:-1]
    return markov, float(ambient_value)


def realize_group(markov: np.ndarray, output_indices: list[int], blocks: int,
                  requested_rank: int, relative_floor: float):
    if not output_indices or blocks < 2 or requested_rank < 1:
        raise OfflineError("ERA group dimensions must be positive")
    if 2 * blocks + 1 >= markov.shape[0]:
        raise OfflineError("unit-step response is too short for ERA block count")
    response = markov[:, output_indices, :]
    h0 = np.block([[response[row + column + 1]
                    for column in range(blocks)]
                   for row in range(blocks)])
    h1 = np.block([[response[row + column + 2]
                    for column in range(blocks)]
                   for row in range(blocks)])
    left, singular, right = np.linalg.svd(h0, full_matrices=False)
    numerical_rank = int(np.count_nonzero(singular >
                                          singular[0] * relative_floor))
    rank = min(requested_rank, numerical_rank)
    if rank < 1:
        raise OfflineError("ERA Hankel matrix has no retained singular modes")
    root = np.sqrt(singular[:rank])
    inverse_root = 1.0 / root
    a = ((inverse_root[:, None] * left[:, :rank].T) @ h1 @
         (right[:rank].T * inverse_root[None, :]))
    b = root[:, None] * right[:rank, :markov.shape[2]]
    c = left[:len(output_indices), :rank] * root[None, :]
    return a, b, c, singular, rank


def grouped_era(markov: np.ndarray, names: list[str], args: argparse.Namespace):
    external = [index for index, name in enumerate(names)
                if not name.lower().startswith("hbf.") and
                name.lower() != "hbf"]
    hbf = [index for index, name in enumerate(names)
           if name.lower().startswith("hbf.") or name.lower() == "hbf"]
    if not external or not hbf:
        raise OfflineError("grouped ERA requires external and hbf.* outputs")
    definitions = [
        ("external", external, args.external_blocks, args.external_rank),
        ("hbf", hbf, args.hbf_blocks, args.hbf_rank),
    ]
    pieces = []
    metadata = []
    for label, indices, blocks, rank in definitions:
        a, b, c, singular, retained = realize_group(
            markov, indices, blocks, rank, args.singular_relative_floor)
        pieces.append((indices, a, b, c))
        metadata.append({
            "group": label, "outputs": [names[index] for index in indices],
            "blocks": blocks, "requested_rank": rank,
            "retained_rank": retained,
            "retained_singular_ratio": float(singular[retained - 1] /
                                               singular[0]),
        })
    states = sum(piece[1].shape[0] for piece in pieces)
    if states > MAXIMUM_ROM_STATES:
        raise OfflineError(
            f"grouped ERA needs {states} states; runtime limit is "
            f"{MAXIMUM_ROM_STATES}")
    a = np.zeros((states, states))
    b = np.zeros((states, markov.shape[2]))
    c = np.zeros((markov.shape[1], states))
    start = 0
    for indices, group_a, group_b, group_c in pieces:
        stop = start + group_a.shape[0]
        a[start:stop, start:stop] = group_a
        b[start:stop] = group_b
        c[indices, start:stop] = group_c
        start = stop
    d = markov[0]
    bound = certified_spectral_radius_bound(a)
    radius = float(np.max(np.abs(np.linalg.eigvals(a))))
    if not math.isfinite(bound) or bound >= 1.0 - 1.0e-9:
        raise OfflineError(
            f"ERA model is not certifiably stable: rho={radius}, bound={bound}")
    return a, b, c, d, metadata, radius, bound


def predict(trace: dict, a: np.ndarray, b: np.ndarray, c: np.ndarray,
            d: np.ndarray, ambient: float) -> list[list[float]]:
    initial = trace["samples"][0]["temperature_c"]
    if max(initial) - min(initial) > 1.0e-9 or abs(initial[0] - ambient) > 1.0e-9:
        raise OfflineError("held-out trace initial temperature differs from ERA ambient")
    state = np.zeros(a.shape[0])
    result = []
    for sample in trace["samples"]:
        power = np.asarray(sample["power_w"], dtype=float)
        result.append((ambient + c @ state + d @ power).tolist())
        state = a @ state + b @ power
    return result


def main() -> int:
    args = arguments()
    if (len(args.geometry_sha256) != 64 or
            args.singular_relative_floor <= 0.0 or
            args.singular_relative_floor >= 1.0):
        raise OfflineError("invalid geometry hash or singular-value floor")
    training = [load_dataset(path) for path in args.training]
    held = [load_dataset(path) for path in args.held_out]
    reference = training[0]
    for trace in [*training, *held]:
        if (trace["input_names"] != reference["input_names"] or
                trace["output_names"] != reference["output_names"] or
                trace["sample_period_ns"] != reference["sample_period_ns"]):
            raise OfflineError("all ERA datasets must use identical names and sampling")
    training_hashes = {sha256_file(path) for path in args.training}
    held_hashes = {sha256_file(path) for path in args.held_out}
    if training_hashes & held_hashes:
        raise OfflineError("training and held-out datasets overlap")
    missing = REQUIRED_HELD_OUT - {trace["trace_kind"] for trace in held}
    if missing:
        raise OfflineError(f"held-out set lacks required trace kinds: {sorted(missing)}")
    evidence_label = select_evidence_label(
        args.evidence_label,
        [trace["provenance"]["evidence_label"] for trace in [*training, *held]],
        "ERA ROM output")
    markov, ambient = unit_markov(training)
    a, b, c, d, groups, radius, bound = grouped_era(
        markov, reference["output_names"], args)
    errors = []
    for trace in held:
        prediction = predict(trace, a, b, c, d, ambient)
        for sample, estimate in zip(trace["samples"], prediction):
            errors.extend(abs(actual - predicted) for actual, predicted in
                          zip(sample["temperature_c"], estimate))
    states = a.shape[0]
    inputs = len(reference["input_names"])
    output_count = len(reference["output_names"])
    shifted_bias = ambient * (np.ones(states) - a @ np.ones(states))
    shifted_offset = ambient * (np.ones(output_count) - c @ np.ones(states))
    payload = {
        "model_id": args.model_id,
        "evidence_label": evidence_label,
        "sample_period_ns": reference["sample_period_ns"],
        "input_names": reference["input_names"],
        "output_names": reference["output_names"],
        "state_count": int(states),
        "a": a.reshape(-1).tolist(),
        "b": b.reshape(-1).tolist(),
        "bias": shifted_bias.tolist(),
        "c": c.reshape(-1).tolist(),
        "d": d.reshape(-1).tolist(),
        "offset": shifted_offset.tolist(),
        "solver_identity": args.solver_identity,
        "geometry_sha256": args.geometry_sha256,
        "training_split": ",".join(sorted(training_hashes)),
        "held_out_split": ",".join(sorted(held_hashes)),
        "held_out_max_error_c": max(errors),
        "held_out_p95_error_c": percentile(errors, 0.95),
    }
    write_json(args.output, {
        "schema_version": 2, "payload": payload,
        "payload_sha256": rom_payload_sha256(payload, 2)})
    write_json(args.output.with_suffix(args.output.suffix + ".fit.json"), {
        "schema_version": 1, "method": "grouped_era",
        "state_count": int(states), "ambient_c": ambient,
        "spectral_radius": radius, "certified_spectral_radius_bound": bound,
        "groups": groups, "held_out_max_error_c": max(errors),
        "held_out_p95_error_c": percentile(errors, 0.95),
    })
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfflineError as error:
        print(f"fit_era_model.py: {error}", file=sys.stderr)
        raise SystemExit(2)

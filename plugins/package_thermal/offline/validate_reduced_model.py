#!/usr/bin/env python3
"""Validate a ROM only against disjoint held-out 3D-ICE traces."""

from __future__ import annotations

import argparse
import math
import pathlib
import sys

from _common import (OfflineError, load_dataset, load_json, percentile,
                     rom_payload_sha256, sha256_file, write_json)

REQUIRED = {"square_wave", "burst", "mixed_gpu_hbm_hbf",
            "write_heavy_hbf", "read_heavy_hbf"}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Held-out ROM acceptance validation")
    parser.add_argument("--model", type=pathlib.Path, required=True)
    parser.add_argument("--held-out", type=pathlib.Path, action="append", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--max-rmse-c", type=float, default=1.0)
    parser.add_argument("--max-steady-hotspot-error-c", type=float, default=1.0)
    parser.add_argument("--max-absolute-error-c", type=float, default=1.0)
    parser.add_argument("--threshold-c", type=float, action="append", default=[80.0, 90.0])
    return parser.parse_args()


def matrix(values: list[float], rows: int, columns: int, name: str) -> list[list[float]]:
    if len(values) != rows * columns:
        raise OfflineError(f"ROM {name} dimensions are invalid")
    return [values[row * columns:(row + 1) * columns] for row in range(rows)]


def crossing(trace: list[tuple[int, float]], threshold: float):
    return next((time for time, value in trace if value >= threshold), None)


def main() -> int:
    args = arguments()
    envelope = load_json(args.model)
    if (set(envelope) != {"schema_version", "payload", "payload_sha256"} or
            envelope["schema_version"] not in {1, 2}):
        raise OfflineError("invalid ROM envelope")
    payload = envelope["payload"]
    if rom_payload_sha256(payload, envelope["schema_version"]) != \
            envelope["payload_sha256"]:
        raise OfflineError("ROM payload checksum mismatch")
    held = [load_dataset(path) for path in args.held_out]
    kinds = {trace["trace_kind"] for trace in held}
    if REQUIRED - kinds:
        raise OfflineError(f"missing held-out trace kinds: {sorted(REQUIRED - kinds)}")
    training_hashes = set(payload["training_split"].split(","))
    actual_hashes = {sha256_file(path) for path in args.held_out}
    if training_hashes & actual_hashes:
        raise OfflineError("validation data overlaps ROM training split")
    if actual_hashes != set(payload["held_out_split"].split(",")):
        raise OfflineError("validation data does not match ROM held-out split")
    names_in = payload["input_names"]
    names_out = payload["output_names"]
    states = payload["state_count"]
    a = matrix(payload["a"], states, states, "A")
    b = matrix(payload["b"], states, len(names_in), "B")
    c = matrix(payload["c"], len(names_out), states, "C")
    d = matrix(payload["d"], len(names_out), len(names_in), "D")
    hbf = [index for index, name in enumerate(names_out) if name.startswith("hbf.")]
    if not hbf:
        raise OfflineError("ROM outputs contain no hbf.* nodes")
    all_errors = []
    aggregate_maximum = None
    trace_reports = []
    all_threshold_classifications_match = True
    for trace in held:
        if trace["input_names"] != names_in or trace["output_names"] != names_out:
            raise OfflineError("held-out node order does not match ROM")
        initial = trace["samples"][0]["temperature_c"]
        if max(initial) - min(initial) > 1.0e-9 and states != len(names_out):
            raise OfflineError(
                "hidden-state ROM validation requires a uniform initial temperature")
        state = (initial[:] if states == len(names_out) else
                 [initial[0]] * states)
        predicted = []
        for sample in trace["samples"]:
            power = sample["power_w"]
            output = [payload["offset"][row] +
                      sum(c[row][column] * state[column] for column in range(states)) +
                      sum(d[row][column] * power[column] for column in range(len(power)))
                      for row in range(len(names_out))]
            predicted.append(output)
            state = [payload["bias"][row] +
                     sum(a[row][column] * state[column] for column in range(states)) +
                     sum(b[row][column] * power[column] for column in range(len(power)))
                     for row in range(states)]
            if (any(not math.isfinite(item) for item in state) or
                    any(not math.isfinite(item) or item < -273.15 or
                        item > 1000.0 for item in output)):
                raise OfflineError("ROM produced invalid thermal state")
        errors = [left - right for sample, estimate in zip(trace["samples"], predicted)
                  for left, right in zip(sample["temperature_c"], estimate)]
        maximum_detail = max(
            ({"absolute_error_c": abs(actual - estimate),
              "signed_error_c": actual - estimate,
              "time_ns": sample["time_ns"],
              "node": names_out[index],
              "actual_c": actual,
              "predicted_c": estimate}
             for sample, prediction in zip(trace["samples"], predicted)
             for index, (actual, estimate) in
             enumerate(zip(sample["temperature_c"], prediction))),
            key=lambda item: item["absolute_error_c"])
        if (aggregate_maximum is None or
                maximum_detail["absolute_error_c"] >
                aggregate_maximum["absolute_error_c"]):
            aggregate_maximum = {**maximum_detail,
                                 "trace_id": trace["trace_id"],
                                 "trace_kind": trace["trace_kind"]}
        all_errors.extend(abs(item) for item in errors)
        rmse = math.sqrt(sum(item * item for item in errors) / len(errors))
        actual_hotspot = [(sample["time_ns"], max(sample["temperature_c"][i] for i in hbf))
                          for sample in trace["samples"]]
        predicted_hotspot = [(sample["time_ns"], max(estimate[i] for i in hbf))
                             for sample, estimate in zip(trace["samples"], predicted)]
        steady_error = abs(actual_hotspot[-1][1] - predicted_hotspot[-1][1])
        actual_gradient = [max(sample["temperature_c"][i] for i in hbf) -
                           min(sample["temperature_c"][i] for i in hbf)
                           for sample in trace["samples"]]
        predicted_gradient = [max(estimate[i] for i in hbf) - min(estimate[i] for i in hbf)
                              for estimate in predicted]
        threshold_errors = {}
        threshold_reports = {}
        for threshold in args.threshold_c:
            actual_time = crossing(actual_hotspot, threshold)
            predicted_time = crossing(predicted_hotspot, threshold)
            classification_match = ((actual_time is None) ==
                                    (predicted_time is None))
            all_threshold_classifications_match &= classification_match
            absolute_error = (abs(actual_time - predicted_time)
                              if actual_time is not None and
                              predicted_time is not None else None)
            # Preserve the legacy scalar field while adding the unambiguous
            # classification and timestamp record required by Phase II.
            threshold_errors[str(threshold)] = absolute_error
            threshold_reports[str(threshold)] = {
                "actual_time_ns": actual_time,
                "predicted_time_ns": predicted_time,
                "absolute_error_ns": absolute_error,
                "classification_match": classification_match,
            }
        trace_reports.append({"trace_id": trace["trace_id"],
                              "trace_kind": trace["trace_kind"],
                              "transient_rmse_c": rmse,
                              "maximum_absolute_error_c": max(abs(item) for item in errors),
                              "maximum_absolute_error": maximum_detail,
                              "steady_state_hotspot_error_c": steady_error,
                              "maximum_stack_gradient_error_c": max(
                                  abs(left - right) for left, right in
                                  zip(actual_gradient, predicted_gradient)),
                              "threshold_crossing_time_error_ns": threshold_errors,
                              "threshold_crossing": threshold_reports})
    maximum_rmse = max(item["transient_rmse_c"] for item in trace_reports)
    maximum_steady = max(item["steady_state_hotspot_error_c"] for item in trace_reports)
    maximum_absolute = max(all_errors)
    accepted = (maximum_rmse <= args.max_rmse_c and
                maximum_steady <= args.max_steady_hotspot_error_c and
                maximum_absolute <= args.max_absolute_error_c and
                all_threshold_classifications_match)
    report = {"schema_version": 1, "accepted": accepted,
              "model_sha256": sha256_file(args.model),
              "held_out_sha256": sorted(actual_hashes),
              "acceptance": {"max_rmse_c": args.max_rmse_c,
                             "max_steady_hotspot_error_c": args.max_steady_hotspot_error_c,
                             "max_absolute_error_c": args.max_absolute_error_c,
                             "require_threshold_classification_match": True},
              "aggregate": {"maximum_rmse_c": maximum_rmse,
                            "maximum_steady_hotspot_error_c": maximum_steady,
                            "maximum_absolute_error_c": maximum_absolute,
                            "maximum_absolute_error": aggregate_maximum,
                            "threshold_classifications_match":
                                all_threshold_classifications_match,
                            "p95_absolute_error_c": percentile(all_errors, 0.95)},
              "traces": trace_reports}
    write_json(args.output, report)
    if not accepted:
        raise OfflineError("ROM failed held-out acceptance thresholds; report was written")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfflineError as error:
        print(f"validate_reduced_model.py: {error}", file=sys.stderr)
        raise SystemExit(2)

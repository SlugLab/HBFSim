"""Retrospective EQ3 acceptance v3; consumes existing CSVs and never solves."""
import argparse
import csv
import json
import math
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

from eq3_acceptance_v2 import (ENERGY_RELATIVE_LIMIT, MAX_HOTSPOT_CONTROL_ERROR_K,
                               V2_ABSOLUTE_CAP_K, V2_AMPLITUDE_FRACTION,
                               V2_BASE_K, energy_score)

NS_PER_S = Decimal("1000000000")


def _finite(value, label):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


def _ns(value, label):
    raw = Decimal(str(value)) * NS_PER_S
    tick = int(raw.to_integral_value(rounding=ROUND_HALF_EVEN))
    if abs(raw - tick) > Decimal("0.5"):
        raise ValueError(f"{label} cannot be normalized to integer ns")
    return tick


def read_series(path):
    result = {}
    with path.open(newline="") as source:
        reader = csv.DictReader(source)
        required = {"time_s", "sensor_id", "temperature_k"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("sensor CSV must contain time_s,sensor_id,temperature_k in K")
        for row in reader:
            sensor = row["sensor_id"]
            if not sensor:
                raise ValueError("sensor_id must not be empty")
            time_ns = _ns(row["time_s"], "time_s")
            temperature = _finite(row["temperature_k"], "temperature_k")
            sequence = result.setdefault(sensor, [])
            if sequence and time_ns <= sequence[-1][0]:
                raise ValueError(f"non-increasing or duplicate normalized time for {sensor}")
            sequence.append((time_ns, temperature))
    if not result:
        raise ValueError("sensor CSV is empty")
    return result


def validate_method(method, sensors):
    if method.get("schema_version") != "eq3-acceptance-v3-method-v1":
        raise ValueError("unsupported acceptance v3 method schema")
    if method.get("temperature_unit") != "K" or method.get("time_unit") != "ns":
        raise ValueError("v3 requires temperature_unit K and time_unit ns")
    ids = method.get("sensor_ids")
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or set(ids) != set(sensors):
        raise ValueError("CSV sensor coverage differs from unique method sensor_ids")
    initial_ns = _ns(method.get("initial_time_s"), "initial_time_s")
    initial = method.get("initial_temperature_k")
    if isinstance(initial, dict):
        if set(initial) != set(sensors):
            raise ValueError("per-sensor initial temperature coverage differs")
        initials = {key: _finite(value, "initial_temperature_k") for key, value in initial.items()}
    else:
        value = _finite(initial, "initial_temperature_k")
        initials = {sensor: value for sensor in sensors}
    windows, seen, kinds = [], set(), set()
    for window in method.get("windows", []):
        wid, kind = window.get("id"), window.get("kind")
        start, end = _ns(window.get("start_s"), "window start_s"), _ns(window.get("end_s"), "window end_s")
        if not wid or wid in seen or kind not in {"full", "excitation", "cooling"} or not initial_ns <= start < end:
            raise ValueError("invalid or duplicate v3 window")
        windows.append({"id": wid, "kind": kind, "start_ns": start, "end_ns": end})
        seen.add(wid); kinds.add(kind)
    if not {"full", "excitation", "cooling"}.issubset(kinds):
        raise ValueError("full, excitation, and cooling windows must be preregistered")
    hotspots, controls = set(method.get("hotspot_sensor_ids", [])), set(method.get("control_sensor_ids", []))
    if not hotspots or not hotspots.issubset(sensors) or not controls.issubset(sensors):
        raise ValueError("invalid hotspot/control sensor coverage")
    thresholds = [_finite(x, "crossing threshold") for x in method.get("crossing_thresholds_k", [])]
    if not thresholds:
        raise ValueError("crossing_thresholds_k must be nonempty")
    q_ref = _finite(method.get("reference_quantization_k", method.get("quantization_k")),
                    "reference_quantization_k")
    q_cand = _finite(method.get("candidate_quantization_k", method.get("quantization_k")),
                     "candidate_quantization_k")
    if q_ref < 0 or q_cand < 0 or method.get("quantization_mode") != "nearest_rounding":
        raise ValueError("v3 requires nonnegative nearest-rounding quantization")
    minimum_ns = _ns(method.get("crossing_min_time_s"), "crossing_min_time_s")
    fraction = _finite(method.get("crossing_fraction"), "crossing_fraction")
    if minimum_ns < 0 or fraction < 0:
        raise ValueError("crossing tolerances must be nonnegative")
    return {"initial_ns": initial_ns, "initials": initials, "windows": windows,
            "hotspots": hotspots, "controls": controls, "thresholds": thresholds,
            "q_ref": q_ref, "q_cand": q_cand,
            "minimum_ns": minimum_ns, "fraction": fraction}


def with_initial(sequence, time_ns, temperature):
    if sequence[0][0] < time_ns:
        raise ValueError("CSV begins before declared initial time")
    if sequence[0][0] == time_ns:
        if sequence[0][1] != temperature:
            raise ValueError("CSV initial temperature differs from declared state")
        return sequence
    return [(time_ns, temperature), *sequence]


def _side(value, threshold, half_q):
    if value + half_q < threshold:
        return -1
    if value - half_q > threshold:
        return 1
    return 0


def crossing_events(sequence, threshold, quantization_k):
    """Extract certain directed events once across the complete trajectory."""
    half = quantization_k / 2.0
    sides = [_side(value, threshold, half) for _, value in sequence]
    events, indeterminate = [], []
    last_definite = None
    ambiguous_start = None
    event_id = 0
    for index, side in enumerate(sides):
        if side == 0:
            if ambiguous_start is None:
                ambiguous_start = index
            continue
        if last_definite is not None and side != sides[last_definite]:
            event_id += 1
            left_time, left_value = sequence[last_definite]
            right_time, right_value = sequence[index]
            possible = []
            for left in (left_value-half, left_value+half):
                for right in (right_value-half, right_value+half):
                    if right == left:
                        continue
                    fraction = (threshold-left)/(right-left)
                    if 0 <= fraction <= 1:
                        possible.append(left_time+(right_time-left_time)*fraction)
            if not possible:
                raise AssertionError("opposite definite sides must bracket threshold")
            events.append({"event_id": event_id,
                           "direction": "up" if side > 0 else "down",
                           "time_lo_ns": math.floor(min(possible)),
                           "time_hi_ns": math.ceil(max(possible)),
                           "quantization_bridged": ambiguous_start is not None})
        elif ambiguous_start is not None:
            # The trace returned to the same certain side; a crossing pair may
            # have occurred inside the quantization band and cannot be scored.
            indeterminate.append({"time_lo_ns": sequence[ambiguous_start][0],
                                  "time_hi_ns": sequence[index][0],
                                  "reason": "threshold_overlap_returned_to_same_side"})
        last_definite = index
        ambiguous_start = None
    if ambiguous_start is not None:
        indeterminate.append({"time_lo_ns": sequence[ambiguous_start][0],
                              "time_hi_ns": sequence[-1][0],
                              "reason": "threshold_overlap_at_trajectory_end"})
    if last_definite is None:
        indeterminate = [{"time_lo_ns": sequence[0][0], "time_hi_ns": sequence[-1][0],
                          "reason": "entire_trajectory_overlaps_threshold"}]
    return events, indeterminate


def _interval_gap(left, right):
    return max(0, left["time_lo_ns"] - right["time_hi_ns"],
               right["time_lo_ns"] - left["time_hi_ns"])


def _window_assignment(lo, hi, windows):
    terminal = max(window["end_ns"] for window in windows)
    certain, possible = [], []
    for window in windows:
        closed_end = window["end_ns"] == terminal
        contains_hi = hi <= window["end_ns"] if closed_end else hi < window["end_ns"]
        if window["start_ns"] <= lo and contains_hi:
            certain.append(window["id"])
        elif hi >= window["start_ns"] and (lo < window["end_ns"] or (closed_end and lo == window["end_ns"])):
            possible.append(window["id"])
    return {"certain_window_ids": certain, "possible_window_ids": possible,
            "status": ("INDETERMINATE_WINDOW_ASSIGNMENT" if possible else
                       "ASSIGNED" if certain else "OUTSIDE_DECLARED_WINDOWS"),
            "window_semantics": "[start,end), global terminal end included"}


def crossing_score(reference, candidate, threshold, q_ref, q_cand,
                   minimum_ns, fraction, windows):
    ref, ref_ind = crossing_events(reference, threshold, q_ref)
    cand, cand_ind = crossing_events(candidate, threshold, q_cand)
    matched, failure = [], None
    # A count difference is definite only when neither trace contains an
    # unresolved threshold-band excursion. Otherwise quantization could hide
    # the event, so the result must remain indeterminate.
    if len(ref) != len(cand) and not (ref_ind or cand_ind):
        failure = "DEFINITE_MISSING_OR_EXTRA_CROSSING"
    elif len(ref) == len(cand):
        for expected, actual in zip(ref, cand):
            midpoint = (expected["time_lo_ns"] + expected["time_hi_ns"]) // 2
            allowed = max(minimum_ns, int(round(fraction * midpoint)))
            gap = _interval_gap(expected, actual)
            if expected["direction"] != actual["direction"]:
                failure = "WRONG_DIRECTION"
            elif gap > allowed:
                failure = "CROSSING_TIMEOUT"
            lo = min(expected["time_lo_ns"], actual["time_lo_ns"])
            hi = max(expected["time_hi_ns"], actual["time_hi_ns"])
            matched.append({"reference_event_id": expected["event_id"],
                            "candidate_event_id": actual["event_id"],
                            "direction": expected["direction"], "interval_gap_ns": gap,
                            "allowed_ns": allowed, "reference": expected, "candidate": actual,
                            "window_assignment": _window_assignment(lo, hi, windows)})
            if failure:
                break
    window_ambiguous = any(row["window_assignment"]["status"].startswith("INDETERMINATE") for row in matched)
    if failure:
        status = "FAIL"
    elif ref_ind or cand_ind or window_ambiguous:
        status = "INDETERMINATE_QUANTIZATION"
    else:
        status = "PASS" if ref else "NOT_APPLICABLE"
    return {"threshold_k": threshold, "reference_quantization_k": q_ref,
            "candidate_quantization_k": q_cand, "status": status,
            "failure_reason": failure, "reference_events": ref, "candidate_events": cand,
            "reference_indeterminate": ref_ind, "candidate_indeterminate": cand_ind,
            "matches": matched, "extraction_scope": "complete_trajectory_once"}


def _interpolate(sequence, tick):
    for t, value in sequence:
        if t == tick:
            return value
    for left, right in zip(sequence, sequence[1:]):
        if left[0] < tick < right[0]:
            fraction = (tick - left[0]) / (right[0] - left[0])
            return left[1] + fraction * (right[1] - left[1])
    raise ValueError("window boundary outside available data")


def window_score(reference, candidate, window, is_hotspot, is_control):
    start, end = window["start_ns"], window["end_ns"]
    if start < reference[0][0] or start < candidate[0][0] or end > reference[-1][0] or end > candidate[-1][0]:
        raise ValueError("window outside available data")
    ticks = sorted({start, end, *(t for t, _ in reference if start < t < end),
                    *(t for t, _ in candidate if start < t < end)})
    refs, cands = [_interpolate(reference, t) for t in ticks], [_interpolate(candidate, t) for t in ticks]
    signed = [b-a for a, b in zip(refs, cands)]
    area = 0.0
    for index, (left, right) in enumerate(zip(signed, signed[1:])):
        duration = (ticks[index+1]-ticks[index]) / 1e9
        if left * right >= 0:
            area += duration * (abs(left)+abs(right)) / 2
        else:
            area += duration * (left*left+right*right) / (2*(abs(left)+abs(right)))
    duration = (end-start)/1e9
    mae = area/duration
    amplitude = max(refs)-min(refs)
    limit = min(V2_ABSOLUTE_CAP_K, V2_BASE_K+V2_AMPLITUDE_FRACTION*amplitude)
    maximum = max(abs(x) for x in signed)
    passed = mae <= limit and (not (is_hotspot or is_control) or maximum <= MAX_HOTSPOT_CONTROL_ERROR_K)
    return {"window_id": window["id"], "window_kind": window["kind"],
            "start_ns": start, "end_ns": end, "time_weighted_mae_k": mae,
            "v2_mae_limit_k": limit, "reference_amplitude_k": amplitude,
            "max_abs_error_k": maximum, "hotspot_control_max_limit_k": MAX_HOTSPOT_CONTROL_ERROR_K,
            "temperature_pass": passed, "comparison_grid": "union_of_integer_ns_sample_times"}


def analyze(reference_path, candidate_path, method, energy_receipt):
    reference, candidate = read_series(reference_path), read_series(candidate_path)
    if set(reference) != set(candidate):
        raise ValueError("sensor coverage differs")
    checked = validate_method(method, reference)
    scores, crossings = [], []
    for sensor in sorted(reference):
        ref = with_initial(reference[sensor], checked["initial_ns"], checked["initials"][sensor])
        cand = with_initial(candidate[sensor], checked["initial_ns"], checked["initials"][sensor])
        for window in checked["windows"]:
            row = window_score(ref, cand, window, sensor in checked["hotspots"], sensor in checked["controls"])
            row["sensor_id"] = sensor; scores.append(row)
        for threshold in checked["thresholds"]:
            row = crossing_score(ref, cand, threshold, checked["q_ref"], checked["q_cand"],
                                 checked["minimum_ns"], checked["fraction"], checked["windows"])
            row["sensor_id"] = sensor; crossings.append(row)
    energy = energy_score(energy_receipt)
    numeric_fail = not energy["pass"] or any(not row["temperature_pass"] for row in scores)
    crossing_fail = any(row["status"] == "FAIL" for row in crossings)
    indeterminate = any(row["status"] == "INDETERMINATE_QUANTIZATION" for row in crossings)
    status = ("NUMERICAL_FAIL" if numeric_fail else "CROSSING_FAIL" if crossing_fail else
              "INDETERMINATE_QUANTIZATION" if indeterminate else "PASS")
    return {"schema_version": "eq3-acceptance-v3-result-v1", "status": status,
            "analysis_kind": "RETROSPECTIVE_EXISTING_RAW", "physical_calibration": False,
            "reference_qualification_inherited": False, "method": method, "energy": energy,
            "temperature_scores": scores, "crossing_scores": crossings,
            "unchanged_limits": {"v2_absolute_cap_k": V2_ABSOLUTE_CAP_K,
                                 "v2_base_k": V2_BASE_K,
                                 "v2_amplitude_fraction": V2_AMPLITUDE_FRACTION,
                                 "hotspot_control_max_k": MAX_HOTSPOT_CONTROL_ERROR_K,
                                 "energy_relative_limit": ENERGY_RELATIVE_LIMIT}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--method", type=Path, required=True)
    parser.add_argument("--energy-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(args.reference, args.candidate, json.loads(args.method.read_text()),
                     json.loads(args.energy_receipt.read_text()))
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
    print(json.dumps({"status": result["status"], "temperature_scores": len(result["temperature_scores"]),
                      "crossing_scores": len(result["crossing_scores"])}))


if __name__ == "__main__":
    main()

"""EQ3 D2 fast-model acceptance v2; never runs a thermal solver."""
import argparse
import csv
import json
import math
from pathlib import Path


V2_ABSOLUTE_CAP_K = 1.0
V2_BASE_K = 0.25
V2_AMPLITUDE_FRACTION = 0.05
LEGACY_NMAE_LIMIT = 0.05
MAX_HOTSPOT_CONTROL_ERROR_K = 2.0
ENERGY_RELATIVE_LIMIT = 0.001


def _finite(value, label):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return value


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
            time_s = _finite(row["time_s"], "time_s")
            temperature_k = _finite(row["temperature_k"], "temperature_k")
            sequence = result.setdefault(sensor, [])
            if sequence and time_s <= sequence[-1][0]:
                raise ValueError(f"non-increasing or duplicate time for {sensor}")
            sequence.append((time_s, temperature_k))
    if not result:
        raise ValueError("sensor CSV is empty")
    return result


def validate_method(method, sensors):
    if method.get("schema_version") != "eq3-acceptance-v2-method-v1":
        raise ValueError("unsupported acceptance method schema")
    if method.get("temperature_unit") != "K":
        raise ValueError("temperature_unit must be K")
    expected_sensors = method.get("sensor_ids")
    if (not isinstance(expected_sensors, list) or not expected_sensors or
            len(expected_sensors) != len(set(expected_sensors)) or
            any(not isinstance(sensor, str) or not sensor for sensor in expected_sensors)):
        raise ValueError("sensor_ids must be a nonempty unique string list")
    if set(expected_sensors) != set(sensors):
        raise ValueError("CSV sensor coverage differs from preregistered sensor_ids")
    initial_time = _finite(method.get("initial_time_s"), "initial_time_s")
    initial = method.get("initial_temperature_k")
    if isinstance(initial, dict):
        if set(initial) != set(sensors):
            raise ValueError("per-sensor initial temperature coverage differs")
        initial_by_sensor = {key: _finite(value, "initial_temperature_k")
                             for key, value in initial.items()}
    else:
        value = _finite(initial, "initial_temperature_k")
        initial_by_sensor = {sensor: value for sensor in sensors}
    windows = method.get("windows")
    if not isinstance(windows, list) or not windows:
        raise ValueError("windows must be a nonempty list")
    seen = set()
    kinds = set()
    checked_windows = []
    for window in windows:
        identifier = window.get("id")
        kind = window.get("kind")
        start = _finite(window.get("start_s"), "window start_s")
        end = _finite(window.get("end_s"), "window end_s")
        if not identifier or identifier in seen or kind not in {"full", "excitation", "cooling"}:
            raise ValueError("window ids must be unique and kinds full/excitation/cooling")
        if not initial_time <= start < end:
            raise ValueError("window must have positive duration after initial_time_s")
        seen.add(identifier)
        kinds.add(kind)
        checked_windows.append({"id": identifier, "kind": kind,
                                "start_s": start, "end_s": end})
    if not {"full", "excitation", "cooling"}.issubset(kinds):
        raise ValueError("full, excitation, and cooling windows must be preregistered")
    hotspots = set(method.get("hotspot_sensor_ids", []))
    controls = set(method.get("control_sensor_ids", []))
    if not hotspots:
        raise ValueError("hotspot sensor set must be nonempty")
    if not hotspots.issubset(sensors) or not controls.issubset(sensors):
        raise ValueError("hotspot/control sensor is absent from CSV coverage")
    thresholds = tuple(_finite(value, "crossing threshold")
                       for value in method.get("crossing_thresholds_k", []))
    if not thresholds:
        raise ValueError("crossing_thresholds_k must be nonempty")
    crossing_min = _finite(method.get("crossing_min_time_s"), "crossing_min_time_s")
    crossing_fraction = _finite(method.get("crossing_fraction"), "crossing_fraction")
    if crossing_min < 0 or crossing_fraction < 0:
        raise ValueError("crossing tolerances must be nonnegative")
    return {"initial_time_s": initial_time, "initial_by_sensor": initial_by_sensor,
            "windows": checked_windows, "hotspots": hotspots, "controls": controls,
            "thresholds": thresholds, "crossing_min_time_s": crossing_min,
            "crossing_fraction": crossing_fraction}


def with_initial(sequence, initial_time_s, initial_k):
    if sequence[0][0] < initial_time_s:
        raise ValueError("CSV begins before declared initial_time_s")
    if sequence[0][0] == initial_time_s:
        if sequence[0][1] != initial_k:
            raise ValueError("CSV initial temperature differs from declared initial state")
        return sequence
    return [(initial_time_s, initial_k), *sequence]


def interpolate(sequence, target):
    for time_s, value in sequence:
        if time_s == target:
            return value
    for left, right in zip(sequence, sequence[1:]):
        if left[0] < target < right[0]:
            fraction = (target - left[0]) / (right[0] - left[0])
            return left[1] + fraction * (right[1] - left[1])
    raise ValueError("window boundary is outside available data")


def clip(sequence, start, end):
    if start < sequence[0][0] or end > sequence[-1][0]:
        raise ValueError("window is outside available data")
    result = [(start, interpolate(sequence, start))]
    result.extend((time_s, value) for time_s, value in sequence if start < time_s < end)
    result.append((end, interpolate(sequence, end)))
    return result


def crossing_events(sequence, threshold):
    events = []
    for left, right in zip(sequence, sequence[1:]):
        t0, v0 = left
        time_s, value = right
        if (v0 < threshold <= value) or (value < threshold <= v0):
            events.append({"direction": "up" if value > v0 else "down",
                           "time_s": t0 + (time_s - t0) *
                           (threshold - v0) / (value - v0)})
    return events


def crossing_score(reference, candidate, threshold, uncertainty_k,
                   minimum_time_s, fraction):
    reference_events = crossing_events(reference, threshold)
    candidate_events = crossing_events(candidate, threshold)
    ambiguous = any(abs(left[1] - threshold) <= uncertainty_k and
                    abs(right[1] - threshold) <= uncertainty_k
                    for left, right in zip(reference, reference[1:]))
    if not reference_events and not candidate_events:
        legacy_status = "NOT_APPLICABLE"
    elif len(reference_events) != len(candidate_events):
        legacy_status = "FAIL"
    else:
        legacy_status = "PASS"
        for expected, actual in zip(reference_events, candidate_events):
            allowed = max(minimum_time_s, fraction * expected["time_s"])
            if (expected["direction"] != actual["direction"] or
                    abs(expected["time_s"] - actual["time_s"]) > allowed):
                legacy_status = "FAIL"
                break
    status = "THRESHOLD_AMBIGUOUS" if ambiguous else legacy_status
    return {"threshold_k": threshold, "uncertainty_band_k": uncertainty_k,
            "reference": reference_events, "candidate": candidate_events,
            "status": status, "legacy_v1_status": legacy_status,
            "ambiguity_rule": "two consecutive reference observations within threshold +/- L",
            "ambiguity_rule_basis": "ENGINEERING_INFERENCE",
            "ambiguity_is_global_numeric_veto": False}


def sensor_window_score(reference, candidate, window, is_hotspot, is_control,
                        thresholds, crossing_min_time_s, crossing_fraction):
    start, end = window["start_s"], window["end_s"]
    reference = clip(reference, start, end)
    candidate = clip(candidate, start, end)
    if [item[0] for item in reference] != [item[0] for item in candidate]:
        raise ValueError("reference and candidate window timestamps differ")
    signed_errors = [right[1] - left[1] for left, right in zip(reference, candidate)]
    errors = [abs(value) for value in signed_errors]
    interval_areas = []
    for index in range(len(errors) - 1):
        duration_s = reference[index + 1][0] - reference[index][0]
        left, right = signed_errors[index], signed_errors[index + 1]
        if left * right >= 0:
            area = duration_s * (abs(left) + abs(right)) * .5
        else:
            magnitudes = abs(left) + abs(right)
            area = duration_s * (left * left + right * right) / (2 * magnitudes)
        interval_areas.append(area)
    integral = math.fsum(interval_areas)
    duration = end - start
    mae = integral / duration
    arithmetic_mae = math.fsum(errors) / len(errors)
    amplitude = max(value for _, value in reference) - min(value for _, value in reference)
    limit = min(V2_ABSOLUTE_CAP_K, V2_BASE_K + V2_AMPLITUDE_FRACTION * amplitude)
    maximum = max(errors)
    crossings = [crossing_score(reference, candidate, threshold, limit,
                                crossing_min_time_s, crossing_fraction)
                 for threshold in thresholds]
    crossing_pass = all(item["legacy_v1_status"] != "FAIL" for item in crossings)
    maximum_pass = (not (is_hotspot or is_control) or
                    maximum <= MAX_HOTSPOT_CONTROL_ERROR_K)
    return {"window_id": window["id"], "window_kind": window["kind"],
            "start_s": start, "end_s": end, "duration_s": duration,
            "reference_amplitude_k": amplitude, "time_weighted_mae_k": mae,
            "v2_mae_limit_k": limit, "max_abs_error_k": maximum,
            "is_grid_hotspot": is_hotspot, "is_control_sensor": is_control,
            "hotspot_control_max_limit_k": MAX_HOTSPOT_CONTROL_ERROR_K,
            "crossings": crossings,
            "v2_pass": mae <= limit and maximum_pass and crossing_pass,
            "sample_arithmetic_mae_k_for_comparison": arithmetic_mae}


def exact_legacy_v1(reference, candidate, initial_time_s, initial_by_sensor,
                    thresholds, crossing_min_time_s, crossing_fraction):
    """Reproduce the historical full-trajectory arithmetic score exactly."""
    rows = []
    for sensor in sorted(reference):
        ref = reference[sensor]
        cand = candidate[sensor]
        errors = [abs(left[1] - right[1]) for left, right in zip(ref, cand)]
        arithmetic_mae = math.fsum(errors) / len(errors)
        amplitude = max(value for _, value in ref) - min(value for _, value in ref)
        maximum = max(errors)
        initial = initial_by_sensor[sensor]
        crossings = [crossing_score(
            with_initial(ref, initial_time_s, initial),
            with_initial(cand, initial_time_s, initial), threshold,
            0.0, crossing_min_time_s, crossing_fraction)
            for threshold in thresholds]
        crossing_pass = all(item["legacy_v1_status"] != "FAIL"
                            for item in crossings)
        normalized = arithmetic_mae / max(amplitude, 1.0)
        passed = (arithmetic_mae <= 1.0 and normalized <= LEGACY_NMAE_LIMIT and
                  ("hotspot" not in sensor or maximum <= MAX_HOTSPOT_CONTROL_ERROR_K) and
                  crossing_pass)
        rows.append({"sensor_id": sensor,
                     "mean_semantics": "sample_arithmetic_mean_without_synthetic_initial",
                     "mae_k": arithmetic_mae, "mae_limit_k": 1.0,
                     "reference_amplitude_k": amplitude,
                     "normalized_mae": normalized,
                     "normalized_mae_limit": LEGACY_NMAE_LIMIT,
                     "max_abs_error_k": maximum,
                     "hotspot_max_limit_k": MAX_HOTSPOT_CONTROL_ERROR_K,
                     "crossings": crossings, "pass": passed})
    return {"status": "PASS" if all(row["pass"] for row in rows)
                      else "NUMERICAL_FAIL",
            "exact_historical_formula": True,
            "retained_not_v2_veto": True,
            "sensors": rows}


def energy_score(receipt):
    try:
        input_j = _finite(receipt["total_input_energy_j"], "total_input_energy_j")
        stored_j = _finite(receipt["stored_energy_change_j"], "stored_energy_change_j")
        boundary_j = _finite(receipt["boundary_loss_j"], "boundary_loss_j")
    except KeyError as error:
        raise ValueError(f"energy receipt missing {error.args[0]}") from error
    residual_j = input_j - boundary_j - stored_j
    relative = abs(residual_j) / max(input_j, 1.0)
    return {"total_input_energy_j": input_j, "boundary_loss_j": boundary_j,
            "stored_energy_change_j": stored_j, "residual_j": residual_j,
            "relative_residual": relative, "limit": ENERGY_RELATIVE_LIMIT,
            "pass": relative <= ENERGY_RELATIVE_LIMIT}


def analyze(reference_path, candidate_path, method, energy_receipt):
    reference = read_series(reference_path)
    candidate = read_series(candidate_path)
    if set(reference) != set(candidate):
        raise ValueError("sensor coverage differs")
    for sensor in reference:
        if [item[0] for item in reference[sensor]] != [item[0] for item in candidate[sensor]]:
            raise ValueError(f"timestamp coverage differs for {sensor}")
    checked = validate_method(method, set(reference))
    scores = []
    for sensor in sorted(reference):
        initial = checked["initial_by_sensor"][sensor]
        ref = with_initial(reference[sensor], checked["initial_time_s"], initial)
        cand = with_initial(candidate[sensor], checked["initial_time_s"], initial)
        for window in checked["windows"]:
            score = sensor_window_score(
                ref, cand, window, sensor in checked["hotspots"],
                sensor in checked["controls"], checked["thresholds"],
                checked["crossing_min_time_s"], checked["crossing_fraction"])
            score["sensor_id"] = sensor
            scores.append(score)
    energy = energy_score(energy_receipt)
    ambiguous = any(crossing["status"] == "THRESHOLD_AMBIGUOUS"
                    for score in scores for crossing in score["crossings"])
    v2_pass = energy["pass"] and all(score["v2_pass"] for score in scores)
    legacy = exact_legacy_v1(
        reference, candidate, checked["initial_time_s"],
        checked["initial_by_sensor"], checked["thresholds"],
        checked["crossing_min_time_s"], checked["crossing_fraction"])
    legacy["energy_reported_separately"] = energy
    status = ("PASS_WITH_THRESHOLD_AMBIGUITY" if v2_pass and ambiguous else
              "PASS" if v2_pass else "NUMERICAL_FAIL_WITH_THRESHOLD_AMBIGUITY"
              if ambiguous else "NUMERICAL_FAIL")
    return {"schema_version": "eq3-acceptance-v2-result-v1",
            "analysis_kind": "fast_against_selected_reference",
            "status": status, "physical_calibration": False,
            "reference_qualification_inherited": False,
            "method": method, "energy": energy, "scores": scores,
            "legacy_v1": legacy}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--method", type=Path, required=True)
    parser.add_argument("--energy-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    method = json.loads(args.method.read_text())
    energy = json.loads(args.energy_receipt.read_text())
    result = analyze(args.reference, args.candidate, method, energy)
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
    print(json.dumps({"status": result["status"],
                      "legacy_v1": result["legacy_v1"]["status"],
                      "sensor_window_scores": len(result["scores"])}))


if __name__ == "__main__":
    main()

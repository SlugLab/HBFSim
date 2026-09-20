"""Windowed adjacent-grid diagnostics for completed EQ3 sensor CSVs.

This is a read-only postprocessor.  It deliberately does not estimate a
Richardson order: one global maximum, potentially from different sensors and
times on each grid, is not a valid convergence observable.
"""
import argparse
import csv
import json
import math
from pathlib import Path

from eq3_acceptance_v2 import clip, read_series, validate_method, with_initial


GRID_INPUTS = (("R01", "4mm"), ("R02", "2mm"), ("R03", "1mm"))


def _cell_rows(path, series):
    """Read only provenance fields omitted by acceptance-v2's shared reader."""
    cells = {}
    with path.open(newline="") as source:
        reader = csv.DictReader(source)
        required = {"time_s", "sensor_id", "temperature_k"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("sensor CSV must contain time_s,sensor_id,temperature_k in K")
        for row in reader:
            key = (row["sensor_id"], float(row["time_s"]))
            if key in cells:
                raise ValueError(f"duplicate raw observation for {key[0]} at {key[1]}")
            cell = row.get("hotspot_cell_id") or None
            cells[key] = {"temperature_k": float(row["temperature_k"]),
                          "hotspot_cell_id": cell}
    expected = {(sensor, time_s): temperature
                for sensor, values in series.items()
                for time_s, temperature in values}
    if set(cells) != set(expected):
        raise ValueError("raw provenance rows differ from parsed sensor coverage")
    for key, temperature in expected.items():
        if cells[key]["temperature_k"] != temperature:
            raise ValueError("raw provenance temperature differs from parsed series")
    return cells


def _sensor_group(sensor):
    leaf = sensor.rsplit(":", 1)[-1]
    if leaf == "mean":
        return "mean"
    if leaf == "hotspot" or "hotspot" in leaf:
        return "hotspot"
    return "other"


def _time_weighted_mae(reference, candidate):
    signed = [right[1] - left[1] for left, right in zip(reference, candidate)]
    areas = []
    for index in range(len(signed) - 1):
        duration = reference[index + 1][0] - reference[index][0]
        left, right = signed[index], signed[index + 1]
        if left * right >= 0:
            areas.append(duration * (abs(left) + abs(right)) * 0.5)
        else:
            magnitude = abs(left) + abs(right)
            areas.append(duration * (left * left + right * right) /
                         (2 * magnitude))
    return math.fsum(areas) / (reference[-1][0] - reference[0][0])


def _sensor_score(sensor, window, reference, candidate, ref_cells, cand_cells):
    start, end = window["start_s"], window["end_s"]
    ref_window = clip(reference, start, end)
    cand_window = clip(candidate, start, end)
    if [item[0] for item in ref_window] != [item[0] for item in cand_window]:
        raise ValueError(f"window timestamp coverage differs for {sensor}")

    # Keep the maximum tied to an actual CSV observation so that cell identity
    # and both temperatures remain exact rather than inferred at a boundary.
    observed = []
    for time_s, reference_k in reference:
        if start <= time_s <= end and (sensor, time_s) in ref_cells:
            candidate_k = cand_cells[(sensor, time_s)]["temperature_k"]
            observed.append((abs(candidate_k - reference_k), time_s,
                             reference_k, candidate_k))
    if not observed:
        raise ValueError(f"window {window['id']} contains no raw observation for {sensor}")
    error, time_s, reference_k, candidate_k = max(observed, key=lambda row: row[0])
    point = {
        "sensor_id": sensor,
        "time_s": time_s,
        "reference_temperature_k": reference_k,
        "candidate_temperature_k": candidate_k,
        "abs_error_k": error,
        "reference_hotspot_cell_id": ref_cells[(sensor, time_s)]["hotspot_cell_id"],
        "candidate_hotspot_cell_id": cand_cells[(sensor, time_s)]["hotspot_cell_id"],
        "observation_semantics": "exact_registered_csv_row",
    }
    return {"sensor_id": sensor, "group": _sensor_group(sensor),
            "time_weighted_mae_k": _time_weighted_mae(ref_window, cand_window),
            "max_registered_abs_error_k": error,
            "worst_registered_point": point}


def _group_scores(sensor_scores):
    output = {}
    for group in ("mean", "hotspot", "other"):
        members = [score for score in sensor_scores if score["group"] == group]
        if not members:
            output[group] = {"sensor_count": 0, "status": "NOT_APPLICABLE"}
            continue
        worst = max(members, key=lambda item: item["max_registered_abs_error_k"])
        output[group] = {
            "sensor_count": len(members),
            "mean_sensor_time_weighted_mae_k": (
                math.fsum(item["time_weighted_mae_k"] for item in members) /
                len(members)),
            "max_registered_abs_error_k": worst["max_registered_abs_error_k"],
            "worst_registered_point": worst["worst_registered_point"],
        }
    return output


def analyze(r01_path, r02_path, r03_path, method):
    paths = (Path(r01_path), Path(r02_path), Path(r03_path))

    # Validate every input before constructing either adjacent-grid result.
    # This is the key guard against reporting a partial, still-running R03.
    series = [read_series(path) for path in paths]
    sensors = set(series[0])
    checked = validate_method(method, sensors)
    for run_id, run_series in zip(("R01", "R02", "R03"), series):
        if set(run_series) != sensors:
            raise ValueError(f"{run_id} sensor coverage differs")
    for sensor in sorted(sensors):
        expected_times = [item[0] for item in series[0][sensor]]
        for run_id, run_series in zip(("R02", "R03"), series[1:]):
            if [item[0] for item in run_series[sensor]] != expected_times:
                raise ValueError(f"{run_id} timestamp coverage differs for {sensor}")
        initial = checked["initial_by_sensor"][sensor]
        for run_series in series:
            full = with_initial(run_series[sensor], checked["initial_time_s"], initial)
            for window in checked["windows"]:
                clip(full, window["start_s"], window["end_s"])

    cells = [_cell_rows(path, run_series)
             for path, run_series in zip(paths, series)]
    pairs = []
    for left_index in range(2):
        right_index = left_index + 1
        left_id, left_grid = GRID_INPUTS[left_index]
        right_id, right_grid = GRID_INPUTS[right_index]
        windows = []
        for window in checked["windows"]:
            scores = []
            for sensor in sorted(sensors):
                initial = checked["initial_by_sensor"][sensor]
                reference = with_initial(series[left_index][sensor],
                                         checked["initial_time_s"], initial)
                candidate = with_initial(series[right_index][sensor],
                                         checked["initial_time_s"], initial)
                scores.append(_sensor_score(sensor, window, reference, candidate,
                                            cells[left_index], cells[right_index]))
            windows.append({"window_id": window["id"], "window_kind": window["kind"],
                            "start_s": window["start_s"], "end_s": window["end_s"],
                            "groups": _group_scores(scores), "sensors": scores})
        pairs.append({"reference_run_id": left_id, "reference_grid": left_grid,
                      "candidate_run_id": right_id, "candidate_grid": right_grid,
                      "windows": windows})

    return {
        "schema_version": "eq3-spatial-diagnostic-v1",
        "analysis_kind": "completed_adjacent_grid_same_window_postprocess",
        "input_status": "ALL_THREE_COMPLETE_AND_COVERAGE_MATCHED",
        "temperature_unit": "K",
        "group_rule": "sensor leaf mean -> mean; leaf containing hotspot -> hotspot; else other",
        "maximum_semantics": "maximum over exact registered CSV rows inside each window",
        "time_weighted_mae_semantics": "linear interpolation at v2 window boundaries",
        "inputs": [{"run_id": run_id, "grid": grid, "sensor_csv": str(path)}
                   for (run_id, grid), path in zip(GRID_INPUTS, paths)],
        "richardson_order": "NOT_COMPUTED",
        "richardson_reason": (
            "No global-maximum Richardson estimate: sensor and time identity must remain fixed"),
        "pairs": pairs,
    }


def write_result(path, result):
    with Path(path).open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--r01", type=Path, required=True,
                        help="completed R01/4mm sensors.csv")
    parser.add_argument("--r02", type=Path, required=True,
                        help="completed R02/2mm sensors.csv")
    parser.add_argument("--r03", type=Path, required=True,
                        help="completed R03/1mm sensors.csv")
    parser.add_argument("--method", type=Path, required=True,
                        help="EQ3 acceptance-v2 method JSON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    method = json.loads(args.method.read_text())
    result = analyze(args.r01, args.r02, args.r03, method)
    write_result(args.output, result)
    print(json.dumps({"status": result["input_status"],
                      "adjacent_pairs": len(result["pairs"])}))


if __name__ == "__main__":
    main()

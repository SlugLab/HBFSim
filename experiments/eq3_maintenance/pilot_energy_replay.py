#!/usr/bin/env python3
"""Convert committed maintenance-pilot thermal windows to RC node events.

Only ``WINDOW_TOTAL`` rows are consumed.  The ACTIVITY rows in the same ledger
are evidence for those totals and must not be integrated a second time.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _seconds(nanoseconds):
    return format(nanoseconds / 1_000_000_000, ".17g")


def read_windows(path):
    windows = []
    with Path(path).open(newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"kind", "start_ns", "end_ns", "component_energy_j", "phase"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("energy ledger lacks the WINDOW_TOTAL contract")
        for row_number, row in enumerate(reader, 2):
            if row["kind"] != "WINDOW_TOTAL":
                continue
            try:
                start = int(row["start_ns"])
                end = int(row["end_ns"])
                totals = json.loads(row["component_energy_j"])
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"malformed WINDOW_TOTAL at CSV row {row_number}") from error
            if start < 0 or end <= start or not isinstance(totals, dict):
                raise ValueError(f"invalid WINDOW_TOTAL at CSV row {row_number}")
            clean = {}
            for component, energy in totals.items():
                if not isinstance(component, str) or not component:
                    raise ValueError(f"invalid component at CSV row {row_number}")
                if isinstance(energy, bool) or not isinstance(energy, (int, float)):
                    raise ValueError(f"invalid energy at CSV row {row_number}")
                energy = float(energy)
                if not math.isfinite(energy) or energy < 0:
                    raise ValueError(f"invalid energy at CSV row {row_number}")
                if energy:
                    clean[component] = energy
            windows.append({"start_ns": start, "end_ns": end,
                            "phase": row["phase"] or "UNKNOWN",
                            "component_energy_j": clean})
    if not windows:
        raise ValueError("energy ledger contains no WINDOW_TOTAL rows")
    for index, window in enumerate(windows):
        if index and window["start_ns"] != windows[index - 1]["end_ns"]:
            raise ValueError("WINDOW_TOTAL intervals must be ordered and contiguous")
    return windows


def convert(energy_csv, grid_json):
    windows = read_windows(energy_csv)
    grid = json.loads(Path(grid_json).read_text())
    cells = grid.get("cells")
    component_cells = grid.get("component_cells")
    if not isinstance(cells, list) or not isinstance(component_cells, dict):
        raise ValueError("grid lacks cells/component_cells")

    lines = ["HBFSIM_EQ3_THERMAL_EVENTS 1"]
    source_by_component = {}
    emitted_by_component = {}
    event_id = 0
    phase_windows = []
    for window in windows:
        phase_windows.append({key: window[key] for key in ("start_ns", "end_ns", "phase")})
        for component, energy in sorted(window["component_energy_j"].items()):
            if component not in component_cells or not component_cells[component]:
                raise ValueError(f"energy component is absent from grid: {component}")
            indices = component_cells[component]
            try:
                volumes = [float(cells[index]["volume_m3"]) for index in indices]
                node_ids = [str(cells[index]["id"]) for index in indices]
            except (IndexError, KeyError, TypeError, ValueError) as error:
                raise ValueError(f"malformed grid mapping for {component}") from error
            if any(not math.isfinite(value) or value <= 0 for value in volumes):
                raise ValueError(f"non-positive cell volume for {component}")
            total_volume = math.fsum(volumes)
            assignments = [energy * volume / total_volume for volume in volumes]
            # Put floating division residue on the last cell so the serialized
            # event conserves the ledger total to normal double precision.
            assignments[-1] += energy - math.fsum(assignments)
            event_id += 1
            payload = " ".join(f"{node} {value:.17g}"
                               for node, value in zip(node_ids, assignments))
            start_s = _seconds(window["start_ns"])
            end_s = _seconds(window["end_ns"])
            lines.append(f"activity {event_id} replay-{event_id} external_heat external "
                         f"0 -1 -1 -1 {start_s} {end_s} {end_s} {payload}")
            source_by_component[component] = source_by_component.get(component, 0.0) + energy
            emitted_by_component[component] = (
                emitted_by_component.get(component, 0.0) + math.fsum(assignments))

    for component, source in source_by_component.items():
        if not math.isclose(source, emitted_by_component[component],
                            rel_tol=1e-13, abs_tol=1e-15):
            raise AssertionError(f"energy conversion failed for {component}")
    events = "\n".join(lines) + "\n"
    receipt = {
        "schema_version": "eq3-maintenance-pilot-energy-replay-v1",
        "status": "GENERATED_NOT_SOLVED",
        "source_energy_csv_sha256": _sha256(energy_csv),
        "grid_sha256": _sha256(grid_json),
        "events_sha256": hashlib.sha256(events.encode()).hexdigest(),
        "window_count": len(windows),
        "event_count": event_id,
        "start_ns": windows[0]["start_ns"],
        "end_ns": windows[-1]["end_ns"],
        "phase_windows": phase_windows,
        "source_energy_by_component_j": source_by_component,
        "emitted_energy_by_component_j": emitted_by_component,
        "source_total_energy_j": math.fsum(source_by_component.values()),
        "emitted_total_energy_j": math.fsum(emitted_by_component.values()),
        "activity_rows_consumed": False,
        "solver_started": False,
    }
    return events, receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--energy-csv", type=Path, required=True)
    parser.add_argument("--grid", type=Path, required=True)
    parser.add_argument("--events-output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    args = parser.parse_args()
    for output in (args.events_output, args.receipt_output):
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            raise FileExistsError(f"refusing to overwrite {output}")
    events, receipt = convert(args.energy_csv, args.grid)
    args.events_output.write_text(events)
    args.receipt_output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"], "window_count": receipt["window_count"],
                      "event_count": receipt["event_count"],
                      "total_energy_j": receipt["source_total_energy_j"]}))


if __name__ == "__main__":
    main()

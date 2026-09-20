#!/usr/bin/env python3
"""Stream a full campaign-runner field into registered sensor observations.

The campaign runner emits one CSV row per node and sample.  This harness keeps
only one node frame in memory, reduces it with the registered sensor
definitions, and compares it with an immutable maintenance-service thermal CSV.
"""
import argparse
import csv
import hashlib
import json
import math
import resource
import subprocess
import time
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_nodes(path):
    result = []
    with Path(path).open() as stream:
        for line in stream:
            fields = line.split()
            if fields and fields[0] == "node":
                result.append({"id": fields[1], "capacity": float(fields[6]),
                               "initial": float(fields[7]),
                               "static_w": float(fields[8]),
                               "boundary_g": float(fields[9]),
                               "boundary_k": float(fields[10])})
    if not result:
        raise ValueError("model contains no nodes")
    return result


def reference_rows(path):
    with Path(path).open(newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("type") != "ADVANCE":
                continue
            yield {
                "time_ns": int(row["time_ns"]),
                "start_ns": int(row["start_ns"]),
                "end_ns": int(row["end_ns"]),
                "sensors": json.loads(row["sensor_temperatures_k"]),
                "range": json.loads(row["temperature_range_k"]),
                "energy": json.loads(row["energy_j"])["cumulative"],
            }


def window_rows(path):
    with Path(path).open(newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("kind") == "WINDOW_TOTAL":
                totals = json.loads(row["component_energy_j"])
                yield {"start_ns": int(row["start_ns"]),
                       "end_ns": int(row["end_ns"]),
                       "phase": row["phase"] or "UNKNOWN",
                       "energy_j": math.fsum(float(value) for value in totals.values())}


def observe(definitions, values):
    output = {}
    for sensor in definitions:
        if sensor["reduction"] == "max":
            value = max(values[index] for index in sensor["cell_indices"])
        elif sensor["reduction"] == "weighted_mean":
            value = math.fsum(values[index] * weight
                              for index, weight in sensor["cell_weights"])
        else:
            raise ValueError(f"unsupported sensor reduction: {sensor['reduction']}")
        output[sensor["id"]] = value
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--runner-source", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--grid", type=Path, required=True)
    parser.add_argument("--sensors", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--reference-thermal", type=Path, required=True)
    parser.add_argument("--energy-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--step-s", type=float, default=0.02)
    parser.add_argument("--end-s", type=float, default=10.0)
    parser.add_argument("--active-end-ns", type=int, default=8_000_000_000)
    parser.add_argument("--temperature-tolerance-k", type=float, default=1e-10)
    parser.add_argument("--energy-tolerance-j", type=float, default=1e-8)
    args = parser.parse_args()
    for name in ("runner", "runner_source", "model", "grid", "sensors", "events",
                 "reference_thermal", "energy_csv"):
        setattr(args, name, getattr(args, name).resolve(strict=True))
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("derived_sensors.csv", "frame_comparison.csv", "runner.stderr.log",
                 "result.json"):
        if (args.output_dir / name).exists():
            raise FileExistsError(f"refusing to overwrite {args.output_dir / name}")

    nodes = model_nodes(args.model)
    grid = json.loads(args.grid.read_text())
    definitions = json.loads(args.sensors.read_text())
    grid_ids = [str(cell["id"]) for cell in grid["cells"]]
    node_ids = [node["id"] for node in nodes]
    if grid_ids != node_ids:
        raise ValueError("model node order differs from rc_grid cell order")
    references = reference_rows(args.reference_thermal)
    windows = window_rows(args.energy_csv)
    expected_frames = round(args.end_s / args.step_s)
    command = [str(args.runner), "--run", "--model", str(args.model),
               "--events", str(args.events), "--step-s", format(args.step_s, ".17g"),
               "--slot-s", format(args.step_s, ".17g"),
               "--sample-s", format(args.step_s, ".17g"),
               "--end-s", format(args.end_s, ".17g"), "--min-k", "300", "--max-k", "400",
               "--model-sha256", sha256(args.model), "--events-sha256", sha256(args.events),
               "--runner-source-sha256", sha256(args.runner_source),
               "--domain-version", "EQ3_MAINTENANCE_PILOT_REPLAY_2MM_V1"]

    sensor_path = args.output_dir / "derived_sensors.csv"
    frame_path = args.output_dir / "frame_comparison.csv"
    stderr_path = args.output_dir / "runner.stderr.log"
    start_wall = time.monotonic()
    max_sensor_diff = 0.0
    max_range_diff = 0.0
    max_energy_diff = 0.0
    worst_sensor = None
    worst_energy = None
    cumulative_input = 0.0
    cumulative_boundary = 0.0
    frame_count = 0
    stdout_rows = 0
    global_min = math.inf
    global_max = -math.inf
    initial_stored = math.fsum(node["capacity"] * node["initial"] for node in nodes)
    static_power = math.fsum(node["static_w"] for node in nodes)

    with sensor_path.open("w", newline="") as sensor_stream, \
            frame_path.open("w", newline="") as frame_stream, \
            stderr_path.open("w") as stderr_stream:
        sensor_writer = csv.writer(sensor_stream)
        sensor_writer.writerow(["time_ns", "source_phase", "derived_segment", "sensor_id",
                                "campaign_k", "service_k", "abs_difference_k"])
        frame_writer = csv.writer(frame_stream)
        frame_writer.writerow(["time_ns", "source_phase", "derived_segment",
                               "campaign_min_k", "service_min_k", "campaign_max_k",
                               "service_max_k", "stored_energy_j", "service_stored_energy_j",
                               "boundary_loss_j", "service_boundary_loss_j", "input_energy_j",
                               "service_input_energy_j", "energy_residual_j",
                               "service_energy_residual_j"])
        process = subprocess.Popen(command, cwd=args.output_dir, text=True,
                                   stdout=subprocess.PIPE, stderr=stderr_stream, bufsize=1)
        assert process.stdout is not None
        header = process.stdout.readline().rstrip("\n")
        if header != "time_s,node_id,temperature_k":
            process.kill()
            raise ValueError(f"unexpected runner stdout header: {header!r}")
        values = [0.0] * len(nodes)
        current_time = None
        index = 0
        try:
            for line in process.stdout:
                stdout_rows += 1
                fields = line.rstrip("\n").split(",")
                if len(fields) != 3:
                    raise ValueError(f"malformed runner stdout row {stdout_rows}")
                time_s = float(fields[0])
                if current_time is None:
                    current_time = time_s
                elif time_s != current_time:
                    raise ValueError("runner changed frame time before emitting every model node")
                if index >= len(nodes) or fields[1] != node_ids[index]:
                    raise ValueError(f"runner node order mismatch at stdout row {stdout_rows}")
                values[index] = float(fields[2])
                index += 1
                if index != len(nodes):
                    continue

                if math.isclose(current_time, 0.0, abs_tol=1e-15):
                    if max(abs(value - node["initial"]) for value, node in zip(values, nodes)) > 1e-12:
                        raise ValueError("runner t0 differs from model initial state")
                else:
                    frame_count += 1
                    reference = next(references)
                    window = next(windows)
                    time_ns = round(current_time * 1_000_000_000)
                    if (time_ns != reference["time_ns"] or time_ns != window["end_ns"] or
                            reference["start_ns"] != window["start_ns"] or
                            reference["end_ns"] != window["end_ns"]):
                        raise ValueError(f"window identity mismatch at frame {frame_count}")
                    campaign = observe(definitions, values)
                    if set(campaign) != set(reference["sensors"]):
                        raise ValueError("sensor identity mismatch")
                    segment = "ACTIVE" if time_ns <= args.active_end_ns else "RECOVERY"
                    for sensor_id, campaign_k in campaign.items():
                        service_k = float(reference["sensors"][sensor_id])
                        difference = abs(campaign_k - service_k)
                        sensor_writer.writerow([time_ns, window["phase"], segment, sensor_id,
                                                format(campaign_k, ".17g"),
                                                format(service_k, ".17g"),
                                                format(difference, ".17g")])
                        if difference > max_sensor_diff:
                            max_sensor_diff = difference
                            worst_sensor = {"time_ns": time_ns, "sensor_id": sensor_id,
                                            "campaign_k": campaign_k, "service_k": service_k}
                    frame_min, frame_max = min(values), max(values)
                    global_min = min(global_min, frame_min)
                    global_max = max(global_max, frame_max)
                    range_difference = max(abs(frame_min - float(reference["range"][0])),
                                           abs(frame_max - float(reference["range"][1])))
                    max_range_diff = max(max_range_diff, range_difference)
                    cumulative_input += window["energy_j"] + static_power * args.step_s
                    stored = (math.fsum(node["capacity"] * value
                                        for node, value in zip(nodes, values)) - initial_stored)
                    cumulative_boundary += args.step_s * math.fsum(
                        node["boundary_g"] * (value - node["boundary_k"])
                        for node, value in zip(nodes, values))
                    residual = cumulative_input - stored - cumulative_boundary
                    energy_pairs = {
                        "input_energy_j": (cumulative_input,
                                           float(reference["energy"]["total_input_j"])),
                        "stored_energy_j": (stored,
                                            float(reference["energy"]["stored_energy_change_j"])),
                        "boundary_loss_j": (cumulative_boundary,
                                            float(reference["energy"]["boundary_loss_j"])),
                        "energy_residual_j": (residual,
                                              float(reference["energy"]["energy_residual_j"])),
                    }
                    for field, pair in energy_pairs.items():
                        difference = abs(pair[0] - pair[1])
                        if difference > max_energy_diff:
                            max_energy_diff = difference
                            worst_energy = {"time_ns": time_ns, "field": field,
                                            "campaign": pair[0], "service": pair[1]}
                    frame_writer.writerow([
                        time_ns, window["phase"], segment, format(frame_min, ".17g"),
                        format(float(reference["range"][0]), ".17g"), format(frame_max, ".17g"),
                        format(float(reference["range"][1]), ".17g"), format(stored, ".17g"),
                        format(float(reference["energy"]["stored_energy_change_j"]), ".17g"),
                        format(cumulative_boundary, ".17g"),
                        format(float(reference["energy"]["boundary_loss_j"]), ".17g"),
                        format(cumulative_input, ".17g"),
                        format(float(reference["energy"]["total_input_j"]), ".17g"),
                        format(residual, ".17g"),
                        format(float(reference["energy"]["energy_residual_j"]), ".17g")])
                current_time = None
                index = 0
        except Exception:
            process.kill()
            process.wait()
            raise
        return_code = process.wait()
    if return_code:
        raise RuntimeError(f"campaign runner failed with status {return_code}")
    if index or frame_count != expected_frames:
        raise ValueError(f"runner emitted {frame_count} complete non-t0 frames; expected {expected_frames}")
    try:
        next(references)
        raise ValueError("reference thermal CSV has extra ADVANCE frames")
    except StopIteration:
        pass
    try:
        next(windows)
        raise ValueError("energy CSV has extra WINDOW_TOTAL rows")
    except StopIteration:
        pass

    runner_receipt_path = args.output_dir / "rc_energy_receipt.json"
    runner_receipt = json.loads(runner_receipt_path.read_text())
    final_ledger_difference = max(
        abs(float(runner_receipt["total_input_energy_j"]) - cumulative_input),
        abs(float(runner_receipt["stored_energy_change_j"]) - stored),
        abs(float(runner_receipt["boundary_loss_j"]) - cumulative_boundary),
        abs(float(runner_receipt["energy_residual_j"]) - residual))
    status = "PASS" if (max_sensor_diff <= args.temperature_tolerance_k and
                        max_range_diff <= args.temperature_tolerance_k and
                        max_energy_diff <= args.energy_tolerance_j and
                        final_ledger_difference <= args.energy_tolerance_j) else "FAIL"
    result = {
        "schema_version": "eq3-maintenance-a3-streamed-pair-v1",
        "status": status,
        "classification": "FULL_2MM_DISCRETE_EQUIVALENCE",
        "limitations": ["NOT_INDEPENDENT_PHYSICAL_REFERENCE", "NO_CONTROL_CHANGE"],
        "frame_count": frame_count,
        "sensor_count": len(definitions),
        "sensor_comparison_count": frame_count * len(definitions),
        "stdout_node_rows_streamed": stdout_rows,
        "stdout_all_node_artifact_retained": False,
        "max_sensor_abs_difference_k": max_sensor_diff,
        "max_global_range_abs_difference_k": max_range_diff,
        "max_cumulative_energy_abs_difference_j": max_energy_diff,
        "final_runner_vs_streamed_energy_abs_difference_j": final_ledger_difference,
        "worst_sensor": worst_sensor,
        "worst_energy": worst_energy,
        "runner_observed_range_k": [global_min, global_max],
        "thresholds": {"temperature_abs_k": args.temperature_tolerance_k,
                       "cumulative_energy_abs_j": args.energy_tolerance_j},
        "runner_command": command,
        "identities": {"runner_sha256": sha256(args.runner),
                       "runner_source_sha256": sha256(args.runner_source),
                       "model_sha256": sha256(args.model), "grid_sha256": sha256(args.grid),
                       "sensors_sha256": sha256(args.sensors),
                       "events_sha256": sha256(args.events),
                       "reference_thermal_sha256": sha256(args.reference_thermal),
                       "source_energy_sha256": sha256(args.energy_csv),
                       "runner_receipt_sha256": sha256(runner_receipt_path)},
        "artifacts": {"derived_sensors": str(sensor_path),
                      "frame_comparison": str(frame_path),
                      "runner_energy_receipt": str(runner_receipt_path),
                      "runner_stderr": str(stderr_path)},
        "resources": {"wall_s": time.monotonic() - start_wall,
                      "maxrss_kib_self_and_waited_children": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss +
                                                            resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss},
    }
    (args.output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    if status != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

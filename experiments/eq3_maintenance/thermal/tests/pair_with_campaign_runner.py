#!/usr/bin/env python3
"""One-step full-model equivalence check; intended for coordinated execution."""
import argparse
import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def observe(definitions, values):
    result = {}
    for sensor in definitions:
        if sensor["reduction"] == "max":
            value = max(values[index] for index in sensor["cell_indices"])
        else:
            value = math.fsum(values[index] * weight
                              for index, weight in sensor["cell_weights"])
        result[sensor["id"]] = value
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", type=Path, required=True)
    parser.add_argument("--campaign-runner", type=Path, required=True)
    parser.add_argument("--runner-source", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--grid", type=Path, required=True)
    parser.add_argument("--sensors", type=Path, required=True)
    parser.add_argument("--component", default="hbm0.base")
    parser.add_argument("--energy-j", type=float, default=0.02)
    parser.add_argument("--step-ns", type=int, default=20_000_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.energy_j < 0 or args.step_ns <= 0:
        raise ValueError("positive step and non-negative energy required")
    grid = json.loads(args.grid.read_text())
    sensors = json.loads(args.sensors.read_text())
    indices = grid["component_cells"][args.component]
    volumes = [grid["cells"][index]["volume_m3"] for index in indices]
    total_volume = math.fsum(volumes)
    assignments = [(grid["cells"][index]["id"], args.energy_j * volume / total_volume)
                   for index, volume in zip(indices, volumes)]
    event = "activity 1 pair external_heat external 0 -1 -1 -1 0 {0} {0} {1}\n".format(
        args.step_ns * 1e-9,
        " ".join(f"{node} {energy:.17g}" for node, energy in assignments))
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        events = root / "events.txt"
        events.write_text("HBFSIM_EQ3_THERMAL_EVENTS 1\n" + event)
        runner = subprocess.run([
            str(args.campaign_runner), "--run", "--model", str(args.model),
            "--events", str(events), "--step-s", str(args.step_ns * 1e-9),
            "--slot-s", str(args.step_ns * 1e-9), "--end-s", str(args.step_ns * 1e-9),
            "--sample-s", str(args.step_ns * 1e-9), "--min-k", "300", "--max-k", "400",
            "--model-sha256", sha(args.model), "--events-sha256", sha(events),
            "--runner-source-sha256", sha(args.runner_source),
            "--domain-version", "eq3-maintenance-paired-test-v1"],
            cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=600, check=True)
        values = {}
        for line in runner.stdout.splitlines()[1:]:
            time_s, node, temperature = line.split(",")
            if math.isclose(float(time_s), args.step_ns * 1e-9, abs_tol=1e-14):
                values[node] = float(temperature)
        node_values = [values[cell["id"]] for cell in grid["cells"]]
        expected = observe(sensors, node_values)
        service_input = (f"ENERGY 0 {args.step_ns} {args.component} {args.energy_j:.17g}\n"
                         f"ADVANCE {args.step_ns}\nQUIT\n")
        service = subprocess.run([
            str(args.service), "--model", str(args.model), "--grid", str(args.grid),
            "--sensors", str(args.sensors), "--model-sha256", sha(args.model),
            "--grid-sha256", sha(args.grid), "--sensors-sha256", sha(args.sensors),
            "--step-ns", str(args.step_ns), "--min-k", "300", "--max-k", "400"],
            input=service_input, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=600, check=True)
        advanced = next(json.loads(line) for line in service.stdout.splitlines()
                        if json.loads(line)["type"] == "ADVANCE")
        actual = advanced["sensor_temperatures_k"]
        worst = max((abs(actual[key] - value), key, value, actual[key])
                    for key, value in expected.items())
        runner_receipt = json.loads((root / "rc_energy_receipt.json").read_text())
        result = {
            "status": "PASS" if worst[0] <= 1e-10 else "FAIL",
            "schema_version": "eq3-maintenance-thermal-paired-v1",
            "component": args.component,
            "energy_j": args.energy_j,
            "step_ns": args.step_ns,
            "sensor_count": len(expected),
            "max_sensor_abs_difference_k": worst[0],
            "worst_sensor": {"id": worst[1], "campaign_k": worst[2], "service_k": worst[3]},
            "campaign_energy": runner_receipt,
            "service_energy": advanced["energy_j"]["cumulative"],
            "identities": {"model_sha256": sha(args.model), "grid_sha256": sha(args.grid),
                           "sensors_sha256": sha(args.sensors)},
        }
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))
        if result["status"] != "PASS":
            raise SystemExit(1)


if __name__ == "__main__":
    main()

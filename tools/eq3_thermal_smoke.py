#!/usr/bin/env python3
"""Run four uncalibrated topology fixtures; preserve inputs, CSV and provenance."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import subprocess
import time

from eq3_thermal_config import generate, load


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    config_root = repo / "configs/eq3_thermal"
    args.output.mkdir(parents=True, exist_ok=False)
    binary = args.binary.resolve()
    env = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    results = []
    for path in sorted((config_root / "topologies").glob("*.json")):
        topology = load(path)
        inputs = [path] + [config_root / p for p in ("devices.json", "thermal_fixture.json", "power_fixture.json")]
        model, events, graph = generate(*(load(p) for p in inputs))
        run = args.output / topology["name"]
        run.mkdir()
        (run / "model.txt").write_text(model)
        (run / "events.txt").write_text(events)
        (run / "graph.json").write_text(json.dumps(graph, indent=2)+"\n")
        modes = {}
        for mode in ("off", "read_only", "shadow", "active"):
            command = [str(binary), "--mode", mode, "--model", str(run / "model.txt"),
                       "--events", str(run / "events.txt"), "--step-s", "0.01", "--end-s", "0.2"]
            begin = time.monotonic()
            process = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            elapsed = time.monotonic()-begin
            (run / f"{mode}.csv").write_text(process.stdout)
            (run / f"{mode}.stderr").write_text(process.stderr)
            expected_exit = 2 if mode == "active" else 0
            if process.returncode != expected_exit:
                raise RuntimeError(f"{topology['name']} {mode}: exit {process.returncode}")
            rows = list(csv.DictReader(io.StringIO(process.stdout)))
            if mode in ("off", "read_only") and rows:
                raise RuntimeError(f"{mode} fabricated sensor samples")
            if mode == "active" and "NOT_IMPLEMENTED" not in process.stderr:
                raise RuntimeError("active must explicitly reject missing implementation")
            if mode == "shadow":
                groups = {r["location"] for r in rows if r["record_type"] == "group"}
                expected = {"gpu"} | {s["stack_id"] for s in graph["stacks"]}
                if not expected <= groups:
                    raise RuntimeError("missing component sensors")
                for row in rows:
                    if row["source"] != "SIMULATED" or row["valid"] != "1":
                        raise RuntimeError("wrong sensor provenance")
                    value = float(row["value_k"] or row["hotspot_k"])
                    if not math.isfinite(value) or value <= 0:
                        raise RuntimeError("invalid sensor temperature")
                if not any(float(r["hotspot_k"]) > 298.15 for r in rows if r["record_type"] == "group" and r["location"] == "gpu"):
                    raise RuntimeError("GPU synthetic activity did not heat its node")
            modes[mode] = {"command": command, "exit_code": process.returncode,
                           "wall_time_s": elapsed, "rows": len(rows),
                           "stdout_sha256": sha(run / f"{mode}.csv")}
        record = {"evidence_class": "UNCALIBRATED_TEST_FIXTURE", "modes": modes,
                  "input_sha256": {p.name: sha(p) for p in inputs},
                  "binary_sha256": sha(binary), "topology": topology["name"]}
        (run / "result.json").write_text(json.dumps(record, indent=2)+"\n")
        results.append(record)
    source_files = [* (repo / "src/eq3_thermal").glob("*"),
                    * (repo / "include/hbfsim/eq3_thermal").glob("*"),
                    * (repo / "tools").glob("*eq3_thermal*.py")]
    provenance = {"status": "PASS", "scope": "P1 CPU fixtures, not live/scientific EQ3",
                  "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
                  "source_files_sha256": {str(p.relative_to(repo)): sha(p) for p in source_files if p.is_file()},
                  "results": results}
    (args.output / "DONE.json").write_text(json.dumps(provenance, indent=2)+"\n")
    print(json.dumps({"status": "PASS", "topologies": len(results), "modes_each":4,
                      "evidence_class":"UNCALIBRATED_TEST_FIXTURE", "output":str(args.output)}))


if __name__ == "__main__":
    main()

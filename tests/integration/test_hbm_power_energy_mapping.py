#!/usr/bin/env python3

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile


MAPPER = pathlib.Path(sys.argv[1]).resolve()


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


with tempfile.TemporaryDirectory(prefix="hbfsim-hbm-map-") as directory:
    root = pathlib.Path(directory)
    source = root / "source.csv"
    source.write_text(
        "time_ns,hbm_stack_0_w,hbm_stack_1_w\n"
        "0,10,20\n100,20,40\n200,0,0\n",
        encoding="utf-8",
    )
    manifest = root / "source.manifest.json"
    manifest.write_text(json.dumps({
        "technology": "synthetic-test",
        "output": {"sha256": digest(source)},
        "stacks": [
            {
                "index": 0, "duration_ns": 200, "baseline_power_w": 2.0,
                "active_power_w": 13.0, "average_power_w": 15.0,
            },
            {
                "index": 1, "duration_ns": 200, "baseline_power_w": 4.0,
                "active_power_w": 26.0, "average_power_w": 30.0,
            },
        ],
    }), encoding="utf-8")
    schedule = root / "schedule.csv"
    schedule.write_text(
        "start_ns,end_ns,phase,resident_fraction,activity_fraction\n"
        "0,1000,low,0.5,0.25\n"
        "1000,3000,high,1,0.75\n",
        encoding="utf-8",
    )
    output = root / "mapped.csv"
    output_manifest = root / "mapped.manifest.json"
    completed = subprocess.run([
        sys.executable, str(MAPPER),
        "--source-csv", str(source),
        "--source-manifest", str(manifest),
        "--schedule", str(schedule),
        "--output-csv", str(output),
        "--manifest", str(output_manifest),
    ], check=True, capture_output=True, text=True)
    assert json.loads(completed.stdout)["status"] == "PASS"
    result = json.loads(output_manifest.read_text(encoding="utf-8"))
    assert result["mode"] == "ENERGY_CONSERVING_WORKLOAD_SCHEDULE_MAPPING"
    assert max(result["output"]["relative_energy_error_per_stack"]) <= 1.0e-12
    assert result["schedule"]["resident_equivalent_ns"] == 2500.0
    assert result["schedule"]["active_equivalent_ns"] == 1750.0
    assert abs(result["output"]["energy_j_per_stack"][0] - 27.75e-6) < 1e-18
    with output.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[-1] == ["3000", "0", "0"]

    invalid = root / "invalid.csv"
    invalid.write_text(
        "start_ns,end_ns,phase,resident_fraction,activity_fraction\n"
        "0,1000,invalid,0.2,0.3\n",
        encoding="utf-8",
    )
    rejected = subprocess.run([
        sys.executable, str(MAPPER),
        "--source-csv", str(source),
        "--source-manifest", str(manifest),
        "--schedule", str(invalid),
        "--output-csv", str(root / "bad.csv"),
        "--manifest", str(root / "bad.json"),
    ], capture_output=True, text=True)
    assert rejected.returncode != 0

print("hbm-power-energy-mapping: PASS")

#!/usr/bin/env python3

import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    root = Path(sys.argv[1])
    analyzer = (
        root
        / "experiments/package_thermal/phase2/analyze_phase2_timeline.py"
    )
    with tempfile.TemporaryDirectory(prefix="hbfsim-phase2-timeline-") as raw:
        directory = Path(raw)
        timeline = directory / "package-thermal-timeline.csv"
        manifest = directory / "experiment-manifest.json"
        rom = directory / "rom.json"
        output = directory / "analysis"
        fields = [
            "thermal_time_ns",
            "host_sample_time_ns",
            "P_accelerator",
            "P_gddr",
            "P_hbf_total",
            "T_hbf_hotspot",
            "raw_policy",
            "effective_policy",
            "gate_open",
            "service_scale",
            "MQSim_events_this_bin",
            "MQSim_read_bytes",
            "MQSim_program_bytes",
            "submitted_requests",
            "admitted_requests",
            "completed_requests",
            "queue_depth",
        ]
        with timeline.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            submitted = 0
            for index in range(240):
                time_ns = (index + 1) * 10_000_000
                severe = 80 <= index < 150
                if not severe:
                    submitted += 1
                if index < 80:
                    temperature = 30.0 + index * 0.20
                    power = 20.0
                    events = 100
                elif severe:
                    temperature = 46.0 - (index - 80) * 0.05
                    power = 5.0
                    events = 20
                else:
                    temperature = 42.5
                    power = 15.0
                    events = 80
                writer.writerow(
                    {
                        "thermal_time_ns": time_ns,
                        "host_sample_time_ns": time_ns + 1_000,
                        "P_accelerator": 100.0,
                        "P_gddr": "",
                        "P_hbf_total": power,
                        "T_hbf_hotspot": temperature,
                        "raw_policy": "severe" if severe else "normal",
                        "effective_policy": "severe" if severe else "normal",
                        "gate_open": 0 if severe else 1,
                        "service_scale": 1.0,
                        "MQSim_events_this_bin": events,
                        "MQSim_read_bytes": events * 4096,
                        "MQSim_program_bytes": 0,
                        "submitted_requests": submitted,
                        "admitted_requests": submitted,
                        "completed_requests": max(0, submitted - 1),
                        "queue_depth": 0,
                    }
                )
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "experiment_id": "synthetic-closed-loop",
                    "thermal_stage": "active",
                    "workload": "read_sustained",
                    "seed": 7,
                    "offered_byte_rate": 409_600_000,
                    "evidence_grid": {"hardware": "synthetic"},
                }
            ),
            encoding="utf-8",
        )
        rom.write_text(
            json.dumps(
                {
                    "state_count": 1,
                    "sample_period_ns": 10_000_000,
                    "a": [0.9],
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                str(analyzer),
                "--timeline",
                str(timeline),
                "--experiment-manifest",
                str(manifest),
                "--rom",
                str(rom),
                "--output-dir",
                str(output),
                "--causal-window-ns",
                "300000000",
                "--minimum-analysis-ns",
                "100000000",
            ],
            check=True,
        )
        closed_loop = json.loads(
            (output / "closed-loop-analysis.json").read_text(encoding="utf-8")
        )
        assert closed_loop["verdict"] == "GO", closed_loop
        assert all(closed_loop["checks"].values()), closed_loop
        power = json.loads(
            (output / "source-power-summary.json").read_text(encoding="utf-8")
        )
        assert power["sources"]["P_accelerator"]["available"]
        assert not power["sources"]["P_gddr"]["available"]
        stationarity = json.loads(
            (output / "stationarity.json").read_text(encoding="utf-8")
        )
        assert stationarity["dominant_tau_ns"] > 0
        assert stationarity["dominant_tau_method"] == "nonnegative_power_iteration"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

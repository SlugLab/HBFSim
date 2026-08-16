#!/usr/bin/env python3

import json
import pathlib
import subprocess
import sys
import tempfile

import jsonschema


ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: test_thermal_report.py FIXTURE")
    fixture = pathlib.Path(sys.argv[1])
    schema = json.loads(
        (ROOT / "configs/schema/thermal-reliability-summary.schema.json")
        .read_text()
    )
    with tempfile.TemporaryDirectory(prefix="hbfsim-thermal-report-") as raw:
        directory = pathlib.Path(raw)
        report = directory / "thermal-reliability-summary.json"
        subprocess.run(
            [fixture, report, "clean"], cwd=ROOT, check=True
        )
        document = json.loads(report.read_text())
        jsonschema.validate(document, schema)
        assert document["schema_version"] == 1
        assert document["profile_sha256"] == "a" * 64
        assert document["source"]["kind"] == "constant"
        assert document["source"]["identity"] == (
            "deterministic-validation-constant"
        )
        assert document["reliability_time_acceleration"] == 1000.0
        assert document["accelerated_reliability_time"] is True
        assert len(document["mtbf_sensitivity"]) == 4
        assert document["accounting"]["completed_refresh_blocks"] == 1
        assert document["terminal_status"] == "clean"
        assert not list(directory.glob("*.tmp.*"))

        subprocess.run(
            [fixture, report, "thermal_shutdown"], cwd=ROOT, check=True
        )
        replaced = json.loads(report.read_text())
        jsonschema.validate(replaced, schema)
        assert replaced["terminal_status"] == "thermal_shutdown"
        assert not list(directory.glob("*.tmp.*"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

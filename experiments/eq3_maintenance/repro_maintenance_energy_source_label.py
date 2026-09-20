#!/usr/bin/env python3
"""Fixed reproduction for the maintenance native-source classification mismatch."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

try:
    from .energy_ledger import ActivityEnergyLedger
except ImportError:  # Direct invocation from the experiment directory.
    from energy_ledger import ActivityEnergyLedger


def diagnose(result_path=None, activity_path=None):
    actual_field = ActivityEnergyLedger._source({"maintenance_request_id": 7})
    legacy_field = ActivityEnergyLedger._source({"maintenance_id": 7})
    if actual_field == legacy_field == "HBF_MAINTENANCE":
        status = "FIXED"
    elif (actual_field == "BACKEND_BACKGROUND"
          and legacy_field == "HBF_MAINTENANCE"):
        status = "CONFIRMED_BUG"
    else:
        status = "UNEXPECTED_CLASSIFICATION"
    receipt = {
        "schema_version": "eq3-maintenance-energy-source-repro-v1",
        "status": status,
        "actual_service_field": "maintenance_request_id",
        "actual_field_classification": actual_field,
        "legacy_alias_classification": legacy_field,
        "physics_effect": "NONE; source is an evidence label, not a power coefficient",
        "minimal_patch": (
            "In ActivityEnergyLedger._source, read maintenance_request_id first; "
            "fall back to maintenance_id only for legacy/replay compatibility."
        ),
        "foreground_classification": ActivityEnergyLedger._source(
            {"external_request_id": 9}),
        "unknown_background_classification": ActivityEnergyLedger._source({}),
    }
    if result_path is not None and activity_path is not None:
        result = json.loads(Path(result_path).read_text())
        native = [event for event in result["timeline"]["native"]
                  if any(row.get("maintenance_request_id") not in (None, 0, "UNKNOWN")
                         for row in event["transactions"])]
        with Path(activity_path).open(newline="") as stream:
            activity = list(csv.DictReader(stream))
        background = [row for row in activity if row["source"] == "BACKEND_BACKGROUND"]
        labelled = [row for row in activity if row["source"] == "HBF_MAINTENANCE"]
        receipt["fixed_point"] = {
            "maintenance_native_events": len(native),
            "maintenance_request_ids": len({row["maintenance_request_id"]
                for event in native for row in event["transactions"]
                if row.get("maintenance_request_id") not in (None, 0, "UNKNOWN")}),
            "backend_background_activity_rows": len(background),
            "backend_background_energy_j": sum(float(row["energy_j"]) for row in background),
            "hbf_maintenance_activity_rows": len(labelled),
            "hbf_maintenance_energy_j": sum(float(row["energy_j"]) for row in labelled),
        }
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path)
    parser.add_argument("--energy-activity", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (args.result is None) != (args.energy_activity is None):
        raise ValueError("result and energy-activity must be supplied together")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    receipt = diagnose(args.result, args.energy_activity)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()

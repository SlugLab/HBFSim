#!/usr/bin/env python3
"""Fixed, foreground-free validation of a complete A2 replay bundle."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

try:
    from .energy_ledger import ActivityEnergyLedger
    from .ideal_maintenance_replay import IdealIndependentMaintenanceReplay
except ImportError:
    from energy_ledger import ActivityEnergyLedger
    from ideal_maintenance_replay import IdealIndependentMaintenanceReplay


class _NoMaintenanceInner:
    def __init__(self):
        self.now = 0
        self.header = {}
        self.requests = {}
        self.completions = {}
        self.observations = []
        self.native_observations = []

    def maintain(self, *args, **kwargs):
        raise AssertionError("replay called actual backend maintenance")

    def submit(self, request):
        self.requests[request["request_id"]] = dict(request)

    def try_submit(self, request):
        self.submit(request)
        return {"submitted": True, "backend_arrival_ns": self.now}

    def until(self, horizon):
        self.now = horizon
        return None

    def finish(self):
        return {"status": "FINISHED", "maintenance_issued": 0,
                "maintenance_completed": 0, "pending_maintenance": 0}

    def close(self):
        pass


def validate(bundle_path, source_point):
    bundle_path = Path(bundle_path).resolve(strict=True)
    source = Path(source_point).resolve(strict=True)
    bundle = json.loads(bundle_path.read_text())
    inner = _NoMaintenanceInner()
    wrapper = IdealIndependentMaintenanceReplay(
        inner, bundle_path, profile_path=source / "profile.json",
        stack_map_path=source / "stack-map.json",
        foreground_requests_path=source / "requests-input.json")
    due = min(row["due_ns"] for row in bundle["maintenance_intents"])
    wrapper.until(due)
    for intent in bundle["maintenance_intents"]:
        job = {key: intent[key] for key in
               ("request_id", "stack", "stack_local_page", "due_ns")}
        job.update(deadline_ns=intent.get("deadline_ns", 0),
                   parent_id=intent.get("parent_id"),
                   reclaim_source_block=intent.get("reclaim_source_block", False),
                   failure_injection=intent.get("failure_injection", "none"),
                   trigger_reason=intent.get("trigger_reason", "RETENTION_AGE_DUE"))
        wrapper.maintain(job)
    wrapper.until(max(row["end_ns"] for row in bundle["completions"]))

    coefficient = json.loads((source / "energy-profile.json").read_text())
    mapping = json.loads((source / "stack-map.json").read_text())
    components = []
    for group in mapping["stacks"]:
        stack = group["id"]
        components.append(stack + ".base")
        components.extend(f"{stack}.die{index}" for index in
                          range(group["declared_dies"]))
    ledger = ActivityEnergyLedger(coefficient, mapping, components, gpu_stop_ns=0)
    for event in wrapper.native_observations:
        ledger.native(event)
    ledger.flush(wrapper.now + 1)
    receipt = wrapper.finish()["ideal_independent_maintenance_replay"]
    expected = bundle["counts"]
    if (len(wrapper.maintenance_events) != expected["backend_events"]
            or len(wrapper.native_observations) != expected["native_events"]
            or len(wrapper.maintenance_completions) != expected["completions"]
            or receipt["actual_backend_maintenance_issued"] != 0
            or receipt["replay_maintenance_completed"] != expected["maintenance"]):
        raise AssertionError("complete replay conservation failed")
    if any(not all(str(value).startswith("A2R-T")
                   for value in row.get("transaction_ids", ()))
           for row in wrapper.maintenance_completions.values()):
        raise AssertionError("replay completion transaction IDs are not namespaced")
    if not ledger.rows or any(row["source"] != "HBF_MAINTENANCE" for row in ledger.rows):
        raise AssertionError("replay maintenance energy source identity failed")
    return {
        "status": "PASS",
        "actual_backend_maintenance_issued": 0,
        "replay_maintenance_issued": expected["maintenance"],
        "replay_maintenance_completed": len(wrapper.maintenance_completions),
        "backend_events": len(wrapper.maintenance_events),
        "native_events": len(wrapper.native_observations),
        "counterfactual_source_versions": sorted({row["source_version"]
            for row in wrapper.maintenance_completions.values()}),
        "current_mqsim_mapping_mutated_by_replay": False,
        "energy_rows": len(ledger.rows),
        "energy_source_labels": sorted({row["source"] for row in ledger.rows}),
        "maintenance_energy_j": ledger.total_j,
        "expected_pilot03_maintenance_energy_match": math.isclose(
            ledger.total_j, 0.00035248064, rel_tol=1e-12, abs_tol=1e-15),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--source-point", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    result = validate(args.bundle, args.source_point)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

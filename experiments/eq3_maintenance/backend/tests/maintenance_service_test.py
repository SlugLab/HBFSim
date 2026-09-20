#!/usr/bin/env python3
"""Fixed process-level test of the isolated maintenance JSON protocol."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: maintenance_service_test.py MAIN_ROOT BINARY ARTIFACT_DIR")
    root, binary, output = map(Path, sys.argv[1:])
    sys.path.insert(0, str(root))
    from experiments.eq3_maintenance.campaign_inputs import configuration
    from experiments.eq3_maintenance.backend.client.maintenance_service import MaintenanceMqsimService

    output.mkdir(parents=True, exist_ok=False)
    config = configuration("mixed_direct")
    profile = output / "profile.json"
    stack_map = output / "stack-map.json"
    profile.write_text(json.dumps(config["profile"], indent=2) + "\n")
    stack_map.write_text(json.dumps(config["stack_map"], indent=2) + "\n")
    service_dir = output / "service"
    with MaintenanceMqsimService(binary, profile, service_dir, timeout=30,
            artifact_root=root.parents[2], stack_map=stack_map,
            native_observations=True) as service:
        service.submit(dict(request_id=1, issue_ns=0, bytes=16384,
            operation="read", stack="hbf0", route="direct", stack_local_page=15))
        while 1 not in service.completions:
            service.until(service.now + 1_000_000)
        accepted = service.maintain(dict(request_id=101, parent_id=7001,
            stack="hbf0", stack_local_page=15, due_ns=service.now,
            deadline_ns=service.now + 1_000_000_000,
            reclaim_source_block=False, trigger_reason="FIXED_RETENTION_DUE"))
        assert accepted["target"] == {"channel": 0, "chip": 0, "die": 15, "plane": 0}
        while 101 not in service.maintenance_completions:
            service.until(service.now + 1_000_000)
        completion = service.maintenance_completions[101]
        assert completion["status"] == "COMMITTED"
        assert completion["mapping_committed"] and completion["source_retired"]
        assert completion["age_reset_ns"] == completion["end_ns"]
        assert completion["source_version"] != completion["committed_version"]
        assert completion["trigger_reason"] == "FIXED_RETENTION_DUE"
        assert completion["coverage_pages"] == 1 and completion["deadline_met"]
        phases = [e["state"] for e in service.maintenance_events
                  if e["request_id"] == 101]
        assert phases == ["DUE", "QUEUED", "READ", "PROGRAM_DEST",
                          "COMMIT", "RETIRE_OLD", "DONE"]
        native = [t for event in service.native_observations
                  for t in event["transactions"]
                  if t["maintenance_request_id"] == 101]
        assert native and all(t["maintenance_parent_id"] == 7001 for t in native)
        assert {t["die"] for t in native} == {15}

        service.submit(dict(request_id=2, issue_ns=service.now, bytes=16384,
            operation="read", stack="hbf0", route="direct", stack_local_page=16))
        while 2 not in service.completions:
            service.until(service.now + 1_000_000)
        late_due = service.now
        service.until(late_due + 1_000)
        service.maintain(dict(request_id=102, parent_id=7002,
            stack="hbf0", stack_local_page=16, due_ns=late_due,
            deadline_ns=late_due + 1_000_000_000,
            reclaim_source_block=False, trigger_reason="THERMAL_GUARD_RELEASE"))
        while 102 not in service.maintenance_completions:
            service.until(service.now + 1_000_000)
        late = service.maintenance_completions[102]
        assert late["status"] == "COMMITTED" and late["enqueue_ns"] >= late_due + 1_000
        assert late["deadline_met"] and len(late["transaction_ids"]) == 2

        service.submit(dict(request_id=3, issue_ns=service.now, bytes=16384,
            operation="read", stack="hbf0", route="direct", stack_local_page=17))
        while 3 not in service.completions:
            service.until(service.now + 1_000_000)
        expired_due = service.now
        expired_deadline = expired_due + 100
        service.until(expired_deadline + 100)
        service.maintain(dict(request_id=103, parent_id=7003,
            stack="hbf0", stack_local_page=17, due_ns=expired_due,
            deadline_ns=expired_deadline, reclaim_source_block=False,
            trigger_reason="THERMAL_GUARD_RELEASE_AFTER_DEADLINE"))
        while 103 not in service.maintenance_completions:
            service.until(service.now + 1_000_000)
        expired = service.maintenance_completions[103]
        assert expired["status"] == "REJECTED_INVALID_TARGET"
        assert not expired["mapping_committed"] and not expired["transaction_ids"]
        assert not expired["deadline_met"]
        expired_phases = [e["state"] for e in service.maintenance_events
                          if e["request_id"] == 103]
        assert expired_phases == ["DUE", "QUEUED", "FAILED"]
        expired_native = [t for event in service.native_observations
                          for t in event["transactions"]
                          if t["maintenance_request_id"] == 103]
        assert not expired_native
        receipt = service.finish()
        assert receipt["maintenance_issued"] == receipt["maintenance_completed"] == 3
    print("PASS isolated maintain JSON horizon/native-ID protocol")


if __name__ == "__main__":
    main()

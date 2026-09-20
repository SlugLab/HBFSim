#!/usr/bin/env python3
"""Build a fixed ideal-resource maintenance replay bundle from one completed point.

The bundle preserves observed maintenance intent, native phases, and terminal
facts.  It does not convert them into new backend observations.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path


SCHEMA = "eq3-ideal-independent-maintenance-replay-v1"
EVIDENCE = "FIXED_COUNTERFACTUAL_REPLAY_NOT_ACTUAL_SHARED_MQSIM"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _maintenance_id(event):
    identities = {
        row.get("maintenance_request_id")
        for row in event.get("transactions", ())
        if row.get("maintenance_request_id") not in (None, 0, "UNKNOWN")
    }
    if len(identities) != 1:
        raise ValueError("native replay event must identify exactly one maintenance request")
    return next(iter(identities))


def _prefix_native(event, order):
    value = copy.deepcopy(event)
    original_command = value["command_id"]
    value.update(
        command_id=f"A2R-C{original_command}",
        source_command_id=original_command,
        replay_order=order,
        evidence=EVIDENCE,
        resource_model="IDEAL_INDEPENDENT_MAINTENANCE_REPLAY",
    )
    for transaction in value["transactions"]:
        original_transaction = transaction["transaction_id"]
        maintenance_id = transaction.get("maintenance_request_id")
        if maintenance_id in (None, 0, "UNKNOWN"):
            raise ValueError("replayed native transaction lacks maintenance identity")
        transaction.update(
            transaction_id=f"A2R-T{original_transaction}",
            source_transaction_id=original_transaction,
            # Compatibility alias for the current experimental energy ledger.
            # The actual service field remains maintenance_request_id.
            maintenance_id=maintenance_id,
            evidence=EVIDENCE,
        )
    return value


def build_bundle(point: Path) -> dict:
    point = point.resolve(strict=True)
    names = ("manifest.json", "profile.json", "stack-map.json",
             "requests-input.json", "maintenance-input.json", "result.json")
    paths = {name: point / name for name in names}
    if not all(path.is_file() for path in paths.values()):
        raise ValueError("source point lacks required frozen artifacts")
    manifest = json.loads(paths["manifest.json"].read_text())
    for name in names[1:5]:
        expected = manifest.get("input_sha256", {}).get(name)
        if expected is None or sha256(paths[name]) != expected:
            raise ValueError(f"source point input identity mismatch: {name}")
    profile = json.loads(paths["profile.json"].read_text())
    stack_map = json.loads(paths["stack-map.json"].read_text())
    foreground = json.loads(paths["requests-input.json"].read_text())
    intents = json.loads(paths["maintenance-input.json"].read_text())
    result = json.loads(paths["result.json"].read_text())
    if not intents or any(row.get("operation", "read") != "read"
                          for row in foreground if row.get("stack", "").startswith("hbf")):
        raise ValueError("fixed A2 source must have maintenance and read-only HBF foreground")
    intent_by_id = {row["request_id"]: row for row in intents}
    if len(intent_by_id) != len(intents):
        raise ValueError("source maintenance intent IDs are not unique")

    inflight = {}
    backend_events = []
    for order, row in enumerate(result["timeline"]["maintenance"]):
        phase = row.get("phase")
        if phase == "INFLIGHT":
            inflight[row["request_id"]] = {
                "submit_ns": row["submit_ns"], "placement": row.get("placement"),
                "target": row.get("target"),
            }
        elif phase == "BACKEND_EVENT":
            value = copy.deepcopy(row)
            value.pop("phase", None)
            value.update(replay_order=order, evidence=EVIDENCE,
                         resource_model="IDEAL_INDEPENDENT_MAINTENANCE_REPLAY")
            if value.get("transaction_id") is not None:
                original = value["transaction_id"]
                value["source_transaction_id"] = original
                value["transaction_id"] = f"A2R-T{original}"
            backend_events.append(value)

    native = []
    for order, event in enumerate(result["timeline"]["native"]):
        try:
            identity = _maintenance_id(event)
        except ValueError:
            continue
        if identity not in intent_by_id:
            raise ValueError("native maintenance fact has unknown intent")
        native.append(_prefix_native(event, order))

    completions = []
    for row in result["maintenance"]:
        request_id = row["request_id"]
        if request_id not in intent_by_id or request_id not in inflight:
            raise ValueError("maintenance result is absent from intent/submission facts")
        completion = copy.deepcopy(row.get("completion"))
        if not isinstance(completion, dict):
            raise ValueError("maintenance result lacks backend completion")
        baseline_source = completion.pop("source_version", None)
        baseline_committed = completion.pop("committed_version", None)
        baseline_transactions = completion.get("transaction_ids", [])
        completion.update(
            source_version="UNKNOWN_REPLAY",
            committed_version="UNKNOWN_REPLAY",
            baseline_source_version=baseline_source,
            baseline_committed_version=baseline_committed,
            transaction_ids=[f"A2R-T{value}" for value in baseline_transactions],
            baseline_transaction_ids=baseline_transactions,
            evidence=EVIDENCE,
            commit_semantics=("VIRTUAL_METADATA_AGE_LEDGER_ONLY; "
                              "CURRENT_MQSIM_MAPPING_NOT_MUTATED"),
        )
        completions.append(completion)
    completions.sort(key=lambda row: (row["end_ns"], row["request_id"]))

    expected = set(intent_by_id)
    if (set(inflight) != expected
            or {row["request_id"] for row in completions} != expected
            or {row["request_id"] for row in backend_events} != expected
            or {_maintenance_id(row) for row in native} != expected):
        raise ValueError("fixed replay does not cover every maintenance intent")
    states = {}
    for row in backend_events:
        states.setdefault(row["request_id"], []).append(row["state"])
    required_states = ["DUE", "QUEUED", "READ", "PROGRAM_DEST", "COMMIT",
                       "RETIRE_OLD", "DONE"]
    if any(value != required_states for value in states.values()):
        raise ValueError("source point lacks the complete maintenance state sequence")

    return {
        "schema_version": SCHEMA,
        "evidence": EVIDENCE,
        "resource_model": "IDEAL_INDEPENDENT_MAINTENANCE_REPLAY",
        "source_point": str(point),
        "source_files_sha256": {name: sha256(path) for name, path in paths.items()},
        "input_contract": {
            "profile_sha256": sha256(paths["profile.json"]),
            "stack_map_sha256": sha256(paths["stack-map.json"]),
            "foreground_requests_sha256": sha256(paths["requests-input.json"]),
            "maintenance_intents_sha256": sha256(paths["maintenance-input.json"]),
            "hbf_foreground": "READ_ONLY",
            "concurrent_write": "REJECT",
            "source_version": "UNKNOWN_REPLAY",
        },
        "profile_identity": {key: profile.get(key) for key in
                             ("name", "channels", "dies_per_channel", "planes_per_die",
                              "page_bytes", "queue_depth")},
        "stack_map_identity": {key: stack_map.get(key) for key in
                               ("schema_version", "physical_kind", "route",
                                "address_layout", "plane_allocation_scheme")},
        "maintenance_intents": intents,
        "submission_facts": inflight,
        "backend_events": backend_events,
        "native_events": native,
        "completions": completions,
        "counts": {"maintenance": len(intents), "backend_events": len(backend_events),
                   "native_events": len(native), "completions": len(completions)},
        "limits": [
            "counterfactual replay, not actual shared-MQSim maintenance",
            "foreground MQSim mapping is not changed by replayed commit facts",
            "valid only for the exact read-only foreground/profile/map/initial-state contract",
            "fixed-intent direct-attribution supplement, not the endogenous-trigger A2 arm",
            "baseline versions are provenance only; counterfactual source version is unknown",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-point", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite {args.output}")
    bundle = build_bundle(args.source_point)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2, sort_keys=True,
                                     allow_nan=False) + "\n")
    print(json.dumps({"status": "GENERATED", **bundle["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()

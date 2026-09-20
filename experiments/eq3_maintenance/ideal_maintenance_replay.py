"""Isolated fixed-intent A2 replay wrapper.

Foreground requests still run on exactly one real MQSim engine.  Maintenance
never enters that engine: observed baseline stages are released as an explicit
ideal-independent counterfactual stream for energy and virtual age accounting.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

try:
    from .build_ideal_maintenance_bundle import EVIDENCE, SCHEMA
except ImportError:  # Direct script-directory import used by run_point.py.
    from build_ideal_maintenance_bundle import EVIDENCE, SCHEMA


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class IdealIndependentMaintenanceReplay:
    """Proxy a foreground-only service and expose frozen maintenance facts."""

    def __init__(self, inner, bundle_path, *, profile_path, stack_map_path,
                 foreground_requests_path):
        self.inner = inner
        self.bundle_path = Path(bundle_path).resolve(strict=True)
        self.bundle = json.loads(self.bundle_path.read_text())
        if (self.bundle.get("schema_version") != SCHEMA
                or self.bundle.get("evidence") != EVIDENCE
                or self.bundle.get("resource_model") !=
                "IDEAL_INDEPENDENT_MAINTENANCE_REPLAY"):
            raise ValueError("invalid ideal-independent maintenance replay bundle")
        contract = self.bundle["input_contract"]
        actual = {
            "profile_sha256": _sha256(profile_path),
            "stack_map_sha256": _sha256(stack_map_path),
            "foreground_requests_sha256": _sha256(foreground_requests_path),
        }
        if any(actual[key] != contract[key] for key in actual):
            raise ValueError("A2 replay input identity differs from frozen source point")
        foreground = json.loads(Path(foreground_requests_path).read_text())
        if any(row.get("operation") != "read"
               for row in foreground if row.get("stack", "").startswith("hbf")):
            raise ValueError("A2 fixed replay rejects concurrent HBF writes")

        self._intents = {row["request_id"]: copy.deepcopy(row)
                         for row in self.bundle["maintenance_intents"]}
        self._submissions = {int(key): copy.deepcopy(value)
                             for key, value in self.bundle["submission_facts"].items()}
        self._accepted = set()
        self._inner_native_seen = 0
        self._event_index = self._native_index = self._completion_index = 0
        self.maintenance_events = []
        self.maintenance_completions = {}
        self.native_observations = []
        self.header = copy.deepcopy(inner.header)
        self.header.update(
            maintenance_resource_model="IDEAL_INDEPENDENT_MAINTENANCE_REPLAY",
            maintenance_replay_evidence=EVIDENCE,
            actual_backend_maintenance="DISABLED_BY_WRAPPER",
            replay_bundle_sha256=_sha256(self.bundle_path),
        )

    @property
    def now(self):
        return self.inner.now

    @property
    def requests(self):
        return self.inner.requests

    @property
    def completions(self):
        return self.inner.completions

    @property
    def observations(self):
        return self.inner.observations

    def _sync(self):
        now = self.inner.now
        new_native = [copy.deepcopy(row) for row in
                      self.inner.native_observations[self._inner_native_seen:]]
        self._inner_native_seen = len(self.inner.native_observations)
        replay_native = []
        rows = self.bundle["native_events"]
        while self._native_index < len(rows) and rows[self._native_index]["time_ns"] <= now:
            row = rows[self._native_index]
            request_ids = {tr["maintenance_request_id"] for tr in row["transactions"]}
            if not request_ids.issubset(self._accepted):
                break
            replay_native.append(copy.deepcopy(row))
            self._native_index += 1
        combined = [(row["time_ns"], 0, index, row)
                    for index, row in enumerate(new_native)]
        combined += [(row["time_ns"], 1, row["replay_order"], row)
                     for row in replay_native]
        self.native_observations.extend(row for *_, row in sorted(combined))

        events = self.bundle["backend_events"]
        while self._event_index < len(events) and events[self._event_index]["time_ns"] <= now:
            row = events[self._event_index]
            if row["request_id"] not in self._accepted:
                break
            self.maintenance_events.append(copy.deepcopy(row))
            self._event_index += 1
        completions = self.bundle["completions"]
        while (self._completion_index < len(completions)
               and completions[self._completion_index]["end_ns"] <= now):
            row = copy.deepcopy(completions[self._completion_index])
            request_id = row["request_id"]
            if request_id not in self._accepted or request_id in self.maintenance_completions:
                raise RuntimeError("invalid replay maintenance completion identity")
            self.maintenance_completions[request_id] = row
            self._completion_index += 1

    def submit(self, request):
        if request.get("operation") != "read":
            raise ValueError("A2 replay foreground is read-only")
        answer = self.inner.submit(request)
        self._sync()
        return answer

    def try_submit(self, request):
        if request.get("operation") != "read":
            raise ValueError("A2 replay foreground is read-only")
        answer = self.inner.try_submit(request)
        self._sync()
        return answer

    def until(self, horizon):
        completion = self.inner.until(horizon)
        self._sync()
        return completion

    def maintain(self, job=None, **kwargs):
        if job is not None:
            if not isinstance(job, dict) or kwargs:
                raise TypeError("maintain accepts one job dict or keyword arguments")
            kwargs = dict(job)
        request_id = kwargs.get("request_id")
        if request_id not in self._intents or request_id in self._accepted:
            raise ValueError("unknown or duplicate fixed replay maintenance request")
        intent = self._intents[request_id]
        normalized = {
            "request_id": request_id,
            "stack": kwargs.get("stack"),
            "stack_local_page": kwargs.get("stack_local_page"),
            "due_ns": kwargs.get("due_ns"),
            "deadline_ns": kwargs.get("deadline_ns", 0),
            "parent_id": request_id if kwargs.get("parent_id") is None else kwargs["parent_id"],
            "reclaim_source_block": kwargs.get("reclaim_source_block", False),
            "failure_injection": kwargs.get("failure_injection", "none"),
            "trigger_reason": kwargs.get("trigger_reason", "UNSPECIFIED"),
        }
        expected = {
            "request_id": request_id,
            "stack": intent["stack"],
            "stack_local_page": intent["stack_local_page"],
            "due_ns": intent["due_ns"],
            "deadline_ns": intent.get("deadline_ns", 0),
            "parent_id": intent.get("parent_id", request_id),
            "reclaim_source_block": intent.get("reclaim_source_block", False),
            "failure_injection": intent.get("failure_injection", "none"),
            "trigger_reason": intent.get("trigger_reason", "UNSPECIFIED"),
        }
        if normalized != expected:
            raise ValueError("maintenance call differs from frozen A2 intent")
        submit = self._submissions[request_id]
        if self.now != submit["submit_ns"]:
            raise RuntimeError("fixed replay is incompatible with changed maintenance submit time")
        self._accepted.add(request_id)
        return {
            "maintenance_accepted": 1,
            "status": "IDEAL_INDEPENDENT_REPLAY_ACCEPTED",
            "placement": copy.deepcopy(submit.get("placement")),
            "target": copy.deepcopy(submit.get("target")),
            "actual_backend_submitted": False,
            "evidence": EVIDENCE,
        }

    def finish(self):
        receipt = self.inner.finish()
        self._sync()
        expected = set(self._intents)
        if (self._accepted != expected
                or set(self.maintenance_completions) != expected
                or self._event_index != len(self.bundle["backend_events"])
                or self._native_index != len(self.bundle["native_events"])):
            raise ValueError("A2 replay finish conservation failed")
        answer = copy.deepcopy(receipt)
        answer["ideal_independent_maintenance_replay"] = {
            "evidence": EVIDENCE,
            "bundle_sha256": _sha256(self.bundle_path),
            "actual_backend_maintenance_issued": receipt.get("maintenance_issued", 0),
            "replay_maintenance_issued": len(expected),
            "replay_maintenance_completed": len(self.maintenance_completions),
            "current_mqsim_mapping_mutated_by_replay": False,
            "source_version": "UNKNOWN_REPLAY",
        }
        return answer

    def close(self):
        return self.inner.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

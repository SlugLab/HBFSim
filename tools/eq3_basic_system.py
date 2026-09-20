"""Minimal CPU coordinator for the authorized EQ3 basic system fixtures.

This module composes existing backends; it does not add a scheduler thread,
media timing, retry loop inside MQSim, or calibrated topology claim.
"""
from __future__ import annotations

import copy


MODES = {"all_hbf_direct", "mixed_direct", "relay", "dash"}
MAX_HORIZON_NS = (1 << 64) - 1


def _integer(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < (1 if positive else 0):
        raise ValueError(f"{label} is outside its allowed range")
    return value


class UnsupportedComposition(RuntimeError):
    pass


def engineering_fixture(mode, page_bytes):
    """Return one explicit small four-topology software fixture declaration."""
    if mode not in MODES:
        raise ValueError("unknown basic-system topology mode")
    page_bytes = _integer(page_bytes, "page_bytes", positive=True)
    hbf_count = 8 if mode == "all_hbf_direct" else 4
    hbm_count = 0 if mode == "all_hbf_direct" else 4

    def stage(latency):
        return {"latency_ns": latency, "bandwidth_bytes_per_s": 1_000_000_000_000}

    fabric = {
        "evidence": "SCENARIO_ASSUMPTION",
        "hbf": {
            f"hbf{i}": {
                "pair": f"hbm{i}" if mode in {"relay", "dash"} else None,
                "bank_count": 2, "bank_capacity_bytes": page_bytes,
                "fill": stage(1), "direct_link": stage(2),
                "relay_link": stage(3) if mode in {"relay", "dash"} else None,
            } for i in range(hbf_count)
        },
        "hbm": {
            f"hbm{i}": {"bank_count": 2, "bank_capacity_bytes": page_bytes,
                         "gpu_link": stage(4)}
            for i in range(hbm_count)
        },
    }
    hbm = None if not hbm_count else {
        "schema_version": "eq3-basic-hbm-v1",
        "evidence_class": "PARAMETRIC_HBM_SCENARIO",
        "stacks": [{
            "stack_id": f"hbm{i}",
            "media_latency_ns": {"read": 10, "write": 10},
            "media_latency_evidence": "SCENARIO_ASSUMPTION",
            "media_bandwidth_Bps": {"read": 1_000_000_000_000,
                                    "write": 1_000_000_000_000},
            "media_bandwidth_evidence": "SCENARIO_ASSUMPTION",
            "energy_j_per_byte": {"read": None, "write": None},
            "energy_evidence": "UNKNOWN_UNPARAMETERIZED",
        } for i in range(hbm_count)],
        "claim_limit": "small basic-system CPU fixture; not a real DRAM backend",
    }
    requests = []
    for i in range(hbf_count):
        routes = (("direct", "relay", "direct", "relay") if mode == "dash" else
                  ("relay", "relay", "relay") if mode == "relay" else
                  ("direct", "direct", "direct"))
        for local_page, route in enumerate(routes):
            requests.append({
                "request_id": f"hbf{i}-{route}-{local_page}", "stack": f"hbf{i}",
                "stack_local_page": local_page, "route": route,
                "bytes": page_bytes, "arrival_ns": 0, "operation": "read",
            })
    for i in range(hbm_count):
        for local_index in range(3):
            requests.append({
                "request_id": f"hbm{i}-direct-{local_index}", "stack": f"hbm{i}",
                "route": "direct", "bytes": page_bytes, "arrival_ns": 0,
                "operation": "read",
            })
    return {"evidence": "ENGINEERING_FIXTURE_BASIC_SYSTEM",
            "research_geometry": False, "mode": mode,
            "fabric": fabric, "hbm": hbm, "requests": requests}


class BasicSystem:
    """Single-threaded horizon consumer for MQSim, BasicHbm and BasicFabric."""

    def __init__(self, mode, mqsim, hbm, fabric):
        if mode not in MODES:
            raise ValueError("unknown basic-system topology mode")
        self.mode = mode
        self.mqsim = mqsim
        self.hbm = hbm
        self.fabric = fabric
        facts = fabric.immutable_facts()
        self._hbf = set(facts["config"]["hbf"])
        self._hbm = set(facts["config"]["hbm"])
        if mode == "all_hbf_direct" and self._hbm:
            raise ValueError("all_hbf_direct cannot configure in-package HBM")
        if mode in {"relay", "dash"} and (not self._hbf or not self._hbm):
            raise ValueError("relay and DASH require explicit HBF/HBM pairs")
        self.now_ns = 0
        self._records = {}
        self._waiting = []
        self._mq_pending = {}
        self._next_backend_id = 1
        self._fabric_completion_count = 0
        self._hbm_facts = []

    def _validate_request(self, raw, sequence):
        required = {"request_id", "stack", "route", "bytes", "arrival_ns", "operation"}
        if not isinstance(raw, dict) or not required.issubset(raw):
            raise ValueError("request lacks required identity, path, extent, or arrival")
        request_id = raw["request_id"]
        if not isinstance(request_id, str) or not request_id or request_id in self._records:
            raise ValueError("request_id must be unique and nonempty")
        stack, route = raw["stack"], raw["route"]
        if stack in self._hbf:
            kind = "HBF"
            if raw["operation"] != "read":
                raise ValueError("basic MQSim HBF path is read-only")
            if "stack_local_page" not in raw:
                raise ValueError("HBF request requires persistent stack_local_page")
            local_page = _integer(raw["stack_local_page"], "stack_local_page")
            if self.mode in {"all_hbf_direct", "mixed_direct"} and route != "direct":
                raise ValueError("this topology exposes only direct HBF package paths")
            if self.mode == "relay" and route != "relay":
                raise ValueError("cascaded topology exposes no HBF direct GPU path")
            if self.mode == "dash" and route not in {"direct", "relay"}:
                raise ValueError("DASH HBF route must be direct or relay")
        elif stack in self._hbm:
            kind, local_page = "HBM", None
            if self.mode == "all_hbf_direct" or route != "direct":
                raise ValueError("HBM is local/direct only where configured")
            if raw["operation"] not in {"read", "write"}:
                raise ValueError("basic HBM path supports read/write only")
        else:
            raise ValueError("request names an unconfigured stack")
        value = {
            "request_id": request_id, "sequence": sequence, "kind": kind,
            "stack": stack, "route": route,
            "bytes": _integer(raw["bytes"], "bytes", positive=True),
            "arrival_ns": _integer(raw["arrival_ns"], "arrival_ns"),
            "operation": raw["operation"], "stack_local_page": local_page,
            "state": "EXTERNAL_WAIT", "backend_submit_ns": None,
            "backend_completion_ns": None, "backend_media_ns": None,
            "fabric_completion_ns": None,
        }
        page_bytes = getattr(self.mqsim, "header", {}).get("page_bytes")
        if kind == "HBF" and page_bytes is not None and value["bytes"] != page_bytes:
            raise ValueError("HBF request must equal the mapped MQSim profile page size")
        self._records[request_id] = value
        return value

    def _admit(self):
        progress = False
        for request in sorted(self._waiting, key=lambda row: row["sequence"]):
            if request["state"] == "GATE_WAIT":
                if request["gate_target_ns"] <= self.now_ns:
                    self._try_hbf_submit(request)
                    progress = True
                continue
            if request["state"] != "EXTERNAL_WAIT" or request["arrival_ns"] > self.now_ns:
                continue
            if not self.fabric.reserve_source(
                    request["request_id"], request["stack"], request["route"],
                    request["bytes"], request["arrival_ns"]):
                continue
            if request["kind"] == "HBF":
                backend_id = self._next_backend_id
                self._next_backend_id += 1
                request["backend_request_id"] = backend_id
                request["state"] = "SOURCE_RESERVED"
                self._try_hbf_submit(request)
            else:
                request["backend_submit_ns"] = self.now_ns
                request["external_wait_ns"] = self.now_ns - request["arrival_ns"]
                request["state"] = "BACKEND_PENDING"
                self.hbm.arrival({
                    "request_id": request["request_id"], "stack_id": request["stack"],
                    "op": request["operation"], "bytes": request["bytes"],
                    "arrival_ns": self.now_ns,
                })
                self.hbm.submit(request["request_id"], self.now_ns)
            progress = True
        return progress

    def _try_hbf_submit(self, request):
        backend_id = request["backend_request_id"]
        payload = {
            "request_id": backend_id, "issue_ns": self.now_ns,
            "stack": request["stack"],
            "stack_local_page": request["stack_local_page"],
            "bytes": request["bytes"], "operation": "read",
            # MQSim placement is always native direct; package relay is owned
            # solely by BasicFabric and remains in the system record.
            "route": "direct",
        }
        decision = self.mqsim.try_submit(payload)
        request["gate_decision"] = copy.deepcopy(decision)
        if decision["submitted"]:
            request["backend_submit_ns"] = decision["backend_arrival_ns"]
            request["external_wait_ns"] = request["backend_submit_ns"] - request["arrival_ns"]
            request["state"] = "BACKEND_PENDING"
            request.pop("gate_target_ns", None)
            self._mq_pending[backend_id] = request["request_id"]
            return
        if decision["disposition"] == 1 and isinstance(decision.get("target_ns"), int):
            if decision["target_ns"] <= self.now_ns:
                raise RuntimeError("MQSim gate returned a nonfuture defer target")
            request["gate_target_ns"] = decision["target_ns"]
            request["state"] = "GATE_WAIT"
            return
        raise UnsupportedComposition(
            f"UNSUPPORTED_COMPOSITION: MQSim gate rejected request: {decision.get('reason', '')}")

    def _raw_mqsim_completion(self, backend_id, reported_ns):
        matches = [event for event in self.mqsim.observations
                   if event.get("kind") == 2 and event.get("request_id") == backend_id]
        if len(matches) != 1:
            raise UnsupportedComposition("UNSUPPORTED_COMPOSITION: missing unique MQSim media callback")
        raw_ns = matches[0]["time_ns"]
        if raw_ns != reported_ns:
            raise UnsupportedComposition(
                "UNSUPPORTED_COMPOSITION: MQSim aggregate bound differs from media callback")
        return raw_ns

    def _accept_mqsim_completion(self, completion):
        if completion is None:
            return
        backend_id = completion["request_id"]
        if backend_id not in self._mq_pending:
            raise RuntimeError("duplicate or unknown MQSim completion")
        request = self._records[self._mq_pending.pop(backend_id)]
        reported = completion["reported_complete"]
        raw = self._raw_mqsim_completion(backend_id, reported)
        request["backend_media_ns"] = raw
        request["backend_completion_ns"] = reported
        request["state"] = "FABRIC_PENDING"
        self.fabric.mark_source_ready(request["request_id"], reported)

    def _accept_hbm_completions(self):
        for completion in self.hbm.take_media_completions() if self.hbm is not None else ():
            request = self._records[completion["request_id"]]
            if request["state"] != "BACKEND_PENDING":
                raise RuntimeError("duplicate or unknown HBM completion")
            request["backend_media_ns"] = completion["time_ns"]
            request["backend_completion_ns"] = completion["time_ns"]
            request["state"] = "FABRIC_PENDING"
            self.fabric.mark_source_ready(request["request_id"], completion["time_ns"])
        if self.hbm is not None:
            self._hbm_facts.extend(self.hbm.take_facts())

    def _accept_fabric_completions(self):
        completions = self.fabric.completions()
        for completion in completions[self._fabric_completion_count:]:
            request = self._records[completion["request_id"]]
            if request["state"] != "FABRIC_PENDING":
                raise RuntimeError("duplicate or premature fabric completion")
            request["fabric_completion_ns"] = completion["completion_ns"]
            request["final_completion_ns"] = max(
                request["backend_completion_ns"], completion["completion_ns"])
            request["state"] = "COMPLETE"
        self._fabric_completion_count = len(completions)

    def _next_horizon(self, not_arrived):
        candidates = [row["arrival_ns"] for row in not_arrived]
        candidates.extend(row["gate_target_ns"] for row in self._records.values()
                          if row["state"] == "GATE_WAIT")
        if self.hbm is not None and self.hbm.next_event_ns() is not None:
            candidates.append(self.hbm.next_event_ns())
        if self.fabric.next_event_ns() is not None:
            candidates.append(self.fabric.next_event_ns())
        if candidates:
            return min(candidates)
        if self._mq_pending:
            return MAX_HORIZON_NS
        return None

    def run(self, requests):
        rows = list(requests)
        for sequence, raw in enumerate(rows):
            self._validate_request(raw, sequence)
        not_arrived = sorted(self._records.values(), key=lambda row: (row["arrival_ns"], row["sequence"]))
        self._waiting = list(not_arrived)
        while any(row["state"] != "COMPLETE" for row in self._records.values()):
            not_arrived = [row for row in not_arrived if row["arrival_ns"] > self.now_ns]
            self._admit()
            horizon = self._next_horizon(not_arrived)
            if horizon is None or horizon < self.now_ns:
                raise RuntimeError("basic system deadlocked with no legal event")
            mq_completion = self.mqsim.until(horizon)
            self.now_ns = self.mqsim.now
            if self.hbm is not None:
                self.hbm.advance(self.now_ns)
            self._accept_mqsim_completion(mq_completion)
            self._accept_hbm_completions()
            self.fabric.advance(self.now_ns)
            self._accept_fabric_completions()
        receipt = self.mqsim.finish()
        state = self.fabric.resource_state()
        leaked = state["unfinished"] or any(
            any(owner is not None for owner in row["banks"]) or
            any(row[name] is not None for name in ("fill", "direct", "relay"))
            for row in state["hbf"].values()) or any(
            any(owner is not None for owner in row["banks"]) or row["gpu"] is not None
            for row in state["hbm"].values())
        if leaked:
            raise RuntimeError("fabric resource ownership leaked after completion")
        completions = [copy.deepcopy(row) for row in sorted(
            self._records.values(), key=lambda row: row["sequence"])]
        return {
            "schema_version": "eq3-basic-system-v1",
            "evidence": "ENGINEERING_FIXTURE_BASIC_SYSTEM",
            "topology_mode": self.mode,
            "time_ns": self.now_ns,
            "completions": completions,
            "stack_request_counts": {
                stack: sum(row["stack"] == stack for row in completions)
                for stack in sorted(self._hbf | self._hbm)
            },
            "mqsim_receipt": receipt,
            "mqsim_native_events": copy.deepcopy(self.mqsim.native_observations),
            "hbm_facts": copy.deepcopy(self._hbm_facts),
            "fabric_events": list(self.fabric.events()),
            "fabric_resource_state": state,
            "limits": {
                "hbf_backend": "ACTUAL_MQSIM_CHANNEL_PARTITIONED_HBF_STACKS",
                "hbm_backend": "PARAMETRIC_HBM_SCENARIO",
                "fabric": "SCENARIO_ASSUMPTION",
                "capacity_validation": "UNAVAILABLE_NOT_RESEARCH_GEOMETRY",
                "target_throughput_12_8_to_24_5_TBps": "NOT_VALIDATED",
                "energy": "UNKNOWN_WHERE_UNPARAMETERIZED",
                "thermal_coupling": "NOT_CONNECTED_REUSES_EXISTING_SEPARATE_GATE_FIXTURE",
                "research_geometry": False,
            },
            "external_devices": ([{
                "kind": "GDDR", "identity": "PHYSICAL_EXTERNAL_GDDR",
                "service": "UNAVAILABLE", "temperature": "UNAVAILABLE",
            }] if self.mode == "all_hbf_direct" else []),
        }

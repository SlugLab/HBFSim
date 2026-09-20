"""Single-threaded experimental EQ3 coordinator.

This composes existing media, fabric, energy, thermal, and policy objects.  It
does not replace their arbitration or infer unavailable reliability facts.
"""
from __future__ import annotations

import copy
import math
from dataclasses import asdict

from read_rate_policy import (ByteTokenLedger, Decision, StackDecision,
                              StackWindowFacts, WindowFacts)


MAX_HORIZON_NS = (1 << 64) - 1


class UnsupportedComposition(RuntimeError):
    pass


def _integer(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < (1 if positive else 0):
        raise ValueError(f"{label} is outside its allowed range")
    return value


def _percentile95(values):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


class ClosedLoopCoordinator:
    """Compose actual backend clients without owning their internal schedule."""

    def __init__(self, mode, mqsim, hbm, fabric, thermal, energy, policy, *,
                 thermal_window_ns, experiment_end_ns, initial_stack_budget_bytes,
                 shared_endpoint_caps_bytes=None, resource_probe=None,
                 drain_submitted=True):
        self.mode = mode
        self.mqsim, self.hbm, self.fabric = mqsim, hbm, fabric
        self.thermal, self.energy, self.policy = thermal, energy, policy
        self.window_ns = _integer(thermal_window_ns, "thermal_window_ns", positive=True)
        self.end_ns = _integer(experiment_end_ns, "experiment_end_ns", positive=True)
        if self.end_ns % self.window_ns:
            raise ValueError("experiment_end_ns must contain complete thermal windows")
        self.initial_budgets = dict(initial_stack_budget_bytes)
        if not self.initial_budgets or any(not isinstance(k, str) or not k or
                                           _integer(v, "initial stack budget") < 0
                                           for k, v in self.initial_budgets.items()):
            raise ValueError("initial stack budgets must cover configured stacks")
        self.endpoint_caps = dict(shared_endpoint_caps_bytes or {})
        if any(_integer(v, "shared endpoint cap") < 0 for v in self.endpoint_caps.values()):
            raise ValueError("shared endpoint caps must be nonnegative")
        self.resource_probe = resource_probe
        self.drain_submitted = bool(drain_submitted)

        fabric_facts = fabric.immutable_facts()
        config = fabric_facts["config"]
        self._hbf, self._hbm = set(config["hbf"]), set(config["hbm"])
        self._pairs = {stack: row["pair"] for stack, row in config["hbf"].items()}
        configured = self._hbf | self._hbm
        if set(self.initial_budgets) != configured:
            raise ValueError("initial stack budget coverage differs from fabric stacks")

        self.now_ns = 0
        self._records, self._waiting, self._mq_pending = {}, [], {}
        self._maintenance, self._maintenance_pending = {}, {}
        self._next_backend_id = 1
        self._fabric_seen = self._native_seen = self._observation_seen = 0
        self._maintenance_event_seen = 0
        self._maintenance_completion_seen = set()
        self._energy_row_seen = 0
        self._raw_media_ns = {}
        self._thermal_start = 0
        self._next_window = self.window_ns
        self._current_budgets = dict(self.initial_budgets)
        self._last_guard_states = {stack: "normal" for stack in configured}
        self._ledger = self._initial_ledger()
        self._timeline = {name: [] for name in (
            "requests", "native", "fabric", "hbm", "thermal", "rates",
            "control", "maintenance", "resources", "energy")}

    @staticmethod
    def resource_probe_contract():
        """Describe the optional observed-only window probe consumed by policy."""
        return {
            "call": "resource_probe(start_ns:int,end_ns:int)->mapping[stack_id,mapping]",
            "optional_fields": ("backend_busy_fraction", "resource_busy",
                                "retry_count", "uecc_count"),
            "unknown_rule": "omit or None; coordinator never substitutes zero/idle",
            "interval": "half-open [start_ns,end_ns)",
        }

    def _initial_ledger(self):
        enabled = self.policy.profile.enabled
        decision = Decision(
            enabled=enabled, strategy=self.policy.profile.strategy,
            applies_to_window_start_ns=0,
            stack_decisions=tuple(StackDecision(stack, budget, "HOLD", "INITIAL",
                                                ("EXPLICIT_INITIAL_BUDGET",))
                                  for stack, budget in sorted(self.initial_budgets.items())),
            shared_endpoint_budget_bytes=dict(self.endpoint_caps))
        return ByteTokenLedger(decision)

    def _route_endpoints(self, row):
        stack = row["stack"]
        if row["route"] == "relay":
            partner = self._pairs.get(stack)
            if partner is None:
                raise ValueError("relay request lacks a configured HBM partner")
            return (f"{stack}->{partner}:relay-link", f"{partner}:gpu-link")
        return (f"{stack}:gpu-link",)

    def _validate_request(self, raw, sequence):
        required = {"request_id", "stack", "route", "bytes", "arrival_ns", "operation"}
        if not isinstance(raw, dict) or not required.issubset(raw):
            raise ValueError("request lacks required identity/path/extent/arrival")
        request_id, stack = raw["request_id"], raw["stack"]
        if not isinstance(request_id, str) or not request_id or request_id in self._records:
            raise ValueError("request_id must be unique and nonempty")
        if stack not in self.initial_budgets:
            raise ValueError("request names an unconfigured stack")
        kind = "HBF" if stack in self._hbf else "HBM"
        route = raw["route"]
        if kind == "HBF":
            if raw["operation"] != "read" or "stack_local_page" not in raw:
                raise ValueError("HBF requests require read and stack_local_page")
            if self.mode == "relay" and route != "relay":
                raise ValueError("relay topology requires relay HBF requests")
            if self.mode in {"all_hbf_direct", "mixed_direct"} and route != "direct":
                raise ValueError("direct topology requires direct HBF requests")
            if self.mode == "dash" and route not in {"direct", "relay"}:
                raise ValueError("DASH route must be direct or relay")
            local_page = _integer(raw["stack_local_page"], "stack_local_page")
        else:
            if route != "direct" or raw["operation"] not in {"read", "write"}:
                raise ValueError("HBM supports direct read/write only")
            local_page = None
        row = {"request_id": request_id, "sequence": sequence, "kind": kind,
               "stack": stack, "route": route,
               "bytes": _integer(raw["bytes"], "bytes", positive=True),
               "arrival_ns": _integer(raw["arrival_ns"], "arrival_ns"),
               "operation": raw["operation"], "stack_local_page": local_page,
               "route_endpoints": self._route_endpoints(raw), "state": "NOT_ARRIVED",
               "backend_submit_ns": None, "backend_completion_ns": None,
               "backend_media_ns": None, "fabric_completion_ns": None,
               "final_completion_ns": None, "gate_limited": False}
        self._records[request_id] = row
        return row

    def _validate_maintenance(self, raw, sequence):
        required = {"request_id", "stack", "stack_local_page", "due_ns", "initial_age_s"}
        if not isinstance(raw, dict) or not required.issubset(raw):
            raise ValueError("maintenance request lacks identity/stack/due/operation/initial_age_s")
        mid = _integer(raw["request_id"], "maintenance request_id", positive=True)
        if mid in self._maintenance:
            raise ValueError("maintenance request_id must be unique")
        if raw["stack"] not in self.initial_budgets:
            raise ValueError("maintenance stack is unconfigured")
        age = raw["initial_age_s"]
        if isinstance(age, bool) or not isinstance(age, (int, float)) or not math.isfinite(age) or age < 0:
            raise ValueError("initial_age_s must be finite and nonnegative")
        value = copy.deepcopy(raw)
        value.update(sequence=sequence, due_ns=_integer(raw["due_ns"], "maintenance due_ns"),
                     state="NOT_DUE", submit_ns=None, completion_ns=None)
        self._maintenance[mid] = value

    def _ingest_backend_facts(self):
        observations = self.mqsim.observations[self._observation_seen:]
        self._observation_seen = len(self.mqsim.observations)
        for event in observations:
            if event.get("kind") == 2:
                self._raw_media_ns[event["request_id"]] = event["time_ns"]
        native = self.mqsim.native_observations[self._native_seen:]
        self._native_seen = len(self.mqsim.native_observations)
        for event in native:
            fact = copy.deepcopy(event)
            self._timeline["native"].append(fact)
            self.energy.native(fact)

    def _accept_mq_completion(self, completion):
        if completion is None:
            return
        backend_id = completion["request_id"]
        if backend_id not in self._mq_pending:
            raise RuntimeError("duplicate or unknown backend completion")
        request = self._records[self._mq_pending.pop(backend_id)]
        reported = completion["reported_complete"]
        raw = self._raw_media_ns.get(backend_id)
        if raw is None:
            raise UnsupportedComposition("UNSUPPORTED_COMPOSITION: missing MQSim media completion fact")
        if raw != reported:
            raise UnsupportedComposition(
                "UNSUPPORTED_COMPOSITION: raw media and reported completion differ")
        request.update(backend_media_ns=raw, backend_completion_ns=reported,
                       state="FABRIC_PENDING")
        self.fabric.mark_source_ready(request["request_id"], reported)

    def _accept_maintenance_facts(self):
        events = getattr(self.mqsim, "maintenance_events", ())
        for event in events[self._maintenance_event_seen:]:
            self._timeline["maintenance"].append({"phase": "BACKEND_EVENT", **copy.deepcopy(event)})
        self._maintenance_event_seen = len(events)
        completions = getattr(self.mqsim, "maintenance_completions", {})
        for backend_id, completion in completions.items():
            if backend_id in self._maintenance_completion_seen:
                continue
            self._maintenance_completion_seen.add(backend_id)
            if backend_id not in self._maintenance_pending:
                raise RuntimeError("unknown maintenance completion")
            mid = self._maintenance_pending.pop(backend_id)
            row = self._maintenance[mid]
            row["completion_ns"] = completion["end_ns"]
            successful = completion.get("status") in {
                "COMMITTED", "COMMITTED_RECLAIM_DEFERRED"}
            if successful and not completion.get("mapping_committed"):
                raise UnsupportedComposition(
                    "maintenance status and mapping commit fact disagree")
            row["state"] = "COMMITTED" if successful else "FAILED"
            row["completion"] = copy.deepcopy(completion)
            self._timeline["maintenance"].append({"phase": row["state"], **copy.deepcopy(row)})

    def _accept_hbm(self):
        if self.hbm is None:
            return
        for completion in self.hbm.take_media_completions():
            request = self._records[completion["request_id"]]
            if request["state"] != "BACKEND_PENDING":
                raise RuntimeError("duplicate or unknown HBM completion")
            request.update(backend_media_ns=completion["time_ns"],
                           backend_completion_ns=completion["time_ns"],
                           state="FABRIC_PENDING")
            self.fabric.mark_source_ready(request["request_id"], completion["time_ns"])
        for fact in self.hbm.take_facts():
            value = copy.deepcopy(fact)
            self._timeline["hbm"].append(value)
            self.energy.hbm(value)

    def _accept_fabric(self):
        events = self.fabric.events()
        for event in events[self._fabric_seen:]:
            request = self._records[event["request_id"]]
            value = copy.deepcopy(event)
            self._timeline["fabric"].append(value)
            energy_request = copy.deepcopy(request)
            if request["route"] == "relay":
                energy_request["partner"] = self._pairs[request["stack"]]
            self.energy.fabric(value, energy_request)
        self._fabric_seen = len(events)
        for completion in self.fabric.completions():
            request = self._records[completion["request_id"]]
            if request["state"] == "COMPLETE":
                continue
            if request["state"] != "FABRIC_PENDING":
                raise RuntimeError("premature fabric completion")
            request["fabric_completion_ns"] = completion["completion_ns"]
            request["final_completion_ns"] = max(request["backend_completion_ns"],
                                                   completion["completion_ns"])
            request["backend_latency_ns"] = (request["backend_completion_ns"]-
                                               request["backend_submit_ns"])
            request["fabric_latency_ns"] = (request["fabric_completion_ns"]-
                                              request["backend_completion_ns"])
            request["end_to_end_latency_ns"] = (request["final_completion_ns"]-
                                                  request["arrival_ns"])
            request["state"] = "COMPLETE"
            self._timeline["requests"].append({"phase": "FINAL_COMPLETE",
                                               **copy.deepcopy(request)})

    def _advance_components(self, target_ns):
        completion = self.mqsim.until(target_ns)
        self.now_ns = self.mqsim.now
        if self.hbm is not None:
            self.hbm.advance(self.now_ns)
        self.fabric.advance(self.now_ns)
        self._ingest_backend_facts()
        self._accept_mq_completion(completion)
        self._accept_maintenance_facts()
        self._accept_hbm()
        # Ready notifications above may create zero-independent future fabric events.
        self.fabric.advance(self.now_ns)
        self._accept_fabric()

    def _admit_request(self, request):
        if self.now_ns >= self.end_ns or request["arrival_ns"] > self.now_ns:
            return False
        if request["state"] not in {"EXTERNAL_WAIT", "POLICY_WAIT", "SOURCE_RESERVED", "GATE_WAIT"}:
            return False
        if not self._ledger.can_consume(request["stack"], request["route_endpoints"], request["bytes"]):
            request["state"] = "POLICY_WAIT" if request["state"] == "EXTERNAL_WAIT" else request["state"]
            request["gate_limited"] = True
            return False
        if request["state"] in {"EXTERNAL_WAIT", "POLICY_WAIT"}:
            if not self.fabric.reserve_source(request["request_id"], request["stack"], request["route"],
                                              request["bytes"], request["arrival_ns"]):
                request["resource_busy"] = True
                return False
            request["state"] = "SOURCE_RESERVED"
        if request["kind"] == "HBM":
            self.hbm.arrival({"request_id": request["request_id"], "stack_id": request["stack"],
                              "op": request["operation"], "bytes": request["bytes"],
                              "arrival_ns": self.now_ns})
            self.hbm.submit(request["request_id"], self.now_ns)
            submitted_ns = self.now_ns
        else:
            backend_id = request.get("backend_request_id")
            if backend_id is None:
                backend_id = self._next_backend_id
                self._next_backend_id += 1
                request["backend_request_id"] = backend_id
            decision = self.mqsim.try_submit({
                "request_id": backend_id, "issue_ns": self.now_ns, "stack": request["stack"],
                "stack_local_page": request["stack_local_page"], "bytes": request["bytes"],
                "operation": "read", "route": "direct"})
            request["gate_decision"] = copy.deepcopy(decision)
            if not decision["submitted"]:
                if decision.get("disposition") == 1 and isinstance(decision.get("target_ns"), int):
                    if decision["target_ns"] <= self.now_ns:
                        raise RuntimeError("backend gate returned nonfuture target")
                    request.update(state="GATE_WAIT", gate_target_ns=decision["target_ns"],
                                   gate_limited=True)
                    return False
                raise UnsupportedComposition(
                    f"UNSUPPORTED_COMPOSITION: backend rejected reserved request: {decision.get('reason','')}")
            submitted_ns = decision["backend_arrival_ns"]
            self._mq_pending[backend_id] = request["request_id"]
        if not self._ledger.try_consume(request["stack"], request["route_endpoints"], request["bytes"]):
            raise AssertionError("token budget changed between check and atomic commit")
        request.update(state="BACKEND_PENDING", backend_submit_ns=submitted_ns,
                       external_wait_ns=submitted_ns-request["arrival_ns"])
        request.pop("gate_target_ns", None)
        self._timeline["requests"].append({"phase": "ADMITTED", **copy.deepcopy(request)})
        return True

    def _admit(self):
        progress = False
        for request in sorted(self._waiting, key=lambda row: row["sequence"]):
            if request["state"] == "NOT_ARRIVED" and request["arrival_ns"] <= self.now_ns:
                request["state"] = "EXTERNAL_WAIT"
                self._timeline["requests"].append({"phase": "ARRIVAL", **copy.deepcopy(request)})
            if request["state"] in {"EXTERNAL_WAIT", "POLICY_WAIT", "SOURCE_RESERVED", "GATE_WAIT"}:
                if request.get("gate_target_ns", self.now_ns) <= self.now_ns:
                    progress = self._admit_request(request) or progress
        return progress

    def _submit_due_maintenance(self):
        for mid, row in sorted(self._maintenance.items(), key=lambda item: item[1]["sequence"]):
            ready_ns = row.get("target_ns", row["due_ns"])
            if row["state"] not in {"NOT_DUE", "DEFERRED", "THERMAL_WAIT"} or ready_ns > self.now_ns or self.now_ns >= self.end_ns:
                continue
            guard = self._last_guard_states[row["stack"]]
            if guard in {"severe", "shutdown"}:
                if row["state"] != "THERMAL_WAIT" or row.get("blocked_guard") != guard:
                    row.update(state="THERMAL_WAIT", blocked_guard=guard)
                    self._timeline["maintenance"].append(
                        {"phase": "THERMAL_BLOCKED", **copy.deepcopy(row)})
                continue
            row.pop("blocked_guard", None)
            if not hasattr(self.mqsim, "maintain"):
                row["state"] = "UNSUPPORTED_CAPABILITY"
                self._timeline["maintenance"].append({"phase": row["state"], **copy.deepcopy(row)})
                continue
            job = {key: row[key] for key in ("request_id", "stack", "stack_local_page", "due_ns")}
            job.update(deadline_ns=row.get("deadline_ns", 0),
                       parent_id=row.get("parent_id"),
                       reclaim_source_block=row.get("reclaim_source_block", False),
                       failure_injection=row.get("failure_injection", "none"))
            response = self.mqsim.maintain(job)
            status = response.get("status")
            if status == "UNSUPPORTED_CAPABILITY":
                row["state"] = status
            elif response.get("maintenance_accepted") == 1:
                backend_id = row["request_id"]
                row.update(state="INFLIGHT", submit_ns=self.now_ns,
                           backend_request_id=backend_id, placement=response.get("placement"),
                           target=response.get("target"))
                self._maintenance_pending[backend_id] = mid
            elif response.get("target_ns", 0) > self.now_ns:
                row.update(state="DEFERRED", target_ns=response["target_ns"])
            else:
                row["state"] = "FAILED"
            self._timeline["maintenance"].append({"phase": row["state"], **copy.deepcopy(row)})

    def _window_facts(self, start, end, thermal_result):
        probes = self.resource_probe(start, end) if self.resource_probe is not None else {}
        stack_facts = []
        for stack in sorted(self.initial_budgets):
            arrivals = [r for r in self._records.values()
                        if r["stack"] == stack and start <= r["arrival_ns"] < end]
            delivered = [r for r in self._records.values()
                         if r["stack"] == stack and r["final_completion_ns"] is not None and
                         start <= r["final_completion_ns"] < end]
            backlog = [r for r in self._records.values() if r["stack"] == stack and
                       r["arrival_ns"] < end and r["state"] != "COMPLETE"]
            latencies = [r["final_completion_ns"]-r["arrival_ns"] for r in delivered]
            probe = probes.get(stack, {})
            due = [m for m in self._maintenance.values() if m["stack"] == stack and
                   m["due_ns"] < end and m["state"] not in {"COMMITTED", "UNSUPPORTED_CAPABILITY"}]
            stack_facts.append(StackWindowFacts(
                stack_id=stack, offered_bytes=sum(r["bytes"] for r in arrivals),
                delivered_bytes=sum(r["bytes"] for r in delivered),
                backlog_bytes=sum(r["bytes"] for r in backlog),
                oldest_wait_ns=max((end-r["arrival_ns"] for r in backlog), default=0),
                latency_p95_ns=_percentile95(latencies), censored_requests=len(backlog),
                gate_limited=any(r.get("gate_limited") for r in backlog),
                backend_busy_fraction=probe.get("backend_busy_fraction"),
                resource_busy=probe.get("resource_busy"),
                maintenance_due_bytes=sum(int(m.get("bytes", 0)) for m in due),
                maintenance_earliest_deadline_ns=min((m.get("deadline_ns", m["due_ns"])
                                                      for m in due), default=None),
                retry_count=probe.get("retry_count"), uecc_count=probe.get("uecc_count")))
        guards = thermal_result.get("stack_states", {})
        if set(guards) != set(self.initial_budgets):
            raise UnsupportedComposition("thermal stack_states coverage mismatch")
        self._last_guard_states = dict(guards)
        endpoint_guards = {}
        rank = {"normal": 0, "light": 1, "severe": 2, "shutdown": 3}
        for endpoint in self.endpoint_caps:
            involved = [stack for stack in guards if stack in endpoint]
            endpoint_guards[endpoint] = max((guards[s] for s in involved),
                                            key=lambda value: rank[value], default="normal")
        routes = {stack: tuple(sorted({endpoint for row in self._records.values()
                                      if row["stack"] == stack
                                      for endpoint in row["route_endpoints"]}))
                  for stack in self.initial_budgets}
        return WindowFacts(start_ns=start, end_ns=end, guard_state="normal",
                           stacks=tuple(stack_facts), current_budget_bytes=self._current_budgets,
                           hysteresis_budget_bytes=thermal_result.get("hysteresis_budget_bytes", {}),
                           shared_endpoint_caps_bytes=self.endpoint_caps,
                           route_endpoints=routes,
                           guard_states=guards, endpoint_guard_states=endpoint_guards)

    def _close_window(self, boundary):
        component_energy = self.energy.flush(boundary)
        rows = getattr(self.energy, "rows", ())
        for row in rows[self._energy_row_seen:]:
            self._timeline["energy"].append({"kind": "ACTIVITY", **copy.deepcopy(row)})
        self._energy_row_seen = len(rows)
        energy_row = {"kind": "WINDOW_TOTAL", "start_ns": self._thermal_start, "end_ns": boundary,
                      "component_energy_j": copy.deepcopy(component_energy),
                      "phase": "OBSERVATION" if boundary <= self.end_ns else "DRAIN"}
        self._timeline["energy"].append(energy_row)
        thermal_result = self.thermal.advance(self._thermal_start, boundary, component_energy)
        if thermal_result.get("start_ns") != self._thermal_start or thermal_result.get("end_ns") != boundary:
            raise UnsupportedComposition("thermal wrapper interval mismatch")
        self._timeline["thermal"].append(copy.deepcopy(thermal_result))
        self._timeline["resources"].append({"time_ns": boundary,
                                            "phase": energy_row["phase"],
                                            "fabric": self.fabric.resource_state()})
        if boundary <= self.end_ns:
            facts = self._window_facts(self._thermal_start, boundary, thermal_result)
            decision = self.policy.evaluate(facts)
            self._timeline["rates"].append({"start_ns": facts.start_ns, "end_ns": facts.end_ns,
                                            "stacks": [asdict(row) for row in facts.stacks]})
            self._timeline["control"].append(asdict(decision))
            self._current_budgets = {row.stack_id: row.budget_bytes for row in decision.stack_decisions}
            self._ledger = ByteTokenLedger(decision)
        self._thermal_start = boundary
        self._next_window = boundary + self.window_ns

    def _unfinished_submitted(self):
        return (any(r["state"] in {"BACKEND_PENDING", "FABRIC_PENDING"}
                    for r in self._records.values()) or bool(self._maintenance_pending))

    def _next_horizon(self):
        candidates = []
        if self.now_ns < self.end_ns:
            candidates.extend(r["arrival_ns"] for r in self._records.values()
                              if r["state"] == "NOT_ARRIVED" and r["arrival_ns"] < self.end_ns)
            candidates.extend(m["due_ns"] for m in self._maintenance.values()
                              if m["state"] == "NOT_DUE" and m["due_ns"] < self.end_ns)
            candidates.extend(m["target_ns"] for m in self._maintenance.values()
                              if m["state"] == "DEFERRED" and m["target_ns"] < self.end_ns)
        candidates.extend(r["gate_target_ns"] for r in self._records.values()
                          if r["state"] == "GATE_WAIT" and self.now_ns < self.end_ns)
        for source in (self.hbm, self.fabric):
            if source is not None and source.next_event_ns() is not None:
                candidates.append(source.next_event_ns())
        if (self._next_window <= self.end_ns or self._unfinished_submitted() or
                self.now_ns > self._thermal_start):
            candidates.append(self._next_window)
        if candidates:
            return min(value for value in candidates if value >= self.now_ns)
        if self._mq_pending or self._maintenance_pending:
            return MAX_HORIZON_NS
        return None

    def run(self, requests, maintenance=()):
        for sequence, raw in enumerate(requests):
            self._validate_request(raw, sequence)
        for sequence, raw in enumerate(maintenance):
            self._validate_maintenance(raw, sequence)
        self._waiting = sorted(self._records.values(), key=lambda row: (row["arrival_ns"], row["sequence"]))
        while True:
            # Component completion/release was processed by the preceding
            # advance.  At an exact boundary, sample/control precedes any new
            # arrival or admission at that timestamp.
            if self.now_ns == self._next_window:
                self._close_window(self.now_ns)
            if self.now_ns < self.end_ns:
                self._admit()
                self._submit_due_maintenance()
            if self.now_ns >= self.end_ns and not self.drain_submitted:
                break
            if (self.now_ns >= self.end_ns and not self._unfinished_submitted() and
                    self.now_ns == self._thermal_start):
                break
            horizon = self._next_horizon()
            if horizon is None or horizon < self.now_ns:
                raise RuntimeError("closed loop deadlocked with no legal horizon")
            self._advance_components(horizon)

        if self._thermal_start != self.now_ns:
            raise AssertionError("drain must finish on a complete thermal window")
        receipt = self.mqsim.finish()
        for row in self._records.values():
            if row["state"] != "COMPLETE":
                row["censored"] = True
                row["censor_reason"] = ("NOT_ADMITTED_BY_EXPERIMENT_END" if
                                         row["backend_submit_ns"] is None else "DRAIN_DISABLED")
                self._timeline["requests"].append({"phase": "CENSORED", **copy.deepcopy(row)})
        resources = self.fabric.resource_state()
        self._timeline["resources"].append({"time_ns": self.now_ns, "phase": "FINAL",
                                            "fabric": copy.deepcopy(resources)})
        completed = [row for row in self._records.values() if row["state"] == "COMPLETE"]
        observation_completed = [row for row in completed if row["final_completion_ns"] <= self.end_ns]
        censored = [row for row in self._records.values() if row["state"] != "COMPLETE"]
        latencies = [row["final_completion_ns"]-row["arrival_ns"] for row in observation_completed]
        external_waits = [row["external_wait_ns"] for row in observation_completed]
        backend_latencies = [row["backend_latency_ns"] for row in observation_completed]
        fabric_latencies = [row["fabric_latency_ns"] for row in observation_completed]
        return {
            "schema_version": "eq3-isolated-maintenance-closed-loop-v1",
            "evidence": "CONDITIONAL_ENGINEERING_COMPOSITION",
            "topology_mode": self.mode, "observation_end_ns": self.end_ns,
            "drain_end_ns": self.now_ns, "timeline": copy.deepcopy(self._timeline),
            "requests": [copy.deepcopy(row) for row in sorted(self._records.values(),
                                                               key=lambda value: value["sequence"])],
            "maintenance": [copy.deepcopy(row) for row in self._maintenance.values()],
            "summary": {
                "offered_count": len(self._records),
                "offered_bytes": sum(row["bytes"] for row in self._records.values()),
                "submitted_count": sum(row["backend_submit_ns"] is not None for row in self._records.values()),
                "observation_completed_count": len(observation_completed),
                "observation_completed_bytes": sum(row["bytes"] for row in observation_completed),
                "drain_completed_count": len(completed)-len(observation_completed),
                "censored_count": len(censored),
                "censored_bytes": sum(row["bytes"] for row in censored),
                "latency_p95_ns": _percentile95(latencies),
                "external_wait_p95_ns": _percentile95(external_waits),
                "backend_latency_p95_ns": _percentile95(backend_latencies),
                "fabric_latency_p95_ns": _percentile95(fabric_latencies),
                "maintenance_committed": sum(row["state"] == "COMMITTED"
                                             for row in self._maintenance.values()),
                "maintenance_failed": sum(row["state"] == "FAILED"
                                          for row in self._maintenance.values()),
                "maintenance_unsupported": sum(row["state"] == "UNSUPPORTED_CAPABILITY"
                                               for row in self._maintenance.values()),
                "total_energy_j": getattr(self.energy, "total_j", sum(
                    sum(row["component_energy_j"].values()) for row in self._timeline["energy"]
                    if row.get("kind") == "WINDOW_TOTAL")),
                "energy_semantics": "timeline.energy preserves observation and drain intervals separately",
            },
            "mqsim_receipt": receipt,
            "limits": {
                "raw_vs_reported": "UNSUPPORTED_COMPOSITION_UNLESS_EQUAL",
                "reliability": "OBSERVED_ONLY_NO_ECC_INFERENCE",
                "maintenance_age": "INPUT_SECONDS_UNCOMPRESSED",
                "unsubmitted_at_cutoff": "CENSORED_NOT_DRAINED",
            },
        }

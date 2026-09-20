"""Minimal event-driven HBM media fixture for PARAMETRIC_HBM_SCENARIO use."""
import copy
import math


SCHEMA = "eq3-basic-hbm-v1"
EVIDENCE = "PARAMETRIC_HBM_SCENARIO"
OPS = ("read", "write")


def _integer(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < (1 if positive else 0):
        raise ValueError(f"{label} is outside its allowed range")
    return value


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value < (0 if not positive else math.nextafter(0.0, 1.0)):
        raise ValueError(f"{label} is outside its allowed range")
    return value


def scenario_example(stack_ids=("hbm0",)):
    """Explicit example values; callers must opt in by using the returned config."""
    return {
        "schema_version": SCHEMA,
        "evidence_class": EVIDENCE,
        "stacks": [{
            "stack_id": stack_id,
            "media_latency_ns": {"read": 100, "write": 100},
            "media_latency_evidence": "SCENARIO_ASSUMPTION",
            "media_bandwidth_Bps": {"read": 2_048_000_000_000,
                                    "write": 2_048_000_000_000},
            "media_bandwidth_evidence":
                "DERIVED_WITH_TRANSFER_ASSUMPTION: 2048-bit times 8 Gbit/s raw interface treated as media service bandwidth; not calibrated",
            "energy_j_per_byte": {"read": None, "write": None},
            "energy_evidence": "UNKNOWN_UNPARAMETERIZED",
        } for stack_id in stack_ids],
        "claim_limit": "basic CPU timing scenario; not a real DRAM backend",
    }


class BasicHbm:
    """One FIFO media server per HBM stack; fabric service is intentionally external."""

    def __init__(self, config):
        if config.get("schema_version") != SCHEMA or config.get("evidence_class") != EVIDENCE:
            raise ValueError("HBM config must explicitly select PARAMETRIC_HBM_SCENARIO")
        rows = config.get("stacks")
        if not isinstance(rows, list) or not rows:
            raise ValueError("stacks must be a nonempty list")
        self._stacks = {}
        for row in rows:
            stack_id = row.get("stack_id")
            if not isinstance(stack_id, str) or not stack_id or stack_id in self._stacks:
                raise ValueError("stack_id must be a unique nonempty string")
            latency = row.get("media_latency_ns")
            bandwidth = row.get("media_bandwidth_Bps")
            if not isinstance(latency, dict) or set(latency) != set(OPS):
                raise ValueError(f"{stack_id} requires read/write media_latency_ns")
            if not isinstance(bandwidth, dict) or set(bandwidth) != set(OPS):
                raise ValueError(f"{stack_id} requires read/write media_bandwidth_Bps")
            latency = {op: _integer(latency[op], f"{stack_id}.{op}.latency") for op in OPS}
            bandwidth = {op: _number(bandwidth[op], f"{stack_id}.{op}.bandwidth",
                                     positive=True) for op in OPS}
            energy = row.get("energy_j_per_byte", {op: None for op in OPS})
            if not isinstance(energy, dict) or set(energy) != set(OPS):
                raise ValueError(f"{stack_id} energy_j_per_byte must cover read/write")
            checked_energy = {}
            for op in OPS:
                checked_energy[op] = (None if energy[op] is None else
                                      _number(energy[op], f"{stack_id}.{op}.energy"))
            latency_evidence = row.get("media_latency_evidence")
            bandwidth_evidence = row.get("media_bandwidth_evidence")
            energy_evidence = row.get("energy_evidence", "UNKNOWN_UNPARAMETERIZED")
            if not isinstance(latency_evidence, str) or not latency_evidence:
                raise ValueError(f"{stack_id} requires media_latency_evidence")
            if not isinstance(bandwidth_evidence, str) or not bandwidth_evidence:
                raise ValueError(f"{stack_id} requires media_bandwidth_evidence")
            if (any(value is not None for value in checked_energy.values()) and
                    (not isinstance(energy_evidence, str) or
                     energy_evidence.startswith("UNKNOWN"))):
                raise ValueError(f"{stack_id} parameterized energy requires non-UNKNOWN evidence")
            self._stacks[stack_id] = {
                "latency": latency, "bandwidth": bandwidth, "energy": checked_energy,
                "latency_evidence": latency_evidence,
                "bandwidth_evidence": bandwidth_evidence,
                "energy_evidence": energy_evidence,
                "queue": [], "active": None,
            }
        self._now_ns = 0
        self._requests = {}
        self._facts = []
        self._completions = []
        self._event_id = 0

    @property
    def now_ns(self):
        return self._now_ns

    def capability(self):
        return {
            "schema_version": SCHEMA,
            "evidence_class": EVIDENCE,
            "operations": list(OPS),
            "queue_model": "one FIFO single media server per stack",
            "fabric_arbitration": "EXTERNAL_REQUIRED",
            "refresh": "UNSUPPORTED_CAPABILITY",
            "die": "UNKNOWN_NOT_MODELED",
            "plane": "UNKNOWN_NOT_MODELED",
            "real_dram_backend": False,
        }

    def _emit(self, phase, request, time_ns, **extra):
        self._event_id += 1
        fact = {
            "event_id": self._event_id, "phase": phase, "time_ns": time_ns,
            "request_id": request["request_id"], "stack_id": request["stack_id"],
            "op": request["op"], "bytes": request["bytes"],
            "resource": f"{request['stack_id']}:media", "die": "UNKNOWN",
            "plane": "UNKNOWN", "evidence_class": EVIDENCE,
            "reported_completion": False,
        }
        fact.update(extra)
        self._facts.append(fact)

    def arrival(self, request):
        required = {"request_id", "stack_id", "op", "bytes", "arrival_ns"}
        if not isinstance(request, dict) or not required.issubset(request):
            raise ValueError("arrival requires request_id, stack_id, op, bytes, arrival_ns")
        request_id = request["request_id"]
        if not isinstance(request_id, str) or not request_id or request_id in self._requests:
            raise ValueError("request_id must be unique and nonempty")
        stack_id = request["stack_id"]
        if stack_id not in self._stacks:
            raise ValueError("unknown HBM stack")
        if request["op"] not in OPS:
            raise ValueError("HBM media supports only read/write")
        byte_count = _integer(request["bytes"], "bytes", positive=True)
        arrival_ns = _integer(request["arrival_ns"], "arrival_ns")
        if any(key in request for key in ("die", "plane")):
            raise ValueError("die/plane must remain UNKNOWN; address-derived placement is unsupported")
        self.advance(arrival_ns)
        value = {"request_id": request_id, "stack_id": stack_id, "op": request["op"],
                 "bytes": byte_count, "arrival_ns": arrival_ns, "state": "ARRIVED"}
        self._requests[request_id] = value
        self._emit("arrival", value, arrival_ns)
        return copy.deepcopy(value)

    def submit(self, request_id, time_ns=None):
        if request_id not in self._requests:
            raise ValueError("request must arrive before submit")
        request = self._requests[request_id]
        if request["state"] != "ARRIVED":
            raise ValueError("request may be submitted exactly once")
        selected_time = self._now_ns if time_ns is None else _integer(time_ns, "submit time")
        self.advance(selected_time)
        if selected_time < request["arrival_ns"]:
            raise ValueError("submit precedes arrival")
        request["submit_ns"] = selected_time
        request["state"] = "QUEUED"
        self._stacks[request["stack_id"]]["queue"].append(request_id)
        self._emit("submit", request, selected_time)
        self._start(request["stack_id"])
        return {"disposition": "ACCEPTED", "request_id": request_id,
                "media_start_ns": request.get("media_start_ns")}

    def _duration_ns(self, request):
        stack = self._stacks[request["stack_id"]]
        transfer = math.ceil(request["bytes"] * 1_000_000_000 /
                             stack["bandwidth"][request["op"]])
        return stack["latency"][request["op"]] + transfer, transfer

    def _start(self, stack_id):
        stack = self._stacks[stack_id]
        if stack["active"] is not None or not stack["queue"]:
            return
        request = self._requests[stack["queue"].pop(0)]
        duration, transfer = self._duration_ns(request)
        request.update({"state": "MEDIA_ACTIVE", "media_start_ns": self._now_ns,
                        "media_end_ns": self._now_ns + duration})
        stack["active"] = request["request_id"]
        self._emit(
            "media_start", request, self._now_ns, media_end_ns=request["media_end_ns"],
            media_duration_ns=duration, fixed_media_latency_ns=duration - transfer,
            bandwidth_transfer_ns=transfer,
            media_latency_evidence=stack["latency_evidence"],
            media_bandwidth_evidence=stack["bandwidth_evidence"],
            duration_formula="media_latency_ns + ceil(bytes*1e9/media_bandwidth_Bps)")

    def next_event_ns(self):
        values = [self._requests[stack["active"]]["media_end_ns"]
                  for stack in self._stacks.values() if stack["active"] is not None]
        return min(values) if values else None

    def advance(self, target_ns):
        target_ns = _integer(target_ns, "target_ns")
        if target_ns < self._now_ns:
            raise ValueError("HBM clock cannot move backward")
        while self.next_event_ns() is not None and self.next_event_ns() <= target_ns:
            self._now_ns = self.next_event_ns()
            completed_stacks = sorted(
                stack_id for stack_id, stack in self._stacks.items()
                if stack["active"] is not None and
                self._requests[stack["active"]]["media_end_ns"] == self._now_ns)
            for stack_id in completed_stacks:
                stack = self._stacks[stack_id]
                request = self._requests[stack["active"]]
                request["state"] = "MEDIA_COMPLETE"
                stack["active"] = None
                energy_parameter = stack["energy"][request["op"]]
                energy = None if energy_parameter is None else request["bytes"] * energy_parameter
                energy_status = ("UNKNOWN_UNPARAMETERIZED" if energy is None else
                                 stack["energy_evidence"])
                self._emit("media_complete", request, self._now_ns,
                           media_energy_j=energy, media_energy_status=energy_status,
                           requires_fabric=True)
                self._completions.append({
                    "phase": "MEDIA_DONE", "request_id": request["request_id"],
                    "time_ns": self._now_ns, "stack": stack_id,
                    "op": request["op"], "bytes": request["bytes"],
                    "requires_fabric": True, "reported_completion": False,
                    "die": "UNKNOWN", "plane": "UNKNOWN",
                    "media_energy_j": energy, "media_energy_status": energy_status,
                    "evidence_class": EVIDENCE,
                })
            for stack_id in completed_stacks:
                self._start(stack_id)
        self._now_ns = target_ns

    def take_facts(self):
        result, self._facts = self._facts, []
        return copy.deepcopy(result)

    def take_media_completions(self):
        result, self._completions = self._completions, []
        return copy.deepcopy(result)

    def refresh(self, stack_id):
        if stack_id not in self._stacks:
            raise ValueError("unknown HBM stack")
        return {"status": "UNSUPPORTED_CAPABILITY", "operation": "refresh",
                "stack_id": stack_id, "reason": "basic HBM media fixture has no refresh backend"}

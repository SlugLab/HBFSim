"""Default-off parameterized HBF/HBM transfer fabric for CPU composition.

The caller supplies media-ready times.  This module models only base SRAM
buffering and links; it does not issue media commands or call back into a
scheduler.  All parameters are engineering SCENARIO_ASSUMPTION values.
"""
from __future__ import annotations

import copy
import heapq


NANOSECONDS_PER_SECOND = 1_000_000_000


def _integer(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < (1 if positive else 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'non-negative'}")
    return value


def _exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _stage(value, label):
    value = _exact(value, ("latency_ns", "bandwidth_bytes_per_s"), label)
    return {
        "latency_ns": _integer(value["latency_ns"], f"{label}.latency_ns"),
        "bandwidth_bytes_per_s": _integer(
            value["bandwidth_bytes_per_s"],
            f"{label}.bandwidth_bytes_per_s",
            positive=True,
        ),
    }


def _duration(stage, byte_count):
    transfer = (byte_count * NANOSECONDS_PER_SECOND + stage["bandwidth_bytes_per_s"] - 1) // stage[
        "bandwidth_bytes_per_s"
    ]
    return stage["latency_ns"] + transfer


def _normalize(config):
    config = _exact(config, ("evidence", "hbf", "hbm"), "config")
    if config["evidence"] != "SCENARIO_ASSUMPTION":
        raise ValueError("basic fabric parameters must remain SCENARIO_ASSUMPTION")
    if not isinstance(config["hbf"], dict) or not config["hbf"]:
        raise ValueError("config.hbf must be a nonempty object")
    if not isinstance(config["hbm"], dict):
        raise ValueError("config.hbm must be an object")
    hbm = {}
    for stack, raw in sorted(config["hbm"].items()):
        if not isinstance(stack, str) or not stack:
            raise ValueError("HBM stack IDs must be nonempty strings")
        raw = _exact(raw, ("bank_count", "bank_capacity_bytes", "gpu_link"), f"hbm.{stack}")
        count = _integer(raw["bank_count"], f"hbm.{stack}.bank_count", positive=True)
        if count != 2:
            raise ValueError("basic HBM relay buffering requires exactly two banks")
        hbm[stack] = {
            "bank_count": count,
            "bank_capacity_bytes": _integer(
                raw["bank_capacity_bytes"], f"hbm.{stack}.bank_capacity_bytes", positive=True
            ),
            "gpu_link": _stage(raw["gpu_link"], f"hbm.{stack}.gpu_link"),
        }
    hbf = {}
    used_pairs = set()
    for stack, raw in sorted(config["hbf"].items()):
        if not isinstance(stack, str) or not stack:
            raise ValueError("HBF stack IDs must be nonempty strings")
        raw = _exact(
            raw,
            ("pair", "bank_count", "bank_capacity_bytes", "fill", "direct_link", "relay_link"),
            f"hbf.{stack}",
        )
        count = _integer(raw["bank_count"], f"hbf.{stack}.bank_count", positive=True)
        if count != 2:
            raise ValueError("basic HBF buffering requires exactly two banks")
        pair = raw["pair"]
        if pair is not None:
            if not isinstance(pair, str) or pair not in hbm or pair in used_pairs:
                raise ValueError(f"hbf.{stack}.pair must name a unique configured HBM")
            used_pairs.add(pair)
            relay = _stage(raw["relay_link"], f"hbf.{stack}.relay_link")
        else:
            if raw["relay_link"] is not None:
                raise ValueError(f"hbf.{stack}.relay_link requires an HBM pair")
            relay = None
        hbf[stack] = {
            "pair": pair,
            "bank_count": count,
            "bank_capacity_bytes": _integer(
                raw["bank_capacity_bytes"], f"hbf.{stack}.bank_capacity_bytes", positive=True
            ),
            "fill": _stage(raw["fill"], f"hbf.{stack}.fill"),
            "direct_link": _stage(raw["direct_link"], f"hbf.{stack}.direct_link"),
            "relay_link": relay,
        }
    if set(hbf) & set(hbm):
        raise ValueError("stack IDs must be unique across HBF and HBM")
    return {"evidence": "SCENARIO_ASSUMPTION", "hbf": hbf, "hbm": hbm}


class BasicFabric:
    """Small deterministic event engine for media-ready byte transfers."""

    def __init__(self, config):
        self._config = _normalize(copy.deepcopy(config))
        self._now = 0
        self._sequence = 0
        self._jobs = {}
        self._arrivals = []
        self._backend_ready = []
        self._active = []
        self._completions = []
        self._events = []
        self._hbf = {
            stack: {"banks": [None] * row["bank_count"], "fill": None, "direct": None, "relay": None}
            for stack, row in self._config["hbf"].items()
        }
        self._hbm = {
            stack: {"banks": [None] * row["bank_count"], "gpu": None}
            for stack, row in self._config["hbm"].items()
        }

    @property
    def time_ns(self):
        return self._now

    def immutable_facts(self):
        return copy.deepcopy(
            {
                "evidence": "SCENARIO_ASSUMPTION",
                "enabled_by_default": False,
                "media_model": "EXTERNAL_READY_TIMESTAMP",
                "backend_delivered_mode": "RESERVE_SOURCE_BANK_THEN_MARK_READY_NO_DUPLICATE_FILL",
                "duration_rule": "latency_ns + ceil(bytes*1e9/bandwidth_bytes_per_s)",
                "pair_scope": "ONE_TO_ONE_PRIVATE_NO_PACKAGE_GLOBAL_LOCK",
                "same_timestamp_order": "COMPLETE_RELEASE_ARRIVE_ADMIT",
                "config": self._config,
            }
        )

    def enqueue(self, request_id, source_stack, route, bytes, ready_ns):
        if not isinstance(request_id, str) or not request_id or request_id in self._jobs:
            raise ValueError("request_id must be a new nonempty string")
        byte_count = _integer(bytes, "bytes", positive=True)
        ready = _integer(ready_ns, "ready_ns")
        if ready < self._now:
            raise ValueError("ready_ns precedes current fabric time")
        if source_stack in self._config["hbf"]:
            if route not in ("direct", "relay"):
                raise ValueError("HBF route must be direct or relay")
            row = self._config["hbf"][source_stack]
            if byte_count > row["bank_capacity_bytes"]:
                raise ValueError("request exceeds HBF bank capacity; chunking is external")
            if route == "relay":
                pair = row["pair"]
                if pair is None:
                    raise ValueError("relay requested for unpaired HBF")
                if byte_count > self._config["hbm"][pair]["bank_capacity_bytes"]:
                    raise ValueError("request exceeds HBM bank capacity; chunking is external")
            kind = "HBF"
        elif source_stack in self._config["hbm"]:
            if route != "direct":
                raise ValueError("HBM source accepts direct route only")
            kind = "HBM"
        else:
            raise ValueError("unknown source_stack")
        sequence = self._sequence
        self._sequence += 1
        job = {
            "request_id": request_id,
            "source_stack": source_stack,
            "source_kind": kind,
            "route": route,
            "bytes": byte_count,
            "ready_ns": ready,
            "sequence": sequence,
            "state": "WAIT_READY",
            "hbf_bank": None,
            "hbm_bank": None,
        }
        self._jobs[request_id] = job
        heapq.heappush(self._arrivals, (ready, sequence, request_id))

    def reserve_source(self, request_id, source_stack, route, bytes, arrival_ns):
        """Reserve bounded controller storage before submitting a backend command.

        False means the caller must retain the request externally.  A successful
        reservation owns one source-base bank until GPU drain completion (or,
        for HBF relay, until the relay has safely occupied an HBM bank).
        """
        if not isinstance(request_id, str) or not request_id or request_id in self._jobs:
            raise ValueError("request_id must be a new nonempty string")
        byte_count = _integer(bytes, "bytes", positive=True)
        arrival = _integer(arrival_ns, "arrival_ns")
        if arrival > self._now:
            raise ValueError("future request cannot reserve a bank before arrival")
        if source_stack in self._config["hbf"]:
            if route not in ("direct", "relay"):
                raise ValueError("HBF route must be direct or relay")
            row = self._config["hbf"][source_stack]
            if byte_count > row["bank_capacity_bytes"]:
                raise ValueError("request exceeds HBF bank capacity; chunking is external")
            if route == "relay":
                pair = row["pair"]
                if pair is None:
                    raise ValueError("relay requested for unpaired HBF")
                if byte_count > self._config["hbm"][pair]["bank_capacity_bytes"]:
                    raise ValueError("request exceeds HBM bank capacity; chunking is external")
            kind = "HBF"
            state = self._hbf[source_stack]
        elif source_stack in self._config["hbm"]:
            if route != "direct":
                raise ValueError("HBM source accepts direct route only")
            row = self._config["hbm"][source_stack]
            if byte_count > row["bank_capacity_bytes"]:
                raise ValueError("request exceeds HBM bank capacity; chunking is external")
            kind = "HBM"
            state = self._hbm[source_stack]
        else:
            raise ValueError("unknown source_stack")
        bank = self._free_bank(state["banks"])
        if bank is None:
            return False
        sequence = self._sequence
        self._sequence += 1
        job = {
            "request_id": request_id,
            "source_stack": source_stack,
            "source_kind": kind,
            "route": route,
            "bytes": byte_count,
            "ready_ns": None,
            "arrival_ns": arrival,
            "sequence": sequence,
            "state": "BACKEND_RESERVED",
            "hbf_bank": bank if kind == "HBF" else None,
            "hbm_bank": bank if kind == "HBM" else None,
        }
        self._jobs[request_id] = job
        state["banks"][bank] = request_id
        self._events.append(
            {
                "kind": "reserve",
                "request_id": request_id,
                "resource": f"{source_stack}:bank:{bank}",
                ("hbf_bank" if kind == "HBF" else "hbm_bank"): bank,
                "bytes": byte_count,
                "arrival_ns": arrival,
                "time_ns": self._now,
            }
        )
        return True

    def reserve_hbf(self, request_id, source_stack, route, bytes, arrival_ns):
        if source_stack not in self._config["hbf"]:
            raise ValueError("reserve_hbf requires a configured HBF source")
        return self.reserve_source(request_id, source_stack, route, bytes, arrival_ns)

    def mark_source_ready(self, request_id, backend_completion_ns):
        if request_id not in self._jobs or self._jobs[request_id]["state"] != "BACKEND_RESERVED":
            raise ValueError("request does not own a pending backend source reservation")
        completion = _integer(backend_completion_ns, "backend_completion_ns")
        if completion < self._now:
            raise ValueError("backend completion precedes current fabric time")
        job = self._jobs[request_id]
        job["state"] = "BACKEND_READY_PENDING"
        job["ready_ns"] = completion
        heapq.heappush(self._backend_ready, (completion, job["sequence"], request_id))

    def mark_hbf_ready(self, request_id, backend_completion_ns):
        if request_id not in self._jobs or self._jobs[request_id]["source_kind"] != "HBF":
            raise ValueError("mark_hbf_ready requires an HBF reservation")
        self.mark_source_ready(request_id, backend_completion_ns)

    def next_event_ns(self):
        times = []
        if self._arrivals:
            times.append(self._arrivals[0][0])
        if self._backend_ready:
            times.append(self._backend_ready[0][0])
        if self._active:
            times.append(self._active[0][0])
        return min(times) if times else None

    def completions(self):
        return tuple(copy.deepcopy(self._completions))

    def events(self):
        return tuple(copy.deepcopy(self._events))

    def resource_state(self):
        """Read-only ownership snapshot for coordinator conservation checks."""
        return copy.deepcopy({
            "time_ns": self._now,
            "hbf": {
                stack: {"banks": row["banks"], "fill": row["fill"],
                        "direct": row["direct"], "relay": row["relay"]}
                for stack, row in self._hbf.items()
            },
            "hbm": {
                stack: {"banks": row["banks"], "gpu": row["gpu"]}
                for stack, row in self._hbm.items()
            },
            "unfinished": sorted(
                request_id for request_id, job in self._jobs.items()
                if job["state"] != "DONE"
            ),
        })

    def _free_bank(self, banks):
        return next((index for index, owner in enumerate(banks) if owner is None), None)

    def _start(self, job, stage_name, stage, resource, *, hbf_bank=None, hbm_bank=None):
        start = self._now
        end = start + _duration(stage, job["bytes"])
        job["state"] = stage_name
        heapq.heappush(self._active, (end, job["sequence"], stage_name, job["request_id"]))
        event = {
            "kind": "start",
            "stage": stage_name,
            "request_id": job["request_id"],
            "resource": resource,
            "bytes": job["bytes"],
            "start_ns": start,
            "end_ns": end,
        }
        if hbf_bank is not None:
            event["hbf_bank"] = hbf_bank
        if hbm_bank is not None:
            event["hbm_bank"] = hbm_bank
        self._events.append(event)

    def _complete(self, stage_name, request_id):
        job = self._jobs[request_id]
        source = job["source_stack"]
        if stage_name == "HBF_FILL":
            self._hbf[source]["fill"] = None
            job["state"] = "READY_HBF"
        elif stage_name == "HBF_DIRECT":
            self._hbf[source]["direct"] = None
            self._hbf[source]["banks"][job["hbf_bank"]] = None
            job["hbf_bank"] = None
            self._finish(job)
        elif stage_name == "HBF_RELAY":
            self._hbf[source]["relay"] = None
            self._hbf[source]["banks"][job["hbf_bank"]] = None
            job["hbf_bank"] = None
            job["state"] = "READY_HBM"
        elif stage_name == "HBM_GPU":
            hbm_stack = source if job["source_kind"] == "HBM" else self._config["hbf"][source]["pair"]
            self._hbm[hbm_stack]["gpu"] = None
            if job["hbm_bank"] is not None:
                self._hbm[hbm_stack]["banks"][job["hbm_bank"]] = None
                job["hbm_bank"] = None
            self._finish(job)
        else:
            raise AssertionError("unknown active stage")
        self._events.append(
            {"kind": "complete", "stage": stage_name, "request_id": request_id, "bytes": job["bytes"], "time_ns": self._now}
        )

    def _finish(self, job):
        job["state"] = "DONE"
        self._completions.append(
            {
                "request_id": job["request_id"],
                "source_stack": job["source_stack"],
                "route": job["route"],
                "bytes": job["bytes"],
                "ready_ns": job["ready_ns"],
                "completion_ns": self._now,
            }
        )

    def _admit(self):
        waiting = sorted(self._jobs.values(), key=lambda row: row["sequence"])
        for stack, row in self._config["hbf"].items():
            state = self._hbf[stack]
            if state["fill"] is not None:
                continue
            bank = self._free_bank(state["banks"])
            job = next((j for j in waiting if j["source_stack"] == stack and j["state"] == "MEDIA_READY"), None)
            if bank is not None and job is not None:
                state["fill"] = job["request_id"]
                state["banks"][bank] = job["request_id"]
                job["hbf_bank"] = bank
                self._start(job, "HBF_FILL", row["fill"], f"{stack}:fill", hbf_bank=bank)

        for job in waiting:
            if job["state"] != "READY_HBF":
                continue
            stack = job["source_stack"]
            row = self._config["hbf"][stack]
            state = self._hbf[stack]
            if job["route"] == "direct" and state["direct"] is None:
                state["direct"] = job["request_id"]
                self._start(
                    job, "HBF_DIRECT", row["direct_link"], f"{stack}:gpu-link", hbf_bank=job["hbf_bank"]
                )
            elif job["route"] == "relay" and state["relay"] is None:
                pair = row["pair"]
                hbm_state = self._hbm[pair]
                bank = self._free_bank(hbm_state["banks"])
                if bank is not None:
                    state["relay"] = job["request_id"]
                    hbm_state["banks"][bank] = job["request_id"]
                    job["hbm_bank"] = bank
                    self._start(
                        job,
                        "HBF_RELAY",
                        row["relay_link"],
                        f"{stack}->{pair}:relay-link",
                        hbf_bank=job["hbf_bank"],
                        hbm_bank=bank,
                    )

        for stack, row in self._config["hbm"].items():
            state = self._hbm[stack]
            if state["gpu"] is not None:
                continue
            candidates = [
                job
                for job in waiting
                if (job["source_kind"] == "HBM" and job["source_stack"] == stack and job["state"] == "HBM_READY")
                or (
                    job["source_kind"] == "HBF"
                    and self._config["hbf"][job["source_stack"]]["pair"] == stack
                    and job["state"] == "READY_HBM"
                )
            ]
            if candidates:
                job = candidates[0]
                state["gpu"] = job["request_id"]
                self._start(job, "HBM_GPU", row["gpu_link"], f"{stack}:gpu-link", hbm_bank=job["hbm_bank"])

    def advance(self, horizon_ns):
        horizon = _integer(horizon_ns, "horizon_ns")
        if horizon < self._now:
            raise ValueError("fabric time cannot move backwards")
        while True:
            next_time = self.next_event_ns()
            if next_time is None or next_time > horizon:
                break
            self._now = next_time
            ending = []
            while self._active and self._active[0][0] == self._now:
                ending.append(heapq.heappop(self._active))
            for _, _, stage_name, request_id in ending:
                self._complete(stage_name, request_id)
            while self._backend_ready and self._backend_ready[0][0] == self._now:
                _, _, request_id = heapq.heappop(self._backend_ready)
                job = self._jobs[request_id]
                job["state"] = "READY_HBF" if job["source_kind"] == "HBF" else "HBM_READY"
                self._events.append(
                    {
                        "kind": "backend_ready",
                        "request_id": request_id,
                        "bytes": job["bytes"],
                        "time_ns": self._now,
                    }
                )
            while self._arrivals and self._arrivals[0][0] == self._now:
                _, _, request_id = heapq.heappop(self._arrivals)
                job = self._jobs[request_id]
                job["state"] = "MEDIA_READY" if job["source_kind"] == "HBF" else "HBM_READY"
                self._events.append(
                    {
                        "kind": "media_ready",
                        "request_id": request_id,
                        "bytes": job["bytes"],
                        "time_ns": self._now,
                    }
                )
            self._admit()
        self._now = horizon

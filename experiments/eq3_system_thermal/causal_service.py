#!/usr/bin/env python3
"""Exact-event aggregate fluid service for causal batch consumers.

Control state and endpoint byte quotas are frozen at thermal-window boundaries,
while storage completions occur at integer-nanosecond event horizons inside the
window.  Shared bandwidth uses exact Fraction max-min rates.  Two-bank
occupancy is finite, though buffer turnover remains a continuous-fluid
approximation rather than a page transaction model.
"""

from __future__ import annotations

from collections import deque
import copy
from dataclasses import dataclass
from fractions import Fraction
import heapq
from typing import Deque, Dict, Mapping, Optional, Sequence, Tuple

from topology_service import (
    MAINTENANCE_OPERATIONS,
    MAINTENANCE_POLICY,
    OPERATIONS,
    READ_OPERATIONS,
    STATES,
    _integer,
    _normalize,
    TopologyService,
)


NS_PER_SECOND = 1_000_000_000
CAUSAL_OPERATIONS = set(OPERATIONS) | {"hbm_fill", "migration_program"}


def _ceil_fraction(value: Fraction) -> int:
    return (value.numerator + value.denominator - 1) // value.denominator


@dataclass
class _CausalJob:
    job_id: str
    stack: str
    channel: str
    operation: str
    route: Optional[str]
    arrival_ns: int
    total_bytes: int
    completed_bytes: int
    unadmitted_bytes: int
    sequence: int
    maintenance_id: Optional[str]
    metadata: dict
    state: str = "QUEUED"
    slice_total_bytes: int = 0
    slice_transferred_bytes: int = 0
    rate_credit: Fraction = Fraction(0, 1)
    first_service_ns: Optional[int] = None
    last_service_ns: Optional[int] = None
    completion_ns: Optional[int] = None


class CausalTopologyService:
    """Event-driven v2 service sharing the v1 topology/configuration contract."""

    schema_version = "eq3-causal-topology-service-v2"

    def __init__(self, config: dict):
        raw_config = copy.deepcopy(config)
        self._channel_groups = raw_config.pop("causal_channel_groups", None)
        self._config = _normalize(raw_config)
        # Reuse the reviewed route/resource/activity rules without sharing its
        # mutable rate-window state.
        self._rules = TopologyService(raw_config)
        if self._channel_groups is not None:
            if set(self._channel_groups) != set(self._config["channels"]):
                raise ValueError("causal_channel_groups must exactly cover stacks")
            for stack, channels in self._config["channels"].items():
                groups = self._channel_groups[stack]
                if set(groups) != set(channels):
                    raise ValueError("causal_channel_groups must exactly cover channels")
                for channel, row in groups.items():
                    if (not isinstance(row, dict) or set(row) != {
                            "resource_id", "bandwidth_bytes_per_s"}):
                        raise ValueError("invalid causal channel group row")
                    if (not isinstance(row["resource_id"], str) or not row["resource_id"]
                            or type(row["bandwidth_bytes_per_s"]) is not int
                            or row["bandwidth_bytes_per_s"] <= 0):
                        raise ValueError("invalid causal channel group identity/rate")
        self._now = 0
        self._window_start: Optional[int] = None
        self._window_end: Optional[int] = None
        self._states: Dict[str, str] = {}
        self._quota_remaining: Dict[str, int] = {}
        self._sequence = 0
        self._jobs: Dict[str, _CausalJob] = {}
        self._known_ids = set()
        self._queues: Dict[Tuple[str, str], Deque[str]] = {
            (stack, channel): deque()
            for stack, channels in self._config["channels"].items()
            for channel in channels
        }
        self._active = set()
        self._draining: list[Tuple[int, int, str, int]] = []
        self._window_activities: list[dict] = []
        self._window_resource_work: Dict[str, int] = {}
        self._window_completion_ids: list[str] = []
        self._window_maintenance_ids: list[str] = []
        self._completed_since_read: Dict[str, int] = {}
        self._activity_offset = 0
        self._completion_offset = 0
        self._maintenance_offset = 0

    @property
    def now_ns(self) -> int:
        return self._now

    def immutable_facts(self) -> dict:
        facts = self._rules.immutable_facts()
        return {
            "schema_version": "eq3-causal-topology-facts-v2",
            "config": facts["config"],
            "control": "FROZEN_PER_THERMAL_WINDOW_FUTURE_ONLY",
            "bandwidth": "EXACT_FRACTION_PROGRESSIVE_MAX_MIN",
            "endpoint_quota": "INTEGER_BYTES_RESERVED_ON_SLICE_ADMISSION_ONCE",
            "buffer": "FINITE_TWO_BANK_ANALYTICAL_OCCUPANCY_BOUND_CONTINUOUS_TURNOVER",
            "pipeline": "SHARED_BANDWIDTH_EXACT_PROPAGATION_LATENCY_APPROXIMATE",
            "completion": "INTEGER_NS_EVENT_NO_THERMAL_WINDOW_FLOOR",
            "completion_query": "NONE_UNTIL_NEXT_STATE_CHANGING_EVENT_IS_TERMINAL_USE_NEXT_EVENT_NS",
            "backend_latency": "UNKNOWN_FLUID_MODEL",
            "maintenance_guard_policy": MAINTENANCE_POLICY,
            "causal_operations": sorted(CAUSAL_OPERATIONS),
            "hbm_fill": "HBM_MEDIA_PLUS_SHARED_HALF_DUPLEX_GPU_LINK_ENGINEERING_PROXY",
            "migration_program": (
                "HBF_PROGRAM_MEDIA_WORK_PLUS_REVERSE_DESTINATION_FABRIC_PATH_ENGINEERING_PROXY"
            ),
            "causal_channel_groups": copy.deepcopy(self._channel_groups),
        }

    def begin_window(self, start_ns: int, end_ns: int,
                     budgets: Mapping[str, int],
                     endpoint_states: Mapping[str, str]) -> dict:
        start = _integer(start_ns, "start_ns")
        end = _integer(end_ns, "end_ns")
        if start != self._now or end <= start:
            raise ValueError("control window must begin at current service time")
        if self._window_end is not None and self._now != self._window_end:
            raise ValueError("previous control window has not reached its boundary")
        stacks = set(self._config["channels"])
        if set(budgets) != stacks or set(endpoint_states) != stacks:
            raise ValueError("budgets and endpoint_states must exactly cover stacks")
        if any(endpoint_states[stack] not in STATES for stack in stacks):
            raise ValueError("unknown endpoint state")
        scale = self._config["work_scale"]
        self._window_start, self._window_end = start, end
        self._states = dict(endpoint_states)
        self._quota_remaining = {
            stack: _integer(budgets[stack], f"budgets.{stack}") * scale
            for stack in stacks
        }
        self._window_activities = []
        self._window_resource_work = {}
        self._window_completion_ids = []
        self._window_maintenance_ids = []
        self._activity_offset = self._completion_offset = self._maintenance_offset = 0
        self._activate()
        return {
            "start_ns": start, "end_ns": end,
            "budgets": dict(budgets), "endpoint_states": dict(endpoint_states),
            "semantics": "FROZEN_FUTURE_CONTROL_NO_REEVALUATION_OF_ACTIVE_SLICES",
        }

    def _default_route(self, stack: str, channel: str) -> Optional[str]:
        return self._rules._default_route(stack, channel)

    def _endpoints(self, job: _CausalJob) -> Tuple[str, ...]:
        return self._rules._endpoints(job)

    def _phase_resources(self, job: _CausalJob) -> list[Tuple[str, dict, int]]:
        if job.operation == "hbm_fill":
            proxy = copy.copy(job)
            proxy.operation = "read"
            proxy.route = "direct"
            phases = self._rules._phase_resources(proxy)
        elif job.operation == "migration_program":
            read_proxy = copy.copy(job)
            read_proxy.operation = "read"
            program_proxy = copy.copy(job)
            program_proxy.operation = "program"
            media = self._rules._phase_resources(program_proxy)[0]
            phases = [media] + self._rules._phase_resources(read_proxy)[1:]
        else:
            phases = self._rules._phase_resources(job)
        if self._channel_groups is not None:
            group = self._channel_groups[job.stack][job.channel]
            _, _, coefficient = phases[0]
            phases[0] = (
                group["resource_id"],
                {"latency_ns": 0,
                 "bandwidth_bytes_per_s": group["bandwidth_bytes_per_s"]},
                coefficient,
            )
        return phases

    def _buffer_stacks(self, job: _CausalJob) -> Tuple[str, ...]:
        if job.operation not in READ_OPERATIONS and job.operation not in {
            "hbm_fill", "migration_program",
        }:
            return ()
        return tuple(row[0] for row in self._rules._buffer_specs(job))

    def _buffer_capacity(self, job: _CausalJob) -> int:
        """Maximum payload resident in each reserved ping-pong bank."""
        specs = self._rules._buffer_specs(job)
        return min((capacity for _, _, capacity in specs), default=job.unadmitted_bytes)

    def _pipeline_latency_ns(self, job: _CausalJob) -> int:
        return sum(stage["latency_ns"] for _, stage, _ in self._phase_resources(job))

    def _slice_drain_latency_ns(self, job: _CausalJob) -> int:
        # Shared rates already model overlapped stage throughput. Finite-bank
        # turnover therefore pays propagation on the final slice only.
        return self._pipeline_latency_ns(job) if job.unadmitted_bytes == 0 else 0

    def _class(self, job: _CausalJob) -> str:
        maintenance = job.operation in MAINTENANCE_OPERATIONS or job.maintenance_id is not None
        return f"maintenance:{job.operation}" if maintenance else f"foreground:{job.route}"

    def submit_jobs(self, jobs: Sequence[dict]) -> dict:
        if self._window_end is None:
            raise RuntimeError("begin_window is required before submit_jobs")
        accepted = []
        for raw in jobs:
            required = {"job_id", "stack", "channel", "operation", "bytes", "arrival_ns"}
            optional = {"route", "maintenance_id", "metadata"}
            if not isinstance(raw, dict) or not required.issubset(raw) or set(raw) - required - optional:
                raise ValueError("job has missing or unknown fields")
            job_id = raw["job_id"]
            if not isinstance(job_id, str) or not job_id or job_id in self._known_ids:
                raise ValueError("job_id must be globally unique and nonempty")
            stack, channel = raw["stack"], raw["channel"]
            if stack not in self._config["channels"] or channel not in self._config["channels"][stack]:
                raise ValueError("job names unknown stack/channel")
            operation = raw["operation"]
            if operation not in CAUSAL_OPERATIONS:
                raise ValueError("unsupported operation")
            arrival = _integer(raw["arrival_ns"], "arrival_ns")
            if arrival < self._now or arrival >= self._window_end:
                raise ValueError("job arrival must lie between current time and control-window end")
            route = raw.get("route", self._default_route(stack, channel))
            if operation == "hbm_fill":
                if stack not in self._config["fabric"]["hbm"]:
                    raise ValueError("hbm_fill requires an HBM destination stack")
                route = None
            elif operation == "migration_program":
                if stack not in self._config["fabric"]["hbf"]:
                    raise ValueError("migration_program requires an HBF destination stack")
                route = self._rules._validate_route(stack, channel, route)
            else:
                route = self._rules._validate_route(stack, channel, route)
            if operation not in READ_OPERATIONS and operation not in {
                "hbm_fill", "migration_program",
            }:
                route = None
            metadata = raw.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be an object")
            maintenance_id = raw.get("maintenance_id")
            if maintenance_id is not None and (not isinstance(maintenance_id, str) or not maintenance_id):
                raise ValueError("maintenance_id must be a nonempty string")
            byte_count = _integer(raw["bytes"], "bytes", positive=True)
            job = _CausalJob(
                job_id=job_id, stack=stack, channel=channel, operation=operation,
                route=route, arrival_ns=arrival, total_bytes=byte_count,
                completed_bytes=0, unadmitted_bytes=byte_count,
                sequence=self._sequence, maintenance_id=maintenance_id,
                metadata=copy.deepcopy(metadata),
            )
            self._sequence += 1
            self._known_ids.add(job_id)
            self._jobs[job_id] = job
            self._queues[(stack, channel)].append(job_id)
            accepted.append(job_id)
        self._activate()
        return {"accepted_job_ids": accepted, "time_ns": self._now}

    def _eligible_heads(self) -> list[_CausalJob]:
        result = []
        for key in sorted(self._queues):
            queue = self._queues[key]
            if queue:
                self._queues[key] = queue = deque(
                    job_id for job_id in queue if self._jobs[job_id].state != "DONE"
                )
            first = {}
            for job_id in queue:
                job = self._jobs[job_id]
                if job.state == "QUEUED":
                    group = self._class(job)
                    prior = first.get(group)
                    if prior is None or (job.arrival_ns, job.sequence) < (prior.arrival_ns, prior.sequence):
                        first[group] = job
            result.extend(first.values())
        return sorted(result, key=lambda job: job.sequence)

    def _state_allows(self, job: _CausalJob) -> bool:
        maintenance = job.operation in MAINTENANCE_OPERATIONS or job.maintenance_id is not None
        states = [self._states[endpoint] for endpoint in self._endpoints(job)]
        if maintenance:
            return "shutdown" not in states
        return not any(state in {"severe", "shutdown"} for state in states)

    def _buffers_available(self, job: _CausalJob) -> bool:
        # Continuous aggregate turnover does not bind one whole bank to one
        # channel flow. Occupancy remains bounded analytically in receipts.
        return True

    def _reserve_buffers(self, job: _CausalJob) -> None:
        pass

    def _release_buffers(self, job: _CausalJob) -> None:
        pass

    def _buffer_bounds(self) -> dict:
        bounds = {}
        fabric = self._config["fabric"]
        for stack in self._config["channels"]:
            row = fabric["hbf"].get(stack) or fabric["hbm"].get(stack)
            if row is None:
                continue
            active = [job for job in self._jobs.values()
                      if job.state == "ACTIVE" and stack in self._buffer_stacks(job)]
            capacity = row["bank_count"] * row["bank_capacity_bytes"]
            demand_bound = sum(
                min(job.slice_total_bytes - job.slice_transferred_bytes,
                    row["bank_capacity_bytes"])
                for job in active
            )
            bounds[stack] = {
                "bank_count": row["bank_count"],
                "bank_capacity_bytes": row["bank_capacity_bytes"],
                "maximum_occupancy_bytes": min(capacity, demand_bound),
                "active_flow_count": len(active),
                "semantics": "ANALYTICAL_UPPER_BOUND_NOT_OBSERVED_BANK_OWNERSHIP",
            }
        return bounds

    def _activate(self) -> None:
        if self._window_end is None or self._now >= self._window_end:
            return
        scale = self._config["work_scale"]
        while True:
            heads = [job for job in self._eligible_heads()
                     if job.arrival_ns <= self._now and self._state_allows(job)
                     and self._buffers_available(job)]
            if not heads:
                return
            progress = False
            for job in heads:
                if job.state != "QUEUED" or not self._buffers_available(job):
                    continue
                maintenance = job.operation in MAINTENANCE_OPERATIONS or job.maintenance_id is not None
                if maintenance:
                    slice_bytes = job.unadmitted_bytes
                else:
                    endpoints = self._endpoints(job)
                    peers = {
                        endpoint: sum(1 for other in heads if endpoint in self._endpoints(other)
                                      and not (other.operation in MAINTENANCE_OPERATIONS
                                               or other.maintenance_id is not None))
                        for endpoint in endpoints
                    }
                    slice_bytes = min(
                        job.unadmitted_bytes,
                        *(self._quota_remaining[endpoint] // (scale * max(1, peers[endpoint]))
                          for endpoint in endpoints),
                    )
                if slice_bytes <= 0:
                    continue
                if not maintenance:
                    for endpoint in self._endpoints(job):
                        self._quota_remaining[endpoint] -= slice_bytes * scale
                job.unadmitted_bytes -= slice_bytes
                job.slice_total_bytes = slice_bytes
                job.slice_transferred_bytes = 0
                job.rate_credit = Fraction(0, 1)
                job.state = "ACTIVE"
                job.first_service_ns = self._now if job.first_service_ns is None else job.first_service_ns
                self._active.add(job.job_id)
                self._reserve_buffers(job)
                progress = True
            if not progress:
                return

    def _rates(self) -> Dict[str, Fraction]:
        active = [self._jobs[job_id] for job_id in sorted(self._active)]
        if not active:
            return {}
        resource_rates: Dict[str, Fraction] = {}
        coefficients: Dict[str, Dict[str, int]] = {}
        for job in active:
            coefficients[job.job_id] = {}
            for resource, stage, coefficient in self._phase_resources(job):
                coefficients[job.job_id][resource] = coefficient
                resource_rates[resource] = Fraction(
                    stage["bandwidth_bytes_per_s"] * self._config["work_scale"], 1
                )
        residual = dict(resource_rates)
        rates = {job.job_id: Fraction(0, 1) for job in active}
        unfrozen = set(rates)
        while unfrozen:
            bounds = []
            for resource, capacity in residual.items():
                total_coefficient = sum(
                    coefficients[job_id].get(resource, 0) for job_id in unfrozen
                )
                if total_coefficient:
                    bounds.append((capacity / total_coefficient, resource))
            if not bounds:
                break
            increment = min(bound for bound, _ in bounds)
            for job_id in unfrozen:
                rates[job_id] += increment
            for resource in residual:
                used = increment * sum(
                    coefficients[job_id].get(resource, 0) for job_id in unfrozen
                )
                residual[resource] -= used
            saturated = {resource for resource, value in residual.items() if value == 0}
            frozen = {
                job_id for job_id in unfrozen
                if any(resource in saturated for resource in coefficients[job_id])
            }
            if not frozen:
                raise AssertionError("max-min allocation made no progress")
            unfrozen -= frozen
        return rates

    def _next_transfer_finish(self, rates: Mapping[str, Fraction]) -> Optional[int]:
        times = []
        for job_id, rate in rates.items():
            if rate <= 0:
                continue
            job = self._jobs[job_id]
            remaining = Fraction(job.slice_total_bytes - job.slice_transferred_bytes, 1) - job.rate_credit
            duration_ns = _ceil_fraction(remaining * NS_PER_SECOND / rate)
            times.append(self._now + max(1, duration_ns))
        return min(times) if times else None

    def next_completion_ns(self) -> Optional[int]:
        events = []
        events.extend((completion_ns, "drain", job_id)
                      for completion_ns, _, job_id, _ in self._draining)
        rates = self._rates()
        for job_id, rate in rates.items():
            job = self._jobs[job_id]
            if rate <= 0:
                continue
            remaining = Fraction(job.slice_total_bytes - job.slice_transferred_bytes, 1) - job.rate_credit
            finish = self._now + max(1, _ceil_fraction(remaining * NS_PER_SECOND / rate))
            events.append((finish, "transfer", job_id))
        events.extend((job.arrival_ns, "arrival", job.job_id)
                      for job in self._jobs.values()
                      if job.state == "QUEUED" and job.arrival_ns > self._now)
        if not events:
            return None
        earliest = min(time_ns for time_ns, _, _ in events)
        # Only a terminal drain at the earliest state-changing event is an
        # exact next completion. Other events may change max-min rates first.
        terminal = [
            time_ns for time_ns, kind, job_id in events
            if time_ns == earliest and kind == "drain"
            and self._jobs[job_id].unadmitted_bytes == 0
        ]
        return min(terminal) if terminal else None

    def next_event_ns(self) -> Optional[int]:
        if self._window_end is None:
            return None
        values = [self._window_end]
        if self._draining:
            values.append(self._draining[0][0])
        transfer = self._next_transfer_finish(self._rates())
        if transfer is not None:
            values.append(transfer)
        arrivals = [job.arrival_ns for job in self._jobs.values()
                    if job.state == "QUEUED" and job.arrival_ns > self._now]
        if arrivals:
            values.append(min(arrivals))
        return min(value for value in values if value >= self._now)

    def _progress(self, target_ns: int, rates: Mapping[str, Fraction]) -> None:
        duration = target_ns - self._now
        if duration <= 0:
            return
        for job_id, rate in rates.items():
            job = self._jobs[job_id]
            exact = job.rate_credit + rate * duration / NS_PER_SECOND
            integer_bytes = min(
                job.slice_total_bytes - job.slice_transferred_bytes,
                exact.numerator // exact.denominator,
            )
            job.rate_credit = exact - integer_bytes
            if integer_bytes <= 0:
                continue
            job.slice_transferred_bytes += integer_bytes
            job.last_service_ns = target_ns
            if job.operation in {"hbm_fill", "migration_program"}:
                rows = []
                base = {
                    "operation": job.operation, "stack": job.stack,
                    "channel": job.channel, "bytes": integer_bytes,
                    "route": job.route, "start_ns": self._now, "end_ns": target_ns,
                }
                if job.operation == "hbm_fill":
                    phases = (
                        ("media_fill", f"{job.stack}:channel:{job.channel}:media", None),
                        ("hbm_base_fill", f"{job.stack}:base", None),
                        ("gpu_link_fill", f"{job.stack}:gpu-link", None),
                    )
                elif job.route == "direct":
                    phases = (
                        ("media_program", f"{job.stack}:channel:{job.channel}:media", None),
                        ("destination_base", f"{job.stack}:base", None),
                        ("destination_fill", f"{job.stack}:fill", None),
                        ("reverse_gpu_link", f"{job.stack}:gpu-link", None),
                    )
                else:
                    partner = self._config["fabric"]["hbf"][job.stack]["pair"]
                    phases = (
                        ("media_program", f"{job.stack}:channel:{job.channel}:media", None),
                        ("destination_base", f"{job.stack}:base", None),
                        ("destination_fill", f"{job.stack}:fill", None),
                        ("reverse_relay", f"{job.stack}->{partner}:relay-link", partner),
                        ("partner_gpu_receive", f"{partner}:gpu-link", partner),
                    )
                for phase, resource, partner in phases:
                    row = dict(base, phase=phase, resource=resource)
                    if partner is not None:
                        row["partner"] = partner
                    rows.append(row)
            else:
                rows = self._rules._activity_rows(job, integer_bytes, self._now, target_ns)
            self._window_activities.extend(rows)
            for resource, _, coefficient in self._phase_resources(job):
                self._window_resource_work[resource] = (
                    self._window_resource_work.get(resource, 0) + integer_bytes * coefficient
                )

    def _finish_transfers(self) -> None:
        for job_id in list(self._active):
            job = self._jobs[job_id]
            if job.slice_transferred_bytes != job.slice_total_bytes:
                continue
            self._active.remove(job_id)
            job.state = "DRAINING"
            completion_ns = self._now + self._slice_drain_latency_ns(job)
            heapq.heappush(
                self._draining,
                (completion_ns, self._sequence, job_id, job.slice_total_bytes),
            )
            self._sequence += 1

    def _finish_drains(self) -> None:
        while self._draining and self._draining[0][0] <= self._now:
            completion_ns, _, job_id, byte_count = heapq.heappop(self._draining)
            job = self._jobs[job_id]
            job.completed_bytes += byte_count
            self._completed_since_read[job_id] = self._completed_since_read.get(job_id, 0) + byte_count
            self._release_buffers(job)
            job.slice_total_bytes = 0
            job.slice_transferred_bytes = 0
            job.rate_credit = Fraction(0, 1)
            if job.unadmitted_bytes:
                job.state = "QUEUED"
            else:
                job.state = "DONE"
                job.completion_ns = completion_ns
                self._window_completion_ids.append(job_id)
                if job.maintenance_id is not None:
                    self._window_maintenance_ids.append(job.maintenance_id)

    def _progress_rows(self) -> list[dict]:
        rows = []
        for job in sorted(self._jobs.values(), key=lambda row: row.sequence):
            rows.append({
                "job_id": job.job_id, "maintenance_id": job.maintenance_id,
                "metadata": copy.deepcopy(job.metadata), "operation": job.operation,
                "stack": job.stack, "channel": job.channel, "route": job.route,
                "arrival_ns": job.arrival_ns, "total_bytes": job.total_bytes,
                "served_since_last_advance_bytes": self._completed_since_read.get(job.job_id, 0),
                "cumulative_served_bytes": job.completed_bytes,
                "remaining_bytes": job.total_bytes - job.completed_bytes,
                "unadmitted_bytes": job.unadmitted_bytes,
                "slice_total_bytes": job.slice_total_bytes,
                "slice_transferred_bytes": job.slice_transferred_bytes,
                "inflight_transferred_bytes": (
                    job.slice_transferred_bytes if job.state in {"ACTIVE", "DRAINING"} else 0
                ),
                "state": job.state, "first_service_ns": job.first_service_ns,
                "last_service_ns": job.last_service_ns,
                "completion_ns": job.completion_ns,
            })
        return rows

    def _retire_completed(self, completion_ids: Sequence[str]) -> None:
        """Drop emitted terminal records while retaining global ID uniqueness."""
        completed = set(completion_ids)
        if not completed:
            return
        for key, queue in self._queues.items():
            self._queues[key] = deque(job_id for job_id in queue if job_id not in completed)
        for job_id in completed:
            self._jobs.pop(job_id, None)

    def advance_to(self, horizon_ns: int) -> dict:
        if self._window_end is None:
            raise RuntimeError("begin_window is required before advance_to")
        horizon = _integer(horizon_ns, "horizon_ns")
        if horizon < self._now or horizon > self._window_end:
            raise ValueError("horizon must lie within the current control window")
        start = self._now
        self._completed_since_read = {}
        while self._now < horizon:
            self._finish_drains()
            self._activate()
            rates = self._rates()
            candidates = [horizon]
            transfer = self._next_transfer_finish(rates)
            if transfer is not None:
                candidates.append(transfer)
            if self._draining:
                candidates.append(self._draining[0][0])
            arrivals = [job.arrival_ns for job in self._jobs.values()
                        if job.state == "QUEUED" and job.arrival_ns > self._now]
            if arrivals:
                candidates.append(min(arrivals))
            target = min(value for value in candidates if value >= self._now)
            if target == self._now:
                # Only same-time completion/release/admission is allowed here.
                self._finish_transfers()
                self._finish_drains()
                self._activate()
                next_value = [value for value in candidates if value > self._now]
                if not next_value:
                    break
                target = min(next_value)
            self._progress(target, rates)
            self._now = target
            self._finish_transfers()
        self._finish_drains()
        self._activate()
        activities = self._window_activities[self._activity_offset:]
        completion_ids = self._window_completion_ids[self._completion_offset:]
        maintenance_ids = self._window_maintenance_ids[self._maintenance_offset:]
        self._activity_offset = len(self._window_activities)
        self._completion_offset = len(self._window_completion_ids)
        self._maintenance_offset = len(self._window_maintenance_ids)
        receipt = {
            "schema_version": self.schema_version,
            "start_ns": start, "end_ns": self._now,
            "control_window_start_ns": self._window_start,
            "control_window_end_ns": self._window_end,
            "activities": copy.deepcopy(activities),
            "resource_work_units_scaled": dict(self._window_resource_work),
            "endpoint_quota_remaining_scaled": dict(self._quota_remaining),
            "buffer_occupancy_bounds": self._buffer_bounds(),
            "job_progress": self._progress_rows(),
            "completion_ids": list(completion_ids),
            "maintenance_completion_ids": list(maintenance_ids),
            "next_completion_ns": self.next_completion_ns(),
            "next_event_ns": self.next_event_ns(),
            "semantics": {
                "time": "EXACT_INTEGER_NS_EVENTS_WITHIN_FROZEN_THERMAL_CONTROL_WINDOW",
                "bandwidth": "EXACT_FRACTION_PROGRESSIVE_MAX_MIN_INTEGER_BYTE_EMISSION",
                "buffer": "FINITE_TWO_BANK_ANALYTICAL_OCCUPANCY_BOUND_CONTINUOUS_TURNOVER",
                "pipeline": "BANDWIDTH_SHARED_EXACT_PROPAGATION_LATENCY_APPROXIMATE",
                "energy": "ACTIVITY_FACTS_SHARE_THE_SAME_PROGRESS_LEDGER",
            },
        }
        if self._now == self._window_end:
            receipt["window_complete"] = True
        self._retire_completed(completion_ids)
        return receipt

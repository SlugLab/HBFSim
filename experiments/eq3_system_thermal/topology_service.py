#!/usr/bin/env python3
"""Aggregated four-topology byte service for coupled system/thermal studies.

The service reuses the BasicFabric route, pair, link and two-bank configuration
shape, but it does not create a request per page.  It allocates integer byte
cohorts over fixed windows and reports activity facts for a separate energy
mapper.  Parameters are engineering scenario assumptions, not calibrated
device timing.
"""

from __future__ import annotations

from collections import deque
import copy
from dataclasses import dataclass
from fractions import Fraction
import heapq
import math
from pathlib import Path
import sys
from typing import Deque, Dict, Mapping, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from eq3_basic_fabric import BasicFabric


NANOSECONDS_PER_SECOND = 1_000_000_000
WINDOW_NS = 20_000_000
TOPOLOGIES = {"mixed_direct", "all_hbf_direct", "relay", "dash"}
OPERATIONS = {"read", "retry", "refresh_read", "program", "erase", "migration"}
READ_OPERATIONS = {"read", "retry"}
MAINTENANCE_OPERATIONS = {"refresh_read", "program", "erase", "migration"}
STATES = {"normal", "light", "severe", "shutdown"}
MAINTENANCE_POLICY = "LIGHT_SEVERE_EXEMPT_SHUTDOWN_BLOCKED"


def _integer(value: object, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < (1 if positive else 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'non-negative'}")
    return value


def _exact(value: object, keys: Sequence[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _stage(latency_ns: int, bandwidth_Bps: int) -> dict:
    return {"latency_ns": latency_ns, "bandwidth_bytes_per_s": bandwidth_Bps}


def media_cost_from_service_rate(read_equivalent_Bps: int, operation_payload_Bps: int) -> dict:
    """Return an exact read-equivalent work ratio for an operation service rate."""

    read_rate = _integer(read_equivalent_Bps, "read_equivalent_Bps", positive=True)
    operation_rate = _integer(operation_payload_Bps, "operation_payload_Bps", positive=True)
    ratio = Fraction(read_rate, operation_rate)
    return {"numerator": ratio.numerator, "denominator": ratio.denominator}


def default_config(topology: str, stack_count: Optional[int] = None) -> dict:
    """Return an explicit, uncalibrated 16-channel engineering configuration."""

    if topology not in TOPOLOGIES:
        raise ValueError("unknown topology")
    expected = 8 if topology == "all_hbf_direct" else 4
    if stack_count is None:
        stack_count = expected
    if stack_count != expected:
        raise ValueError(f"{topology} requires {expected} HBF stacks in the v1 fixture")
    paired = topology in {"relay", "dash"}
    hbm_count = 0 if topology == "all_hbf_direct" else 4
    link_Bps = 2_048_000_000_000
    link_latency_ns = 10
    fabric = {
        "evidence": "SCENARIO_ASSUMPTION",
        "hbf": {
            f"hbf{i}": {
                "pair": f"hbm{i}" if paired else None,
                "bank_count": 2,
                "bank_capacity_bytes": 8 * 1024 * 1024,
                "fill": _stage(link_latency_ns, link_Bps),
                "direct_link": _stage(link_latency_ns, link_Bps),
                "relay_link": _stage(link_latency_ns, link_Bps) if paired else None,
            }
            for i in range(stack_count)
        },
        "hbm": {
            f"hbm{i}": {
                "bank_count": 2,
                "bank_capacity_bytes": 4 * 1024 * 1024,
                "gpu_link": _stage(link_latency_ns, link_Bps),
            }
            for i in range(hbm_count)
        },
    }
    all_stacks = sorted(set(fabric["hbf"]) | set(fabric["hbm"]))
    channels = {
        stack: {str(channel): 96_000_000_000 for channel in range(16)}
        for stack in all_stacks
    }
    dash_routes = (
        {
            stack: {str(channel): ("direct" if channel % 2 == 0 else "relay")
                    for channel in range(16)}
            for stack in fabric["hbf"]
        }
        if topology == "dash" else {}
    )
    unit_cost = {"numerator": 1, "denominator": 1}
    return {
        "schema_version": "eq3-topology-fluid-config-v1",
        "evidence": "SCENARIO_ASSUMPTION",
        "topology": topology,
        "window_ns": WINDOW_NS,
        "fabric": fabric,
        "channels": channels,
        "dash_routes": dash_routes,
        "operation_media_cost": {operation: dict(unit_cost) for operation in sorted(OPERATIONS)},
        "operation_cost_evidence": "ENGINEERING_FIXTURE_DEFAULT_ONE_FORMAL_PROFILE_MUST_OVERRIDE",
        "maintenance_guard_policy": MAINTENANCE_POLICY,
    }


def _lcm(left: int, right: int) -> int:
    return left * right // math.gcd(left, right)


@dataclass
class _Job:
    job_id: str
    stack: str
    channel: str
    operation: str
    route: Optional[str]
    arrival_ns: int
    total_bytes: int
    admission_remaining_bytes: int
    completed_bytes: int
    sequence: int
    foreground: bool
    external: bool
    maintenance_id: Optional[str]
    metadata: dict
    first_service_ns: Optional[int] = None
    last_service_ns: Optional[int] = None
    completion_ns: Optional[int] = None
    pending_batches: int = 0


def _normalize(config: dict) -> dict:
    keys = (
        "schema_version", "evidence", "topology", "window_ns", "fabric", "channels",
        "dash_routes", "operation_media_cost", "operation_cost_evidence",
        "maintenance_guard_policy",
    )
    config = _exact(copy.deepcopy(config), keys, "config")
    if config["schema_version"] != "eq3-topology-fluid-config-v1":
        raise ValueError("unsupported topology fluid config schema")
    if config["evidence"] != "SCENARIO_ASSUMPTION":
        raise ValueError("topology fluid parameters must remain SCENARIO_ASSUMPTION")
    if config["topology"] not in TOPOLOGIES:
        raise ValueError("unsupported topology")
    if _integer(config["window_ns"], "window_ns", positive=True) != WINDOW_NS:
        raise ValueError("v1 requires a 20 ms window")
    if config["maintenance_guard_policy"] != MAINTENANCE_POLICY:
        raise ValueError("maintenance guard policy differs from the preserved contract")
    fabric = BasicFabric(config["fabric"]).immutable_facts()["config"]
    hbf, hbm = fabric["hbf"], fabric["hbm"]
    stacks = set(hbf) | set(hbm)
    topology = config["topology"]
    if topology == "all_hbf_direct" and hbm:
        raise ValueError("all_hbf_direct cannot contain in-package HBM")
    if topology in {"relay", "dash"} and (not hbm or any(row["pair"] is None for row in hbf.values())):
        raise ValueError("relay and DASH require explicit HBF/HBM pairs")
    if topology in {"mixed_direct", "all_hbf_direct"} and any(row["pair"] is not None for row in hbf.values()):
        raise ValueError("direct topologies cannot silently configure relay pairs")
    if not isinstance(config["channels"], dict) or set(config["channels"]) != stacks:
        raise ValueError("channels must exactly cover configured HBF and HBM stacks")
    channels = {}
    for stack in sorted(stacks):
        raw = config["channels"][stack]
        if not isinstance(raw, dict) or not raw:
            raise ValueError(f"channels.{stack} must be nonempty")
        channels[stack] = {}
        for channel, rate in sorted(raw.items()):
            if not isinstance(channel, str) or not channel:
                raise ValueError("channel IDs must be nonempty strings")
            channels[stack][channel] = _integer(rate, f"channels.{stack}.{channel}", positive=True)
    dash_routes = config["dash_routes"]
    if topology == "dash":
        if not isinstance(dash_routes, dict) or set(dash_routes) != set(hbf):
            raise ValueError("DASH routes must exactly cover HBF stacks")
        for stack in hbf:
            if set(dash_routes[stack]) != set(channels[stack]):
                raise ValueError("DASH routes must exactly cover HBF channels")
            if set(dash_routes[stack].values()) - {"direct", "relay"}:
                raise ValueError("DASH channel route must be direct or relay")
    elif dash_routes != {}:
        raise ValueError("dash_routes must be empty outside DASH topology")
    costs = config["operation_media_cost"]
    if not isinstance(costs, dict) or set(costs) != OPERATIONS:
        raise ValueError("operation_media_cost must exactly cover supported operations")
    normalized_costs = {}
    scale = 1
    for operation in sorted(OPERATIONS):
        row = _exact(costs[operation], ("numerator", "denominator"), f"cost.{operation}")
        numerator = _integer(row["numerator"], f"cost.{operation}.numerator", positive=True)
        denominator = _integer(row["denominator"], f"cost.{operation}.denominator", positive=True)
        value = Fraction(numerator, denominator)
        normalized_costs[operation] = value
        scale = _lcm(scale, value.denominator)
    if scale > 1_000_000:
        raise ValueError("operation media-cost denominator scale is too large")
    config["fabric"] = fabric
    config["channels"] = channels
    config["operation_media_cost_fraction"] = normalized_costs
    config["work_scale"] = scale
    return config


class TopologyService:
    """Persistent, window-quantized aggregate topology service."""

    schema_version = "eq3-topology-fluid-receipt-v1"

    def __init__(self, config: dict):
        self._config = _normalize(config)
        self._now = 0
        self._sequence = 0
        self._auto_id = 0
        self._jobs: Dict[str, _Job] = {}
        self._known_ids = set()
        self._external_active = set()
        self._queues: Dict[Tuple[str, str], Deque[str]] = {
            (stack, channel): deque()
            for stack, channels in self._config["channels"].items()
            for channel in channels
        }
        self._inflight: list[Tuple[int, int, str, int]] = []
        self._completion_ids_seen = set()
        self._maintenance_ids_seen = set()
        self._cumulative_offered = {key: 0 for key in self._queues}
        self._cumulative_completed = {key: 0 for key in self._queues}
        self._foreground_backlog = {key: 0 for key in self._queues}

    @property
    def now_ns(self) -> int:
        return self._now

    def immutable_facts(self) -> dict:
        public = copy.deepcopy(self._config)
        public.pop("operation_media_cost_fraction")
        return {
            "schema_version": "eq3-topology-fluid-facts-v1",
            "enabled_by_default": False,
            "config": public,
            "service_semantics": "WINDOW_QUANTIZED_AGGREGATED_BYTE_COHORTS",
            "buffer_semantics": "FINITE_TWO_BANK_CONTINUOUS_TURNOVER_NOT_WINDOW_CAPACITY",
            "pipeline_semantics": "FIRST_CHUNK_STARTUP_PLUS_BOTTLENECK_TURNOVER_APPROXIMATION",
            "latency_semantics": "AGGREGATED_PIPELINE_ESTIMATE_WINDOW_COMPLETION_FLOOR",
            "backend_latency": "UNKNOWN",
            "maintenance_guard_policy": MAINTENANCE_POLICY,
            "light_budget_semantics": "CALLER_ALREADY_CAPPED_APPLIED_EXACTLY_ONCE",
        }

    def _default_route(self, stack: str, channel: str) -> Optional[str]:
        topology = self._config["topology"]
        if stack in self._config["fabric"]["hbm"]:
            return "direct"
        if topology in {"mixed_direct", "all_hbf_direct"}:
            return "direct"
        if topology == "relay":
            return "relay"
        return self._config["dash_routes"][stack][channel]

    def _validate_route(self, stack: str, channel: str, route: Optional[str]) -> Optional[str]:
        expected = self._default_route(stack, channel)
        if stack in self._config["fabric"]["hbm"] and route != "direct":
            raise ValueError("HBM local work must use direct route")
        topology = self._config["topology"]
        if stack in self._config["fabric"]["hbf"]:
            if topology == "dash":
                if route not in {"direct", "relay"}:
                    raise ValueError("DASH HBF work must use direct or relay route")
            elif route != expected:
                raise ValueError("route differs from topology")
        return route

    def _add_job(self, *, job_id: str, stack: str, channel: str, operation: str,
                 route: Optional[str], byte_count: int, arrival_ns: int,
                 foreground: bool, external: bool, maintenance_id: Optional[str],
                 metadata: Optional[dict] = None) -> None:
        if not isinstance(job_id, str) or not job_id or job_id in self._known_ids:
            raise ValueError("job_id must be a new nonempty string")
        if stack not in self._config["channels"] or channel not in self._config["channels"][stack]:
            raise ValueError("job names an unknown stack/channel")
        if operation not in OPERATIONS:
            raise ValueError("unsupported operation")
        route = self._validate_route(stack, channel, route)
        if operation not in READ_OPERATIONS and route is not None:
            # Internal operations have no package delivery route.
            route = None
        if maintenance_id is not None and (not isinstance(maintenance_id, str) or not maintenance_id):
            raise ValueError("maintenance_id must be a nonempty string")
        if maintenance_id is not None and maintenance_id in self._maintenance_ids_seen:
            raise ValueError("maintenance_id was already completed")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        job = _Job(
            job_id=job_id, stack=stack, channel=channel, operation=operation, route=route,
            arrival_ns=arrival_ns, total_bytes=byte_count,
            admission_remaining_bytes=byte_count, completed_bytes=0,
            sequence=self._sequence, foreground=foreground, external=external,
            maintenance_id=maintenance_id,
            metadata=copy.deepcopy(metadata),
        )
        self._sequence += 1
        self._jobs[job_id] = job
        self._known_ids.add(job_id)
        if external:
            self._external_active.add(job_id)
        self._queues[(stack, channel)].append(job_id)
        if foreground:
            self._cumulative_offered[(stack, channel)] += byte_count
            self._foreground_backlog[(stack, channel)] += byte_count

    def _endpoints(self, job: _Job) -> Tuple[str, ...]:
        if job.route != "relay":
            return (job.stack,)
        pair = self._config["fabric"]["hbf"][job.stack]["pair"]
        return tuple(dict.fromkeys((job.stack, pair)))

    def _phase_resources(self, job: _Job) -> list[Tuple[str, dict, int]]:
        scale = self._config["work_scale"]
        media_rate = self._config["channels"][job.stack][job.channel]
        media_cost = self._config["operation_media_cost_fraction"][job.operation]
        media_coeff = media_cost.numerator * (scale // media_cost.denominator)
        phases = [(f"{job.stack}:channel:{job.channel}:media", _stage(0, media_rate), media_coeff)]
        if job.operation not in READ_OPERATIONS:
            return phases
        fabric = self._config["fabric"]
        if job.stack in fabric["hbm"]:
            phases.append((f"{job.stack}:gpu-link", fabric["hbm"][job.stack]["gpu_link"], scale))
            return phases
        row = fabric["hbf"][job.stack]
        phases.append((f"{job.stack}:fill", row["fill"], scale))
        if job.route == "direct":
            phases.append((f"{job.stack}:gpu-link", row["direct_link"], scale))
        else:
            pair = row["pair"]
            phases.append((f"{job.stack}->{pair}:relay-link", row["relay_link"], scale))
            phases.append((f"{pair}:gpu-link", fabric["hbm"][pair]["gpu_link"], scale))
        return phases

    def _buffer_capacity(self, job: _Job) -> int:
        fabric = self._config["fabric"]
        capacities = []
        if job.stack in fabric["hbf"]:
            capacities.append(fabric["hbf"][job.stack]["bank_capacity_bytes"])
            if job.route == "relay":
                pair = fabric["hbf"][job.stack]["pair"]
                capacities.append(fabric["hbm"][pair]["bank_capacity_bytes"])
        elif job.stack in fabric["hbm"]:
            capacities.append(fabric["hbm"][job.stack]["bank_capacity_bytes"])
        return min(capacities) if capacities else job.total_bytes

    def _buffer_specs(self, job: _Job) -> list[Tuple[str, int, int]]:
        fabric = self._config["fabric"]
        if job.stack in fabric["hbf"]:
            row = fabric["hbf"][job.stack]
            result = [(job.stack, row["bank_count"], row["bank_capacity_bytes"])]
            if job.route == "relay":
                partner = row["pair"]
                partner_row = fabric["hbm"][partner]
                result.append((partner, partner_row["bank_count"],
                               partner_row["bank_capacity_bytes"]))
            return result
        row = fabric["hbm"][job.stack]
        return [(job.stack, row["bank_count"], row["bank_capacity_bytes"])]

    def _pipeline_completion(self, job: _Job, byte_count: int, service_start_ns: int) -> int:
        phases = self._phase_resources(job)
        scale = self._config["work_scale"]
        chunk = min(byte_count, self._buffer_capacity(job))

        def duration(stage: dict, coefficient: int, amount: int) -> int:
            work = amount * coefficient
            transfer = (work * NANOSECONDS_PER_SECOND + stage["bandwidth_bytes_per_s"] * scale - 1) // (
                stage["bandwidth_bytes_per_s"] * scale
            )
            return stage["latency_ns"] + transfer

        first_chunk = [duration(stage, coefficient, chunk) for _, stage, coefficient in phases]
        if byte_count <= chunk:
            return service_start_ns + sum(first_chunk)
        turns = (byte_count + chunk - 1) // chunk
        # Two-bank ping-pong permits adjacent phases to overlap after the first
        # chunk.  The slowest phase sets continuous turnover; latency is not
        # unconditionally added for every byte or every phase turn.
        return service_start_ns + sum(first_chunk) + (turns - 1) * max(first_chunk)

    def _activity_rows(self, job: _Job, byte_count: int, start_ns: int, end_ns: int) -> list[dict]:
        base = {
            "operation": job.operation, "stack": job.stack, "channel": job.channel,
            "bytes": byte_count, "route": job.route, "start_ns": start_ns, "end_ns": end_ns,
        }
        rows = []

        def add(phase: str, resource: str, *, stack: Optional[str] = None,
                partner: Optional[str] = None, operation: Optional[str] = None) -> None:
            row = dict(base)
            row.update(phase=phase, resource=resource)
            if stack is not None:
                row["stack"] = stack
            if partner is not None:
                row["partner"] = partner
            if operation is not None:
                row["operation"] = operation
            rows.append(row)

        media = f"{job.stack}:channel:{job.channel}:media"
        if job.operation == "migration":
            add("media_read", media, operation="migration_read")
            add("media_program", media, operation="migration_program")
        elif job.operation in {"read", "retry", "refresh_read"}:
            add("media_read", media)
        elif job.operation == "program":
            add("media_program", media)
        else:
            add("media_erase", media)
        add("source_base", f"{job.stack}:base")
        if job.operation not in READ_OPERATIONS:
            return rows
        fabric = self._config["fabric"]
        if job.stack in fabric["hbm"]:
            add("gpu_drain", f"{job.stack}:gpu-link")
        elif job.route == "direct":
            add("direct_gpu_link", f"{job.stack}:gpu-link")
        else:
            pair = fabric["hbf"][job.stack]["pair"]
            add("relay_send", f"{job.stack}->{pair}:relay-link", partner=pair)
            add("relay_receive", f"{pair}:base", stack=pair, partner=pair)
            add("partner_gpu_drain", f"{pair}:gpu-link", stack=pair, partner=pair)
        return rows

    def _complete_batches(self, horizon_ns: int, completed_by_channel: dict,
                          completed_by_job: dict, delay_samples: dict,
                          completion_ids: list[str], maintenance_ids: list[str]) -> None:
        while self._inflight and self._inflight[0][0] <= horizon_ns:
            completion_ns, _, job_id, byte_count = heapq.heappop(self._inflight)
            job = self._jobs[job_id]
            job.pending_batches -= 1
            job.completed_bytes += byte_count
            completed_by_job[job_id] = completed_by_job.get(job_id, 0) + byte_count
            if job.foreground:
                key = (job.stack, job.channel)
                completed_by_channel[key] += byte_count
                self._cumulative_completed[key] += byte_count
                self._foreground_backlog[key] -= byte_count
                delay_samples[job.stack].append((completion_ns - job.arrival_ns, byte_count))
            if job.admission_remaining_bytes == 0 and job.pending_batches == 0:
                job.completion_ns = completion_ns
                if job.external and job.job_id not in self._completion_ids_seen:
                    completion_ids.append(job.job_id)
                    self._completion_ids_seen.add(job.job_id)
                if job.maintenance_id is not None and job.maintenance_id not in self._maintenance_ids_seen:
                    maintenance_ids.append(job.maintenance_id)
                    self._maintenance_ids_seen.add(job.maintenance_id)

    @staticmethod
    def _histogram(samples: list[Tuple[int, int]]) -> list[dict]:
        totals = {}
        for delay, byte_count in samples:
            totals[delay] = totals.get(delay, 0) + byte_count
        return [{"delay_ns": delay, "bytes": totals[delay]} for delay in sorted(totals)]

    def advance(self, start_ns: int, end_ns: int,
                offered_by_stack_channel: Mapping[str, Mapping[str, int]],
                budgets: Mapping[str, int], endpoint_states: Mapping[str, str],
                extra_jobs: Sequence[dict] = ()) -> dict:
        start_ns = _integer(start_ns, "start_ns")
        end_ns = _integer(end_ns, "end_ns")
        if start_ns != self._now or end_ns - start_ns != self._config["window_ns"]:
            raise ValueError("advance requires the next contiguous fixed window")
        stacks = set(self._config["channels"])
        if set(budgets) != stacks or set(endpoint_states) != stacks:
            raise ValueError("budgets and endpoint_states must exactly cover configured stacks")
        checked_budgets = {stack: _integer(budgets[stack], f"budgets.{stack}") for stack in stacks}
        if any(endpoint_states[stack] not in STATES for stack in stacks):
            raise ValueError("unknown endpoint state")
        unknown_stacks = set(offered_by_stack_channel) - stacks
        if unknown_stacks:
            raise ValueError(f"unknown offered stacks: {sorted(unknown_stacks)}")

        offered_this_window = {key: 0 for key in self._queues}
        for stack, raw_channels in offered_by_stack_channel.items():
            unknown_channels = set(raw_channels) - set(self._config["channels"][stack])
            if unknown_channels:
                raise ValueError(f"unknown channels for {stack}: {sorted(unknown_channels)}")
            for channel, raw_bytes in raw_channels.items():
                byte_count = _integer(raw_bytes, f"offered.{stack}.{channel}")
                if byte_count:
                    job_id = f"foreground:{self._auto_id}"
                    self._auto_id += 1
                    self._add_job(
                        job_id=job_id, stack=stack, channel=channel, operation="read",
                        route=self._default_route(stack, channel), byte_count=byte_count,
                        arrival_ns=start_ns, foreground=True, external=False, maintenance_id=None,
                        metadata={},
                    )
                    offered_this_window[(stack, channel)] += byte_count

        for raw in extra_jobs:
            required = {"job_id", "stack", "channel", "operation", "bytes", "arrival_ns"}
            optional = {"route", "maintenance_id", "metadata"}
            if not isinstance(raw, dict) or not required.issubset(raw) or set(raw) - required - optional:
                raise ValueError("extra job has missing or unknown fields")
            arrival = _integer(raw["arrival_ns"], "extra_job.arrival_ns")
            if arrival < start_ns or arrival >= end_ns:
                raise ValueError("extra job arrival must lie in the current half-open window")
            operation = raw["operation"]
            route = raw.get("route", self._default_route(raw["stack"], raw["channel"]))
            maintenance_id = raw.get("maintenance_id")
            foreground = operation in READ_OPERATIONS and maintenance_id is None
            byte_count = _integer(raw["bytes"], "extra_job.bytes", positive=True)
            self._add_job(
                job_id=raw["job_id"], stack=raw["stack"], channel=raw["channel"],
                operation=operation, route=route, byte_count=byte_count,
                arrival_ns=arrival, foreground=foreground, external=True,
                maintenance_id=maintenance_id,
                metadata=raw.get("metadata", {}),
            )
            if foreground:
                offered_this_window[(raw["stack"], raw["channel"])] += byte_count

        scale = self._config["work_scale"]
        resource_specs = {}
        for stack, channels in self._config["channels"].items():
            for channel, rate in channels.items():
                resource_specs[f"{stack}:channel:{channel}:media"] = _stage(0, rate)
        fabric = self._config["fabric"]
        for stack, row in fabric["hbf"].items():
            resource_specs[f"{stack}:fill"] = row["fill"]
            resource_specs[f"{stack}:gpu-link"] = row["direct_link"]
            if row["pair"] is not None:
                resource_specs[f"{stack}->{row['pair']}:relay-link"] = row["relay_link"]
        for stack, row in fabric["hbm"].items():
            resource_specs[f"{stack}:gpu-link"] = row["gpu_link"]
        residual = {
            resource: max(0, (end_ns - start_ns - spec["latency_ns"]))
            * spec["bandwidth_bytes_per_s"] * scale // NANOSECONDS_PER_SECOND
            for resource, spec in resource_specs.items()
        }
        for stack in stacks:
            residual[f"endpoint:{stack}"] = checked_budgets[stack] * scale
        initial_capacity = dict(residual)
        allocations: Dict[str, int] = {}
        blocked = []
        blocked_seen = set()

        def scheduling_class(job: _Job) -> str:
            maintenance = job.operation in MAINTENANCE_OPERATIONS or job.maintenance_id is not None
            if maintenance:
                return f"maintenance:{job.operation}"
            return f"foreground:{job.route}"

        def candidate() -> list[Tuple[_Job, Dict[str, int]]]:
            result = []
            for key in sorted(self._queues):
                queue = self._queues[key]
                if queue:
                    self._queues[key] = queue = deque(
                        job_id for job_id in queue
                        if self._jobs[job_id].admission_remaining_bytes > 0
                    )
                if not queue:
                    continue
                first_by_class = {}
                for job_id in queue:
                    job = self._jobs[job_id]
                    first_by_class.setdefault(scheduling_class(job), job)
                for job in sorted(first_by_class.values(), key=lambda row: row.sequence):
                    if job.arrival_ns >= end_ns:
                        continue
                    endpoints = self._endpoints(job)
                    maintenance = job.operation in MAINTENANCE_OPERATIONS or job.maintenance_id is not None
                    states = {endpoint: endpoint_states[endpoint] for endpoint in endpoints}
                    reasons = []
                    if maintenance:
                        reasons = [f"{endpoint}:shutdown" for endpoint, state in states.items()
                                   if state == "shutdown"]
                    else:
                        reasons = [f"{endpoint}:{state}" for endpoint, state in states.items()
                                   if state in {"severe", "shutdown"}]
                    if reasons:
                        if job.job_id not in blocked_seen:
                            blocked.append({"job_id": job.job_id, "bytes": job.admission_remaining_bytes,
                                            "reasons": reasons, "maintenance": maintenance})
                            blocked_seen.add(job.job_id)
                        continue
                    coefficients = {resource: coefficient for resource, _, coefficient
                                    in self._phase_resources(job)}
                    if not maintenance:
                        for endpoint in endpoints:
                            coefficients[f"endpoint:{endpoint}"] = scale
                    result.append((job, coefficients))
            return result

        while True:
            active = []
            for job, coefficients in candidate():
                if all(residual.get(resource, 0) >= coefficient
                       for resource, coefficient in coefficients.items()):
                    active.append((job, coefficients))
            if not active:
                break
            bounds = [job.admission_remaining_bytes for job, _ in active]
            for resource in residual:
                users = sum(coefficients.get(resource, 0) for _, coefficients in active)
                if users:
                    bounds.append(residual[resource] // users)
            share = min(bounds)
            if share <= 0:
                progress = False
                for job, coefficients in active:
                    if all(residual[resource] >= coefficient
                           for resource, coefficient in coefficients.items()):
                        for resource, coefficient in coefficients.items():
                            residual[resource] -= coefficient
                        job.admission_remaining_bytes -= 1
                        allocations[job.job_id] = allocations.get(job.job_id, 0) + 1
                        progress = True
                if not progress:
                    break
                continue
            for job, coefficients in active:
                amount = min(share, job.admission_remaining_bytes)
                for resource, coefficient in coefficients.items():
                    residual[resource] -= amount * coefficient
                job.admission_remaining_bytes -= amount
                allocations[job.job_id] = allocations.get(job.job_id, 0) + amount

        activities = []
        buffer_rows = []
        for job_id, amount in sorted(allocations.items(), key=lambda item: self._jobs[item[0]].sequence):
            job = self._jobs[job_id]
            service_start = max(start_ns, job.arrival_ns)
            job.first_service_ns = service_start if job.first_service_ns is None else job.first_service_ns
            job.last_service_ns = end_ns
            pipeline_estimated_completion_ns = self._pipeline_completion(
                job, amount, service_start
            )
            # Rate-only v1 settles admitted aggregate bytes at the common
            # thermal-window boundary.  Shared resource capacities above are
            # authoritative; the pipeline estimate is diagnostic and must not
            # be used as an exact online dependency completion.
            completion_ns = end_ns
            job.pending_batches += 1
            heapq.heappush(self._inflight, (completion_ns, self._sequence, job_id, amount))
            self._sequence += 1
            activities.extend(self._activity_rows(job, amount, start_ns, end_ns))
            if job.operation in READ_OPERATIONS:
                for buffer_stack, bank_count, bank_capacity in self._buffer_specs(job):
                    buffer_rows.append({
                        "job_id": job.job_id, "source_stack": job.stack,
                        "buffer_stack": buffer_stack, "route": job.route,
                        "bank_count": bank_count, "bank_capacity_bytes": bank_capacity,
                        "maximum_occupancy_bytes": min(amount, bank_count * bank_capacity),
                        "turnovers": (amount + bank_capacity - 1) // bank_capacity,
                        "bytes": amount,
                        "pipeline_estimated_completion_ns": pipeline_estimated_completion_ns,
                        "settled_completion_ns": completion_ns,
                    })

        completed_by_channel = {key: 0 for key in self._queues}
        completed_by_job = {}
        delay_samples = {stack: [] for stack in stacks}
        completion_ids: list[str] = []
        maintenance_ids: list[str] = []
        self._complete_batches(
            end_ns, completed_by_channel, completed_by_job, delay_samples,
            completion_ids, maintenance_ids,
        )

        served = {
            stack: {channel: completed_by_channel[(stack, channel)] for channel in channels}
            for stack, channels in self._config["channels"].items()
        }
        stack_rows = {}
        for stack, channels in self._config["channels"].items():
            backlog = sum(self._foreground_backlog[(stack, channel)] for channel in channels)
            waiting = []
            for channel in channels:
                queue = self._queues[(stack, channel)]
                if queue:
                    waiting.append(end_ns - self._jobs[queue[0]].arrival_ns)
            stack_activities = [row for row in activities if row["stack"] == stack]
            stack_rows[stack] = {
                "offered_effective_bytes": sum(offered_this_window[(stack, channel)] for channel in channels),
                "delivered_effective_bytes": sum(served[stack].values()),
                "backlog_effective_bytes": backlog,
                "oldest_wait_ns": max(waiting) if waiting else None,
                "delivered_delay_histogram_bytes": self._histogram(delay_samples[stack]),
                "media_activity_bytes": sum(row["bytes"] for row in stack_activities
                                             if row["phase"].startswith("media_")),
                "link_bytes_by_phase": {
                    phase: sum(row["bytes"] for row in stack_activities if row["phase"] == phase)
                    for phase in sorted({row["phase"] for row in stack_activities
                                         if row["phase"] in {
                                             "direct_gpu_link", "relay_send", "relay_receive",
                                             "gpu_drain", "partner_gpu_drain",
                                         }})
                },
            }
            cumulative_offered = sum(self._cumulative_offered[(stack, channel)] for channel in channels)
            cumulative_completed = sum(self._cumulative_completed[(stack, channel)] for channel in channels)
            if cumulative_offered != cumulative_completed + backlog:
                raise AssertionError(f"foreground byte conservation failed for {stack}")
            stack_rows[stack]["cumulative_offered_effective_bytes"] = cumulative_offered
            stack_rows[stack]["cumulative_delivered_effective_bytes"] = cumulative_completed
            stack_rows[stack]["cumulative_conserved"] = True

        resource_rows = {}
        for resource, capacity in initial_capacity.items():
            used_work = capacity - residual[resource]
            resource_rows[resource] = {
                "capacity_work_units_scaled": capacity,
                "used_work_units_scaled": used_work,
                "remaining_work_units_scaled": residual[resource],
                "work_scale": scale,
                "capacity_semantics": (
                    "CALLER_FUTURE_ENDPOINT_BUDGET_APPLIED_ONCE"
                    if resource.startswith("endpoint:")
                    else "WINDOW_BANDWIDTH_MINUS_ONE_STARTUP_LATENCY"
                ),
            }

        progress = []
        progress_ids = set(self._external_active) | set(completion_ids)
        for job in sorted((self._jobs[job_id] for job_id in progress_ids), key=lambda row: row.sequence):
            progress.append({
                "job_id": job.job_id, "maintenance_id": job.maintenance_id,
                "metadata": copy.deepcopy(job.metadata),
                "operation": job.operation, "stack": job.stack, "channel": job.channel,
                "route": job.route, "arrival_ns": job.arrival_ns,
                "total_bytes": job.total_bytes,
                "admitted_this_window_bytes": allocations.get(job.job_id, 0),
                "served_this_window_bytes": completed_by_job.get(job.job_id, 0),
                "cumulative_served_bytes": job.completed_bytes,
                "remaining_bytes": job.total_bytes - job.completed_bytes,
                "admission_remaining_bytes": job.admission_remaining_bytes,
                "first_service_ns": job.first_service_ns,
                "last_service_ns": job.last_service_ns,
                "completion_ns": job.completion_ns,
            })

        route_rows = {}
        for job_id, amount in allocations.items():
            job = self._jobs[job_id]
            key = f"{job.stack}:{job.route or 'internal'}:{job.operation}"
            route_rows[key] = route_rows.get(key, 0) + amount

        self._now = end_ns
        receipt = {
            "schema_version": self.schema_version,
            "start_ns": start_ns, "end_ns": end_ns,
            "topology": self._config["topology"],
            "served_by_stack_channel": served,
            "stacks": stack_rows,
            "activities": activities,
            "route_phase_bytes": route_rows,
            "resources": resource_rows,
            "buffers": buffer_rows,
            "blocked": blocked,
            "job_progress": progress,
            "completion_ids": completion_ids,
            "maintenance_completion_ids": maintenance_ids,
            "semantics": {
                "service": "AGGREGATED_FLUID_NOT_MQSIM_TRANSACTION",
                "buffer": "FINITE_TWO_BANK_CONTINUOUS_TURNOVER_NOT_WINDOW_CAPACITY",
                "buffer_arbitration": "AGGREGATE_TURNOVER_SHARED_LINK_IS_AUTHORITATIVE_BANK_EVENT_ORDER_APPROXIMATE",
                "pipeline": "FIRST_CHUNK_STARTUP_PLUS_BOTTLENECK_TURNOVER_APPROXIMATION",
                "completion": "RATE_ONLY_COMMON_THERMAL_WINDOW_END_AFTER_SHARED_RESOURCE_ALLOCATION",
                "latency_error_bound": "ONE_WINDOW_PLUS_AGGREGATE_CHUNK_PIPELINE_APPROXIMATION",
                "causal_subwindow_api": "NOT_AVAILABLE_IN_V1_ADVANCE_CALLER_MUST_NOT_CHAIN_AT_WINDOW_END",
                "backend_latency": "UNKNOWN",
                "light_quota": "CALLER_BUDGET_APPLIED_EXACTLY_ONCE_NO_INTERNAL_HALF",
                "inflight_state_change": "NO_CROSS_WINDOW_INFLIGHT_RATE_SETTLEMENT",
                "maintenance_guard_policy": MAINTENANCE_POLICY,
                "energy": "ACTIVITY_FACTS_ONLY_NO_UNSOURCED_JOULES",
            },
        }
        completed_job_ids = [
            job_id for job_id, job in self._jobs.items() if job.completion_ns is not None
        ]
        for job_id in completed_job_ids:
            self._external_active.discard(job_id)
            del self._jobs[job_id]
        return receipt

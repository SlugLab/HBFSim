"""Window-quantized byte service for rate-driven thermal experiments.

This module is deliberately independent of MQSim and the thermal solver.  It
models only finite byte service and queued byte cohorts.  A caller supplies a
future service budget for every stack on every window; the service never
changes that budget based on temperatures or queue state.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Mapping, Optional, Sequence, Tuple


NANOSECONDS_PER_SECOND = 1_000_000_000
DEFAULT_WINDOW_NS = 20_000_000
LATENCY_SEMANTICS = "FLUID_WINDOW_QUANTIZED_DELAY"
BACKEND_LATENCY = "UNKNOWN"


@dataclass
class _Cohort:
    arrival_ns: int
    remaining_bytes: int


def _integer_bytes(value: object, field: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer byte count")
    if value < 0 or (positive and value == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{field} must be {qualifier}")
    return value


def _weighted_nearest_rank_p95(samples: Sequence[Tuple[int, int]]) -> Optional[int]:
    """Return the nearest-rank p95 over byte-weighted integer delays."""

    total = sum(byte_count for _, byte_count in samples)
    if total == 0:
        return None
    rank = (95 * total + 99) // 100
    cumulative = 0
    for delay_ns, byte_count in sorted(samples):
        cumulative += byte_count
        if cumulative >= rank:
            return delay_ns
    raise AssertionError("weighted percentile did not reach its rank")


def _delay_histogram(samples: Sequence[Tuple[int, int]]) -> list[dict]:
    """Return a JSON-friendly byte histogram sorted by quantized delay."""

    totals: Dict[int, int] = {}
    for delay_ns, byte_count in samples:
        totals[delay_ns] = totals.get(delay_ns, 0) + byte_count
    return [
        {"delay_ns": delay_ns, "bytes": totals[delay_ns]}
        for delay_ns in sorted(totals)
    ]


def _max_min_allocation(demand: Mapping[str, int], budget: int) -> Dict[str, int]:
    """Deterministic integer max-min allocation, bounded by channel demand."""

    allocation = {channel: 0 for channel in demand}
    remaining = min(budget, sum(demand.values()))
    active = sorted(channel for channel, value in demand.items() if value > 0)
    while remaining and active:
        share, remainder = divmod(remaining, len(active))
        if share == 0:
            for channel in active[:remainder]:
                allocation[channel] += 1
            remaining = 0
            break

        granted = 0
        for channel in active:
            room = demand[channel] - allocation[channel]
            amount = min(share, room)
            allocation[channel] += amount
            granted += amount
        remaining -= granted
        active = [
            channel
            for channel in active
            if allocation[channel] < demand[channel]
        ]
    return allocation


class FluidService:
    """Persistent per-channel FIFO queues served in fixed time windows.

    ``budget_by_stack`` is a future-only byte budget for the supplied window.
    Channel capacities and the stack budget jointly limit service.  The stack
    budget is distributed with integer max-min fairness among channels that
    have serviceable backlog.  Endpoint-group arbitration is intentionally not
    represented in schema v1.
    """

    schema_version = "eq3-fluid-service-v1"

    def __init__(
        self,
        stack_channels: Mapping[str, Sequence[str]],
        channel_capacity_Bps: Mapping[str, Mapping[str, int]],
        window_ns: int = DEFAULT_WINDOW_NS,
    ) -> None:
        self.window_ns = _integer_bytes(window_ns, "window_ns", positive=True)
        if not stack_channels:
            raise ValueError("stack_channels must not be empty")

        self._channels: Dict[str, Tuple[str, ...]] = {}
        self._capacity_bps: Dict[str, Dict[str, int]] = {}
        self._capacity_bytes: Dict[str, Dict[str, int]] = {}
        self._queues: Dict[Tuple[str, str], Deque[_Cohort]] = {}
        self._cumulative_offered: Dict[Tuple[str, str], int] = {}
        self._cumulative_delivered: Dict[Tuple[str, str], int] = {}

        declared_stacks = set(stack_channels)
        if set(channel_capacity_Bps) != declared_stacks:
            raise ValueError("channel_capacity_Bps must exactly match declared stacks")
        for stack in sorted(declared_stacks):
            if not isinstance(stack, str) or not stack:
                raise ValueError("stack identifiers must be non-empty strings")
            raw_channels = tuple(stack_channels[stack])
            if not raw_channels or len(set(raw_channels)) != len(raw_channels):
                raise ValueError(f"{stack} must declare unique, non-empty channels")
            if any(not isinstance(channel, str) or not channel for channel in raw_channels):
                raise ValueError(f"{stack} channel identifiers must be non-empty strings")
            channels = tuple(sorted(raw_channels))
            if set(channel_capacity_Bps[stack]) != set(channels):
                raise ValueError(f"channel_capacity_Bps[{stack}] must match its channels")

            self._channels[stack] = channels
            self._capacity_bps[stack] = {}
            self._capacity_bytes[stack] = {}
            for channel in channels:
                rate = _integer_bytes(
                    channel_capacity_Bps[stack][channel],
                    f"channel_capacity_Bps[{stack}][{channel}]",
                    positive=True,
                )
                self._capacity_bps[stack][channel] = rate
                self._capacity_bytes[stack][channel] = (
                    rate * self.window_ns // NANOSECONDS_PER_SECOND
                )
                key = (stack, channel)
                self._queues[key] = deque()
                self._cumulative_offered[key] = 0
                self._cumulative_delivered[key] = 0

        self._now_ns = 0

    @property
    def now_ns(self) -> int:
        return self._now_ns

    def _validate_offered(
        self, offered_by_channel: Mapping[str, Mapping[str, int]]
    ) -> Dict[str, Dict[str, int]]:
        unknown_stacks = set(offered_by_channel) - set(self._channels)
        if unknown_stacks:
            raise ValueError(f"unknown offered stacks: {sorted(unknown_stacks)}")
        result: Dict[str, Dict[str, int]] = {}
        for stack, channels in self._channels.items():
            supplied = offered_by_channel.get(stack, {})
            unknown_channels = set(supplied) - set(channels)
            if unknown_channels:
                raise ValueError(
                    f"unknown offered channels for {stack}: {sorted(unknown_channels)}"
                )
            result[stack] = {
                channel: _integer_bytes(
                    supplied.get(channel, 0),
                    f"offered_by_channel[{stack}][{channel}]",
                )
                for channel in channels
            }
        return result

    def _validate_budget(self, budget_by_stack: Mapping[str, int]) -> Dict[str, int]:
        if set(budget_by_stack) != set(self._channels):
            raise ValueError("budget_by_stack must exactly match declared stacks")
        return {
            stack: _integer_bytes(budget_by_stack[stack], f"budget_by_stack[{stack}]")
            for stack in self._channels
        }

    @staticmethod
    def _queue_bytes(queue: Deque[_Cohort]) -> int:
        return sum(cohort.remaining_bytes for cohort in queue)

    def advance(
        self,
        start_ns: int,
        end_ns: int,
        offered_by_channel: Mapping[str, Mapping[str, int]],
        budget_by_stack: Mapping[str, int],
    ) -> dict:
        """Enqueue arrivals and serve exactly one fixed window.

        Omitted declared channels in ``offered_by_channel`` mean zero arrivals,
        which permits cooling/recovery windows to continue draining old bytes.
        All delivery timestamps are quantized to ``end_ns``.
        """

        start_ns = _integer_bytes(start_ns, "start_ns")
        end_ns = _integer_bytes(end_ns, "end_ns")
        if start_ns != self._now_ns:
            raise ValueError(f"start_ns must equal service now_ns {self._now_ns}")
        if end_ns - start_ns != self.window_ns:
            raise ValueError(f"window must be exactly {self.window_ns} ns")

        offered = self._validate_offered(offered_by_channel)
        budgets = self._validate_budget(budget_by_stack)
        previous_backlog: Dict[str, Dict[str, int]] = {
            stack: {
                channel: self._queue_bytes(self._queues[(stack, channel)])
                for channel in channels
            }
            for stack, channels in self._channels.items()
        }

        for stack, channels in self._channels.items():
            for channel in channels:
                amount = offered[stack][channel]
                if amount:
                    self._queues[(stack, channel)].append(_Cohort(start_ns, amount))
                    self._cumulative_offered[(stack, channel)] += amount

        served_by_channel: Dict[str, Dict[str, int]] = {}
        channel_rows: Dict[str, Dict[str, dict]] = {}
        stack_rows: Dict[str, dict] = {}
        window_samples_by_stack: Dict[str, list[Tuple[int, int]]] = {}

        for stack, channels in self._channels.items():
            demand = {
                channel: min(
                    self._queue_bytes(self._queues[(stack, channel)]),
                    self._capacity_bytes[stack][channel],
                )
                for channel in channels
            }
            allocation = _max_min_allocation(demand, budgets[stack])
            served_by_channel[stack] = {}
            channel_rows[stack] = {}
            window_samples_by_stack[stack] = []

            for channel in channels:
                key = (stack, channel)
                queue = self._queues[key]
                remaining_service = allocation[channel]
                samples: list[Tuple[int, int]] = []
                while remaining_service:
                    cohort = queue[0]
                    amount = min(remaining_service, cohort.remaining_bytes)
                    delay_ns = end_ns - cohort.arrival_ns
                    samples.append((delay_ns, amount))
                    window_samples_by_stack[stack].append((delay_ns, amount))
                    cohort.remaining_bytes -= amount
                    remaining_service -= amount
                    if cohort.remaining_bytes == 0:
                        queue.popleft()

                delivered = allocation[channel]
                self._cumulative_delivered[key] += delivered
                backlog = self._queue_bytes(queue)
                oldest_wait = end_ns - queue[0].arrival_ns if queue else None
                served_by_channel[stack][channel] = delivered
                channel_rows[stack][channel] = {
                    "offered_bytes": offered[stack][channel],
                    "delivered_bytes": delivered,
                    "backlog_bytes": backlog,
                    "oldest_wait_ns": oldest_wait,
                    "latency_p95_ns": _weighted_nearest_rank_p95(samples),
                    "delivered_delay_histogram_bytes": _delay_histogram(samples),
                    "capacity_bytes": self._capacity_bytes[stack][channel],
                    "capacity_Bps": self._capacity_bps[stack][channel],
                }

            stack_offered = sum(offered[stack].values())
            stack_delivered = sum(served_by_channel[stack].values())
            stack_backlog = sum(
                channel_rows[stack][channel]["backlog_bytes"] for channel in channels
            )
            oldest_values = [
                channel_rows[stack][channel]["oldest_wait_ns"]
                for channel in channels
                if channel_rows[stack][channel]["oldest_wait_ns"] is not None
            ]
            stack_rows[stack] = {
                "offered_bytes": stack_offered,
                "delivered_bytes": stack_delivered,
                "backlog_bytes": stack_backlog,
                "oldest_wait_ns": max(oldest_values) if oldest_values else None,
                "latency_p95_ns": _weighted_nearest_rank_p95(
                    window_samples_by_stack[stack]
                ),
                "delivered_delay_histogram_bytes": _delay_histogram(
                    window_samples_by_stack[stack]
                ),
                "budget_bytes": budgets[stack],
                "latency_semantics": LATENCY_SEMANTICS,
                "backend_latency_ns": BACKEND_LATENCY,
            }

        per_stack_conservation = {}
        for stack, channels in self._channels.items():
            per_channel_conservation = {}
            for channel in channels:
                key = (stack, channel)
                previous_channel = previous_backlog[stack][channel]
                arrivals_channel = offered[stack][channel]
                delivered_channel = served_by_channel[stack][channel]
                backlog_channel = channel_rows[stack][channel]["backlog_bytes"]
                if previous_channel + arrivals_channel != delivered_channel + backlog_channel:
                    raise AssertionError(
                        f"window byte conservation failed for {stack}/{channel}"
                    )
                if (
                    self._cumulative_offered[key]
                    != self._cumulative_delivered[key] + backlog_channel
                ):
                    raise AssertionError(
                        f"cumulative byte conservation failed for {stack}/{channel}"
                    )
                per_channel_conservation[channel] = {
                    "previous_backlog_bytes": previous_channel,
                    "offered_bytes": arrivals_channel,
                    "delivered_bytes": delivered_channel,
                    "backlog_bytes": backlog_channel,
                    "window_conserved": True,
                    "cumulative_offered_bytes": self._cumulative_offered[key],
                    "cumulative_delivered_bytes": self._cumulative_delivered[key],
                    "cumulative_conserved": True,
                }
            previous = sum(previous_backlog[stack].values())
            arrivals = stack_rows[stack]["offered_bytes"]
            delivered = stack_rows[stack]["delivered_bytes"]
            backlog = stack_rows[stack]["backlog_bytes"]
            cumulative_offered = sum(
                self._cumulative_offered[(stack, channel)] for channel in channels
            )
            cumulative_delivered = sum(
                self._cumulative_delivered[(stack, channel)] for channel in channels
            )
            if previous + arrivals != delivered + backlog:
                raise AssertionError(f"window byte conservation failed for {stack}")
            if cumulative_offered != cumulative_delivered + backlog:
                raise AssertionError(f"cumulative byte conservation failed for {stack}")
            per_stack_conservation[stack] = {
                "previous_backlog_bytes": previous,
                "offered_bytes": arrivals,
                "delivered_bytes": delivered,
                "backlog_bytes": backlog,
                "window_conserved": True,
                "cumulative_offered_bytes": cumulative_offered,
                "cumulative_delivered_bytes": cumulative_delivered,
                "cumulative_conserved": True,
                "per_channel": per_channel_conservation,
            }

        self._now_ns = end_ns
        return {
            "schema_version": self.schema_version,
            "start_ns": start_ns,
            "end_ns": end_ns,
            "window_ns": self.window_ns,
            "served_by_channel": served_by_channel,
            "stacks": stack_rows,
            "channels": channel_rows,
            "conservation": {
                "per_stack": per_stack_conservation,
                "window_conserved": True,
                "cumulative_conserved": True,
            },
            "semantics": {
                "latency": LATENCY_SEMANTICS,
                "delivered_delay_histogram": "BYTE_WEIGHTED_WINDOW_END_DELAY_NS",
                "undelivered_delay": "CENSORED_AS_BACKLOG_NO_DELAY_ASSIGNED",
                "backend_latency_ns": BACKEND_LATENCY,
                "capacity_conversion": "FLOOR_INTEGER_BYTES_PER_WINDOW",
                "stack_budget_allocation": "INTEGER_MAX_MIN_FAIR",
                "service_scope": "STACK_AND_CHANNEL_ONLY",
                "endpoint_group_arbitration": "UNSUPPORTED_IN_V1",
                "transaction_model": "NONE_FLUID_BYTES_ONLY",
            },
        }

    def snapshot(self) -> dict:
        """Return current queue facts without changing service state."""

        stacks = {}
        channels = {}
        for stack, stack_channels in self._channels.items():
            channels[stack] = {}
            stack_backlog = 0
            oldest_values = []
            for channel in stack_channels:
                queue = self._queues[(stack, channel)]
                backlog = self._queue_bytes(queue)
                oldest_wait = self._now_ns - queue[0].arrival_ns if queue else None
                channels[stack][channel] = {
                    "backlog_bytes": backlog,
                    "oldest_wait_ns": oldest_wait,
                    "cohort_count": len(queue),
                }
                stack_backlog += backlog
                if oldest_wait is not None:
                    oldest_values.append(oldest_wait)
            stacks[stack] = {
                "backlog_bytes": stack_backlog,
                "oldest_wait_ns": max(oldest_values) if oldest_values else None,
            }
        return {
            "schema_version": self.schema_version,
            "now_ns": self._now_ns,
            "stacks": stacks,
            "channels": channels,
        }

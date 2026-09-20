"""Conditional retention-age accounting; never an RBER or lifetime model."""

from __future__ import annotations

import math
import hashlib
from copy import deepcopy
from typing import Any


ALLOWED_EA_EV = (1.01, 1.04, 1.08)
DEFAULT_TREF_K = 358.15
KB_EV_PER_K = 8.62e-5
DAY_NS = 86_400_000_000_000


def arrhenius_acceleration(temperature_k: float, ea_ev: float, tref_k: float) -> float:
    """Return equivalent-reference-age rate at ``temperature_k``."""
    for value, name in ((temperature_k, "temperature_k"), (ea_ev, "ea_ev"),
                        (tref_k, "tref_k")):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    return math.exp(ea_ev / KB_EV_PER_K * (1.0 / tref_k - 1.0 / temperature_k))


class ReliabilityLedger:
    """Keep per-block conditional age and observed program/erase facts.

    The 24-hour value is a wall-clock scenario cadence.  It is deliberately
    independent of equivalent age and is not a failure threshold.
    """

    def __init__(self, config: dict[str, Any]):
        ea_ev = float(config.get("ea_ev", 1.04))
        if ea_ev not in ALLOWED_EA_EV:
            raise ValueError(f"ea_ev must be one of {ALLOWED_EA_EV}")
        self.ea_ev = ea_ev
        self.tref_k = float(config.get("tref_k", DEFAULT_TREF_K))
        if self.tref_k != DEFAULT_TREF_K:
            raise ValueError("this scenario contract fixes tref_k at 358.15 K")
        self.period_ns = int(config.get("maintenance_period_ns", DAY_NS))
        if self.period_ns != DAY_NS:
            raise ValueError("this scenario contract fixes maintenance cadence at 24 h")
        self.initial_age_ns = int(config.get("initial_equivalent_age_ns", 0))
        self.initial_wall_age_ns = int(config.get("initial_wall_age_ns", 0))
        self.epoch_ns = int(config.get("maintenance_epoch_ns", 0))
        self.refresh_trigger = config.get("refresh_trigger", "wall_only")
        if self.refresh_trigger not in {"wall_only", "equivalent_age_or_wall"}:
            raise ValueError("unsupported refresh_trigger")
        if min(self.initial_age_ns, self.initial_wall_age_ns, self.epoch_ns) < 0:
            raise ValueError("initial age and epoch must be non-negative")
        if self.initial_wall_age_ns > self.period_ns:
            raise ValueError("initial wall age cannot exceed the 24 h cadence")
        self._blocks: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self._drained_event_counts: dict[str, int] = {}

    def _block(self, block_id: str) -> dict[str, Any]:
        if not isinstance(block_id, str) or not block_id:
            raise ValueError("block_id must be a non-empty physical identity")
        return self._blocks.setdefault(block_id, {
            "block_id": block_id,
            "equivalent_age_ns": float(self.initial_age_ns),
            "last_update_ns": self.epoch_ns,
            "program_phase_started_count": 0,
            "program_completed_count": 0,
            "erase_phase_started_count": 0,
            "erase_completed_count": 0,
            "last_refresh_commit_ns": None,
            "next_wall_due_ns": self.epoch_ns + self.period_ns - self.initial_wall_age_ns,
            "retry_scenario_count": 0,
            "retry_scenario_latency_ns": 0,
            "retry_scenario_energy_j": 0.0,
        })

    def due_reasons(self, block_id: str, at_ns: int) -> list[str]:
        """Return conservative refresh-policy triggers, never failure facts."""
        state = self._block(block_id)
        if not isinstance(at_ns, int) or at_ns < state["last_update_ns"]:
            raise ValueError("due query precedes accounted block time")
        reasons = []
        if at_ns >= state["next_wall_due_ns"]:
            reasons.append("WALL_24H_SCENARIO_CADENCE")
        if (self.refresh_trigger == "equivalent_age_or_wall"
                and state["equivalent_age_ns"] >= self.period_ns):
            reasons.append("EQUIVALENT_AGE_24H_CONSERVATIVE_POLICY")
        return reasons

    def accounted_through_ns(self, block_id: str) -> int:
        """Expose the exact age-integration frontier for online consumers."""
        return self._block(block_id)["last_update_ns"]

    def advance_temperature(self, block_id: str, start_ns: int, end_ns: int,
                            temperature_k: float) -> float:
        state = self._block(block_id)
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (start_ns, end_ns)):
            raise ValueError("temperature interval times must be integer ns")
        if start_ns != state["last_update_ns"] or end_ns < start_ns:
            raise ValueError("temperature intervals must be contiguous and non-negative")
        rate = arrhenius_acceleration(float(temperature_k), self.ea_ev, self.tref_k)
        delta = (end_ns - start_ns) * rate
        state["equivalent_age_ns"] += delta
        state["last_update_ns"] = end_ns
        self.events.append({"kind": "temperature_age", "block_id": block_id,
                            "start_ns": start_ns, "end_ns": end_ns,
                            "temperature_k": float(temperature_k),
                            "acceleration": rate, "equivalent_age_delta_ns": delta})
        return delta

    def advance_temperature_many(self, block_ids: list[str], start_ns: int, end_ns: int,
                                 temperature_k: float) -> float:
        """Advance a same-temperature cohort with one compact audit event."""
        if not block_ids or len(set(block_ids)) != len(block_ids):
            raise ValueError("block_ids must be a nonempty unique list")
        if not all(isinstance(item, str) and item for item in block_ids):
            raise ValueError("block IDs must be nonempty strings")
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (start_ns, end_ns)):
            raise ValueError("temperature interval times must be integer ns")
        if end_ns < start_ns:
            raise ValueError("temperature interval must be non-negative")
        rate = arrhenius_acceleration(float(temperature_k), self.ea_ev, self.tref_k)
        delta = (end_ns - start_ns) * rate
        for block_id in block_ids:
            state = self._block(block_id)
            if state["last_update_ns"] != start_ns:
                raise ValueError("temperature cohort intervals must be contiguous")
            state["equivalent_age_ns"] += delta
            state["last_update_ns"] = end_ns
        identities = "\n".join(sorted(block_ids)).encode()
        self.events.append({"kind": "temperature_age_cohort",
                            "block_count": len(block_ids),
                            "block_ids_sha256": hashlib.sha256(identities).hexdigest(),
                            "first_block_id": min(block_ids), "last_block_id": max(block_ids),
                            "start_ns": start_ns, "end_ns": end_ns,
                            "temperature_k": float(temperature_k),
                            "acceleration": rate, "equivalent_age_delta_ns_each": delta})
        return delta

    def _media_terminal(self, kind: str, block_id: str, completion_ns: int,
                        succeeded: bool, operation_id: str, phase_started: bool) -> None:
        if kind not in {"program", "erase"}:
            raise ValueError("unsupported media operation")
        state = self._block(block_id)
        if not isinstance(completion_ns, int) or completion_ns < state["last_update_ns"]:
            raise ValueError("media completion precedes accounted block time")
        if succeeded and not phase_started:
            raise ValueError("successful media operation must have started")
        if phase_started:
            state[f"{kind}_phase_started_count"] += 1
        if succeeded:
            state[f"{kind}_completed_count"] += 1
        self.events.append({"kind": kind, "block_id": block_id,
                            "operation_id": str(operation_id),
                            "completion_ns": completion_ns,
                            "phase_started": bool(phase_started),
                            "succeeded": bool(succeeded),
                            "completed_counted": bool(succeeded),
                            "wear_exposure_semantics": "PHASE_START_COUNT_ONLY_NO_DAMAGE_CURVE"})

    def record_program(self, block_id: str, completion_ns: int, succeeded: bool,
                       operation_id: str, *, phase_started: bool = True) -> None:
        self._media_terminal("program", block_id, completion_ns, succeeded, operation_id,
                             phase_started)

    def record_erase(self, block_id: str, completion_ns: int, succeeded: bool,
                     operation_id: str, *, phase_started: bool = True) -> None:
        self._media_terminal("erase", block_id, completion_ns, succeeded, operation_id,
                             phase_started)

    def record_refresh_terminal(self, block_id: str, completion_ns: int, committed: bool,
                                maintenance_request_id: str) -> None:
        state = self._block(block_id)
        if not isinstance(completion_ns, int) or completion_ns != state["last_update_ns"]:
            raise ValueError("refresh terminal must coincide with accounted block time")
        age_before = state["equivalent_age_ns"]
        if committed:
            state["equivalent_age_ns"] = 0.0
            state["last_refresh_commit_ns"] = completion_ns
            state["next_wall_due_ns"] = completion_ns + self.period_ns
        self.events.append({
            "kind": "refresh_terminal", "block_id": block_id,
            "maintenance_request_id": str(maintenance_request_id),
            "completion_ns": completion_ns, "committed": bool(committed),
            "age_before_ns": age_before,
            "age_after_ns": state["equivalent_age_ns"],
        })

    def record_retry_scenario(self, block_id: str, at_ns: int, count: int,
                              extra_latency_ns: int, extra_energy_j: float,
                              scenario_id: str) -> None:
        state = self._block(block_id)
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0
                   for v in (at_ns, count, extra_latency_ns)):
            raise ValueError("retry fact counts/times must be non-negative integer values")
        if at_ns < state["last_update_ns"] or not math.isfinite(extra_energy_j) or extra_energy_j < 0:
            raise ValueError("invalid retry scenario fact")
        state["retry_scenario_count"] += count
        state["retry_scenario_latency_ns"] += extra_latency_ns
        state["retry_scenario_energy_j"] += extra_energy_j
        self.events.append({
            "kind": "retry_scenario", "block_id": block_id, "at_ns": at_ns,
            "count": count, "extra_latency_ns": extra_latency_ns,
            "extra_energy_j": extra_energy_j, "scenario_id": str(scenario_id),
            "evidence_class": "SCENARIO_ASSUMPTION_NO_RBER_CLAIM",
        })

    def drain_events(self) -> list[dict[str, Any]]:
        """Move the current audit delta to the caller and release its memory."""
        result = self.events
        self.events = []
        for row in result:
            kind = row["kind"]
            self._drained_event_counts[kind] = self._drained_event_counts.get(kind, 0) + 1
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "eq3-conditional-reliability-v1",
            "evidence_class": "CONDITIONAL_SIMULATED_HEATWATCH_PROXY",
            "ea_ev": self.ea_ev, "tref_k": self.tref_k,
            "maintenance_period_ns": self.period_ns,
            "maintenance_period_semantics": "SCENARIO_CADENCE_NOT_FAILURE_THRESHOLD",
            "refresh_trigger": self.refresh_trigger,
            "equivalent_age_trigger_semantics": "CONSERVATIVE_REFRESH_POLICY_NOT_FAILURE_THRESHOLD",
            "initial_age_semantics": "WALL_AND_EQUIVALENT_AGE_ARE_INDEPENDENT_AND_UNSCALED",
            "blocks": deepcopy(self._blocks), "retained_events": deepcopy(self.events),
            "drained_event_counts": dict(sorted(self._drained_event_counts.items())),
            "unavailable": ["RBER", "ECC_STRENGTH", "FAILURE_PROBABILITY", "LIFETIME"],
        }


__all__ = ["ReliabilityLedger", "arrhenius_acceleration"]

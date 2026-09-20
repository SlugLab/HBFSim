"""Optional slow feedback overlay for the temperature retry proxy.

The existing read-rate policy remains responsible for severe/shutdown guards.
This overlay observes 20 ms windows, evaluates only every 200 ms, changes the
future budget by five percent, and waits one second before accepting or rolling
back a candidate decrease.  All thresholds are explicit scenario assumptions.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class TemperatureRetryObservation:
    start_ns: int
    end_ns: int
    temperature_k: float
    offered_bytes: int
    delivered_bytes: int
    backlog_bytes: int
    retry_bytes: int
    current_budget_bytes: int
    protected_budget_bytes: int
    guard_state: str
    gate_limited: bool = False


class TemperatureRetryFeedbackPolicy:
    def __init__(self, profile, maximum_budget_bytes):
        self.profile = dict(profile)
        self.maximum_budget_bytes = int(maximum_budget_bytes)
        self.observation_ns = int(profile.get("observation_window_ns", 20_000_000))
        self.evaluation_ns = int(profile.get("evaluation_interval_ns", 200_000_000))
        self.rollback_ns = int(profile.get("rollback_interval_ns", 1_000_000_000))
        self.step_fraction = float(profile.get("step_fraction", 0.05))
        self.near_light_k = float(profile.get("near_light_temperature_k", 348.15))
        self.minimum_fraction = float(profile.get("minimum_budget_fraction", 0.10))
        if (self.maximum_budget_bytes <= 0 or self.observation_ns <= 0
                or self.evaluation_ns <= 0
                or self.rollback_ns <= 0 or self.evaluation_ns % self.observation_ns
                or self.rollback_ns % self.evaluation_ns
                or not math.isfinite(self.step_fraction)
                or not math.isfinite(self.minimum_fraction)
                or not 0 < self.step_fraction <= 1
                or not 0 <= self.minimum_fraction <= 1
                or not math.isfinite(self.near_light_k)):
            raise ValueError("invalid temperature retry feedback profile")
        self.step_bytes = max(1, int(round(self.maximum_budget_bytes * self.step_fraction)))
        self.minimum_budget_bytes = int(round(self.maximum_budget_bytes * self.minimum_fraction))
        self._windows = []
        self._previous_retry_fraction = None
        self._candidate = None
        self._healthy_evaluations = 0
        self._next_start_ns = None

    @staticmethod
    def _metrics(windows):
        delivered = sum(row.delivered_bytes for row in windows)
        retry = sum(row.retry_bytes for row in windows)
        return {
            "temperature_k": max(row.temperature_k for row in windows),
            "offered_bytes": sum(row.offered_bytes for row in windows),
            "delivered_bytes": delivered,
            "backlog_bytes": windows[-1].backlog_bytes,
            "retry_bytes": retry,
            "retry_bytes_per_delivered_byte": (retry / delivered if delivered else None),
            "gate_limited": any(row.gate_limited for row in windows),
        }

    def evaluate(self, observation):
        if observation.end_ns - observation.start_ns != self.observation_ns:
            raise ValueError("temperature retry observation window mismatch")
        if self._next_start_ns is not None and observation.start_ns != self._next_start_ns:
            raise ValueError("temperature retry observations must be contiguous")
        self._next_start_ns = observation.end_ns
        if observation.guard_state not in {"normal", "light", "severe", "shutdown"}:
            raise ValueError("unknown thermal guard state")
        if not math.isfinite(observation.temperature_k):
            raise ValueError("controller temperature must be finite")
        if any(value < 0 for value in (observation.offered_bytes, observation.delivered_bytes,
                                       observation.backlog_bytes, observation.retry_bytes,
                                       observation.current_budget_bytes,
                                       observation.protected_budget_bytes)):
            raise ValueError("negative controller fact")
        # The base ReadRatePolicy guard always wins immediately.
        if observation.guard_state in {"severe", "shutdown"}:
            self._windows = []
            self._candidate = None
            self._previous_retry_fraction = None
            self._healthy_evaluations = 0
            return self._decision(observation.protected_budget_bytes, "THERMAL_GUARD",
                                  ("EXISTING_SEVERE_PROTECTION",), None)
        self._windows.append(observation)
        required = self.evaluation_ns // self.observation_ns
        if len(self._windows) < required:
            return self._decision(observation.protected_budget_bytes, "HOLD",
                                  ("WAITING_FOR_200MS_EVALUATION",), None)
        if len(self._windows) > required:
            raise AssertionError("evaluation accumulator exceeded its fixed interval")
        metrics = self._metrics(self._windows)
        self._windows = []
        budget = observation.protected_budget_bytes
        action, reasons = "HOLD", []
        retry_fraction = metrics["retry_bytes_per_delivered_byte"]
        if self._candidate is not None:
            self._candidate["observations"].append(metrics)
            if observation.end_ns >= self._candidate["evaluate_at_ns"]:
                current = self._metrics_from_evaluations(self._candidate["observations"])
                baseline = self._candidate["baseline"]
                retry_improved = (current["retry_bytes_per_delivered_byte"] is not None
                                  and baseline["retry_bytes_per_delivered_byte"] is not None
                                  and current["retry_bytes_per_delivered_byte"]
                                  < baseline["retry_bytes_per_delivered_byte"])
                performance_harmed = (current["delivered_bytes_per_evaluation"]
                                      < baseline["delivered_bytes"]
                                      and current["backlog_bytes"] > baseline["backlog_bytes"])
                if not retry_improved or performance_harmed:
                    budget = min(self.maximum_budget_bytes, budget + self.step_bytes)
                    action, reasons = "INCREASE", ["ROLLBACK_AFTER_1S_NO_NET_IMPROVEMENT"]
                else:
                    reasons = ["ACCEPT_AFTER_1S_RETRY_IMPROVED_WITHOUT_RATE_QUEUE_HARM"]
                self._candidate = None
            self._healthy_evaluations = 0
        elif metrics["offered_bytes"] == 0 and metrics["backlog_bytes"] == 0:
            reasons = ["NO_DEMAND"]
            self._healthy_evaluations = 0
            self._previous_retry_fraction = None
        else:
            rising = (retry_fraction is not None and self._previous_retry_fraction is not None
                      and retry_fraction > self._previous_retry_fraction)
            if metrics["temperature_k"] >= self.near_light_k and rising:
                candidate_budget = max(self.minimum_budget_bytes, budget - self.step_bytes)
                if candidate_budget < budget:
                    budget, action = candidate_budget, "DECREASE"
                    reasons = ["NEAR_LIGHT_AND_RETRY_LOAD_RISING"]
                    self._candidate = {
                        "baseline": metrics,
                        "evaluate_at_ns": observation.end_ns + self.rollback_ns,
                        "observations": [],
                    }
                else:
                    reasons = ["MINIMUM_BUDGET_HOLD"]
                self._healthy_evaluations = 0
            elif metrics["temperature_k"] < self.near_light_k:
                healthy = (metrics["backlog_bytes"] > 0 and metrics["gate_limited"]
                           and budget < self.maximum_budget_bytes)
                self._healthy_evaluations = self._healthy_evaluations + 1 if healthy else 0
                if self._healthy_evaluations >= 3:
                    budget = min(self.maximum_budget_bytes, budget + self.step_bytes)
                    action, reasons = "INCREASE", ["THREE_COOL_DEMAND_LIMITED_EVALUATIONS"]
                    self._healthy_evaluations = 0
                else:
                    reasons = (["COOL_DEMAND_LIMITED_RECOVERY_PENDING"] if healthy
                               else ["BELOW_NEAR_LIGHT_HOLD"])
            else:
                reasons = ["RETRY_LOAD_NOT_RISING_HOLD"]
                self._healthy_evaluations = 0
        if retry_fraction is not None:
            self._previous_retry_fraction = retry_fraction
        return self._decision(budget, action, tuple(reasons), metrics)

    @staticmethod
    def _metrics_from_evaluations(rows):
        delivered = sum(row["delivered_bytes"] for row in rows)
        retry = sum(row["retry_bytes"] for row in rows)
        return {
            "delivered_bytes_per_evaluation": delivered / len(rows),
            "backlog_bytes": rows[-1]["backlog_bytes"],
            "retry_bytes_per_delivered_byte": (retry / delivered if delivered else None),
        }

    def _decision(self, budget, action, reasons, metrics):
        return {
            "budget_bytes": int(budget),
            "action": action,
            "reasons": list(reasons),
            "evaluation_metrics": metrics,
            "candidate": None if self._candidate is None else {
                "evaluate_at_ns": self._candidate["evaluate_at_ns"],
                "baseline": self._candidate["baseline"],
            },
            "semantics": (
                "OPTIONAL_CONDITIONAL_CONTROLLER;20MS_OBSERVE;200MS_EVALUATE;"
                "5PCT_FUTURE_BUDGET_STEP;1S_CANDIDATE_REVIEW;"
                "THREE_COOL_GATED_EVALUATIONS_FOR_RECOVERY;BASE_THERMAL_GUARD_WINS"
            ),
        }

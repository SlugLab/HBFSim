"""Default-off byte admission policy for the isolated EQ3 experiment.

The policy consumes immutable completed-window facts and returns budgets for
future admission. It does not schedule, advance time, or infer ECC/retry facts.
All numeric profiles are explicit engineering assumptions.
"""
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

STRATEGIES = {"guard_only", "thermal_hysteresis_guard",
              "read_rate_feedback_thermal_guard_v1"}
GUARDS = {"normal", "light", "severe", "shutdown"}


@dataclass(frozen=True)
class EngineeringProfile:
    profile_id: str
    enabled: bool = False
    strategy: str = "guard_only"
    window_ns: int = 1_000_000_000
    target_bytes_per_s: int = 0
    target_latency_p95_ns: Optional[int] = None
    tolerance_fraction: float = 0.05
    step_bytes: int = 0
    minimum_budget_bytes: int = 0
    maximum_budget_bytes: int = 0
    severe_budget_bytes: int = 0
    smoothing_excess_fraction: float = 0.10
    light_fraction: float = 0.5
    source_class: str = "ENGINEERING_PROFILE_SCENARIO_ASSUMPTION"

    def __post_init__(self):
        if self.strategy not in STRATEGIES:
            raise ValueError("unsupported read-rate strategy")
        if self.window_ns <= 0 or self.target_bytes_per_s < 0 or self.step_bytes < 0:
            raise ValueError("time, target, and step must be nonnegative")
        if (not 0 <= self.tolerance_fraction < 1 or self.smoothing_excess_fraction < 0 or
                not 0 <= self.light_fraction <= 1):
            raise ValueError("invalid tolerance")
        if not 0 <= self.minimum_budget_bytes <= self.maximum_budget_bytes:
            raise ValueError("invalid budget bounds")
        if not 0 <= self.severe_budget_bytes <= self.maximum_budget_bytes:
            raise ValueError("invalid severe budget")
        if self.enabled and self.target_bytes_per_s <= 0:
            raise ValueError("enabled policy requires an explicit positive target")
        if self.target_latency_p95_ns is not None and self.target_latency_p95_ns <= 0:
            raise ValueError("target_latency_p95_ns must be positive when configured")


@dataclass(frozen=True)
class StackWindowFacts:
    stack_id: str
    offered_bytes: int
    delivered_bytes: int
    backlog_bytes: int
    oldest_wait_ns: int
    latency_p95_ns: Optional[int]
    censored_requests: int
    gate_limited: bool
    backend_busy_fraction: Optional[float]
    resource_busy: Optional[bool]
    maintenance_due_bytes: int = 0
    maintenance_earliest_deadline_ns: Optional[int] = None
    retry_count: Optional[int] = None
    uecc_count: Optional[int] = None

    def __post_init__(self):
        if not self.stack_id:
            raise ValueError("stack_id is required")
        values = (self.offered_bytes, self.delivered_bytes, self.backlog_bytes,
                  self.oldest_wait_ns, self.censored_requests, self.maintenance_due_bytes)
        if any(value < 0 for value in values):
            raise ValueError("window counters must be nonnegative")
        if self.backend_busy_fraction is not None and not 0 <= self.backend_busy_fraction <= 1:
            raise ValueError("backend_busy_fraction must be in [0,1]")


@dataclass(frozen=True)
class WindowFacts:
    start_ns: int
    end_ns: int
    guard_state: str
    stacks: Tuple[StackWindowFacts, ...]
    current_budget_bytes: Mapping[str, int]
    hysteresis_budget_bytes: Mapping[str, int] = field(default_factory=dict)
    shared_endpoint_caps_bytes: Mapping[str, int] = field(default_factory=dict)
    route_endpoints: Mapping[str, Tuple[str, ...]] = field(default_factory=dict)
    guard_states: Mapping[str, str] = field(default_factory=dict)
    endpoint_guard_states: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.start_ns < 0 or self.end_ns <= self.start_ns:
            raise ValueError("window must be a positive half-open interval")
        if self.guard_state not in GUARDS:
            raise ValueError("unknown thermal guard state")
        ids = [stack.stack_id for stack in self.stacks]
        if len(ids) != len(set(ids)) or set(ids) != set(self.current_budget_bytes):
            raise ValueError("stack/current-budget coverage mismatch")
        if any(value < 0 for value in self.current_budget_bytes.values()):
            raise ValueError("budgets must be nonnegative")
        unknown_routes = set(self.route_endpoints)-set(ids)
        if unknown_routes:
            raise ValueError("route_endpoints contains unknown stack")
        if set(self.guard_states)-set(ids) or any(value not in GUARDS for value in self.guard_states.values()):
            raise ValueError("guard_states contains unknown stack or state")
        if (set(self.endpoint_guard_states)-set(self.shared_endpoint_caps_bytes) or
                any(value not in GUARDS for value in self.endpoint_guard_states.values())):
            raise ValueError("endpoint_guard_states contains unknown endpoint or state")


@dataclass(frozen=True)
class StackDecision:
    stack_id: str
    budget_bytes: int
    action: str
    outcome: str
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    enabled: bool
    strategy: str
    applies_to_window_start_ns: int
    stack_decisions: Tuple[StackDecision, ...]
    shared_endpoint_budget_bytes: Mapping[str, int]
    fact_semantics: str = "delivered_bytes count final successful fabric delivery only"
    effect_semantics: str = "future byte admission only"
    reliability_semantics: str = "retry/UECC consumed only when observed; no ECC inference"


class ReadRatePolicy:
    def __init__(self, profile: EngineeringProfile):
        self.profile = profile
        self._previous = {}

    def _bounded(self, value):
        return min(self.profile.maximum_budget_bytes,
                   max(self.profile.minimum_budget_bytes, value))

    def _protected_budget(self, stack_id, facts):
        guard = facts.guard_states.get(stack_id, facts.guard_state)
        if guard == "shutdown":
            return 0, "THERMAL_SHUTDOWN"
        if guard == "severe":
            return self.profile.severe_budget_bytes, "THERMAL_SEVERE"
        if self.profile.strategy == "guard_only":
            # P0 has no ordinary/light limiter and must recover from a prior
            # emergency stop to its explicit baseline.
            return self.profile.maximum_budget_bytes, None
        if self.profile.strategy == "thermal_hysteresis_guard":
            return facts.hysteresis_budget_bytes.get(
                stack_id, self.profile.maximum_budget_bytes), None
        current = facts.current_budget_bytes[stack_id]
        if (guard == "light" and self.profile.strategy != "guard_only" and
                stack_id in facts.hysteresis_budget_bytes):
            return min(current, facts.hysteresis_budget_bytes[stack_id]), "THERMAL_LIGHT_CAP"
        if guard == "normal" and current == 0:
            # Feedback resumes cautiously after an emergency stop; later
            # windows may increase only with observed gate+idle evidence.
            return self.profile.minimum_budget_bytes, None
        return current, None

    def evaluate(self, facts: WindowFacts):
        if facts.end_ns-facts.start_ns != self.profile.window_ns:
            raise ValueError("facts window differs from engineering profile")
        decisions = []
        target = self.profile.target_bytes_per_s * self.profile.window_ns / 1_000_000_000
        for observed in facts.stacks:
            original_budget = facts.current_budget_bytes[observed.stack_id]
            if not self.profile.enabled:
                decisions.append(StackDecision(observed.stack_id,
                                               facts.current_budget_bytes[observed.stack_id],
                                               "HOLD", "DISABLED",
                                               ("DEFAULT_OFF",)))
                continue
            current, thermal_reason = self._protected_budget(observed.stack_id, facts)
            if thermal_reason:
                action = ("STOP" if current == 0 else "DECREASE" if current < original_budget
                          else "INCREASE" if current > original_budget else "HOLD")
                decisions.append(StackDecision(observed.stack_id, current,
                                               action,
                                               "THERMAL_GUARD", (thermal_reason,)))
                self._previous[observed.stack_id] = (observed, current, "guard")
                continue
            if self.profile.strategy == "guard_only":
                budget = self._bounded(current)
                action = "INCREASE" if budget > original_budget else "DECREASE" if budget < original_budget else "HOLD"
                decisions.append(StackDecision(observed.stack_id, budget, action, "GUARD_ONLY",
                                               ("NO_ORDINARY_RATE_FEEDBACK",)))
                continue
            if self.profile.strategy == "thermal_hysteresis_guard":
                budget = self._bounded(current)
                decisions.append(StackDecision(observed.stack_id, budget,
                                               "INCREASE" if budget > original_budget else
                                               "DECREASE" if budget < original_budget else "HOLD",
                                               "THERMAL_HYSTERESIS", ("EXISTING_HYSTERESIS_CAP",)))
                continue

            # New arrivals alone are not the demand population: after an input
            # cutoff, previously offered requests can remain queued.  max()
            # avoids adding overlapping offered and delivered/backlog views.
            demand_bytes = max(observed.offered_bytes,
                               observed.delivered_bytes + observed.backlog_bytes)
            enough_demand = demand_bytes >= target
            latency_met = (self.profile.target_latency_p95_ns is None or
                           (observed.latency_p95_ns is not None and
                            observed.latency_p95_ns <= self.profile.target_latency_p95_ns))
            met_rate = (observed.delivered_bytes >= target*(1-self.profile.tolerance_fraction)
                        and latency_met)
            reliability_bad = ((observed.uecc_count or 0) > 0)
            previous = self._previous.get(observed.stack_id)
            budget, action, outcome, reasons = current, "HOLD", "STABLE", []
            if not enough_demand:
                outcome, reasons = "INSUFFICIENT_DEMAND", ["DEMAND_BELOW_TARGET"]
            elif reliability_bad:
                outcome, reasons = "UNMET_TARGET", ["OBSERVED_UECC"]
            elif met_rate:
                excess = observed.delivered_bytes > target*(1+self.profile.smoothing_excess_fraction)
                if excess and current > self.profile.minimum_budget_bytes:
                    budget, action = self._bounded(current-self.profile.step_bytes), "DECREASE"
                    reasons = ["OVERDELIVERY_SMOOTHING"]
                else:
                    reasons = ["TARGET_MET_HOLD"]
            else:
                outcome = "UNMET_TARGET"
                backend_idle = (observed.backend_busy_fraction is not None and
                                observed.backend_busy_fraction < 1 and observed.resource_busy is False)
                facts_unknown = observed.backend_busy_fraction is None or observed.resource_busy is None
                rollback = False
                if previous and previous[2] == "increase":
                    prior, prior_budget, _ = previous
                    rollback = (current > prior_budget and observed.delivered_bytes <= prior.delivered_bytes and
                                (observed.backlog_bytes > prior.backlog_bytes or
                                 (observed.latency_p95_ns is not None and prior.latency_p95_ns is not None and
                                  observed.latency_p95_ns > prior.latency_p95_ns)))
                if rollback:
                    budget, action, reasons = self._bounded(current-self.profile.step_bytes), "DECREASE", ["ROLLBACK_NO_DELIVERY_GAIN"]
                elif observed.gate_limited and observed.backlog_bytes > 0 and backend_idle:
                    budget, action, reasons = self._bounded(current+self.profile.step_bytes), "INCREASE", ["GATE_LIMITED_BACKEND_IDLE"]
                elif facts_unknown:
                    reasons = ["BOTTLENECK_UNKNOWN_HOLD"]
                elif observed.backend_busy_fraction == 1 or observed.resource_busy:
                    reasons = ["BACKEND_OR_RESOURCE_BUSY_HOLD"]
                else:
                    reasons = ["NO_EVIDENCE_TO_INCREASE_HOLD"]
                if observed.maintenance_due_bytes:
                    reasons.append("MAINTENANCE_DUE_VISIBLE")
            decisions.append(StackDecision(observed.stack_id, int(budget), action, outcome, tuple(reasons)))
            self._previous[observed.stack_id] = (observed, current, action.lower())

        endpoint = dict(facts.shared_endpoint_caps_bytes)
        if self.profile.enabled:
            for key, value in tuple(endpoint.items()):
                guard = facts.endpoint_guard_states.get(key, facts.guard_state)
                if guard == "shutdown":
                    endpoint[key] = 0
                elif guard == "severe":
                    endpoint[key] = min(value, self.profile.severe_budget_bytes)
                elif guard == "light" and self.profile.strategy != "guard_only":
                    endpoint[key] = int(value*self.profile.light_fraction)
        return Decision(self.profile.enabled, self.profile.strategy, facts.end_ns,
                        tuple(decisions), endpoint)


class ByteTokenLedger:
    """Atomic consumer helper; duplicate route endpoints are charged once."""
    def __init__(self, decision: Decision):
        self.enabled = decision.enabled
        self.stack = {row.stack_id: row.budget_bytes for row in decision.stack_decisions}
        self.endpoint = dict(decision.shared_endpoint_budget_bytes)

    def can_consume(self, stack_id: str, route_endpoints, byte_count: int):
        if byte_count < 0:
            raise ValueError("byte_count must be nonnegative")
        if not self.enabled:
            return True
        names = set(route_endpoints)
        if stack_id not in self.stack or any(name not in self.endpoint for name in names):
            raise ValueError("unknown stack or route endpoint")
        return (self.stack[stack_id] >= byte_count and
                all(self.endpoint[name] >= byte_count for name in names))

    def try_consume(self, stack_id: str, route_endpoints, byte_count: int):
        if not self.can_consume(stack_id, route_endpoints, byte_count):
            return False
        if not self.enabled:
            return True
        names = set(route_endpoints)
        # All checks precede all mutations: failed admission leaves no partial reservation.
        self.stack[stack_id] -= byte_count
        for name in names:
            self.endpoint[name] -= byte_count
        return True

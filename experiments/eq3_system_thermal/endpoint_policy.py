"""Default-disconnected policy adapter for shared HBM route endpoints.

HBF stacks retain the existing read-rate policy exactly.  HBM entries in the
topology service are shared routing endpoints, so their future admission cap is
derived only from their thermal guard state; absent HBM foreground demand is
not evidence that a previously reduced endpoint cap should remain reduced.
"""

from read_rate_policy import Decision, ReadRatePolicy, StackDecision


class EndpointAwarePolicy(ReadRatePolicy):
    """Use guard-only caps for an HBM shared endpoint and legacy policy for HBF."""

    def __init__(self, profile):
        super().__init__(profile)
        self.stack_id = profile.profile_id.rsplit(":", 1)[-1]
        self.shared_hbm_endpoint = self.stack_id.startswith("hbm")

    def evaluate(self, facts):
        if not self.shared_hbm_endpoint:
            return super().evaluate(facts)
        if facts.end_ns - facts.start_ns != self.profile.window_ns:
            raise ValueError("facts window differs from engineering profile")
        if len(facts.stacks) != 1 or facts.stacks[0].stack_id != self.stack_id:
            raise ValueError("HBM endpoint adapter requires one matching stack fact")
        original = facts.current_budget_bytes[self.stack_id]
        if not self.profile.enabled:
            budget, outcome, reasons = original, "DISABLED", ("DEFAULT_OFF",)
        else:
            state = facts.guard_states.get(self.stack_id, facts.guard_state)
            if state == "shutdown":
                budget, outcome, reasons = 0, "THERMAL_GUARD", ("THERMAL_SHUTDOWN",)
            elif state == "severe":
                budget = self.profile.severe_budget_bytes
                outcome, reasons = "THERMAL_GUARD", ("THERMAL_SEVERE",)
            elif state == "light":
                budget = int(self.profile.maximum_budget_bytes * self.profile.light_fraction)
                outcome, reasons = "THERMAL_GUARD", ("THERMAL_LIGHT_ENDPOINT_CAP",)
            else:
                budget = self.profile.maximum_budget_bytes
                outcome, reasons = "ENDPOINT_GUARD_ONLY", ("THERMAL_NORMAL_RESTORE_BASELINE",)
        budget = min(self.profile.maximum_budget_bytes, max(0, int(budget)))
        action = ("INCREASE" if budget > original else
                  "DECREASE" if budget < original else "HOLD")
        decision = StackDecision(self.stack_id, budget, action, outcome, reasons)
        return Decision(
            self.profile.enabled,
            self.profile.strategy,
            facts.end_ns,
            (decision,),
            dict(facts.shared_endpoint_caps_bytes),
            fact_semantics=("HBM shared-endpoint cap consumes thermal guard state only; "
                            "no HBM foreground delivery is inferred"),
            effect_semantics="future shared-endpoint byte admission only",
        )

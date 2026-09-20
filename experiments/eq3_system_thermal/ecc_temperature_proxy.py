"""Memoryless temperature-to-recoverable-read-cost scenario proxy.

This is an optional experimental provider for ``ReliabilityCausalService``.
It is not an HBF RBER/UBER model and is not physically calibrated.  A cost is
sampled only when the service first allocates a read, so later observations do
not retroactively change active work.
"""
from copy import deepcopy
import math


CLASSIFICATION = "CONDITIONAL_TEMPERATURE_RETRY_SCENARIO_NOT_MEASURED_HBF_RBER"


class TemperatureProxyDomainError(ValueError):
    pass


class TemperatureReadCostProxy:
    """Map the latest observed stack temperature to expected read attempts."""

    def __init__(self, profile, initial_temperature_k_by_stack):
        self.profile = deepcopy(profile)
        if self.profile.get("classification") != CLASSIFICATION:
            raise ValueError("explicit non-calibrated temperature-proxy classification required")
        self.p85 = float(self.profile["recoverable_read_probability_at_85c"])
        if not math.isfinite(self.p85) or not 0 <= self.p85 < 1:
            raise ValueError("recoverable-read probability at 85C must be finite in [0,1)")
        self.ea_ev = float(self.profile["activation_energy_ev"])
        self.k_ev_per_k = float(self.profile["boltzmann_ev_per_k"])
        self.reference_k = float(self.profile["temperature_reference_k"])
        domain = self.profile["temperature_domain_k"]
        if (len(domain) != 2 or any(not math.isfinite(float(x)) for x in domain)
                or not 0 < float(domain[0]) <= self.reference_k <= float(domain[1])):
            raise ValueError("invalid temperature proxy domain")
        self.temperature_domain_k = (float(domain[0]), float(domain[1]))
        if (not math.isfinite(self.ea_ev) or self.ea_ev <= 0
                or not math.isfinite(self.k_ev_per_k) or self.k_ev_per_k <= 0
                or not math.isfinite(self.reference_k) or self.reference_k <= 0):
            raise ValueError("invalid Arrhenius proxy parameters")
        if float(self.profile["expected_extra_attempts_per_recoverable_read"]) != 2.0:
            raise ValueError("this proxy fixes two expected extra attempts per recoverable read")
        self.states = {}
        for stack, temperature_k in initial_temperature_k_by_stack.items():
            if not stack.startswith("hbf"):
                raise ValueError("temperature retry proxy cannot apply to HBM")
            self.states[stack] = {"last_ns": 0, "temperature_k": float(temperature_k)}
            self.cost(stack, 0)

    def probability(self, temperature_k):
        temperature_k = float(temperature_k)
        if not math.isfinite(temperature_k):
            raise TemperatureProxyDomainError("temperature must be finite")
        lower, upper = self.temperature_domain_k
        if not lower <= temperature_k <= upper:
            raise TemperatureProxyDomainError(
                f"temperature {temperature_k}K outside conditional proxy domain {lower}..{upper}K")
        if self.p85 == 0:
            return 0.0
        acceleration = math.exp(
            self.ea_ev / self.k_ev_per_k * (1 / self.reference_k - 1 / temperature_k))
        probability = -math.expm1(math.log1p(-self.p85) * acceleration)
        if not math.isfinite(probability) or not 0 <= probability < 1:
            raise TemperatureProxyDomainError("temperature retry probability is not finite in [0,1)")
        return probability

    def observe(self, start_ns, end_ns, temperatures):
        if end_ns < start_ns or set(temperatures) != set(self.states):
            raise ValueError("temperature coverage/time mismatch")
        for stack, state in self.states.items():
            if state["last_ns"] != start_ns:
                raise ValueError("noncontiguous temperature observations")
            temperature_k = float(temperatures[stack])
            self.probability(temperature_k)
            state.update(last_ns=end_ns, temperature_k=temperature_k)

    def cost(self, stack, at_ns):
        state = self.states[stack]
        if at_ns < state["last_ns"]:
            raise ValueError("cannot query a past temperature state")
        probability = self.probability(state["temperature_k"])
        expected_extra_attempts = 2.0 * probability
        return {
            "temperature_k": state["temperature_k"],
            "recoverable_read_probability": probability,
            "expected_retry_steps": expected_extra_attempts,
            "attempt_work_milli": 1000 + int(round(1000 * expected_extra_attempts)),
            "effort_quantization_absolute_retry_steps": 0.0005,
            "temperature_coupling": "LATEST_COMPLETED_WINDOW_MEMORYLESS_SCENARIO",
            "classification": CLASSIFICATION,
            "UECC": "UNKNOWN",
        }

    def snapshot(self):
        return {
            "profile": deepcopy(self.profile),
            "states": deepcopy(self.states),
            "limitations": [
                "NOT_MEASURED_HBF_RBER_OR_UBER",
                "MEMORYLESS_RECOVERABLE_READ_SCENARIO_ONLY",
                "NO_STOCHASTIC_RETRY_TAIL",
                "NO_RETENTION_WEAR_OR_DATA_IDENTITY",
            ],
        }

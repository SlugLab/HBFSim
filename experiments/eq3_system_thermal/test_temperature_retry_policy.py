import unittest

from temperature_retry_policy import (TemperatureRetryFeedbackPolicy,
                                      TemperatureRetryObservation)


PROFILE = {
    "observation_window_ns": 20_000_000,
    "evaluation_interval_ns": 200_000_000,
    "rollback_interval_ns": 1_000_000_000,
    "step_fraction": .05,
    "minimum_budget_fraction": .1,
    "near_light_temperature_k": 348.15,
}


def feed(policy, *, start_window, temperature, offered, delivered, backlog, retry,
         budget=1000, guard="normal", gate_limited=False):
    result = None
    for index in range(start_window, start_window + 10):
        result = policy.evaluate(TemperatureRetryObservation(
            start_ns=index * 20_000_000, end_ns=(index + 1) * 20_000_000,
            temperature_k=temperature, offered_bytes=offered,
            delivered_bytes=delivered, backlog_bytes=backlog,
            retry_bytes=retry, current_budget_bytes=budget,
            protected_budget_bytes=budget, guard_state=guard,
            gate_limited=gate_limited))
    return result


class TemperatureRetryPolicyTests(unittest.TestCase):
    def test_no_demand_and_below_threshold_do_not_reduce_budget(self):
        policy = TemperatureRetryFeedbackPolicy(PROFILE, 1000)
        no_demand = feed(policy, start_window=0, temperature=360, offered=0,
                         delivered=0, backlog=0, retry=0)
        self.assertEqual(no_demand["budget_bytes"], 1000)
        self.assertEqual(no_demand["reasons"], ["NO_DEMAND"])
        first = feed(policy, start_window=10, temperature=340, offered=100,
                     delivered=100, backlog=0, retry=10)
        second = feed(policy, start_window=20, temperature=340, offered=100,
                      delivered=100, backlog=0, retry=20)
        self.assertEqual(first["budget_bytes"], 1000)
        self.assertEqual(second["budget_bytes"], 1000)
        self.assertEqual(second["reasons"], ["BELOW_NEAR_LIGHT_HOLD"])

    def test_hot_rising_retry_creates_five_percent_candidate_then_rolls_back(self):
        policy = TemperatureRetryFeedbackPolicy(PROFILE, 1000)
        feed(policy, start_window=0, temperature=350, offered=100,
             delivered=100, backlog=10, retry=10)
        candidate = feed(policy, start_window=10, temperature=351, offered=100,
                         delivered=100, backlog=20, retry=20)
        self.assertEqual(candidate["action"], "DECREASE")
        self.assertEqual(candidate["budget_bytes"], 950)
        result = None
        for evaluation in range(5):
            result = feed(policy, start_window=20 + evaluation * 10,
                          temperature=351, offered=100, delivered=90,
                          backlog=30 + evaluation, retry=20, budget=950)
        self.assertEqual(result["action"], "INCREASE")
        self.assertEqual(result["budget_bytes"], 1000)
        self.assertIn("ROLLBACK_AFTER_1S", result["reasons"][0])

    def test_severe_guard_is_immediate(self):
        policy = TemperatureRetryFeedbackPolicy(PROFILE, 1000)
        result = policy.evaluate(TemperatureRetryObservation(
            0, 20_000_000, 370, 100, 50, 50, 10, 1000, 0, "severe"))
        self.assertEqual(result["budget_bytes"], 0)
        self.assertEqual(result["reasons"], ["EXISTING_SEVERE_PROTECTION"])

    def test_severe_cancels_candidate_and_stale_retry_trend(self):
        policy = TemperatureRetryFeedbackPolicy(PROFILE, 1000)
        feed(policy, start_window=0, temperature=350, offered=100,
             delivered=100, backlog=10, retry=10)
        candidate = feed(policy, start_window=10, temperature=351, offered=100,
                         delivered=100, backlog=20, retry=20)
        self.assertIsNotNone(candidate["candidate"])
        severe = policy.evaluate(TemperatureRetryObservation(
            400_000_000, 420_000_000, 370, 100, 50, 50, 10, 950, 0, "severe"))
        self.assertIsNone(severe["candidate"])
        resumed = feed(policy, start_window=21, temperature=351, offered=100,
                       delivered=100, backlog=20, retry=30, budget=1000)
        self.assertEqual(resumed["budget_bytes"], 1000)
        self.assertEqual(resumed["reasons"], ["RETRY_LOAD_NOT_RISING_HOLD"])

    def test_three_cool_demand_limited_evaluations_recover_five_percent(self):
        policy = TemperatureRetryFeedbackPolicy(PROFILE, 1000)
        result = None
        for evaluation in range(3):
            result = feed(policy, start_window=evaluation * 10, temperature=340,
                          offered=100, delivered=95, backlog=100, retry=5,
                          budget=950, gate_limited=True)
        self.assertEqual(result["action"], "INCREASE")
        self.assertEqual(result["budget_bytes"], 1000)
        self.assertEqual(result["reasons"], ["THREE_COOL_DEMAND_LIMITED_EVALUATIONS"])

    def test_profile_and_observation_validation(self):
        bad = dict(PROFILE, observation_window_ns=0)
        with self.assertRaisesRegex(ValueError, "invalid temperature retry feedback profile"):
            TemperatureRetryFeedbackPolicy(bad, 1000)
        policy = TemperatureRetryFeedbackPolicy(PROFILE, 1000)
        policy.evaluate(TemperatureRetryObservation(
            0, 20_000_000, 340, 0, 0, 0, 0, 1000, 1000, "normal"))
        with self.assertRaisesRegex(ValueError, "contiguous"):
            policy.evaluate(TemperatureRetryObservation(
                40_000_000, 60_000_000, 340, 0, 0, 0, 0, 1000, 1000, "normal"))


if __name__ == "__main__":
    unittest.main()

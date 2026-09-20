#!/usr/bin/env python3
"""Fixed, solver-free checks for the rate-thermal fluid byte service."""

import unittest

from fluid_service import (
    BACKEND_LATENCY,
    LATENCY_SEMANTICS,
    FluidService,
)


WINDOW_NS = 20_000_000


class FluidServiceTests(unittest.TestCase):
    def make_service(self, channels=("c0",), capacity_bps=100):
        return FluidService(
            {"hbf0": list(channels)},
            {"hbf0": {channel: capacity_bps for channel in channels}},
        )

    def test_fifo_conservation_and_cooling_drain(self):
        service = self.make_service(capacity_bps=100)  # 2 bytes/window
        first = service.advance(0, WINDOW_NS, {"hbf0": {"c0": 5}}, {"hbf0": 99})
        self.assertEqual(first["served_by_channel"]["hbf0"]["c0"], 2)
        self.assertEqual(first["stacks"]["hbf0"]["backlog_bytes"], 3)
        self.assertEqual(first["stacks"]["hbf0"]["latency_p95_ns"], WINDOW_NS)

        second = service.advance(WINDOW_NS, 2 * WINDOW_NS, {}, {"hbf0": 99})
        self.assertEqual(second["served_by_channel"]["hbf0"]["c0"], 2)
        self.assertEqual(second["stacks"]["hbf0"]["backlog_bytes"], 1)
        self.assertEqual(second["stacks"]["hbf0"]["latency_p95_ns"], 2 * WINDOW_NS)

        third = service.advance(2 * WINDOW_NS, 3 * WINDOW_NS, {}, {"hbf0": 99})
        self.assertEqual(third["served_by_channel"]["hbf0"]["c0"], 1)
        self.assertEqual(third["stacks"]["hbf0"]["backlog_bytes"], 0)
        self.assertIsNone(third["stacks"]["hbf0"]["oldest_wait_ns"])
        conservation = third["conservation"]["per_stack"]["hbf0"]
        self.assertEqual(conservation["cumulative_offered_bytes"], 5)
        self.assertEqual(conservation["cumulative_delivered_bytes"], 5)
        self.assertTrue(conservation["cumulative_conserved"])

    def test_stack_budget_is_max_min_fair_across_channels(self):
        service = self.make_service(("c0", "c1"), capacity_bps=1_000)
        result = service.advance(
            0,
            WINDOW_NS,
            {"hbf0": {"c0": 20, "c1": 20}},
            {"hbf0": 11},
        )
        served = result["served_by_channel"]["hbf0"]
        self.assertEqual(sum(served.values()), 11)
        self.assertLessEqual(abs(served["c0"] - served["c1"]), 1)
        self.assertGreater(served["c0"], 0)
        self.assertGreater(served["c1"], 0)
        self.assertEqual(result["semantics"]["stack_budget_allocation"], "INTEGER_MAX_MIN_FAIR")

    def test_byte_weighted_p95_uses_served_cohorts(self):
        service = self.make_service(capacity_bps=10_000)
        service.advance(0, WINDOW_NS, {"hbf0": {"c0": 6}}, {"hbf0": 0})
        result = service.advance(
            WINDOW_NS,
            2 * WINDOW_NS,
            {"hbf0": {"c0": 94}},
            {"hbf0": 100},
        )
        # Bytes 1..94 have a 20 ms delay and bytes 95..100 have 40 ms.
        self.assertEqual(result["stacks"]["hbf0"]["latency_p95_ns"], 2 * WINDOW_NS)
        self.assertEqual(
            result["stacks"]["hbf0"]["delivered_delay_histogram_bytes"],
            [
                {"delay_ns": WINDOW_NS, "bytes": 94},
                {"delay_ns": 2 * WINDOW_NS, "bytes": 6},
            ],
        )
        self.assertEqual(result["stacks"]["hbf0"]["latency_semantics"], LATENCY_SEMANTICS)
        self.assertEqual(result["stacks"]["hbf0"]["backend_latency_ns"], BACKEND_LATENCY)
        channel_conservation = result["conservation"]["per_stack"]["hbf0"]["per_channel"]["c0"]
        self.assertTrue(channel_conservation["window_conserved"])
        self.assertTrue(channel_conservation["cumulative_conserved"])

    def test_integer_capacity_floor_and_future_budget(self):
        service = self.make_service(capacity_bps=149)  # floor(2.98) = 2 bytes/window
        first = service.advance(0, WINDOW_NS, {"hbf0": {"c0": 8}}, {"hbf0": 1})
        self.assertEqual(first["channels"]["hbf0"]["c0"]["capacity_bytes"], 2)
        self.assertEqual(first["stacks"]["hbf0"]["delivered_bytes"], 1)
        second = service.advance(WINDOW_NS, 2 * WINDOW_NS, {}, {"hbf0": 2})
        self.assertEqual(second["stacks"]["hbf0"]["delivered_bytes"], 2)
        self.assertEqual(second["stacks"]["hbf0"]["backlog_bytes"], 5)

    def test_stacks_are_independent_and_snapshot_is_read_only(self):
        stacks = {f"hbf{i}": ["c0", "c1"] for i in range(8)}
        capacities = {
            stack: {"c0": 1_000, "c1": 1_000} for stack in stacks
        }
        service = FluidService(stacks, capacities)
        offered = {
            stack: {"c0": index + 1, "c1": index + 2}
            for index, stack in enumerate(stacks)
        }
        budgets = {stack: index for index, stack in enumerate(stacks)}
        result = service.advance(0, WINDOW_NS, offered, budgets)
        for index, stack in enumerate(stacks):
            self.assertEqual(result["stacks"][stack]["delivered_bytes"], index)
            self.assertEqual(
                result["stacks"][stack]["backlog_bytes"],
                (index + 1) + (index + 2) - index,
            )
        snapshot = service.snapshot()
        snapshot["stacks"]["hbf0"]["backlog_bytes"] = -1
        self.assertGreaterEqual(service.snapshot()["stacks"]["hbf0"]["backlog_bytes"], 0)

    def test_invalid_inputs_fail_without_advancing_time(self):
        service = self.make_service()
        invalid_calls = [
            lambda: service.advance(1, WINDOW_NS + 1, {}, {"hbf0": 0}),
            lambda: service.advance(0, WINDOW_NS - 1, {}, {"hbf0": 0}),
            lambda: service.advance(0, WINDOW_NS, {"hbf9": {}}, {"hbf0": 0}),
            lambda: service.advance(0, WINDOW_NS, {"hbf0": {"bad": 1}}, {"hbf0": 0}),
            lambda: service.advance(0, WINDOW_NS, {"hbf0": {"c0": -1}}, {"hbf0": 0}),
            lambda: service.advance(0, WINDOW_NS, {"hbf0": {"c0": True}}, {"hbf0": 0}),
            lambda: service.advance(0, WINDOW_NS, {}, {}),
        ]
        for call in invalid_calls:
            with self.assertRaises(ValueError):
                call()
            self.assertEqual(service.now_ns, 0)

    def test_constructor_rejects_incomplete_capacity_map(self):
        with self.assertRaises(ValueError):
            FluidService({"hbf0": ["c0", "c1"]}, {"hbf0": {"c0": 96_000_000_000}})


if __name__ == "__main__":
    unittest.main()

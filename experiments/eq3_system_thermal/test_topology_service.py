#!/usr/bin/env python3
import copy
import unittest

from topology_service import TopologyService, default_config, media_cost_from_service_rate
from eq3_basic_fabric import BasicFabric


W = 20_000_000


def budgets(config, value=10**15):
    return {stack: value for stack in config["channels"]}


def states(config, value="normal"):
    return {stack: value for stack in config["channels"]}


class TopologyServiceTests(unittest.TestCase):
    def test_default_factory_covers_four_topologies(self):
        for topology in ("mixed_direct", "all_hbf_direct", "relay", "dash"):
            config = default_config(topology)
            service = TopologyService(config)
            facts = service.immutable_facts()
            self.assertEqual(facts["config"]["topology"], topology)
            self.assertEqual(facts["buffer_semantics"],
                             "FINITE_TWO_BANK_CONTINUOUS_TURNOVER_NOT_WINDOW_CAPACITY")
            self.assertTrue(all(len(row) == 16 for row in config["channels"].values()))

    def test_direct_conservation_and_large_continuous_bank_turnover(self):
        config = default_config("all_hbf_direct")
        service = TopologyService(config)
        amount = 40 * 1024 * 1024
        result = service.advance(0, W, {"hbf0": {"0": amount}},
                                 budgets(config), states(config))
        self.assertEqual(result["served_by_stack_channel"]["hbf0"]["0"], amount)
        self.assertTrue(result["stacks"]["hbf0"]["cumulative_conserved"])
        buffer = result["buffers"][0]
        self.assertGreater(buffer["turnovers"], 1)
        self.assertLessEqual(buffer["maximum_occupancy_bytes"],
                             buffer["bank_count"] * buffer["bank_capacity_bytes"])

    def test_relay_joint_endpoint_gate_and_light_budget_applied_once(self):
        config = default_config("relay")
        service = TopologyService(config)
        b = budgets(config)
        b["hbf0"] = 1_000
        b["hbm0"] = 600
        s = states(config)
        s["hbf0"] = "light"
        s["hbm0"] = "light"
        first = service.advance(0, W, {"hbf0": {"0": 2_000}}, b, s)
        self.assertEqual(first["served_by_stack_channel"]["hbf0"]["0"], 600)
        self.assertEqual(first["resources"]["endpoint:hbm0"]["used_work_units_scaled"], 600)

        s["hbm0"] = "shutdown"
        second = service.advance(W, 2 * W, {"hbf0": {"1": 100}}, b, s)
        self.assertEqual(second["served_by_stack_channel"]["hbf0"]["1"], 0)
        self.assertTrue(any("hbm0:shutdown" in row["reasons"] for row in second["blocked"]))

    def test_hbm_local_and_relay_share_gpu_link(self):
        config = default_config("relay")
        config["fabric"]["hbm"]["hbm0"]["gpu_link"]["bandwidth_bytes_per_s"] = 1_000
        service = TopologyService(config)
        result = service.advance(
            0, W,
            {"hbf0": {"0": 100}, "hbm0": {"0": 100}},
            budgets(config), states(config),
        )
        # 20 bytes fit on the shared 1 kB/s HBM GPU link in 20 ms.
        self.assertEqual(
            result["resources"]["hbm0:gpu-link"]["used_work_units_scaled"], 19
        )
        total = (result["served_by_stack_channel"]["hbf0"]["0"]
                 + result["served_by_stack_channel"]["hbm0"]["0"])
        self.assertEqual(total, 19)
        self.assertGreater(result["served_by_stack_channel"]["hbf0"]["0"], 0)
        self.assertGreater(result["served_by_stack_channel"]["hbm0"]["0"], 0)

    def test_dash_same_channel_two_ready_blocks_use_unique_routes(self):
        config = default_config("dash")
        service = TopologyService(config)
        jobs = [
            {"job_id": "d", "stack": "hbf0", "channel": "0", "operation": "read",
             "route": "direct", "bytes": 1000, "arrival_ns": 0},
            {"job_id": "r", "stack": "hbf0", "channel": "0", "operation": "read",
             "route": "relay", "bytes": 1000, "arrival_ns": 0},
        ]
        result = service.advance(0, W, {}, budgets(config), states(config), jobs)
        self.assertEqual(set(result["completion_ids"]), {"d", "r"})
        phases = {(row["operation"], row["phase"]) for row in result["activities"]}
        self.assertIn(("read", "direct_gpu_link"), phases)
        self.assertIn(("read", "relay_send"), phases)
        progress = {row["job_id"]: row for row in result["job_progress"]}
        self.assertEqual(progress["d"]["remaining_bytes"], 0)
        self.assertEqual(progress["r"]["remaining_bytes"], 0)

    def test_partial_job_completion_id_is_unique(self):
        config = default_config("mixed_direct")
        config["channels"]["hbf0"]["0"] = 1_000
        service = TopologyService(config)
        job = {"job_id": "causal:1", "stack": "hbf0", "channel": "0",
               "operation": "read", "route": "direct", "bytes": 30, "arrival_ns": 0}
        first = service.advance(0, W, {}, budgets(config), states(config), [job])
        self.assertNotIn("causal:1", first["completion_ids"])
        row = next(row for row in first["job_progress"] if row["job_id"] == "causal:1")
        self.assertGreater(row["remaining_bytes"], 0)
        second = service.advance(W, 2 * W, {}, budgets(config), states(config))
        self.assertIn("causal:1", second["completion_ids"])
        third = service.advance(2 * W, 3 * W, {}, budgets(config), states(config))
        self.assertNotIn("causal:1", third["completion_ids"])

    def test_maintenance_severe_exempt_shutdown_blocked_then_recovers(self):
        config = default_config("mixed_direct")
        service = TopologyService(config)
        severe = states(config)
        severe["hbf0"] = "severe"
        jobs = [
            {"job_id": "fg", "stack": "hbf0", "channel": "0", "operation": "read",
             "route": "direct", "bytes": 10, "arrival_ns": 0},
            {"job_id": "m", "maintenance_id": "maint:m", "stack": "hbf0", "channel": "1",
             "operation": "program", "bytes": 10, "arrival_ns": 0},
        ]
        first = service.advance(0, W, {}, budgets(config), severe, jobs)
        self.assertEqual(next(row for row in first["job_progress"] if row["job_id"] == "fg")["admitted_this_window_bytes"], 0)
        self.assertIn("maint:m", first["maintenance_completion_ids"])

        shutdown = states(config)
        shutdown["hbf0"] = "shutdown"
        blocked_job = {"job_id": "blocked-maint", "maintenance_id": "maint:blocked",
                       "stack": "hbf0", "channel": "2", "operation": "erase",
                       "bytes": 10, "arrival_ns": W}
        second = service.advance(W, 2 * W, {}, budgets(config), shutdown, [blocked_job])
        self.assertNotIn("maint:blocked", second["maintenance_completion_ids"])
        normal = states(config)
        third = service.advance(2 * W, 3 * W, {}, budgets(config), normal)
        self.assertIn("maint:blocked", third["maintenance_completion_ids"])

    def test_operation_media_cost_separates_payload_from_work(self):
        config = default_config("mixed_direct")
        config["channels"]["hbf0"]["0"] = 1_000
        config["channels"]["hbf0"]["1"] = 1_000
        config["operation_media_cost"]["program"] = {"numerator": 10, "denominator": 1}
        service = TopologyService(config)
        jobs = [
            {"job_id": "read", "stack": "hbf0", "channel": "0", "operation": "read",
             "route": "direct", "bytes": 100, "arrival_ns": 0},
            {"job_id": "program", "maintenance_id": "maint:p", "stack": "hbf0",
             "channel": "1", "operation": "program", "bytes": 100, "arrival_ns": 0},
        ]
        result = service.advance(0, W, {}, budgets(config), states(config), jobs)
        progress = {row["job_id"]: row for row in result["job_progress"]}
        self.assertGreater(progress["read"]["admitted_this_window_bytes"],
                           progress["program"]["admitted_this_window_bytes"])
        resource = result["resources"]["hbf0:channel:0:media"]
        self.assertLessEqual(resource["used_work_units_scaled"],
                             resource["capacity_work_units_scaled"])
        self.assertTrue(any(row["phase"] == "media_program" for row in result["activities"]))

    def test_program_proxy_ratio_and_single_chunk_basic_fabric_crosscheck(self):
        self.assertEqual(
            media_cost_from_service_rate(96_000_000_000, 16 * 40_960_000),
            {"numerator": 9375, "denominator": 64},
        )
        config = default_config("relay")
        service = TopologyService(config)
        job = {"job_id": "relay", "stack": "hbf0", "channel": "0",
               "operation": "read", "route": "relay", "bytes": 100,
               "arrival_ns": 0, "metadata": {"tensor_id": "t0"}}
        receipt = service.advance(0, W, {}, budgets(config), states(config), [job])
        progress = next(row for row in receipt["job_progress"] if row["job_id"] == "relay")
        self.assertEqual(progress["metadata"], {"tensor_id": "t0"})

        fabric = BasicFabric(config["fabric"])
        fabric.enqueue("relay", "hbf0", "relay", 100, 0)
        fabric.advance(100)
        exact = fabric.completions()[0]["completion_ns"]
        # The topology service adds the explicit media phase; its remaining
        # one-chunk route agrees with BasicFabric's fill/relay/drain timing.
        media_ns = (100 * 1_000_000_000 + 96_000_000_000 - 1) // 96_000_000_000
        diagnostic = next(row for row in receipt["buffers"] if row["job_id"] == "relay")
        self.assertEqual(diagnostic["pipeline_estimated_completion_ns"], exact + media_ns)
        self.assertEqual(progress["completion_ns"], W)

    def test_completed_automatic_cohorts_are_retired(self):
        config = default_config("all_hbf_direct")
        service = TopologyService(config)
        for index in range(20):
            service.advance(index * W, (index + 1) * W,
                            {"hbf0": {"0": 1024}}, budgets(config), states(config))
        self.assertEqual(service._jobs, {})

    def test_invalid_or_duplicate_identity_rejected(self):
        config = default_config("dash")
        service = TopologyService(config)
        bad = {"job_id": "x", "stack": "hbf0", "channel": "0", "operation": "read",
               "route": "invalid", "bytes": 1, "arrival_ns": 0}
        with self.assertRaises(ValueError):
            service.advance(0, W, {}, budgets(config), states(config), [bad])


if __name__ == "__main__":
    unittest.main()

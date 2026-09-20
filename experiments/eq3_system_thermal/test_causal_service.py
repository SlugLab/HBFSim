#!/usr/bin/env python3
import unittest

from causal_service import CausalTopologyService
from topology_service import default_config, media_cost_from_service_rate


W = 20_000_000


def budgets(config, value=10**12):
    return {stack: value for stack in config["channels"]}


def states(config, value="normal"):
    return {stack: value for stack in config["channels"]}


def advance_next_completion(service):
    while True:
        horizon = service.next_completion_ns()
        if horizon is None:
            horizon = service.next_event_ns()
        receipt = service.advance_to(horizon)
        if receipt["completion_ids"]:
            return receipt
        if receipt.get("window_complete"):
            raise AssertionError("no completion within control window")


class CausalServiceTests(unittest.TestCase):
    def test_completion_and_dependency_chain_are_not_window_quantized(self):
        config = default_config("all_hbf_direct")
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([{
            "job_id": "layer0", "stack": "hbf0", "channel": "0", "route": "direct",
            "operation": "read", "bytes": 4096, "arrival_ns": 0,
            "metadata": {"tensor_id": "t0"},
        }])
        first = advance_next_completion(service)
        self.assertEqual(first["completion_ids"], ["layer0"])
        completion = next(row for row in first["job_progress"]
                          if row["job_id"] == "layer0")["completion_ns"]
        service.submit_jobs([{
            "job_id": "layer1", "stack": "hbf0", "channel": "1", "route": "direct",
            "operation": "read", "bytes": 4096, "arrival_ns": completion,
            "metadata": {"tensor_id": "t1"},
        }])
        second = advance_next_completion(service)
        self.assertEqual(second["completion_ids"], ["layer1"])

    def test_hbm_local_and_relay_share_exact_bandwidth(self):
        config = default_config("relay")
        config["fabric"]["hbm"]["hbm0"]["gpu_link"] = {
            "latency_ns": 0, "bandwidth_bytes_per_s": 1_000,
        }
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([
            {"job_id": "relay", "stack": "hbf0", "channel": "0", "route": "relay",
             "operation": "read", "bytes": 100, "arrival_ns": 0},
            {"job_id": "local", "stack": "hbm0", "channel": "0", "route": "direct",
             "operation": "read", "bytes": 100, "arrival_ns": 0},
        ])
        receipt = service.advance_to(W)
        progress = {row["job_id"]: row for row in receipt["job_progress"]}
        transferred = sum(progress[job]["inflight_transferred_bytes"]
                          for job in ("relay", "local"))
        self.assertLessEqual(transferred, 20)
        self.assertEqual(progress["relay"]["state"], "ACTIVE")
        self.assertEqual(progress["local"]["state"], "ACTIVE")

    def test_two_bank_analytical_occupancy_is_bounded_with_many_flows(self):
        config = default_config("dash")
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([
            {"job_id": "a", "stack": "hbf0", "channel": "0", "route": "direct",
             "operation": "read", "bytes": 10**9, "arrival_ns": 0},
            {"job_id": "b", "stack": "hbf0", "channel": "1", "route": "relay",
             "operation": "read", "bytes": 10**9, "arrival_ns": 0},
            {"job_id": "c", "stack": "hbf0", "channel": "2", "route": "direct",
             "operation": "read", "bytes": 10**9, "arrival_ns": 0},
        ])
        receipt = service.advance_to(1)
        bound = receipt["buffer_occupancy_bounds"]["hbf0"]
        self.assertLessEqual(
            bound["maximum_occupancy_bytes"],
            bound["bank_count"] * bound["bank_capacity_bytes"],
        )
        progress = {row["job_id"]: row for row in receipt["job_progress"]}
        self.assertEqual(sum(row["state"] == "ACTIVE" for row in progress.values()), 3)

    def test_future_quota_reserved_once_and_new_shutdown_work_blocked(self):
        config = default_config("mixed_direct")
        service = CausalTopologyService(config)
        b = budgets(config)
        b["hbf0"] = 100
        light = states(config)
        light["hbf0"] = "light"
        service.begin_window(0, W, b, light)
        service.submit_jobs([
            {"job_id": "a", "stack": "hbf0", "channel": "0", "operation": "read",
             "route": "direct", "bytes": 100, "arrival_ns": 0},
            {"job_id": "b", "stack": "hbf0", "channel": "1", "operation": "read",
             "route": "direct", "bytes": 100, "arrival_ns": 0},
        ])
        receipt = service.advance_to(W)
        self.assertEqual(receipt["endpoint_quota_remaining_scaled"]["hbf0"], 0)
        admitted = {row["job_id"]: row["unadmitted_bytes"] for row in receipt["job_progress"]}
        self.assertEqual(sum(100 - value for value in admitted.values()), 100)

        shutdown = states(config)
        shutdown["hbf0"] = "shutdown"
        service.begin_window(W, 2 * W, b, shutdown)
        service.submit_jobs([{
            "job_id": "new", "stack": "hbf0", "channel": "2", "operation": "read",
            "route": "direct", "bytes": 10, "arrival_ns": W,
        }])
        second = service.advance_to(2 * W)
        row = next(row for row in second["job_progress"] if row["job_id"] == "new")
        self.assertEqual(row["state"], "QUEUED")
        self.assertEqual(row["remaining_bytes"], 10)

    def test_maintenance_severe_allowed_shutdown_waits(self):
        config = default_config("mixed_direct")
        service = CausalTopologyService(config)
        severe = states(config)
        severe["hbf0"] = "severe"
        service.begin_window(0, W, budgets(config), severe)
        service.submit_jobs([{
            "job_id": "program", "maintenance_id": "program", "stack": "hbf0",
            "channel": "0", "operation": "program", "bytes": 4096, "arrival_ns": 0,
        }])
        receipt = service.advance_to(W)
        self.assertEqual(receipt["maintenance_completion_ids"], ["program"])

    def test_program_service_cost_reduces_rate_on_shared_media(self):
        config = default_config("mixed_direct")
        config["operation_media_cost"]["program"] = media_cost_from_service_rate(
            96_000_000_000, 16 * 40_960_000
        )
        progress = {}
        for operation in ("read", "program"):
            service = CausalTopologyService(config)
            service.begin_window(0, W, budgets(config), states(config))
            job = {"job_id": operation, "stack": "hbf0", "channel": "0",
                   "operation": operation, "bytes": 10**9, "arrival_ns": 0}
            if operation == "read":
                job["route"] = "direct"
            else:
                job["maintenance_id"] = operation
            service.submit_jobs([job])
            receipt = service.advance_to(1_000_000)
            row = next(row for row in receipt["job_progress"] if row["job_id"] == operation)
            progress[operation] = row["inflight_transferred_bytes"]
        read_progress = progress["read"]
        program_progress = progress["program"]
        self.assertGreater(read_progress, program_progress)

    def test_subwindow_advance_does_not_reset_endpoint_quota(self):
        config = default_config("mixed_direct")
        service = CausalTopologyService(config)
        limited = budgets(config)
        limited["hbf0"] = 100
        service.begin_window(0, W, limited, states(config))
        service.submit_jobs([{
            "job_id": "large", "stack": "hbf0", "channel": "0", "operation": "read",
            "route": "direct", "bytes": 1000, "arrival_ns": 0,
        }])
        first = service.advance_to(1)
        second = service.advance_to(2)
        self.assertEqual(first["endpoint_quota_remaining_scaled"]["hbf0"], 0)
        self.assertEqual(second["endpoint_quota_remaining_scaled"]["hbf0"], 0)
        row = next(row for row in second["job_progress"] if row["job_id"] == "large")
        self.assertEqual(row["unadmitted_bytes"], 900)

    def test_inflight_slice_drains_after_new_shutdown_state(self):
        config = default_config("all_hbf_direct")
        config["fabric"]["hbf"]["hbf0"]["direct_link"]["latency_ns"] = W + 10
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([{
            "job_id": "old", "stack": "hbf0", "channel": "0", "operation": "read",
            "route": "direct", "bytes": 1, "arrival_ns": 0,
        }])
        service.advance_to(W)
        shutdown = states(config)
        shutdown["hbf0"] = "shutdown"
        service.begin_window(W, 2 * W, budgets(config), shutdown)
        receipt = advance_next_completion(service)
        self.assertEqual(receipt["completion_ids"], ["old"])

    def test_many_channels_share_resources_without_bank_per_flow_cap(self):
        config = default_config("all_hbf_direct")
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([
            {"job_id": name, "stack": "hbf0", "channel": str(index),
             "operation": "read", "route": "direct", "bytes": 1, "arrival_ns": 0}
            for index, name in enumerate(("a", "b", "c"))
        ])
        first = service.advance_to(1)
        progress = {row["job_id"]: row for row in first["job_progress"]}
        self.assertNotIn("QUEUED", {row["state"] for row in progress.values()})

    def test_shutdown_maintenance_waits_then_resumes(self):
        config = default_config("mixed_direct")
        service = CausalTopologyService(config)
        shutdown = states(config)
        shutdown["hbf0"] = "shutdown"
        service.begin_window(0, W, budgets(config), shutdown)
        service.submit_jobs([{
            "job_id": "m", "maintenance_id": "m", "stack": "hbf0", "channel": "0",
            "operation": "erase", "bytes": 1, "arrival_ns": 0,
        }])
        blocked = service.advance_to(W)
        self.assertEqual(blocked["completion_ids"], [])
        service.begin_window(W, 2 * W, budgets(config), states(config))
        resumed = advance_next_completion(service)
        self.assertEqual(resumed["maintenance_completion_ids"], ["m"])

    def test_boundary_does_not_admit_with_expired_window_state(self):
        config = default_config("mixed_direct")
        service = CausalTopologyService(config)
        zero = budgets(config)
        zero["hbf0"] = 0
        service.begin_window(0, W, zero, states(config))
        service.submit_jobs([{
            "job_id": "boundary", "stack": "hbf0", "channel": "0",
            "operation": "read", "route": "direct", "bytes": 1, "arrival_ns": 0,
        }])
        first = service.advance_to(W)
        row = next(row for row in first["job_progress"] if row["job_id"] == "boundary")
        self.assertEqual(row["state"], "QUEUED")
        service.begin_window(W, 2 * W, budgets(config), states(config))
        second = advance_next_completion(service)
        self.assertEqual(second["completion_ids"], ["boundary"])

    def test_bank_turnover_does_not_repeat_pipeline_latency(self):
        config = default_config("all_hbf_direct")
        row = config["fabric"]["hbf"]["hbf0"]
        row["bank_capacity_bytes"] = 1
        row["fill"] = {"latency_ns": 10, "bandwidth_bytes_per_s": 1_000_000_000}
        row["direct_link"] = {"latency_ns": 10, "bandwidth_bytes_per_s": 1_000_000_000}
        config["channels"]["hbf0"]["0"] = 1_000_000_000
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([{
            "job_id": "turnover", "stack": "hbf0", "channel": "0",
            "operation": "read", "route": "direct", "bytes": 3, "arrival_ns": 0,
        }])
        receipt = advance_next_completion(service)
        row = next(row for row in receipt["job_progress"] if row["job_id"] == "turnover")
        self.assertEqual(row["completion_ns"], 23)

    def test_completion_id_emitted_once(self):
        config = default_config("all_hbf_direct")
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([{
            "job_id": "once", "stack": "hbf0", "channel": "0", "operation": "read",
            "route": "direct", "bytes": 1, "arrival_ns": 0,
        }])
        first = advance_next_completion(service)
        self.assertEqual(first["completion_ids"], ["once"])
        second = service.advance_to(W)
        self.assertEqual(second["completion_ids"], [])

    def test_hbm_fill_uses_shared_half_duplex_gpu_link_and_is_not_useful_read(self):
        config = default_config("relay")
        config["fabric"]["hbm"]["hbm0"]["gpu_link"] = {
            "latency_ns": 0, "bandwidth_bytes_per_s": 1_000,
        }
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([
            {"job_id": "fill", "stack": "hbm0", "channel": "0",
             "operation": "hbm_fill", "bytes": 100, "arrival_ns": 0},
            {"job_id": "read", "stack": "hbm0", "channel": "1", "route": "direct",
             "operation": "read", "bytes": 100, "arrival_ns": 0},
        ])
        receipt = service.advance_to(W)
        progress = {row["job_id"]: row for row in receipt["job_progress"]}
        transferred = sum(progress[job]["inflight_transferred_bytes"]
                          for job in ("fill", "read"))
        self.assertLessEqual(transferred, 20)
        fill_phases = {row["phase"] for row in receipt["activities"]
                       if row["operation"] == "hbm_fill"}
        self.assertEqual(fill_phases, {"media_fill", "hbm_base_fill", "gpu_link_fill"})

    def test_migration_program_uses_program_media_and_reverse_relay_path(self):
        config = default_config("relay")
        config["operation_media_cost"]["program"] = media_cost_from_service_rate(
            96_000_000_000, 16 * 40_960_000
        )
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([{
            "job_id": "move:program", "stack": "hbf0", "channel": "0",
            "operation": "migration_program", "route": "relay",
            "bytes": 4096, "arrival_ns": 0,
            "metadata": {"source_job_id": "move:read"},
        }])
        receipt = service.advance_to(W)
        phases = {row["phase"] for row in receipt["activities"]}
        self.assertEqual(phases, {
            "media_program", "destination_base", "destination_fill",
            "reverse_relay", "partner_gpu_receive",
        })
        self.assertEqual(receipt["completion_ids"], ["move:program"])

    def test_dash_route_children_share_one_explicit_media_group(self):
        config = default_config("dash")
        groups = {}
        for stack in config["fabric"]["hbf"]:
            config["channels"][stack] = {"direct": 1_000, "relay": 1_000}
            config["dash_routes"][stack] = {"direct": "direct", "relay": "relay"}
            row = {"resource_id": f"{stack}:uniform-media-group",
                   "bandwidth_bytes_per_s": 1_000}
            groups[stack] = {"direct": dict(row), "relay": dict(row)}
        for stack in config["fabric"]["hbm"]:
            config["channels"][stack] = {"local": 1_000}
            groups[stack] = {"local": {
                "resource_id": f"{stack}:uniform-media-group",
                "bandwidth_bytes_per_s": 1_000}}
        config["causal_channel_groups"] = groups
        service = CausalTopologyService(config)
        service.begin_window(0, W, budgets(config), states(config))
        service.submit_jobs([
            {"job_id": "direct", "stack": "hbf0", "channel": "direct",
             "operation": "read", "route": "direct", "bytes": 100, "arrival_ns": 0},
            {"job_id": "relay", "stack": "hbf0", "channel": "relay",
             "operation": "read", "route": "relay", "bytes": 100, "arrival_ns": 0},
        ])
        receipt = service.advance_to(W)
        progress = {row["job_id"]: row for row in receipt["job_progress"]}
        transferred = sum(row["inflight_transferred_bytes"] for row in progress.values())
        self.assertLessEqual(transferred, 20)
        self.assertIn("hbf0:uniform-media-group", receipt["resource_work_units_scaled"])


if __name__ == "__main__":
    unittest.main()

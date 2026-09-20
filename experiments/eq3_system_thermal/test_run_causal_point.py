#!/usr/bin/env python3
import io
import json
import unittest
from copy import deepcopy

from causal_service import CausalTopologyService
from causal_workload import TRACE_ORIGIN
from run_causal_point import CausalEnergyAdapter, execute, _useful_backlog
from topology_service import default_config


def normalized(config):
    rows = [{"id": "gpu", "role": "gpu", "device_id": "gpu"}]
    for stack in config["channels"]:
        rows.append({"id": stack + ".base", "role": "base", "device_id": stack})
        physical = "HBM3_PROXY" if stack.startswith("hbm") else "HBF_PROXY"
        die_count = 4 if stack.startswith("hbm") else 16
        for index in range(die_count):
            rows.append({"id": f"{stack}.die{index}", "role": "array_die",
                         "device_id": stack, "die_index": index,
                         "physical_type": physical})
    return {"components": rows}


def trace(index):
    arrival = index * 100
    return {
        "trace_origin": TRACE_ORIGIN, "model_id": "tiny", "embedding_access": "tiny",
        "prefetch_layers": 0, "batches": [{
            "interval_id": index, "arrival_ns": arrival,
            "terminal_task_id": f"c{index}", "token_ids": [f"t{index}"],
            "tasks": [
                {"task_id": f"r{index}", "type": "storage",
                 "tensor": {"tensor_id": "weight", "bytes": 4096},
                 "issue_after": [], "consume_after": [], "consumer_count": 1,
                 "batch_interval_id": index},
                {"task_id": f"c{index}", "type": "compute", "duration_ns": 5,
                 "depends_on": [f"r{index}"], "is_token_terminal": True,
                 "batch_interval_id": index},
            ],
        }],
    }


class FakeThermal:
    def __init__(self, stacks, baseline):
        self.stacks = stacks
        self.baseline = baseline
        self.total = 0.0

    def advance(self, start, end, energy):
        self.total += sum(energy.values())
        entities = {
            f"{stack}.die{index}": {"hotspot_k": 300.0, "mean_k": 300.0}
            for stack in self.stacks if stack.startswith("hbf") for index in range(16)
        }
        return {
            "start_ns": start, "end_ns": end,
            "temperatures": {**{stack: 300.0 for stack in self.stacks}, "gpu": 300.0},
            "stack_states": {stack: "normal" for stack in self.stacks},
            "hysteresis_budget_bytes": dict(self.baseline),
            "energy_j": {"cumulative": {"total_input_j": self.total}},
            "entity_temperatures_k": entities,
        }


def energy_profile():
    return {
        "read_array_j_per_byte": 40e-12, "read_base_j_per_byte": 10e-12,
        "program_array_j_per_byte": 1e-9, "program_base_j_per_byte": 2e-9,
        "hbm_array_j_per_byte": 40e-12, "hbm_base_j_per_byte": 2e-12,
        "hbm_fill_array_j_per_byte": 40e-12, "hbm_fill_base_j_per_byte": 2e-12,
        "relay_receive_j_per_byte": 2e-12, "relay_send_j_per_byte": 2e-12,
        "erase_j_per_block": 50e-6, "erase_block_bytes": 1_048_576,
    }


class RunnerTests(unittest.TestCase):
    def test_effective_backlog_keeps_finished_sibling_until_group_retry_completes(self):
        self.assertEqual(_useful_backlog(8192, 0, 4096), 12288)
        self.assertEqual(_useful_backlog(8192, 8192, 4096), 4096)
        with self.assertRaises(AssertionError):
            _useful_backlog(4096, 8192, 0)

    def test_two_batches_close_exact_service_compute_energy_and_control_loop(self):
        service = default_config("all_hbf_direct")
        baseline = {stack: sum(channels.values()) * 20_000_000 // 10**9
                    for stack, channels in service["channels"].items()}
        config = {
            "point_id": "tiny", "active_ns": 20_000_000, "recovery_ns": 0,
            "window_ns": 20_000_000, "strategy": "guard_only",
            "service": service, "energy": energy_profile(),
            "gpu_compute_w": 100.0, "gpu_external_w": 10.0,
            "target_bytes_per_s_by_stack": {stack: 1 for stack in service["channels"]},
            "trace": {"total_batches": 2, "max_active_batches": 2,
                      "batch_interval_ns": 100},
            "executor": {"cache_mode": "disabled", "cache_capacity_bytes": 0,
                         "coalescing_enabled": True,
                         "prefetch_wait_mode": "wait_at_consumption",
                         "stripe_unit_bytes": 4096, "migration_mode": "fixed",
                         "retry_count_per_source_read": 1,
                         "stripe_targets": [{"stack": "hbf0", "channel": "0",
                                             "route": "direct"}]},
        }
        sink = io.StringIO()
        summary = execute(config, normalized(service),
                          FakeThermal(sorted(service["channels"]), baseline), sink,
                          initial_trace=trace(0), trace_factory=trace)
        self.assertEqual(summary["completed_tokens"], 2)
        self.assertEqual(summary["pending_external_jobs"], 0)
        self.assertEqual(summary["uninstantiated_batches"], 0)
        row = json.loads(sink.getvalue())
        self.assertEqual(row["executor"]["completed_tokens"], 2)
        self.assertGreater(row["energy"]["scope_energy_j"]["gpu:causal_compute"], 0)
        # Two logical consumers coalesce into one source read; its configured
        # retry is physical traffic and does not create useful bytes.
        self.assertEqual(len(row["service"]["completions"]), 2)
        self.assertEqual(summary["offered_useful_bytes_by_stack"]["hbf0"], 4096)
        self.assertEqual(summary["delivered_useful_bytes_by_stack"]["hbf0"], 4096)
        media_bytes = sum(item["bytes"] for item in row["service"]["activities"]
                          if item["phase"] == "media_read")
        self.assertEqual(media_bytes, 8192)
        ids = [item["job_id"] for item in row["service"]["changed_job_progress"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_energy_adapter_counts_only_disjoint_phases_and_prorates_erase(self):
        service = default_config("relay")
        adapter = CausalEnergyAdapter(normalized(service), service, energy_profile())
        rows = [
            {"operation": "hbm_fill", "phase": "media_fill", "stack": "hbm0",
             "channel": "0", "bytes": 100},
            {"operation": "hbm_fill", "phase": "hbm_base_fill", "stack": "hbm0",
             "channel": "0", "bytes": 100},
            {"operation": "migration_program", "phase": "reverse_relay", "stack": "hbf0",
             "partner": "hbm0", "channel": "0", "bytes": 100},
            {"operation": "migration_program", "phase": "partner_gpu_receive", "stack": "hbf0",
             "partner": "hbm0", "channel": "0", "bytes": 100},
            {"operation": "erase", "phase": "media_erase", "stack": "hbf0",
             "channel": "0", "bytes": 524_288},
        ]
        result = adapter.map(rows)
        self.assertAlmostEqual(result["scope_energy_j"]["hbm_fill:array_uniform"], 4e-9)
        self.assertAlmostEqual(result["scope_energy_j"]["hbm_fill:base"], .2e-9)
        self.assertAlmostEqual(result["scope_energy_j"]["migration:reverse_relay"], .2e-9)
        self.assertAlmostEqual(result["scope_energy_j"]["migration:partner_gpu_receive"], .2e-9)
        self.assertAlmostEqual(result["scope_energy_j"]["erase:array_prorated"], 25e-6)

    def test_foreground_and_due_maintenance_share_one_exact_service(self):
        service = default_config("mixed_direct")
        baseline = {stack: sum(channels.values()) * 20_000_000 // 10**9
                    for stack, channels in service["channels"].items()}
        config = {
            "point_id": "shared-maint", "active_ns": 20_000_000, "recovery_ns": 0,
            "window_ns": 20_000_000, "strategy": "guard_only",
            "service": service, "energy": energy_profile(),
            "gpu_compute_w": 0.0, "gpu_external_w": 0.0,
            "target_bytes_per_s_by_stack": {stack: 1 for stack in service["channels"]},
            "trace": {"total_batches": 1, "max_active_batches": 1,
                      "batch_interval_ns": 100},
            "executor": {"cache_mode": "disabled", "cache_capacity_bytes": 0,
                         "coalescing_enabled": True,
                         "prefetch_wait_mode": "wait_at_consumption",
                         "stripe_unit_bytes": 4096, "migration_mode": "fixed",
                         "stripe_targets": [{"stack": "hbf0", "channel": "0",
                                             "route": "direct"}]},
            "maintenance": {
                "mode": "shared", "ea_ev": 1.04, "refresh_trigger": "wall_only",
                "initial_equivalent_age_ns": 0,
                "initial_wall_age_ns": 86_400_000_000_000,
                "initial_temperature_k": 300.0, "block_bytes": 4096,
                "pages_per_block": 1, "aged_blocks_per_stack": 16,
                "spares_per_channel": 1, "max_blocks_per_cohort": 1,
                "program_energy_j_per_byte": None,
                "erase_energy_j_per_operation": None,
                "energy_evidence": {"program": "TEST_PROXY", "erase": "TEST_PROXY"},
            },
        }
        sink = io.StringIO()
        summary = execute(config, normalized(service),
                          FakeThermal(sorted(service["channels"]), baseline), sink,
                          initial_trace=trace(0), trace_factory=trace)
        terminal = summary["maintenance"]["driver"]["terminal_summary"]
        self.assertEqual(summary["completed_tokens"], 1)
        self.assertEqual(terminal["extent_count"], 64)
        self.assertEqual(terminal["status_counts"], {"COMMITTED": 64})
        row = json.loads(sink.getvalue())
        operations = {item["operation"] for item in row["service"]["completions"]}
        self.assertTrue({"read", "refresh_read", "program", "erase"}.issubset(operations))
        self.assertEqual(row["maintenance"]["window_age_finish"]["temperature_semantics"],
                         "PREVIOUS_KNOWN_WINDOW_TEMPERATURE_PIECEWISE_CONSTANT_NO_RETROACTIVE_REAGE")

    def test_uniform_16_channel_group_matches_physical_bytes_capacity_and_energy(self):
        physical = default_config("all_hbf_direct")
        grouped = deepcopy(physical)
        groups = {}
        for stack in grouped["channels"]:
            grouped["channels"][stack] = {"group": 16 * 96_000_000_000}
            groups[stack] = {"group": {
                "resource_id": f"{stack}:uniform-16ch-media",
                "bandwidth_bytes_per_s": 16 * 96_000_000_000}}
        grouped["causal_channel_groups"] = groups
        receipts = []
        for config, jobs in (
            (physical, [{"job_id": f"p{i}", "stack": "hbf0", "channel": str(i),
                         "operation": "read", "route": "direct", "bytes": 1000,
                         "arrival_ns": 0} for i in range(16)]),
            (grouped, [{"job_id": "g", "stack": "hbf0", "channel": "group",
                        "operation": "read", "route": "direct", "bytes": 16000,
                        "arrival_ns": 0}]),
        ):
            service = CausalTopologyService(config)
            service.begin_window(0, 20_000_000,
                                 {stack: 10**9 for stack in config["channels"]},
                                 {stack: "normal" for stack in config["channels"]})
            service.submit_jobs(jobs)
            receipts.append(service.advance_to(20_000_000))
        self.assertEqual([sum(row["bytes"] for row in receipt["activities"]
                              if row["phase"] == "media_read") for receipt in receipts],
                         [16000, 16000])
        profile = energy_profile()
        physical_energy = CausalEnergyAdapter(
            normalized(physical), physical, profile).map(receipts[0]["activities"])
        group_energy = CausalEnergyAdapter(
            normalized(grouped), grouped, profile,
            {"mode": "uniform_stack_group_16ch", "physical_channels_per_stack": 16}
        ).map(receipts[1]["activities"])
        self.assertEqual(set(physical_energy["component_energy_j"]),
                         set(group_energy["component_energy_j"]))
        for owner, value in physical_energy["component_energy_j"].items():
            self.assertAlmostEqual(value, group_energy["component_energy_j"][owner])


if __name__ == "__main__":
    unittest.main()

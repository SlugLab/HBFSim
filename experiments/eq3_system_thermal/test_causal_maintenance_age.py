import unittest

from causal_maintenance_age import CausalMaintenanceAgeAdapter
from causal_service import CausalTopologyService
from maintenance_driver import MaintenanceDriver
from reliability import DAY_NS, ReliabilityLedger
from topology_service import default_config


W = 20_000_000


class CausalMaintenanceAgeTests(unittest.TestCase):
    def test_exact_completion_commit_then_window_tail_age(self):
        config = default_config("mixed_direct")
        service = CausalTopologyService(config)
        ledger = ReliabilityLedger({"ea_ev": 1.04,
                                    "refresh_trigger": "equivalent_age_or_wall",
                                    "initial_equivalent_age_ns": DAY_NS})
        driver = MaintenanceDriver(ledger, {
            "block_bytes": 4096, "pages_per_block": 1, "max_blocks_per_cohort": 1,
            "spare_block_ids_by_stack_channel": {"hbf0": {"0": ["spare0"]}},
            "program_energy_j_per_byte": 0.0, "erase_energy_j_per_operation": 0.0,
            "energy_evidence": {"program": "FIXED_TEST", "erase": "FIXED_TEST"},
        })
        driver.register_extents([{
            "extent_id": "extent0", "stack": "hbf0", "channel": "0",
            "source_block_id": "source0", "version": 0,
        }])
        temperatures = {"hbf0": {"0": 358.15}}
        age = CausalMaintenanceAgeAdapter(ledger, driver, temperatures)
        budgets = {stack: 10**12 for stack in config["channels"]}
        states = {stack: "normal" for stack in config["channels"]}
        service.begin_window(0, W, budgets, states)
        service.submit_jobs(age.start_window(0))
        while service.now_ns < W:
            horizon = service.next_event_ns()
            receipt = service.advance_to(horizon)
            delta = age.consume_receipt(receipt)
            if delta["next_phase_jobs"] and service.now_ns < W:
                service.submit_jobs(delta["next_phase_jobs"])
        age.finish_window(W, {"hbf0": {"0": 360.0}})
        state = ledger.snapshot()["blocks"]["extent0"]
        self.assertIsNotNone(state["last_refresh_commit_ns"])
        self.assertLess(state["last_refresh_commit_ns"], W)
        self.assertGreater(state["equivalent_age_ns"], 0)
        self.assertEqual(state["last_update_ns"], W)
        self.assertEqual(driver.snapshot()["terminal_summary"]["status_counts"],
                         {"COMMITTED": 1})

    def test_rejects_missing_temperature_identity(self):
        ledger = ReliabilityLedger({"ea_ev": 1.04})
        driver = MaintenanceDriver(ledger, {
            "block_bytes": 4096, "pages_per_block": 1, "max_blocks_per_cohort": 1,
            "spare_block_ids_by_stack_channel": {"hbf0": {"0": ["spare0"]}},
            "program_energy_j_per_byte": None, "erase_energy_j_per_operation": None,
            "energy_evidence": {"program": "UNKNOWN", "erase": "UNKNOWN"},
        })
        driver.register_extents([{"extent_id": "extent0", "stack": "hbf0",
                                  "channel": "0", "source_block_id": "source0",
                                  "version": 0}])
        with self.assertRaisesRegex(ValueError, "cover"):
            CausalMaintenanceAgeAdapter(ledger, driver, {})


if __name__ == "__main__":
    unittest.main()

import unittest

from maintenance_driver import MaintenanceDriver
from reliability import DAY_NS, ReliabilityLedger
from topology_service import TopologyService, default_config


W = 20_000_000


def driver_config(spares):
    return {
        "block_bytes": 4096,
        "pages_per_block": 256,
        "max_blocks_per_cohort": 8,
        "spare_block_ids_by_stack_channel": {"hbf0": {"0": spares}},
        "program_energy_j_per_byte": 0.05 * 100e-6 / 4096,
        "erase_energy_j_per_operation": 0.05 * 1e-3,
        "energy_evidence": {
            "program": "DERIVED_ENGINEERING_PROXY_0.05W_100US_NOT_HBF_CALIBRATION",
            "erase": "DERIVED_ENGINEERING_PROXY_0.05W_NATIVE_ADAPTER_1MS_NOT_HBF_CALIBRATION",
        },
    }


def budgets(config):
    return {stack: 10**15 for stack in config["channels"]}


def states(config):
    return {stack: "normal" for stack in config["channels"]}


class Harness:
    def __init__(self, extent_count=1, spare_count=2):
        self.config = default_config("mixed_direct")
        self.service = TopologyService(self.config)
        self.ledger = ReliabilityLedger({"ea_ev": 1.04,
                                         "refresh_trigger": "equivalent_age_or_wall",
                                         "initial_equivalent_age_ns": DAY_NS})
        self.driver = MaintenanceDriver(
            self.ledger, driver_config([f"spare{i}" for i in range(spare_count)]))
        self.extents = [f"extent{i}" for i in range(extent_count)]
        self.driver.register_extents([
            {"extent_id": extent, "stack": "hbf0", "channel": "0",
             "source_block_id": f"source{i}", "version": 0}
            for i, extent in enumerate(self.extents)
        ])
        self.now = 0

    def window(self, jobs, failed=()):
        start, end = self.now, self.now + W
        receipt = self.service.advance(start, end, {}, budgets(self.config),
                                       states(self.config), jobs)
        for extent in self.extents:
            self.ledger.advance_temperature(extent, start, end, 358.15)
        delta = self.driver.consume_receipt(receipt, failed_job_ids=failed)
        self.now = end
        return receipt, delta


class MaintenanceDriverTests(unittest.TestCase):
    def test_equal_near_due_initial_age_reaches_policy_without_time_compression(self):
        ledger = ReliabilityLedger({"ea_ev": 1.04,
                                    "refresh_trigger": "equivalent_age_or_wall",
                                    "initial_equivalent_age_ns": DAY_NS - W})
        driver = MaintenanceDriver(ledger, driver_config(["spare0", "spare1"]))
        driver.register_extents([
            {"extent_id": f"extent{i}", "stack": "hbf0", "channel": "0",
             "source_block_id": f"source{i}", "version": 0}
            for i in range(2)
        ])
        self.assertEqual(driver.poll(0), [])
        for extent in ("extent0", "extent1"):
            ledger.advance_temperature(extent, 0, W, 358.15)
        jobs = driver.poll(W)
        self.assertEqual(jobs[0]["metadata"]["extent_ids"], ["extent0", "extent1"])
        self.assertEqual(jobs[0]["metadata"]["block_count"], 2)

    def test_actual_service_read_program_commit_erase_and_energy(self):
        h = Harness()
        read = h.driver.poll(0)
        self.assertEqual(read[0]["operation"], "refresh_read")
        h.window(read)
        program = h.driver.poll(h.now)
        self.assertEqual(program[0]["operation"], "program")
        _, program_delta = h.window(program)
        self.assertEqual(program_delta["summary"]["outstanding_job_count"], 0)
        self.assertTrue(program_delta["energy_facts"])
        # Age resets at program/version commit, before old-source erase.
        self.assertEqual(h.ledger.snapshot()["blocks"]["extent0"]["equivalent_age_ns"], 0)
        erase = h.driver.poll(h.now)
        self.assertEqual(erase[0]["operation"], "erase")
        self.assertEqual(erase[0]["metadata"]["physical_block_ids"], ["source0"])
        _, erase_delta = h.window(erase)
        self.assertEqual(erase_delta["summary"]["committed_extent_count"], 1)
        self.assertEqual(h.driver.drain_delta()["events"], [])

        result = h.driver.snapshot()
        self.assertEqual(result["terminal_summary"]["status_counts"], {"COMMITTED": 1})
        self.assertEqual(result["extents"]["extent0"]["physical_block_id"], "spare0")
        self.assertEqual(result["physical_wear"]["spare0"]["block_program_work_completed"], 1)
        self.assertEqual(result["physical_wear"]["spare0"]["nand_page_programs_completed"], 256)
        self.assertEqual(result["physical_wear"]["source0"]["erase_completed"], 1)
        self.assertIn("source0", result["free_spares_by_stack_channel"]["hbf0"]["0"])
        energies = {row["phase"]: row["energy_j"]
                    for row in program_delta["energy_facts"] + erase_delta["energy_facts"]}
        self.assertAlmostEqual(energies["program"], 5e-6)
        self.assertAlmostEqual(energies["erase_old"], 50e-6)

    def test_version_conflict_keeps_age_and_reclaims_destination(self):
        h = Harness()
        h.window(h.driver.poll(0))
        h.driver.record_foreground_write("extent0", h.now)
        h.window(h.driver.poll(h.now))
        age_after_program = h.ledger.snapshot()["blocks"]["extent0"]["equivalent_age_ns"]
        self.assertGreater(age_after_program, DAY_NS)
        cleanup = h.driver.poll(h.now)
        self.assertEqual(cleanup[0]["metadata"]["phase"], "erase_cleanup")
        self.assertEqual(cleanup[0]["metadata"]["physical_block_ids"], ["spare0"])
        h.window(cleanup)
        result = h.driver.snapshot()
        self.assertEqual(result["terminal_summary"]["status_counts"],
                         {"FAILED_OR_VERSION_CONFLICT": 1})
        self.assertEqual(result["extents"]["extent0"]["physical_block_id"], "source0")
        self.assertIn("spare0", result["free_spares_by_stack_channel"]["hbf0"]["0"])

    def test_program_failure_retains_age_and_spare_is_bounded(self):
        h = Harness(extent_count=2, spare_count=1)
        read = h.driver.poll(0)
        self.assertEqual(read[0]["metadata"]["block_count"], 1)
        self.assertEqual(len(h.driver.active_extents), 1)
        h.window(read)
        program = h.driver.poll(h.now)
        h.window(program, failed={program[0]["job_id"]})
        self.assertGreater(h.ledger.snapshot()["blocks"]["extent0"]["equivalent_age_ns"], DAY_NS)
        cleanup = h.driver.poll(h.now)
        h.window(cleanup)
        result = h.driver.snapshot()
        self.assertEqual(result["terminal_summary"]["status_counts"],
                         {"FAILED_OR_VERSION_CONFLICT": 1})
        self.assertEqual(result["physical_wear"]["spare0"]["nand_page_programs_started"], 256)
        self.assertEqual(result["physical_wear"]["spare0"]["nand_page_programs_completed"], 0)
        self.assertIn("spare0", result["free_spares_by_stack_channel"]["hbf0"]["0"])
        # The second due extent can now claim the single recovered spare.
        next_read = h.driver.poll(h.now)
        self.assertEqual(next_read[0]["metadata"]["extent_ids"], ["extent1"])


if __name__ == "__main__":
    unittest.main()

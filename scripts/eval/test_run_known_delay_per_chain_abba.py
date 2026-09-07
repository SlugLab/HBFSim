import copy
import pathlib
import sys
import unittest


EVAL_DIR = pathlib.Path(__file__).resolve().parent
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import run_known_delay_per_chain_abba as abba


class KnownDelayPerChainAbbaTests(unittest.TestCase):
    IDENTITY = {
        "module_handle": 101,
        "context_handle": 102,
        "input_address": 4096,
        "chain_output_address": 20_000_000,
        "block_output_address": 21_000_000,
        "storage_address": 22_000_000,
        "config_symbol_address": 23_000_000,
    }

    def launch(self, index, delay, epoch, chain_duration, event_ns):
        chain_begin = 100
        chain_end = chain_begin + chain_duration
        block_begin = 90
        block_end = chain_end + 10
        chain_address = self.IDENTITY["chain_output_address"]
        block_address = self.IDENTITY["block_output_address"]

        def event(address, bytes_, operation, event_class, timestamp,
                  end_timestamp=None):
            return {
                "thread_id": 0,
                "address": address,
                "order": 0,
                "begin_ns": timestamp,
                "end_ns": timestamp if end_timestamp is None else end_timestamp,
                "bytes": bytes_,
                "operation": operation,
                "event_class": event_class,
                "status": 1,
            }

        events = [
            event(block_address, 8, 1, 3, block_begin),
            event(block_address + 16, 8, 1, 3, block_begin + 1),
            event(self.IDENTITY["input_address"], 4, 0, 1,
                  chain_begin, chain_begin + delay),
        ]
        events.extend(
            event(chain_address + offset, 8, 1, 2, chain_end + slot)
            for slot, offset in enumerate((0, 8, 16, 24))
        )
        events.append(event(block_address + 8, 8, 1, 3, block_end + 3))
        for order, item in enumerate(events):
            item["order"] = order

        wait = {
            "thread_id": 0,
            "address": self.IDENTITY["input_address"],
            "wait_enter_ns": chain_begin,
            "wait_exit_ns": chain_begin + delay,
            "delay_ns": delay,
        }
        row = {
            "row": 0,
            "covered_accesses": 1,
            "covered_bytes": 4,
            "bypass_accesses": 7,
            "bypass_bytes": 56,
            "rejected_accesses": 0,
            "trace_overflow": 0,
            "event_count": 8,
            "launch_epoch": epoch,
            "writer_thread_id": 0,
            "writer_observed": 1,
            "reserved": 0,
            "unused_slots_zero": True,
            "events": events,
        }
        diagnostic = {
            "enabled": True,
            "magic": 0x4556434841494E31,
            "symbol_bytes": 136,
            "version": 2,
            "config_bytes": 136,
            "delay_ns": delay,
            "launch_epoch": epoch,
            "grid_x": 1,
            "grid_y": 1,
            "grid_z": 1,
            "block_x": 32,
            "block_y": 1,
            "block_z": 1,
            "warps_per_block": 1,
            "hops": 1,
            "row_count": 1,
            "row_stride": 576,
            "trace_capacity": 8,
            "storage_address": self.IDENTITY["storage_address"],
            "storage_bytes": 576,
            "chain_output_address": chain_address,
            "chain_output_bytes": 32,
            "block_output_address": block_address,
            "block_output_bytes": 24,
            "rows": [row],
        }
        return {
            "launch_index": index,
            "label": "A_D0" if delay == 0 else "B_D500",
            "config_readback_before_launch_exact": True,
            "config_readback_after_launch_exact": True,
            "shared_runtime_identity": copy.deepcopy(self.IDENTITY),
            "schema_version": 1,
            "evidence": "GPU_ACQUISITION",
            "treatment": "hbf_logical",
            "trace_mode": "per_chain",
            "requested_delay_ns": delay,
            "applied_delay_ns": delay,
            "hops": 1,
            "warps": 1,
            "occupancy": "low",
            "blocks": 1,
            "sm_count": 1,
            "registers": 20,
            "theoretical_blocks_per_sm": 1,
            "dynamic_shared_bytes": 49_152,
            "event_ns": event_ns,
            "covered_accesses": 1,
            "covered_bytes": 4,
            "bypass_accesses": 7,
            "bypass_bytes": 56,
            "rejected_accesses": 0,
            "trace_overflow": 0,
            "rewritten_instructions": 1,
            "unsupported_instructions": 0,
            "unknown_bytes": 0,
            "eligible_bytes": 4,
            "seed": 0,
            "permutation_rule": "next=(17*i+1)%4096; stride=4096 bytes; version=1",
            "input_base": self.IDENTITY["input_address"],
            "input_bytes": 4096 * 4096,
            "chains": [{
                "block": 0,
                "warp": 0,
                "sm": 0,
                "begin_ns": chain_begin,
                "end_ns": chain_end,
                "checksum": 77,
                "expected_checksum": 77,
            }],
            "block_intervals": [{
                "block": 0,
                "sm": 0,
                "begin_ns": block_begin,
                "end_ns": block_end,
            }],
            "waits": [wait],
            "chain_diagnostic": diagnostic,
            "validation": "PASS",
        }

    def fixture(self):
        launches = [
            self.launch(0, 0, 2, 1000, 2000),
            self.launch(1, 500, 3, 900, 1800),
            self.launch(2, 500, 4, 1200, 2100),
            self.launch(3, 0, 5, 1300, 2400),
        ]
        return {
            "schema_version": 1,
            "evidence": "GPU_ACQUISITION",
            "validation_status": "UNVALIDATED",
            "scientific_claim": False,
            "g2_gate_closed": False,
            "trace_mode": "per_chain_abba",
            "treatment": "hbf_logical",
            "diagnostic_blocks_requested": 1,
            "actual_grid_blocks": 1,
            "actual_chain_rows": 1,
            "actual_events_per_row": 8,
            "warmup_delay_ns": 500,
            "sequence_delay_ns": [0, 500, 500, 0],
            "module_load_count": 1,
            "context_create_count": 1,
            "shared_runtime_identity": copy.deepcopy(self.IDENTITY),
            "launches": launches,
        }

    def two_block_fixture(self):
        raw = self.fixture()
        for key in ("diagnostic_blocks_requested", "actual_grid_blocks", "actual_chain_rows"):
            raw[key] = 2
        for launch in raw["launches"]:
            launch["blocks"] = launch["sm_count"] = 2
            for key in ("covered_accesses", "covered_bytes", "bypass_accesses", "bypass_bytes", "eligible_bytes"):
                launch[key] *= 2
            diagnostic = launch["chain_diagnostic"]
            for key in ("grid_x", "row_count", "storage_bytes", "chain_output_bytes", "block_output_bytes"):
                diagnostic[key] *= 2
            for collection in ("chains", "block_intervals"):
                extra = copy.deepcopy(launch[collection][0])
                extra["block"] = extra["sm"] = 1
                launch[collection].append(extra)
            wait = copy.deepcopy(launch["waits"][0])
            wait["thread_id"] = 32
            wait["address"] += 4096
            launch["waits"].append(wait)
            row = copy.deepcopy(diagnostic["rows"][0])
            row["row"] = 1
            row["writer_thread_id"] = 32
            for event in row["events"]:
                event["thread_id"] = 32
                event["address"] += {1: 4096, 2: 32, 3: 24}[event["event_class"]]
            diagnostic["rows"].append(row)
        return raw

    def test_two_blocks_preserve_every_chain_and_reject_partial_geometry(self):
        raw = self.two_block_fixture()
        report = abba.analyze_abba(raw, expected_blocks=2)
        self.assertEqual(report["diagnostic_geometry"]["actual_chain_rows"], 2)
        for pair in report["pairs"]:
            self.assertEqual([r["thread_id"] for r in pair["per_chain"]], [0, 32])
        with self.assertRaises(ValueError):
            abba.analyze_abba(raw, expected_blocks=1)
        for missing in ("chains", "waits", "block_intervals"):
            invalid = copy.deepcopy(raw)
            invalid["launches"][1][missing].pop()
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                abba.analyze_abba(invalid, expected_blocks=2)
        for unsupported in (0, 3, True):
            with self.subTest(blocks=unsupported), self.assertRaises(ValueError):
                abba.analyze_abba(raw, expected_blocks=unsupported)

    def test_validates_actual_rows_and_retains_signed_adjacent_pairs(self):
        report = abba.analyze_abba(self.fixture())
        self.assertEqual(report["validation_status"], "CAPTURED_UNVALIDATED")
        self.assertFalse(report["g2_gate_closed"])
        self.assertEqual(report["diagnostic_geometry"], {
            "diagnostic_blocks_requested": 1, "actual_grid_blocks": 1,
            "actual_chain_rows": 1, "actual_events_per_row": 8,
        })
        self.assertEqual(
            [pair["signed_event_delta_ns"] for pair in report["pairs"]],
            [-200, -300],
        )
        self.assertEqual(
            [pair["per_chain"][0]["signed_chain_delta_ns"]
             for pair in report["pairs"]],
            [-100, -100],
        )
        self.assertEqual(
            [pair["per_chain"][0]["signed_wait_delta_ns"]
             for pair in report["pairs"]],
            [500, 500],
        )

    def test_rejects_order_epoch_residual_missing_and_producer_pairs(self):
        mutations = {
            "nan-event": lambda raw: raw["launches"][0].__setitem__("event_ns", float("nan")),
            "infinite-event": lambda raw: raw["launches"][1].__setitem__("event_ns", float("inf")),
            "order": lambda raw: raw["launches"][1].__setitem__(
                "label", "A_D0"),
            "epoch": lambda raw: raw["launches"][1]["chain_diagnostic"].__setitem__(
                "launch_epoch", 2),
            "residual": lambda raw: raw["launches"][2]["chain_diagnostic"]
                ["rows"][0].__setitem__("unused_slots_zero", False),
            "missing": lambda raw: raw["launches"].pop(),
            "producer-pairs": lambda raw: raw.__setitem__("pairs", [{"delta": 0}]),
            "identity": lambda raw: raw["launches"][3]["shared_runtime_identity"]
                .__setitem__("module_handle", 999),
            "grid-marker": lambda raw: raw.__setitem__(
                "actual_grid_blocks", 2),
            "launch-grid": lambda raw: raw["launches"][2].__setitem__(
                "blocks", 2),
        }
        for name, mutate in mutations.items():
            raw = self.fixture()
            mutate(raw)
            with self.subTest(name=name), self.assertRaises(ValueError):
                abba.analyze_abba(raw)


if __name__ == "__main__":
    unittest.main()

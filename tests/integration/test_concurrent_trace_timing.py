#!/usr/bin/env python3
"""CPU MQSim controls; fixtures are not physical measurements or serving traces."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BINARY = Path(sys.argv.pop(1)).resolve()
ROOT = Path(__file__).resolve().parents[2]


class ConcurrentReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.profile = json.loads((ROOT / "configs/profiles/nominal.json").read_text())
        self.profile.update(capacity_bytes=16 << 30, hbm_cache_bytes=64 << 20,
                            queue_depth=2, time_scale=1)
        self.events = [dict(request_id=i + 1, issue_ns=0 if i < 4 else 15000,
                            consume_deadline=1_000_000_000 if i == 0 else 50000,
                            logical_address=i * 16384, bytes=16384, operation="read",
                            layer=i // 2, step=0, sequence=i % 2,
                            resource="mqsim_media", channel="profile")
                       for i in range(6)]

    def invoke(self, *, expect_success=True, extra=()):
        profile = self.directory / "profile.json"
        events = self.directory / "events.jsonl"
        output = self.directory / "result.json"
        profile.write_text(json.dumps(self.profile))
        events.write_text("".join(json.dumps(row) + "\n" for row in self.events))
        output.unlink(missing_ok=True)
        process = subprocess.run([str(BINARY), "--profile", str(profile), "--events", str(events),
                                  "--output", str(output), *extra], capture_output=True,
                                 text=True, timeout=30)
        if expect_success:
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(output.read_text())
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(output.exists(), "failed input published a success artifact")
        return process

    def test_concurrent_arrivals_and_conservation(self):
        result = self.invoke()
        self.assertEqual(result["service_source"], "MQSIM_SIMULATED")
        self.assertEqual(result["provenance"], "PROJECTED")
        self.assertEqual(result["summary"]["issued"], 6)
        self.assertEqual(result["summary"]["completed"], 6)
        self.assertEqual(result["summary"]["completed_bytes"], 6 * 16384)
        self.assertEqual(result["summary"]["peak_device_qd"], 2)
        by_id = {row["request_id"]: row for row in result["requests"]}
        self.assertEqual(set(by_id), set(range(1, 7)))
        for source in self.events:
            row = by_id[source["request_id"]]
            self.assertEqual(row["queue_enter"], source["issue_ns"])
            self.assertEqual(row["consume"], max(source["consume_deadline"], row["reported_complete"]))
            self.assertGreaterEqual(row["service_start"], row["queue_enter"])
            self.assertGreaterEqual(row["service_complete"], row["service_start"])
            self.assertEqual(row["residual_delay"], row["consume"] - source["consume_deadline"])
        self.assertLess(by_id[6]["queue_enter"], by_id[1]["consume"])

    def test_qd_one_remains_serial_at_admission(self):
        self.profile["queue_depth"] = 1
        result = self.invoke()
        self.assertEqual(result["summary"]["peak_device_qd"], 1)
        rows = sorted(result["requests"], key=lambda row: row["service_start"])
        for previous, following in zip(rows, rows[1:]):
            self.assertGreaterEqual(following["service_start"], previous["service_complete"])

    def test_write_geometry_is_rejected_before_replay(self):
        self.events[0]["operation"] = "write"
        for event in self.events:
            event["issue_ns"] = 0
        for mode in ("fixed_arrival_trace", "closed_loop_qd"):
            with self.subTest(mode=mode):
                process = self.invoke(expect_success=False, extra=("--arrival-mode", mode))
                self.assertIn("write workloads require more than 10 blocks per plane", process.stderr)

    def test_write_and_mixed_requests_conserve_identity_and_bytes(self):
        self.profile["capacity_bytes"] = 64 << 30
        for event in self.events:
            event["issue_ns"] = 0
        for mixed in (False, True):
            for event in self.events:
                event["operation"] = "read" if mixed and event["request_id"] % 2 else "write"
            for mode in ("fixed_arrival_trace", "closed_loop_qd"):
                with self.subTest(mixed=mixed, mode=mode):
                    result = self.invoke(extra=("--arrival-mode", mode))
                    self.assertEqual(result["summary"]["completed"], len(self.events))
                    self.assertEqual(result["summary"]["completed_bytes"], 6 * 16384)
                    self.assertEqual([(r["request_id"], r["operation"]) for r in result["requests"]],
                                     [(r["request_id"], r["operation"]) for r in self.events])

    def test_late_issue_preserves_original_consume_deadline(self):
        self.events[-1]["consume_deadline"] = 0
        result = self.invoke()
        row = next(row for row in result["requests"] if row["request_id"] == 6)
        self.assertEqual(row["consume_deadline"], 0)
        self.assertEqual(row["residual_delay"], row["reported_complete"])

    def test_closed_loop_replenishes_after_reported_completion(self):
        for event in self.events:
            event["issue_ns"] = 0
        result = self.invoke(extra=("--arrival-mode", "closed_loop_qd"))
        self.assertEqual(result["arrival_process"], "closed_loop_qd")
        rows = result["requests"]
        completions = sorted(row["reported_complete"] for row in rows)
        self.assertEqual([row["queue_enter"] for row in rows[:2]], [0, 0])
        self.assertEqual([row["queue_enter"] for row in rows[2:]], completions[:4])
        self.assertTrue(all(row["issue_ns"] == row["queue_enter"] for row in rows))

    def test_closed_loop_does_not_silently_discard_fixed_schedule(self):
        self.invoke(expect_success=False, extra=("--arrival-mode", "closed_loop_qd"))

    def test_duplicate_ids_rejected(self):
        self.events[1]["request_id"] = self.events[0]["request_id"]
        self.assertIn("duplicate", self.invoke(expect_success=False).stderr)

    def test_fractional_clock_and_invalid_consume_rejected(self):
        for key, value in (("issue_ns", 0.5), ("consume_deadline", -1), ("bytes", 0)):
            with self.subTest(key=key):
                old = self.events[0][key]
                self.events[0][key] = value
                self.invoke(expect_success=False)
                self.events[0][key] = old

    def test_unknown_resource_or_channel_rejected(self):
        for key in ("resource", "channel"):
            with self.subTest(key=key):
                old = self.events[0][key]
                self.events[0][key] = "unmapped"
                self.invoke(expect_success=False)
                self.events[0][key] = old

    def test_unmapped_parallel_units_are_not_called_mqsim(self):
        process = self.invoke(expect_success=False, extra=("--parallel-units", "1537"))
        self.assertIn("PROJECTED_ANALYTICAL", process.stderr)

    def test_existing_output_is_not_overwritten(self):
        self.invoke()
        output = self.directory / "result.json"
        original = output.read_bytes()
        process = subprocess.run([str(BINARY), "--profile", str(self.directory / "profile.json"),
                                  "--events", str(self.directory / "events.jsonl"),
                                  "--output", str(output)], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

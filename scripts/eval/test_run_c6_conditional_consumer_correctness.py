"""Finite TEST_ONLY CPU controls; never launch the runner, compiler, or GPU."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))
if importlib.util.find_spec("resource_guard") is None:
    resource_guard = types.ModuleType("resource_guard")
    resource_guard.ResourceBusy = type("ResourceBusy", (RuntimeError,), {})
    resource_guard.ResourceGuard = object
    sys.modules["resource_guard"] = resource_guard
    run_manifest = types.ModuleType("run_manifest")
    run_manifest.artifact_inventory = lambda _path: ({}, [])
    run_manifest.atomic_json = lambda path, value: path.write_text(json.dumps(value))
    run_manifest.environment_snapshot = lambda: {}
    run_manifest.git_snapshot = lambda _root: {}
    run_manifest.now = lambda: "test-time"
    sys.modules["run_manifest"] = run_manifest
    run_matrix = types.ModuleType("run_matrix")
    run_matrix.FailedRun = type("FailedRun", (RuntimeError,), {})
    run_matrix.InterruptedRun = type("InterruptedRun", (RuntimeError,), {})
    run_matrix.run_child = None
    sys.modules["run_matrix"] = run_matrix

import run_c6_conditional_consumer_correctness as target


def fixture():
    """Synthetic record for rejection controls; not physical acquisition."""
    raw = {"evidence": "TEST_ONLY", "kernel": target.SELECTED_KERNEL,
           "seed": 0x9e3779b9, "profile_page_bytes": 4096, "instruction_id": 15,
           "launch": {"grid": [1, 1, 1], "block": [32, 1, 1]}, "cases": []}
    counts = dict.fromkeys(("issued", "model_ready", "consumed", "drained", "terminal_error",
                           "pending", "native_loads", "native_bytes", "rejected", "groups_issued",
                           "groups_completed", "trace_count", "trace_overflow", "next_reservation"), 0)
    counts["next_reservation"] = 1
    for number, (name, first_mask) in enumerate(target.CONDITIONAL_CASES):
        before = dict(counts)
        for field, increment in (("issued", 32), ("model_ready", 32), ("consumed", 32),
                                 ("groups_issued", 1), ("groups_completed", 1),
                                 ("trace_count", 64), ("next_reservation", 1)):
            counts[field] += increment
        outputs = [target.expected_lane_output(lane, first_mask) for lane in range(32)]
        checksum = 0
        for value in outputs:
            checksum = ((checksum * 131) ^ value) & 0xffffffffffffffff
        traces = []
        for event, status in ((0, 0), (5, 1)):
            for lane in range(32):
                traces.append({"lane": lane, "event": event, "status": status,
                               "address": 0x100000 + 4 * lane, "bytes": 4,
                               "group_mask": 0xffffffff, "instruction_id": 15,
                               "reservation_id": number + 1,
                               "issue_ns": number * 100000,
                               "ready_ns": number * 100000 + 10000,
                               "finish_ns": number * 100000 + (10 if event == 0 else 10010)})
        sentinels = [value ^ 0xffffffff for value in outputs]
        raw["cases"].append({
            "case": name, "first_consumer_mask": first_mask,
            "first_consumer_lanes": first_mask.bit_count(), "active_mask": 0xffffffff,
            "active_lanes": 32, "stride_bytes": 4, "expected_groups": 1,
            "observed_unique_reservations": 1, "validation": "PASS",
            "observed_outputs": outputs, "sentinel_outputs": sentinels,
            "sentinel_echo": sentinels, "observed_checksum": checksum,
            "outputs": [{"lane": lane, "active": True, "observed": value,
                         "first_consumer_executed": bool(first_mask & (1 << lane))}
                        for lane, value in enumerate(outputs)],
            "counters_before": before, "counters_after": dict(counts),
            "counter_delta": {key: counts[key] - before[key] for key in counts},
            "raw_traces": traces, "traces": copy.deepcopy(traces),
            "trace_window": {"before_count": before["trace_count"],
                             "after_count": counts["trace_count"], "copied_records": 64,
                             "truncated_or_anomalous": False},
        })
    raw["final_counters"] = counts
    return raw


class ConditionalConsumerControls(unittest.TestCase):
    def test_oracle_matches_frozen_three_checksums_and_distinguishes_paths(self):
        actual = []
        for _name, mask in target.CONDITIONAL_CASES:
            checksum = 0
            for lane in range(32):
                checksum = ((checksum * 131) ^ target.expected_lane_output(lane, mask)) & 0xffffffffffffffff
            actual.append(checksum)
        self.assertEqual(actual, [6246203690665295131, 2699201272268498957, 955806103974083035])
        self.assertTrue(all(target.expected_lane_output(lane, 0) !=
                            target.expected_lane_output(lane, 0xffffffff) for lane in range(32)))

    def test_exact_three_case_fixture_passes_observation_checks(self):
        raw = fixture()
        self.assertEqual(raw["evidence"], "TEST_ONLY")
        target.validate_conditional_observations(raw)

    def test_false_first_consumer_must_not_disable_producers(self):
        raw = fixture()
        raw["cases"][0]["active_mask"] = 0
        raw["cases"][0]["active_lanes"] = 0
        with self.assertRaisesRegex(ValueError, "producer group mismatch"):
            target.validate_conditional_observations(raw)

    def test_second_consumer_cannot_consume_again(self):
        raw = fixture()
        raw["cases"][1]["counters_after"]["consumed"] += 32
        with self.assertRaisesRegex(ValueError, "counter conservation"):
            target.validate_conditional_observations(raw)

    def test_missing_mixed_lane_consume_trace_rejects(self):
        raw = fixture()
        raw["cases"][2]["raw_traces"].pop()
        raw["cases"][2]["traces"].pop()
        with self.assertRaisesRegex(ValueError, "trace conservation"):
            target.validate_conditional_observations(raw)

    def test_wrong_actual_output_cannot_use_saved_expected_fields(self):
        raw = fixture()
        raw["cases"][0]["observed_outputs"][0] ^= 1
        raw["cases"][0]["expected_outputs"] = list(raw["cases"][0]["observed_outputs"])
        with self.assertRaisesRegex(ValueError, "output mismatch"):
            target.validate_conditional_observations(raw)

    def test_unfrozen_identity_rejects_before_output_or_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must_not_exist"
            with mock.patch.object(target, "REVIEWED_CUBIN_BYTES", 0):
                with self.assertRaisesRegex(ValueError, "UNFROZEN"):
                    target.execute(output, None, None, None, None, "unused")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

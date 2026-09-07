"""Seven finite TEST_ONLY controls; no compiler, real child or GPU execution."""
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

import run_c6_future_lifecycle_correctness as target


def fixture():
    """Synthetic saved record; never attributed to physical acquisition."""
    raw = {"evidence": "TEST_ONLY", "kernel": target.SELECTED_KERNEL,
           "seed": 0x9e3779b9, "profile_page_bytes": 4096, "instruction_id": 15,
           "launch": {"grid": [1, 1, 1], "block": [32, 1, 1]}, "cases": []}
    counts = dict.fromkeys(("issued", "model_ready", "consumed", "drained", "terminal_error",
                           "pending", "native_loads", "native_bytes", "rejected", "groups_issued",
                           "groups_completed", "trace_count", "trace_overflow", "next_reservation"), 0)
    counts["next_reservation"] = 1
    for number, (name, mode) in enumerate(target.LIFECYCLE_CASES):
        before = dict(counts)
        for field, increment in (("issued", 32), ("model_ready", 32),
                                 ("consumed", 32 if mode == 1 else 0),
                                 ("drained", 0 if mode == 1 else 32),
                                 ("groups_issued", 1), ("groups_completed", 1),
                                 ("trace_count", 64), ("next_reservation", 1)):
            counts[field] += increment
        outputs = [target.expected_lane_output(lane, mode) for lane in range(32)]
        checksum = 0
        for value in outputs:
            checksum = ((checksum * 131) ^ value) & 0xffffffffffffffff
        traces = []
        for event, status in ((0, 0), (5 if mode == 1 else 9, 1)):
            for lane in range(32):
                traces.append({"lane": lane, "event": event, "status": status,
                               "address": 0x100000 + 4 * lane, "bytes": 4,
                               "group_mask": 0xffffffff, "instruction_id": 15,
                               "reservation_id": number + 1,
                               "issue_ns": number * 100000,
                               "ready_ns": number * 100000 + 10000,
                               "finish_ns": number * 100000 + (10 if event == 0 else 10010)})
        sentinels = [0xc0dec000 ^ ((lane * 0x01020304) & 0xffffffff)
                     for lane in range(32)] if mode == 2 else [value ^ 0xffffffff for value in outputs]
        raw["cases"].append({
            "case": name, "mode": mode, "overwrite_executed": mode == 0,
            "output_store_executed": mode != 2,
            "terminal_kind": "CONSUMED" if mode == 1 else "DRAINED",
            "active_mask": 0xffffffff, "active_lanes": 32,
            "stride_bytes": 4, "expected_groups": 1,
            "observed_unique_reservations": 1, "validation": "PASS",
            "observed_outputs": outputs, "sentinel_outputs": sentinels,
            "sentinel_echo": list(sentinels), "observed_checksum": checksum,
            "outputs": [{"lane": lane, "active": True, "observed": value,
                         "overwrite_executed": mode == 0, "output_store_executed": mode != 2}
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


class FutureLifecycleControls(unittest.TestCase):
    def test_numeric_outputs_checksums_and_paths(self):
        # Independently evaluated unsigned arithmetic, not values imported from the runner.
        samples = [[1786537951, 772653144, 2370304507],
                   [1374569532, 185645345, 830706210],
                   [3235823616, 3352352028, 3756039548]]
        checksums = []
        for mode in range(3):
            self.assertEqual([target.expected_lane_output(lane, mode) for lane in (0, 7, 31)],
                             samples[mode])
            checksum = 0
            for lane in range(32):
                checksum = ((checksum * 131) ^ target.expected_lane_output(lane, mode)) & 0xffffffffffffffff
            checksums.append(checksum)
        self.assertEqual(checksums, [3524002983134157071, 6246203690665295131, 17054659478395592960])
        self.assertTrue(all(target.expected_lane_output(lane, 0) !=
                            target.expected_lane_output(lane, 1) for lane in range(32)))

    def test_three_paths_conserve_96_issues_32_consumes_64_drains(self):
        raw = fixture()
        self.assertEqual(raw["evidence"], "TEST_ONLY")
        target.validate_lifecycle_observations(raw)
        final = raw["final_counters"]
        self.assertEqual([final[key] for key in ("issued", "model_ready", "consumed", "drained",
                                                "pending", "groups_completed", "trace_count")],
                         [96, 96, 32, 64, 0, 3, 192])
        self.assertEqual(raw["cases"][2]["observed_outputs"], raw["cases"][2]["sentinel_echo"])

    def test_executed_overwrite_cannot_count_as_dependency_consume(self):
        raw = fixture(); case = raw["cases"][0]
        for group in ("counters_after", "counter_delta"):
            case[group]["drained"] -= 32
            case[group]["consumed"] += 32
        with self.assertRaisesRegex(ValueError, "counter conservation"):
            target.validate_lifecycle_observations(raw)

    def test_false_overwrite_cannot_replace_consume_with_drain(self):
        raw = fixture(); case = raw["cases"][1]
        for field in ("raw_traces", "traces"):
            for trace in case[field]:
                if trace["event"] == 5:
                    trace["event"] = 9
        with self.assertRaisesRegex(ValueError, "terminate exactly once"):
            target.validate_lifecycle_observations(raw)

    def test_unused_exit_rejects_missing_terminal_pending_or_output_write(self):
        for corruption in ("missing_terminal", "pending", "output_write"):
            with self.subTest(corruption=corruption):
                raw = fixture(); case = raw["cases"][2]
                if corruption == "missing_terminal":
                    case["raw_traces"].pop(); case["traces"].pop()
                    message = "trace conservation"
                elif corruption == "pending":
                    case["counters_after"]["pending"] = 1
                    case["counter_delta"]["pending"] = 1
                    message = "counter conservation"
                else:
                    case["observed_outputs"][0] ^= 1
                    message = "output mismatch"
                with self.assertRaisesRegex(ValueError, message):
                    target.validate_lifecycle_observations(raw)

    def test_wrong_output_cannot_be_hidden_by_expected_field_or_wrong_sentinel(self):
        raw = fixture()
        raw["cases"][0]["observed_outputs"][0] ^= 1
        raw["cases"][0]["expected_outputs"] = list(raw["cases"][0]["observed_outputs"])
        with self.assertRaisesRegex(ValueError, "output mismatch"):
            target.validate_lifecycle_observations(raw)
        raw = fixture()
        raw["cases"][1]["sentinel_echo"][0] ^= 1
        with self.assertRaisesRegex(ValueError, "sentinel mismatch"):
            target.validate_lifecycle_observations(raw)

    def test_unfrozen_identity_rejects_before_output_or_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must_not_exist"
            with mock.patch.object(target, "REVIEWED_CUBIN_BYTES", 0), \
                    mock.patch.object(target, "ResourceGuard") as guard, \
                    mock.patch.object(target, "run_child") as child:
                with self.assertRaisesRegex(ValueError, "UNFROZEN"):
                    target.execute(output, None, None, None, None, "unused")
                guard.assert_not_called(); child.assert_not_called()
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)

import json
from pathlib import Path
import tempfile
import unittest

from analyze_controlled_campaign import (
    EXPECTED_POINT_COUNT,
    POINT_FILES,
    _discover_point_dirs,
    _validate_index_identity,
    analyze_campaign,
    analyze_point,
    write_outputs,
)


def save(path, value):
    path.write_text(json.dumps(value) + "\n")


def jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def make_point(root: Path, strategy: str, served=(60, 20)) -> Path:
    point = root / strategy
    point.mkdir()
    stacks = ["hbf0", "hbf1"]
    workload = {
        "metadata": {"model_id": "Qwen/Qwen2.5-7B-Instruct", "pattern": "continuous",
                     "stack_ids": stacks, "active_ns": 40, "weight_bytes": 100,
                     "full_scans_per_s": 16},
        "windows": [{"start_ns": 0, "end_ns": 20, "total_offered_bytes": 100},
                    {"start_ns": 20, "end_ns": 40, "total_offered_bytes": 0}],
    }
    save(point / "workload.json", workload)
    save(point / "scenario.json", {"topology": "mixed_direct"})
    save(point / "manifest.json", {"strategy": strategy,
                                    "input_sha256": {"workload.json": "same"}})
    delivered_per_stack = sum(served) // 2
    histories = {stack: [{"delay_ns": 20, "bytes": delivered_per_stack // 2},
                         {"delay_ns": 40, "bytes": delivered_per_stack - delivered_per_stack // 2}]
                 for stack in stacks}
    per_stack_wait = {stack: {"delivered_delay_histogram_bytes": rows,
                              "delivered_delay_p95_ns": 40,
                              "delivered_delay_p99_ns": 40}
                      for stack, rows in histories.items()}
    energy = sum(served) * 50e-12
    save(point / "DONE.json", {
        "execution_status": "COMPLETED", "strategy": strategy,
        "summary": {"energy_j": energy, "fluid_wait_by_stack": per_stack_wait,
                    "final_guard_states": {"hbf0": "normal", "hbf1": "light"}},
        "thermal_energy_receipt": {"total_input_j": energy},
        "semantics": {"backend_latency": "UNKNOWN",
                      "maintenance": "UNAVAILABLE_IN_THIS_FLUID_PATH"},
    })
    rates = [
        {"start_ns": 0, "end_ns": 20, "window_arrived_bytes": 100,
         "window_served_bytes": served[0],
         "stacks": {"hbf0": {"offered_bytes": 50, "delivered_bytes": served[0] // 2,
                              "backlog_bytes": 50 - served[0] // 2},
                    "hbf1": {"offered_bytes": 50, "delivered_bytes": served[0] // 2,
                              "backlog_bytes": 50 - served[0] // 2}}},
        {"start_ns": 20, "end_ns": 40, "window_arrived_bytes": 0,
         "window_served_bytes": served[1],
         "stacks": {"hbf0": {"offered_bytes": 0, "delivered_bytes": served[1] // 2,
                              "backlog_bytes": 50 - sum(served) // 2},
                    "hbf1": {"offered_bytes": 0, "delivered_bytes": served[1] // 2,
                              "backlog_bytes": 50 - sum(served) // 2}}},
    ]
    controls = [{"observed_window_start_ns": row["start_ns"],
                 "observed_window_end_ns": row["end_ns"]} for row in rates]
    energies = [{"start_ns": row["start_ns"], "end_ns": row["end_ns"],
                 "component_energy_j": {"array": row["window_served_bytes"] * 40e-12,
                                         "base": row["window_served_bytes"] * 10e-12},
                 "window_total_j": row["window_served_bytes"] * 50e-12}
                for row in rates]
    thermals = [
        {"start_ns": 0, "end_ns": 20, "temperatures": {"hbf0": 350.0, "hbf1": 351.0},
         "stack_states": {"hbf0": "normal", "hbf1": "light"}},
        {"start_ns": 20, "end_ns": 40, "temperatures": {"hbf0": 349.0, "hbf1": 350.0},
         "stack_states": {"hbf0": "normal", "hbf1": "normal"}},
    ]
    jsonl(point / "rates.jsonl", rates)
    jsonl(point / "control.jsonl", controls)
    jsonl(point / "energy.jsonl", energies)
    jsonl(point / "thermal.jsonl", thermals)
    return point


class ControlledAnalysisTests(unittest.TestCase):
    def test_recomputes_metrics_conservation_states_and_weighted_latency(self):
        with tempfile.TemporaryDirectory() as directory:
            point = make_point(Path(directory), "guard_only")
            result = analyze_point(point)
            self.assertEqual(result["totals"]["offered_bytes"], 100)
            self.assertEqual(result["totals"]["delivered_bytes"], 80)
            self.assertEqual(result["totals"]["final_backlog_bytes"], 20)
            self.assertEqual(result["totals"]["byte_conservation_error"], 0)
            self.assertEqual(result["totals"]["byte_weighted_delay_p95_ns"], 40)
            self.assertEqual(result["totals"]["peak_temperature_k"], 351.0)
            self.assertEqual(result["any_stack_state_time_ns"]["light"], 20)
            self.assertEqual(result["per_stack"]["hbf0"]["state_time_ns"]["normal"], 40)
            self.assertAlmostEqual(result["totals"]["energy_j"], 80 * 50e-12)
            self.assertEqual(result["limitations"]["token_per_s"], "UNKNOWN")
            trace = result["_trace"]
            self.assertEqual(trace["offered_Bps"], [5e9, 0.0])
            self.assertEqual(trace["temperature_k_by_stack"]["hbf0"], [350.0, 349.0])
            self.assertEqual(len(trace["temperature_k_by_stack"]["hbf1"]), len(trace["end_ns"]))
            active = result["service_rate_stability"]["active_full"]["total"]
            self.assertEqual(active["window_count"], 2)
            self.assertAlmostEqual(active["population_cv"], 0.5)
            self.assertEqual(active["p5_Bps"], 1e9)
            self.assertEqual(active["p50_Bps"], 1e9)
            self.assertEqual(active["p95_Bps"], 3e9)
            self.assertEqual(active["zero_service_window_fraction"], 0.0)
            latter = result["service_rate_stability"]["active_second_half"]["total"]
            self.assertEqual(latter["window_count"], 1)
            self.assertEqual(latter["population_cv"], 0.0)

    def test_partial_campaign_pairs_all_policies_without_benefit_label(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_point(root, "guard_only", (60, 20))
            make_point(root, "thermal_hysteresis_guard", (50, 10))
            make_point(root, "read_rate_feedback_thermal_guard_v1", (40, 10))
            result = analyze_campaign(root, require_complete=False)
            self.assertEqual(result["completed_point_count"], 3)
            self.assertEqual(len(result["pairwise_policy_costs"]), 3)
            row = result["pairwise_policy_costs"][0]
            self.assertEqual(row["delta_semantics"], "RIGHT_MINUS_LEFT_NO_BENEFIT_DIRECTION_ASSUMED")
            self.assertLess(row["delivered_bytes_delta"], 0)
            output = root / "derived"
            write_outputs(result, output, plots=True)
            self.assertTrue((output / "CONTROLLED_CAMPAIGN_ANALYSIS.json").is_file())
            self.assertTrue((output / "pairwise-policy-costs.csv").is_file())
            self.assertTrue((output / "controlled-campaign-summary.png").is_file())
            self.assertEqual(len(list(output.glob("trajectory-*.png"))), 1)
            self.assertEqual(len(list(output.glob("stack-temperatures-*.png"))), 1)

    def test_detects_byte_conservation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            point = make_point(Path(directory), "guard_only")
            rows = list(json.loads(line) for line in (point / "rates.jsonl").read_text().splitlines())
            rows[-1]["stacks"]["hbf0"]["backlog_bytes"] += 1
            jsonl(point / "rates.jsonl", rows)
            with self.assertRaisesRegex(ValueError, "byte conservation"):
                analyze_point(point)

    def test_run_index_whitelists_main_and_excludes_pilot_and_stage_done(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save(root / "DONE.json", {"stage": "complete"})
            pilot = root / "pilot"
            pilot.mkdir()
            for name in POINT_FILES:
                (pilot / name).write_text("{}\n")
            entries = [{"phase": "pilot", "output": str(pilot)}]
            expected = []
            for index in range(EXPECTED_POINT_COUNT):
                point = root / f"main-{index:02d}"
                point.mkdir()
                for name in POINT_FILES:
                    (point / name).write_text("{}\n")
                expected.append(point.resolve())
                entries.append({"phase": "main", "output": str(point)})
            save(root / "RUN_INDEX.json", {
                "main_count": EXPECTED_POINT_COUNT,
                "pilot_count": 1,
                "points": entries,
            })
            found, main_entries, mode = _discover_point_dirs(root)
            self.assertEqual(found, expected)
            self.assertEqual(len(main_entries), EXPECTED_POINT_COUNT)
            self.assertEqual(mode, "RUN_INDEX_PHASE_MAIN_WHITELIST")
            self.assertNotIn(pilot.resolve(), found)
            (expected[-1] / "DONE.json").unlink()
            with self.assertRaisesRegex(ValueError, "incomplete"):
                _discover_point_dirs(root)

    def test_run_index_identity_checks_point_inputs(self):
        point_path = Path("/tmp/fixed-main-point")
        point = {
            "point_id": "RT-MAIN-fixed", "point_path": str(point_path),
            "topology": "mixed_direct", "model_id": "Qwen/Qwen2.5-7B-Instruct",
            "pattern": "continuous", "strategy": "guard_only", "full_scans_per_s": 16,
            "active_ns": 20_000_000_000, "duration_ns": 30_000_000_000,
            "totals": {"offered_bytes": 123},
            "input_identity": {"profile.json": "profile-hash", "workload.json": "workload-hash",
                               "scenario.json": "scenario-hash"},
        }
        entry = {
            "phase": "main", "point_id": "RT-MAIN-fixed", "output": str(point_path),
            "topology": "mixed_direct", "model": "7B", "pattern": "continuous",
            "strategy": "guard_only", "full_scans_per_s": 16,
            "active_s": 20, "recovery_s": 10, "expected_active_offered_bytes": 123,
            "input_sha256": {"profile": "profile-hash", "workload": "workload-hash",
                             "scenario": "scenario-hash"},
        }
        _validate_index_identity(entry, point)
        entry["input_sha256"]["workload"] = "wrong"
        with self.assertRaisesRegex(ValueError, "workload input identity"):
            _validate_index_identity(entry, point)


if __name__ == "__main__":
    unittest.main()

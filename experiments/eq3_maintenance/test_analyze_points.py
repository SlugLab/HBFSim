import csv
import json
import tempfile
import unittest
from pathlib import Path

from analyze_points import analyze_point


def write_csv(path, fields, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


class AnalyzePointsTest(unittest.TestCase):
    def test_raw_derivation_preserves_unknown_and_observed_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            point = Path(directory) / "point"; point.mkdir()
            (point / "manifest.json").write_text(json.dumps({
                "active_ns": 10, "workload": "W1", "policy": "guard_only",
                "maintenance_count": 0,
            }))
            (point / "DONE.json").write_text('{"execution_status":"COMPLETED"}')
            facts = [{"stack_id": "hbf0", "offered_bytes": 8, "delivered_bytes": 8,
                      "backlog_bytes": 0, "censored_requests": 0,
                      "physical_offered_bytes": 16, "physical_delivered_bytes": 16,
                      "physical_backlog_bytes": 0}]
            write_csv(point / "rates.csv", ["start_ns", "end_ns", "stacks"], [
                {"start_ns": 0, "end_ns": 10, "stacks": json.dumps(facts)},
                {"start_ns": 10, "end_ns": 20, "stacks": json.dumps([{**facts[0], "offered_bytes": 0,
                                                                          "delivered_bytes": 0}])},
            ])
            fields = ["phase", "request_id", "stack", "bytes", "valid_weight_bytes", "arrival_ns",
                      "final_completion_ns", "end_to_end_latency_ns"]
            write_csv(point / "requests.csv", fields, [
                {"phase": "ARRIVAL", "request_id": "r", "stack": "hbf0", "bytes": 16,
                 "valid_weight_bytes": 8, "arrival_ns": 0},
                {"phase": "FINAL_COMPLETE", "request_id": "r", "stack": "hbf0", "bytes": 16,
                 "valid_weight_bytes": 8, "arrival_ns": 0,
                 "final_completion_ns": 5, "end_to_end_latency_ns": 5},
            ])
            energy = {"cumulative": {"energy_residual_j": 1e-6, "total_input_j": 2.0}}
            write_csv(point / "thermal.csv", ["end_ns", "temperatures", "energy_j"], [
                {"end_ns": 10, "temperatures": json.dumps({"gpu": 301, "hbf0": 300.1}),
                 "energy_j": json.dumps(energy)},
                {"end_ns": 20, "temperatures": "", "energy_j": ""},
            ])
            decisions = [{"stack_id": "hbf0", "budget_bytes": 32, "action": "HOLD"}]
            write_csv(point / "control.csv", ["applies_to_window_start_ns", "stack_decisions"], [
                {"applies_to_window_start_ns": 0, "stack_decisions": json.dumps(decisions)}])
            (point / "maintenance.csv").write_text("\n")
            (point / "energy.csv").write_text("kind\n")

            result = analyze_point(point)
            self.assertEqual(result["requests_by_stack"]["hbf0"]["counts"]["final_delivered"], 1)
            self.assertEqual(result["time_series"][1]["delivered_bytes"], 0)
            self.assertIsNone(result["time_series"][1]["temperatures_k"]["gpu"])
            self.assertEqual(result["maintenance"]["due_count"], 0)
            self.assertEqual(result["requests_by_stack"]["hbf0"]["bytes"]
                             ["final_delivered_effective"], 8)
            self.assertEqual(result["requests_by_stack"]["hbf0"]["bytes"]
                             ["final_delivered_physical"], 16)
            self.assertEqual(result["delivered_totals_by_backend_kind"]["HBF"]
                             ["effective_bytes"], 8)
            self.assertAlmostEqual(result["time_series"][0]["cumulative_energy_relative_residual"], 5e-7)
            self.assertEqual(result["time_series"][0]["tail_latency_sample_count"], 1)

    def test_empty_maintenance_without_declared_zero_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            point = Path(directory) / "point"; point.mkdir()
            (point / "manifest.json").write_text('{"active_ns":10}')
            (point / "DONE.json").write_text('{"execution_status":"COMPLETED"}')
            facts = [{"stack_id": "hbf0", "offered_bytes": 0, "delivered_bytes": 0,
                      "backlog_bytes": 0, "censored_requests": 0}]
            write_csv(point / "rates.csv", ["start_ns", "end_ns", "stacks"], [
                {"start_ns": 0, "end_ns": 10, "stacks": json.dumps(facts)}])
            for name, fields in [("requests.csv", ["phase"]), ("thermal.csv", ["end_ns"]),
                                 ("control.csv", ["applies_to_window_start_ns"]),
                                 ("energy.csv", ["kind"])]:
                write_csv(point / name, fields, [])
            (point / "maintenance.csv").write_text("\n")
            result = analyze_point(point)
            self.assertIsNone(result["maintenance"]["due_count"])
            self.assertEqual(result["maintenance"]["semantics"], "UNKNOWN_NO_ROWS")

    def test_age_clears_only_from_mapping_commit_and_reports_declared_fraction(self):
        with tempfile.TemporaryDirectory() as directory:
            point = Path(directory) / "point"; point.mkdir()
            (point / "manifest.json").write_text('{"active_ns":100,"maintenance_count":3}')
            (point / "DONE.json").write_text('{"execution_status":"COMPLETED"}')
            (point / "weight-model-extent.json").write_text('{"global_page_count":1000}')
            facts = [{"stack_id": "hbf0", "offered_bytes": 0, "delivered_bytes": 0,
                      "backlog_bytes": 0, "censored_requests": 0}]
            write_csv(point / "rates.csv", ["start_ns", "end_ns", "stacks"], [
                {"start_ns": 0, "end_ns": 100, "stacks": json.dumps(facts)}])
            for name, fields in [("requests.csv", ["phase"]), ("thermal.csv", ["end_ns"]),
                                 ("control.csv", ["applies_to_window_start_ns"]),
                                 ("energy.csv", ["kind"])]:
                write_csv(point / name, fields, [])
            fields = ["request_id", "stack", "stack_local_page", "due_ns", "initial_age_s",
                      "state", "mapping_committed", "age_reset_ns", "backend_status",
                      "cleanup_failed"]
            write_csv(point / "maintenance.csv", fields, [
                {"request_id": 1, "stack": "hbf0", "stack_local_page": 3, "due_ns": 10,
                 "initial_age_s": 100, "state": "COMMITTED_CLEANUP_FAILED",
                 "mapping_committed": True, "age_reset_ns": 50,
                 "backend_status": "FAILED_AFTER_COMMIT_NEEDS_RECONCILE", "cleanup_failed": True},
                {"request_id": 2, "stack": "hbf0", "stack_local_page": 4, "due_ns": 10,
                 "initial_age_s": 200, "state": "FAILED", "mapping_committed": False,
                 "backend_status": "FAILED_PROGRAM", "cleanup_failed": False},
                {"request_id": 3, "stack": "hbf0", "stack_local_page": 5, "due_ns": 10,
                 "initial_age_s": 150, "state": "COMMITTED", "mapping_committed": True,
                 "age_reset_ns": 60, "backend_status": "COMMITTED_RECLAIM_DEFERRED",
                 "cleanup_failed": True},
            ])
            result = analyze_point(point)
            maintenance = result["maintenance"]
            self.assertEqual(maintenance["committed_count"], 2)
            self.assertEqual(maintenance["failed_count"], 1)
            self.assertEqual(maintenance["cleanup_failed_after_commit_count"], 1)
            self.assertEqual(maintenance["backlog_at_observation_end"], 1)
            self.assertEqual(maintenance["declared_page_count"], 3)
            self.assertEqual(maintenance["declared_model_page_fraction"], 0.003)
            self.assertEqual(maintenance["age_reset_count"], 2)
            self.assertGreater(maintenance["declared_age_at_observation_max_s"], 200)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from analyze_extensions import analyze_point, plot_panels


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def jsonl(path, values):
    path.write_text("".join(json.dumps(value, separators=(",", ":")) + "\n"
                            for value in values))


def thermal(start, end, cumulative):
    return {
        "start_ns": start, "end_ns": end,
        "temperatures": {"gpu": 301.0, "hbf0": 302.0, "hbm0": 303.0},
        "stack_states": {"hbf0": "normal", "hbm0": "light"},
        "energy_j": {"cumulative": {"total_input_j": cumulative}},
    }


def identity(point, config):
    save(point / "config.json", config)
    config_hash = hashlib.sha256((point / "config.json").read_bytes()).hexdigest()
    save(point / "manifest.json", {
        "input_sha256": config_hash, "source_revision": "fixed-test-revision",
        "source_sha256": {"runner.py": "b" * 64},
        "thermal_binary_sha256": "c" * 64,
    })


def service_stacks(window, cumulative):
    return {
        "hbf0": {"offered_effective_bytes": 50, "delivered_effective_bytes": 50,
                 "backlog_effective_bytes": 0,
                 "cumulative_offered_effective_bytes": cumulative,
                 "cumulative_delivered_effective_bytes": cumulative},
        "hbm0": {"offered_effective_bytes": 0, "delivered_effective_bytes": 0,
                 "backlog_effective_bytes": 0,
                 "cumulative_offered_effective_bytes": 0,
                 "cumulative_delivered_effective_bytes": 0},
    }


class AnalyzeExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def maintenance_point(self):
        point = self.root / "maintenance"
        point.mkdir()
        config = {"point_id": "maintenance-fixed",
                  "workload": {"active_ns": 10}, "recovery_ns": 10,
                  "maintenance": {"mode": "shared"}}
        identity(point, config)
        rows = []
        for index, (start, end) in enumerate(((0, 10), (10, 20))):
            terminal = ([] if index == 0 else [{
                "operation_id": "m0", "status": "COMMITTED", "start_ns": 0,
                "end_ns": 20, "extent_ids": ["e0"], "committed_extents": ["e0"],
                "conflicted_extents": [], "failures": [],
                "age_reset_extent_ids": ["e0"]}])
            rows.append({
                "start_ns": start, "end_ns": end,
                "service": {"stacks": service_stacks(index, 50 * (index + 1)),
                            "activities": [{"operation": "read", "phase": "media_read",
                                            "stack": "hbf0", "bytes": 50}],
                            "completion_ids": [f"f{index}"], "job_progress": []},
                "independent_maintenance_service": None,
                "maintenance_delta": {"terminal_results": terminal},
                "energy": {"component_energy_j": {"hbf0.die0": 1.0}, "total_j": 1.0},
                "thermal": thermal(start, end, index + 1.0),
                "control": {"observed_states": {"hbf0": "normal", "hbm0": "light"}},
            })
        jsonl(point / "windows.jsonl", rows)
        final = {
            "mode": "shared",
            "driver": {"terminal_summary": {"operation_count": 1, "extent_count": 1,
                                               "status_counts": {"COMMITTED": 1}},
                       "free_spares_by_stack_channel": {"hbf0": {"0": ["s0"]}},
                       "quarantined_blocks": [],
                       "physical_wear": {"src": {"block_program_work_started": 1,
                           "block_program_work_completed": 1,
                           "nand_page_programs_started": 256,
                           "nand_page_programs_completed": 256,
                           "erase_phase_started": 1, "erase_completed": 1}},
                       "limitations": ["METADATA_VERSION_VALIDITY_NO_PAYLOAD_INTEGRITY"]},
            "reliability": {"blocks": {"e0": {"equivalent_age_ns": 7,
                                                  "last_refresh_commit_ns": 20}}},
        }
        save(point / "maintenance-final.json", final)
        final_hash = hashlib.sha256((point / "maintenance-final.json").read_bytes()).hexdigest()
        save(point / "DONE.json", {"status": "COMPLETED", "summary": {
            "offered_bytes": 100, "delivered_bytes": 100, "backlog_bytes": 0,
            "energy_j": 2.0, "maintenance_final_sha256": final_hash}})
        return point

    def causal_point(self, duplicate_completion=False):
        point = self.root / "causal"
        point.mkdir()
        config = {"point_id": "causal-fixed", "active_ns": 10, "recovery_ns": 10,
                  "trace": {"dependency_mode": "synthetic_metadata_dag"}}
        identity(point, config)
        rows = []
        for index, (start, end) in enumerate(((0, 10), (10, 20))):
            completion = "j0" if duplicate_completion else f"j{index}"
            events = ([{"kind": "cache_hit"}] if index == 0 else [
                {"kind": "prefetch_issue"}, {"kind": "migration_commit", "bytes": 8},
                {"kind": "retry_complete", "retry_count": 1}])
            rows.append({
                "start_ns": start, "end_ns": end,
                "service": {"activities": [{"operation": "read", "phase": "media_read",
                                               "stack": "hbf0", "bytes": 50}],
                            "changed_job_progress": [{"remaining_bytes": 0}],
                            "completions": [{"job_id": completion}]},
                "executor": {"events": events, "completed_tokens": 1,
                             "cumulative_completed_tokens": index + 1},
                "maintenance": {"mode": "disabled"},
                "energy": {"component_energy_j": {"hbf0.die0": 1.0}, "total_j": 1.0},
                "thermal": thermal(start, end, index + 1.0),
                "control": {"observed_states": {"hbf0": "normal", "hbm0": "light"},
                            "stack_facts": {
                                "hbf0": {"offered_bytes": 50, "delivered_bytes": 50,
                                         "backlog_bytes": 0},
                                "hbm0": {"offered_bytes": 0, "delivered_bytes": 0,
                                         "backlog_bytes": 0}}},
                "output_granularity": "PHYSICAL_CHANNEL_JOB_DELTAS",
            })
        jsonl(point / "windows.jsonl", rows)
        save(point / "DONE.json", {"status": "COMPLETED", "summary": {
            "trace_origin": "SYNTHETIC_ARCHITECTURE_METADATA_DAG",
            "structure_provenance": "SYNTHETIC_METADATA_ONLY",
            "completed_tokens": 2, "uninstantiated_batches": 0,
            "pending_external_jobs": 0,
            "offered_useful_bytes_by_stack": {"hbf0": 100},
            "delivered_useful_bytes_by_stack": {"hbf0": 100},
            "maintenance": {"mode": "disabled"}, "energy_j": 2.0}})
        return point

    def test_complete_maintenance_receipts_and_wear(self):
        result = analyze_point(self.maintenance_point())
        self.assertEqual(result["analysis_status"], "VALIDATED_COMPLETE_RECEIPTS")
        self.assertEqual(result["maintenance"]["terminal_status_counts"], {"COMMITTED": 1})
        self.assertEqual(result["maintenance"]["wear"]["nand_page_programs_completed"], 256)
        self.assertEqual(result["causal_tokens"]["availability"],
                         "UNAVAILABLE_RATE_WORKLOAD_HAS_NO_TOKEN_DEPENDENCY_DAG")

    def test_complete_causal_receipts_consumers_and_plot(self):
        result = analyze_point(self.causal_point())
        self.assertEqual(result["causal_tokens"]["completed_tokens"], 2)
        self.assertEqual(result["causal_consumers"]["cache"], {"cache_hit": 1})
        self.assertEqual(result["causal_consumers"]["retry"]["observed_retry_count"], 1)
        output = self.root / "panels.png"
        plot_panels(result, output)
        self.assertGreater(output.stat().st_size, 0)

    def test_expected_retry_proxy_does_not_claim_integer_count(self):
        point = self.causal_point()
        config = json.loads((point / "config.json").read_text())
        config["hbf_read_cost_proxy"] = {"mode": "conditional_nand_history_v1"}
        identity(point, config)
        result = analyze_point(point)
        retry = result["causal_consumers"]["retry"]
        self.assertIsNone(retry["observed_retry_count"])
        self.assertEqual(retry["count_semantics"],
                         "UNKNOWN_INTEGER_COUNT_EXPECTED_WORK_PROXY")

    def test_failed_run_is_preserved_without_claim(self):
        point = self.root / "failed"
        point.mkdir()
        save(point / "FAILED.json", {"status": "FAILED", "error": "fixed failure"})
        result = analyze_point(point)
        self.assertEqual(result["analysis_status"], "FAILED_RUN_PRESERVED_NOT_ANALYZED")
        self.assertEqual(result["scientific_pass"], "NOT_ASSESSED")
        self.assertIsNone(result["panels"])

    def test_duplicate_causal_terminal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate causal completion"):
            analyze_point(self.causal_point(duplicate_completion=True))

    def test_energy_timeline_mismatch_is_rejected(self):
        point = self.maintenance_point()
        rows = list(map(json.loads, (point / "windows.jsonl").read_text().splitlines()))
        rows[1]["thermal"]["energy_j"]["cumulative"]["total_input_j"] = 3.0
        jsonl(point / "windows.jsonl", rows)
        with self.assertRaisesRegex(ValueError, "thermal cumulative energy"):
            analyze_point(point)


if __name__ == "__main__":
    unittest.main()

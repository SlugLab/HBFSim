import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from analyze_campaign import analyze_campaign, analyze_point, write_outputs


def save(path: Path, value):
    path.write_text(json.dumps(value, allow_nan=False) + "\n")


def make_point(root: Path, *, corrupt_backlog=False) -> Path:
    point = root / "base-mixed_direct-384-0-01"
    point.mkdir(parents=True)
    config = {"point_id": point.name, "topology": "mixed_direct", "strategy": "guard_only",
              "workload": {"active_ns": 20, "per_stack_Bps": 1_536_000_000_000},
              "recovery_ns": 20}
    save(point / "config.json", config)
    save(point / "manifest.json", {
        "input_sha256": hashlib.sha256((point / "config.json").read_bytes()).hexdigest(),
        "model_dir": "/models/mixed-full-2mm",
    })
    save(point / "DONE.json", {"status": "COMPLETED", "summary": {}})
    rows = []
    for index, (offered, delivered, backlog) in enumerate(((100, 60, 40), (0, 40, 0))):
        if corrupt_backlog and index == 1:
            backlog = 1
        total_j = delivered * 50e-12
        rows.append({
            "start_ns": index * 20, "end_ns": (index + 1) * 20,
            "service": {
                "start_ns": index * 20, "end_ns": (index + 1) * 20,
                "stacks": {
                    "hbf0": {"offered_effective_bytes": offered,
                             "delivered_effective_bytes": delivered,
                             "backlog_effective_bytes": backlog,
                             "media_activity_bytes": delivered,
                             "oldest_wait_ns": 20 if backlog else None},
                    "hbm0": {"offered_effective_bytes": 0,
                             "delivered_effective_bytes": 0,
                             "backlog_effective_bytes": 0,
                             "media_activity_bytes": delivered,
                             "oldest_wait_ns": None},
                },
                "job_progress": [], "maintenance_completion_ids": [],
            },
            "energy": {"component_energy_j": {"hbf0.base": total_j},
                       "scope_energy_j": {"read:base": total_j}, "total_j": total_j},
            "thermal": {"temperatures": {"gpu": 301, "hbf0": 302 + index, "hbm0": 303},
                        "stack_states": {"hbf0": "normal", "hbm0": "light" if index == 0 else "normal"},
                        "entity_temperatures": {},
                        "energy_j": {"window": {"activity_input_j": total_j,
                                                  "total_input_j": total_j},
                                     "cumulative": {"total_input_j": (60 if index == 0 else 100) * 50e-12}}},
            "control": {"budgets": {}, "next_budgets": {}},
            "reliability": {"status": "NO_MAINTENANCE_DEMAND_IN_BASE_RATE_WORKLOAD"},
            "causal": None,
        })
    (point / "windows.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    return point


class AnalyzerTest(unittest.TestCase):
    def test_point_strict_conservation_and_unavailable_labels(self):
        with tempfile.TemporaryDirectory() as td:
            point = make_point(Path(td))
            result = analyze_point(point)
            self.assertEqual(result["totals"]["offered_effective_bytes"], 100)
            self.assertEqual(result["totals"]["delivered_effective_bytes"], 100)
            self.assertEqual(result["totals"]["byte_conservation_error"], 0)
            self.assertEqual(result["per_stack"]["hbm0"]["media_activity_bytes"], 100)
            self.assertEqual(result["per_stack"]["hbm0"]["state_time_ns"]["light"], 20)
            self.assertEqual(result["token_metric"]["status"], "UNAVAILABLE")
            self.assertEqual(result["maintenance_metric"]["status"], "NOT_EXERCISED_NO_DEMAND")
            self.assertEqual(set(result["_trace"]["temperatures_k_by_owner"]),
                             {"gpu", "hbf0", "hbm0"})

    def test_detects_byte_failure(self):
        with tempfile.TemporaryDirectory() as td:
            point = make_point(Path(td), corrupt_backlog=True)
            with self.assertRaisesRegex(ValueError, "byte conservation"):
                analyze_point(point)

    def test_partial_campaign_outputs_six_panel_and_csv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            make_point(root)
            analysis = analyze_campaign(root, require_complete=False)
            self.assertEqual(analysis["completed_point_count"], 1)
            output = root / "derived"
            os.environ["MPLCONFIGDIR"] = str(root / "mpl-cache")
            write_outputs(analysis, output, plots=True)
            self.assertTrue((output / "per-stack-summary.csv").is_file())
            self.assertTrue((output / "six-panel-mixed-direct.png").is_file())
            self.assertTrue((output / "owner-temperatures-mixed-direct-guard-only-1536.png").is_file())
            self.assertIn("Token/s remains unavailable", (output / "SYSTEM_THERMAL_CAMPAIGN_ANALYSIS.md").read_text())


if __name__ == "__main__":
    unittest.main()

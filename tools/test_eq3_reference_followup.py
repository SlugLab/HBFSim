import argparse
import json
import pathlib
import tempfile
import unittest
from unittest import mock

import eq3_experiment_gate
import eq3_reference_followup as followup


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "eq3_thermal" / "reference"


class ReferenceFollowupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.temp = pathlib.Path(self.temporary.name)
        self.solver = self.temp / "solver"
        self.solver.write_bytes(b"fixed test solver bytes\n")
        self.inputs = (CONFIG / "package_scenario.json", CONFIG / "materials.json",
                       CONFIG / "power_traces.json", ROOT / "tools" / "eq3_reference.py",
                       self.solver)

    def tearDown(self):
        self.temporary.cleanup()

    def test_fine_case_changes_only_named_mesh_value(self):
        original = json.loads(self.inputs[0].read_text())
        derived = followup.fine_case(original)
        self.assertEqual(original["mesh_cell_m"]["fine"], 0.001)
        self.assertEqual(derived["mesh_cell_m"]["fine"], 0.0005)
        derived["mesh_cell_m"]["fine"] = original["mesh_cell_m"]["fine"]
        self.assertEqual(derived, original)

    def test_plan_is_exactly_four_serial_single_execution_runs(self):
        plan, derived = followup.build_plan(*self.inputs)
        followup.validate_fixed_plan(plan)
        self.assertEqual(len(plan["runs"]), 4)
        self.assertEqual(len({run["run_id"] for run in plan["runs"]}), 4)
        self.assertEqual({run["trace"] for run in plan["runs"]}, {"train", "heldout"})
        self.assertEqual({run["mesh_cell_m"] for run in plan["runs"]}, {0.001, 0.0005})
        self.assertTrue(plan["resource_limits"]["serial"])
        self.assertEqual(plan["resource_limits"]["total_cpu_seconds"], 1200.0)
        self.assertEqual(plan["inputs"]["derived_fine_case_sha256"],
                         followup.sha256_bytes(derived))
        self.assertIn("run-approved", plan["workflow_commands"]["approved_run"])
        self.assertIn("analyze", plan["workflow_commands"]["analysis"])

    def test_changed_original_mesh_is_refused(self):
        case = json.loads(self.inputs[0].read_text())
        case["mesh_cell_m"]["fine"] = 0.00075
        with self.assertRaises(ValueError):
            followup.fine_case(case)

    def test_missing_approval_refuses_before_output_creation(self):
        output = self.temp / "must-not-exist"
        plan_path = self.temp / "plan.json"
        manifest_path = self.temp / "manifest.json"
        approval_path = self.temp / "approval.json"
        plan, _ = followup.build_plan(*self.inputs)
        plan_path.write_text(json.dumps(plan))
        manifest = {"scientific_config": {
            "time_and_numerics": {"plan_sha256": followup.eq3_reference.sha256(plan_path),
                                  "solver_binary_sha256": plan["inputs"]["solver_binary_sha256"]},
            "scan_matrix_and_repetitions": {"run_ids": [r["run_id"] for r in plan["runs"]]}}}
        manifest_path.write_text(json.dumps(manifest))
        approval_path.write_text("{}")
        args = argparse.Namespace(case=self.inputs[0], materials=self.inputs[1], power=self.inputs[2],
                                  generator=self.inputs[3], solver=self.inputs[4], plan=plan_path,
                                  manifest=manifest_path, approval=approval_path, root=ROOT,
                                  output=output)
        refusal = eq3_experiment_gate.GateError("PENDING_USER_APPROVAL", "missing approval")
        with mock.patch.object(followup.eq3_experiment_gate, "load_json",
                               side_effect=[manifest, {},]), \
             mock.patch.object(followup.eq3_experiment_gate, "validate_gate", side_effect=refusal):
            with self.assertRaises(eq3_experiment_gate.GateError):
                followup.run_approved(args)
        self.assertFalse(output.exists())

    def test_changed_solver_is_refused_before_output_creation(self):
        output = self.temp / "must-not-exist-solver"
        plan_path = self.temp / "plan-solver.json"
        manifest_path = self.temp / "manifest-solver.json"
        approval_path = self.temp / "approval-solver.json"
        plan, _ = followup.build_plan(*self.inputs)
        plan_path.write_text(json.dumps(plan))
        manifest = {"scientific_config": {
            "time_and_numerics": {"plan_sha256": followup.eq3_reference.sha256(plan_path),
                                  "solver_binary_sha256": plan["inputs"]["solver_binary_sha256"]},
            "scan_matrix_and_repetitions": {"run_ids": [r["run_id"] for r in plan["runs"]]}}}
        manifest_path.write_text(json.dumps(manifest))
        approval_path.write_text("{}")
        self.solver.write_bytes(b"changed solver bytes\n")
        args = argparse.Namespace(case=self.inputs[0], materials=self.inputs[1], power=self.inputs[2],
                                  generator=self.inputs[3], solver=self.inputs[4], plan=plan_path,
                                  manifest=manifest_path, approval=approval_path, root=ROOT,
                                  output=output)
        with mock.patch.object(followup.eq3_experiment_gate, "validate_gate", return_value={}):
            with self.assertRaisesRegex(ValueError, "actual solver binary"):
                followup.run_approved(args)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

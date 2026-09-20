import copy
import hashlib
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from eq3_experiment_gate import canonical_manifest_hash
from eq3_layered_launch import (
    LaunchError,
    _sample_linux_rss,
    execute_launch,
    main,
    validate_launch,
)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


class LayeredLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "runs").mkdir()
        (self.root / "inputs").mkdir()
        backend = self.root / "test_backend.py"
        backend.write_text(
            "#!/usr/bin/env python3\n"
            "import os, pathlib, sys\n"
            "out = pathlib.Path(os.environ['EQ3_OUTPUT_DIR'])\n"
            "(out / 'marker.txt').write_text(' '.join(sys.argv[1:]) + '\\n')\n"
            "(pathlib.Path.cwd() / 'field_fixture.txt').write_text('thermal field\\n')\n"
            "(out / 'cuda_visible.txt').write_text(os.environ['CUDA_VISIBLE_DEVICES'])\n"
            "print('test double only')\n"
            "if '--fail' in sys.argv: sys.exit(7)\n",
            encoding="utf-8",
        )
        backend.chmod(0o755)
        (self.root / "inputs/input.json").write_text('{"power_w": 1}\n', encoding="utf-8")
        (self.root / "tests.json").write_text('{"status": "PASSED"}\n', encoding="utf-8")

        artifact = lambda logical_id, role, name: {
            "logical_id": logical_id,
            "semantic_role": role,
            "sha256": digest((self.root / name).read_bytes()),
            "path": name,
        }
        self.launch = {
            "schema_version": "eq3-layered-launch-v1",
            "experiment_id": "EQ3-LAYERED-REFERENCE-R01",
            "version": "1",
            "run_id": "R01",
            "stage": "thermal_only",
            "readiness": "READY",
            "backend": artifact("stock-3dice-backend", "thermal reference backend", "test_backend.py"),
            "artifacts": [artifact("layered-input", "generated layered input", "inputs/input.json")],
            "command": {"argv": ["test_backend.py", "--fixture"], "cwd": "inputs"},
            "output": {"path": "runs/R01", "max_new_gib": 0.01},
            "limits": {
                "threads": 1,
                "ram_gib": 1,
                "watchdog_seconds": 10,
                "gpu_compute_minutes": 0,
            },
        }
        self.launch_path = self.root / "launch.json"
        write_json(self.launch_path, self.launch)
        launch_artifact = artifact(
            "layered-launch-manifest", "approved layered launch binding", "launch.json"
        )
        resources = {
            "cpu_configurations": 1,
            "executions_per_configuration": 1,
            "cpu_hours": 0.1,
            "build_threads": 1,
            "ram_gib": 1,
            "disk_gib": 0.01,
            "gpu_compute_minutes": 0,
        }
        self.manifest = {
            "schema_version": "eq3-experiment-manifest-v1",
            "experiment_id": self.launch["experiment_id"],
            "version": self.launch["version"],
            "approval_status": "PENDING_USER_APPROVAL",
            "canonical_manifest_hash": "0" * 64,
            "scientific_config": {
                "research_question_and_hypothesis": {"question": "fixed fixture"},
                "evidence_type_and_limits": {"type": "test double"},
                "device_topology": {"topology": "fixture"},
                "geometry_materials_boundaries": {"geometry": "fixture"},
                "workload_and_initial_state": {"workload": "fixture"},
                "power_reliability_control": {"power": "fixture"},
                "scan_matrix_and_repetitions": {"points": 1},
                "time_and_numerics": {"step_s": 0.01},
                "controls_ablations_observations": {"control": "fixture"},
                "acceptance_abort_and_outputs": {"output": "fixture"},
            },
            "code": {
                "repository_revision": "abc123",
                "dirty_diff_sha256": "1" * 64,
                "artifacts": [artifact("launcher-code", "launcher source", "test_backend.py")],
            },
            "dependencies": [copy.deepcopy(self.launch["backend"])],
            "inputs": [launch_artifact, copy.deepcopy(self.launch["artifacts"][0])],
            "resource_budget": {"requested": resources, "limits": copy.deepcopy(resources)},
            "prerequisites": [
                {
                    "id": "fixed-tests",
                    "status": "PASSED",
                    "evidence": artifact("test-receipt", "fixed test evidence", "tests.json"),
                }
            ],
        }
        self.manifest["canonical_manifest_hash"] = canonical_manifest_hash(self.manifest)
        statement = (
            f"I approve experiment_id={self.manifest['experiment_id']} "
            f"version={self.manifest['version']} "
            f"canonical_manifest_hash={self.manifest['canonical_manifest_hash']} "
            "within its stated limits."
        )
        self.approval = {
            "schema_version": "eq3-user-confirmation-v1",
            "record_kind": "USER_CONFIRMATION",
            "experiment_id": self.manifest["experiment_id"],
            "version": self.manifest["version"],
            "canonical_manifest_hash": self.manifest["canonical_manifest_hash"],
            "evidence_class": "USER_CONFIRMED",
            "is_test_fixture": False,
            "source": {
                "type": "codex_user_message",
                "reference": "test-double-message",
                "captured_at": "2026-09-19T00:00:00Z",
                "statement": statement,
                "statement_sha256": digest(statement.encode()),
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, manifest=None, approval=object(), launch=None, launch_path=None):
        if approval.__class__ is object:
            approval = self.approval
        return validate_launch(
            manifest or self.manifest,
            approval,
            launch or self.launch,
            launch_path or self.launch_path,
            self.root,
            observed_code_revision="abc123",
            observed_dirty_diff_sha256="1" * 64,
        )

    def assert_refused(self, code, **kwargs):
        with self.assertRaises(LaunchError) as caught:
            self.validate(**kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(caught.exception.launch_performed)

    def rebuild_binding(self, launch):
        write_json(self.launch_path, launch)
        manifest = copy.deepcopy(self.manifest)
        bound = next(x for x in manifest["inputs"] if x["logical_id"] == "layered-launch-manifest")
        bound["sha256"] = digest(self.launch_path.read_bytes())
        known_inputs = {item["logical_id"]: item for item in manifest["inputs"]}
        for artifact in launch["artifacts"]:
            if artifact["logical_id"] in known_inputs:
                known_inputs[artifact["logical_id"]].update(copy.deepcopy(artifact))
            else:
                manifest["inputs"].append(copy.deepcopy(artifact))
        manifest["canonical_manifest_hash"] = canonical_manifest_hash(manifest)
        statement = (
            f"I approve experiment_id={manifest['experiment_id']} version={manifest['version']} "
            f"canonical_manifest_hash={manifest['canonical_manifest_hash']} within its stated limits."
        )
        approval = copy.deepcopy(self.approval)
        approval["canonical_manifest_hash"] = manifest["canonical_manifest_hash"]
        approval["source"]["statement"] = statement
        approval["source"]["statement_sha256"] = digest(statement.encode())
        return manifest, approval

    def test_ready_launch_is_bound_but_not_performed(self):
        result = self.validate()
        self.assertEqual(result["status"], "READY_TO_LAUNCH")
        self.assertEqual(result["run_id"], "R01")
        self.assertFalse(result["launch_performed"])

    def test_linux_rss_sample_parses_fixed_mock_status(self):
        status = (
            "Name:\tfixture\n"
            "VmHWM:\t   4321 kB\n"
            "VmRSS:\t   3210 kB\n"
            "Threads:\t1\n"
        )
        with mock.patch("pathlib.Path.read_text", return_value=status) as read_status:
            self.assertEqual(_sample_linux_rss(2468), (3210, 4321))
        read_status.assert_called_once_with(encoding="utf-8")

    def test_linux_rss_sample_is_unknown_when_proc_is_unavailable(self):
        with mock.patch("pathlib.Path.read_text", side_effect=FileNotFoundError):
            self.assertIsNone(_sample_linux_rss(2468))

    def test_missing_approval_is_refused(self):
        self.assert_refused("PENDING_USER_APPROVAL", approval=None)

    def test_changed_launch_bytes_are_refused(self):
        changed = copy.deepcopy(self.launch)
        changed["run_id"] = "R02"
        write_json(self.launch_path, changed)
        self.assert_refused("ARTIFACT_HASH_MISMATCH", launch=changed)

    def test_changed_scientific_input_is_refused(self):
        (self.root / "inputs/input.json").write_text('{"power_w": 2}\n', encoding="utf-8")
        self.assert_refused("ARTIFACT_HASH_MISMATCH")

    def test_unbound_file_in_input_directory_is_refused(self):
        (self.root / "inputs/unbound.txt").write_text("not approved\n", encoding="utf-8")
        self.assert_refused("UNBOUND_INPUT_PRESENT")

    def test_receipt_names_are_reserved_for_launcher_metadata(self):
        receipt_input = self.root / "inputs/DONE.json"
        receipt_input.write_text("{}\n", encoding="utf-8")
        launch = copy.deepcopy(self.launch)
        launch["artifacts"].append(
            {
                "logical_id": "forbidden-receipt-input",
                "semantic_role": "must be refused",
                "sha256": digest(receipt_input.read_bytes()),
                "path": "inputs/DONE.json",
            }
        )
        manifest, approval = self.rebuild_binding(launch)
        self.assert_refused(
            "RESERVED_INPUT_NAME", manifest=manifest, approval=approval, launch=launch
        )

    def test_incorrect_backend_hash_is_refused(self):
        launch = copy.deepcopy(self.launch)
        launch["backend"]["sha256"] = "f" * 64
        manifest, approval = self.rebuild_binding(launch)
        self.assert_refused(
            "BACKEND_HASH_MISMATCH", manifest=manifest, approval=approval, launch=launch
        )

    def test_wrong_stage_is_refused(self):
        launch = copy.deepcopy(self.launch)
        launch["stage"] = "system_full"
        manifest, approval = self.rebuild_binding(launch)
        self.assert_refused("STAGE_NOT_ALLOWED", manifest=manifest, approval=approval, launch=launch)

    def test_blocked_readiness_is_refused(self):
        launch = copy.deepcopy(self.launch)
        launch["readiness"] = "BLOCKED_RC_NODE_BUDGET"
        manifest, approval = self.rebuild_binding(launch)
        self.assert_refused("READINESS_BLOCKED", manifest=manifest, approval=approval, launch=launch)

    def test_excess_process_ram_is_refused(self):
        launch = copy.deepcopy(self.launch)
        launch["limits"]["ram_gib"] = 12.1
        manifest, approval = self.rebuild_binding(launch)
        self.assert_refused("LAUNCH_LIMIT_EXCEEDED", manifest=manifest, approval=approval, launch=launch)

    def test_twelve_gib_process_ceiling_is_allowed(self):
        launch = copy.deepcopy(self.launch)
        launch["limits"]["ram_gib"] = 12
        manifest, approval = self.rebuild_binding(launch)
        result = self.validate(manifest=manifest, approval=approval, launch=launch)
        self.assertEqual(result["status"], "READY_TO_LAUNCH")

    def test_explicit_stage_resource_policy_uses_bound_scope_limits(self):
        launch=copy.deepcopy(self.launch);launch['limits'].update(ram_gib=16,watchdog_seconds=3600)
        launch['output']['max_new_gib']=8;manifest,approval=self.rebuild_binding(launch)
        manifest['resource_budget']['requested'].update(ram_gib=16,disk_gib=8)
        stage={'status':'READY_FOR_SUBMISSION','experiment_id':manifest['experiment_id'],'version':manifest['version'],
          'resource_policy':'PER_EXPERIMENT_USER_CONFIRMED','resource_limits':{'task_ram_gib':20,'process_ram_gib':16,
            'threads':1,'watchdog_s':3600,'point_disk_gib':8,'task_disk_gib':40,'min_free_disk_gib':10,'gpu':0,'cloud':0}}
        with mock.patch('eq3_layered_launch.validate_gate',return_value=stage):
            self.assertEqual(self.validate(manifest=manifest,approval=approval,launch=launch)['status'],'READY_TO_LAUNCH')
            launch['limits']['watchdog_seconds']=3599
            self.assert_refused('LAUNCH_LIMIT_EXCEEDED',manifest=manifest,approval=approval,launch=launch)

    def test_each_hard_safety_ceiling_is_refused(self):
        changes = (
            ("limits", "threads", 2),
            ("limits", "watchdog_seconds", 601),
            ("limits", "gpu_compute_minutes", 1),
            ("output", "max_new_gib", 4.1),
        )
        for section, field, value in changes:
            with self.subTest(field=field):
                launch = copy.deepcopy(self.launch)
                launch[section][field] = value
                manifest, approval = self.rebuild_binding(launch)
                self.assert_refused(
                    "LAUNCH_LIMIT_EXCEEDED",
                    manifest=manifest,
                    approval=approval,
                    launch=launch,
                )

    def test_output_path_must_bind_run_id(self):
        launch = copy.deepcopy(self.launch)
        launch["output"]["path"] = "runs/not-this-run"
        manifest, approval = self.rebuild_binding(launch)
        self.assert_refused("RUN_BINDING_MISMATCH", manifest=manifest, approval=approval, launch=launch)

    def test_absolute_backend_path_is_refused(self):
        launch = copy.deepcopy(self.launch)
        launch["backend"]["path"] = str((self.root / "test_backend.py").resolve())
        launch["command"]["argv"][0] = launch["backend"]["path"]
        manifest, approval = self.rebuild_binding(launch)
        self.assert_refused("LAUNCH_PATH_INVALID", manifest=manifest, approval=approval, launch=launch)

    def test_executes_only_the_bound_test_double(self):
        result = execute_launch(
            self.manifest,
            self.approval,
            self.launch,
            self.launch_path,
            self.root,
            observed_code_revision="abc123",
            observed_dirty_diff_sha256="1" * 64,
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertTrue(result["launch_performed"])
        self.assertEqual((self.root / "runs/R01/marker.txt").read_text(), "--fixture\n")
        self.assertEqual(
            (self.root / "runs/R01/field_fixture.txt").read_text(), "thermal field\n"
        )
        self.assertFalse((self.root / "inputs/field_fixture.txt").exists())
        self.assertEqual((self.root / "runs/R01/cuda_visible.txt").read_text(), "")
        self.assertEqual(
            (self.root / "runs/R01/input.json").read_text(), '{"power_w": 1}\n'
        )
        self.assertTrue((self.root / "runs/R01/stdout.log").is_file())
        self.assertFalse((self.root / "runs/R01/FAILED.json").exists())
        receipt = json.loads((self.root / "runs/R01/DONE.json").read_text())
        self.assertEqual(receipt["status"], "DONE")
        self.assertEqual(receipt["exit_code"], 0)
        self.assertEqual(receipt["max_rss"], "UNKNOWN")
        self.assertGreaterEqual(receipt["sample_count"], 0)
        if receipt["sample_count"] == 0:
            self.assertEqual(receipt["sampled_peak_rss_kib"], "UNKNOWN")
            self.assertEqual(receipt["sampled_hwm_kib"], "UNKNOWN")
        else:
            self.assertIsInstance(receipt["sampled_peak_rss_kib"], int)
            self.assertIsInstance(receipt["sampled_hwm_kib"], int)
        self.assertEqual(
            receipt["rss_sampling_interpretation"], "lower_bound_not_exact_final_peak"
        )
        self.assertEqual(receipt["rss_sampling_scope"], "solver_process_pid_only_not_process_tree")
        self.assertEqual(receipt["backend_sha256"], self.launch["backend"]["sha256"])
        self.assertEqual(
            receipt["scientific_manifest_hash"], self.manifest["canonical_manifest_hash"]
        )
        output_paths = {item["path"] for item in receipt["output_file_sha256"]}
        self.assertIn("field_fixture.txt", output_paths)
        self.assertIn("input.json", output_paths)
        self.assertNotIn("DONE.json", output_paths)

    def test_backend_failure_writes_failed_receipt(self):
        launch = copy.deepcopy(self.launch)
        launch["command"]["argv"] = ["test_backend.py", "--fail"]
        manifest, approval = self.rebuild_binding(launch)
        with self.assertRaises(LaunchError) as caught:
            execute_launch(
                manifest,
                approval,
                launch,
                self.launch_path,
                self.root,
                observed_code_revision="abc123",
                observed_dirty_diff_sha256="1" * 64,
            )
        self.assertEqual(caught.exception.code, "BACKEND_FAILED")
        self.assertTrue(caught.exception.launch_performed)
        self.assertFalse((self.root / "runs/R01/DONE.json").exists())
        receipt = json.loads((self.root / "runs/R01/FAILED.json").read_text())
        self.assertEqual(receipt["status"], "FAILED")
        self.assertEqual(receipt["exit_code"], 7)
        self.assertTrue(receipt["launch_performed"])
        self.assertEqual(receipt["reason"]["code"], "BACKEND_FAILED")

    def test_cli_observes_explicit_code_root_separately_from_artifact_root(self):
        manifest_path = self.root / "manifest.json"
        approval_path = self.root / "approval.json"
        code_root = self.root / "portable-code-checkout"
        code_root.mkdir()
        write_json(manifest_path, self.manifest)
        write_json(approval_path, self.approval)
        output = StringIO()
        with mock.patch(
            "eq3_layered_launch.observed_git_state",
            return_value=("abc123", "1" * 64),
        ) as observe, redirect_stdout(output):
            status = main(
                [
                    "check",
                    "--manifest", str(manifest_path),
                    "--approval", str(approval_path),
                    "--launch", str(self.launch_path),
                    "--root", str(self.root),
                    "--code-root", str(code_root),
                ]
            )
        self.assertEqual(status, 0)
        observe.assert_called_once_with(code_root)
        self.assertEqual(json.loads(output.getvalue())["status"], "READY_TO_LAUNCH")


if __name__ == "__main__":
    unittest.main()

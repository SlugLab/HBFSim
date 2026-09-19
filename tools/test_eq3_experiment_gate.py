import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from eq3_experiment_gate import GateError, canonical_manifest_hash, validate_gate


def digest(content):
    return hashlib.sha256(content).hexdigest()


class ExperimentGateNegativeTests(unittest.TestCase):
    USE_DEFAULT = object()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        artifacts = {}
        for name, content in (("code.cc", b"code-v1\n"),
                              ("deps.lock", b"solver=4.0\n"),
                              ("input.json", b'{"power_w": 1}\n'),
                              ("unit-tests.json", b'{"status":"PASSED"}\n')):
            (self.root / name).write_bytes(content)
            artifacts[name] = digest(content)
        artifact = lambda logical_id, role, name: {
            "logical_id": logical_id, "semantic_role": role,
            "sha256": artifacts[name], "path": name,
        }
        resources = {
            "cpu_configurations": 1, "executions_per_configuration": 1,
            "cpu_hours": 0.25, "build_threads": 2, "ram_gib": 2,
            "disk_gib": 1, "gpu_compute_minutes": 0,
        }
        self.manifest = {
            "schema_version": "eq3-experiment-manifest-v1",
            "experiment_id": "EQ3-P2-NEGATIVE-TEST", "version": "1",
            "approval_status": "PENDING_USER_APPROVAL",
            "canonical_manifest_hash": "0" * 64,
            "scientific_config": {
                "research_question_and_hypothesis": {"question": "fixture question"},
                "evidence_type_and_limits": {"type": "numerical fixture"},
                "device_topology": {"topology": "fixture"},
                "geometry_materials_boundaries": {"geometry": "fixture"},
                "workload_and_initial_state": {"workload": "fixture"},
                "power_reliability_control": {"power": "fixture"},
                "scan_matrix_and_repetitions": {"points": 1},
                "time_and_numerics": {"step_s": 0.01},
                "controls_ablations_observations": {"control": "fixture"},
                "acceptance_abort_and_outputs": {"output": "fixture"},
            },
            "code": {"repository_revision": "abc123", "dirty_diff_sha256": "1" * 64,
                     "artifacts": [artifact("thermal-core", "simulator source", "code.cc")]},
            "dependencies": [artifact("solver-lock", "dependency lock", "deps.lock")],
            "inputs": [artifact("power-input", "scientific input", "input.json")],
            "resource_budget": {"requested": resources, "limits": copy.deepcopy(resources)},
            "prerequisites": [{"id": "unit-tests", "status": "PASSED",
                               "evidence": artifact("unit-test-receipt", "test evidence",
                                                    "unit-tests.json")}],
        }
        self.manifest["canonical_manifest_hash"] = canonical_manifest_hash(self.manifest)
        statement = (f"I approve experiment_id={self.manifest['experiment_id']} "
                     f"version={self.manifest['version']} "
                     f"canonical_manifest_hash={self.manifest['canonical_manifest_hash']} "
                     "within its stated limits.")
        self.approval = {
            "schema_version": "eq3-user-confirmation-v1", "record_kind": "USER_CONFIRMATION",
            "experiment_id": self.manifest["experiment_id"], "version": self.manifest["version"],
            "canonical_manifest_hash": self.manifest["canonical_manifest_hash"],
            "evidence_class": "USER_CONFIRMED", "is_test_fixture": False,
            "source": {"type": "codex_user_message", "reference": "message-id-from-client",
                       "captured_at": "2026-09-19T00:00:00Z", "statement": statement,
                       "statement_sha256": digest(statement.encode())},
        }

    def tearDown(self):
        self.temp.cleanup()

    def assert_refused(self, code, manifest=USE_DEFAULT, approval=USE_DEFAULT):
        manifest = self.manifest if manifest is self.USE_DEFAULT else manifest
        approval = self.approval if approval is self.USE_DEFAULT else approval
        with self.assertRaises(GateError) as caught:
            validate_gate(manifest, approval, self.root,
                          observed_code_revision="abc123",
                          observed_dirty_diff_sha256="1" * 64)
        self.assertEqual(caught.exception.code, code)

    def test_missing_approval_is_pending(self):
        self.assert_refused("PENDING_USER_APPROVAL", approval=None)

    def test_stale_approval_hash_is_refused(self):
        approval = copy.deepcopy(self.approval)
        approval["canonical_manifest_hash"] = "f" * 64
        self.assert_refused("APPROVAL_BINDING_MISMATCH", approval=approval)

    def test_scientific_config_change_invalidates_manifest_hash(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["scientific_config"]["time_and_numerics"]["step_s"] = 0.02
        self.assert_refused("MANIFEST_HASH_STALE", manifest=manifest)

    def test_input_content_change_is_refused(self):
        (self.root / "input.json").write_text('{"power_w": 2}\n')
        self.assert_refused("ARTIFACT_HASH_MISMATCH")

    def test_prerequisite_evidence_change_is_refused(self):
        (self.root / "unit-tests.json").write_text('{"status":"FAILED"}\n')
        self.assert_refused("PREREQUISITE_EVIDENCE_MISMATCH")

    def test_missing_prerequisite_evidence_is_refused(self):
        (self.root / "unit-tests.json").unlink()
        self.assert_refused("ARTIFACT_MISSING")

    def test_missing_scientific_preflight_section_is_refused(self):
        manifest = copy.deepcopy(self.manifest)
        del manifest["scientific_config"]["device_topology"]
        self.assert_refused("SCIENTIFIC_PREFLIGHT_INCOMPLETE", manifest=manifest)

    def test_observed_code_revision_change_is_refused(self):
        with self.assertRaises(GateError) as caught:
            validate_gate(self.manifest, self.approval, self.root,
                          observed_code_revision="different",
                          observed_dirty_diff_sha256="1" * 64)
        self.assertEqual(caught.exception.code, "CODE_REVISION_MISMATCH")

    def test_observed_tracked_diff_change_is_refused(self):
        with self.assertRaises(GateError) as caught:
            validate_gate(self.manifest, self.approval, self.root,
                          observed_code_revision="abc123",
                          observed_dirty_diff_sha256="3" * 64)
        self.assertEqual(caught.exception.code, "CODE_DIFF_MISMATCH")

    def test_requested_budget_above_limit_is_refused(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["resource_budget"]["requested"]["cpu_hours"] = 0.5
        self.assert_refused("RESOURCE_BUDGET_EXCEEDED", manifest=manifest)

    def test_declared_test_fixture_is_refused(self):
        approval = copy.deepcopy(self.approval)
        approval["is_test_fixture"] = True
        self.assert_refused("TEST_FIXTURE_REFUSED", approval=approval)


if __name__ == "__main__":
    unittest.main()

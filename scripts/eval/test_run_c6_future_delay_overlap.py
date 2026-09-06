import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
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

import run_c6_future_delay_overlap as target


def record(prework=10_000):
    arrival = 1_000_000
    work_end = arrival + prework + 4_000
    wait_exit = max(arrival + 20_000, work_end + 200)
    return {
        "helper_entry_ns": arrival - 100,
        "arrival_ns": arrival,
        "helper_issue_exit_ns": arrival + 1_000,
        "native_instruction_after_ns": arrival + 2_000,
        "work_begin_ns": arrival + prework,
        "work_end_ns": work_end,
        "wait_enter_ns": work_end + 100,
        "wait_exit_ns": wait_exit,
        "consumer_after_ns": wait_exit + 100,
    }


def binding_for(directory: Path):
    value = {
        "schema_version": 1,
        "selected_kernel": target.SELECTED_KERNEL,
        "native_kernel": target.NATIVE_KERNEL,
        "mapping_validation": "NOT_PROVEN",
        "compiler": {"tool": "ptxas", "architecture": "sm_120", "optimization": "-O3"},
    }
    payloads = {
        "original_ptx": b"original",
        "transformed_ptx": b"transformed",
        "cubin": b"cubin",
        "build_manifest": b"build",
        "disassembly_manifest": b"disassembly",
        "nvdisasm": b"nvdisasm",
        "cuobjdump": b"cuobjdump",
    }
    for name, data in payloads.items():
        path = directory / name
        path.write_bytes(data)
        artifact = {"path": str(path), "sha256": target.sha256(data)}
        if name in ("original_ptx", "transformed_ptx", "cubin"):
            artifact["bytes"] = len(data)
        value[name] = artifact
    path = directory / "binding.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path, value


def valid_raw(binding):
    launches = []
    for work in target.WORK_COUNTS:
        for arm in target.ARMS:
            for ordinal in range(target.WARMUPS + target.SAMPLES):
                launches.append({
                    "arm": arm,
                    "work_count": work,
                    "warmup": ordinal < target.WARMUPS,
                    "validation": "PASS",
                    "records": [record() for _lane in range(target.LANES)],
                })
    return {
        "schema_version": 1,
        "evidence": "GPU_ACQUISITION",
        "validation_status": "UNVALIDATED",
        "scientific_claim": False,
        "c6_3_closed": False,
        "c6_4_closed": False,
        "g5_closed": False,
        "overlap_closed": False,
        "native_completion_timing": False,
        "launches": launches,
        "native_image_binding": {
            "original_ptx_sha256": binding["original_ptx"]["sha256"],
            "transformed_ptx_sha256": binding["transformed_ptx"]["sha256"],
            "cubin_sha256": binding["cubin"]["sha256"],
            "build_manifest_sha256": binding["build_manifest"]["sha256"],
            "disassembly_manifest_sha256": binding["disassembly_manifest"]["sha256"],
            "nvdisasm_sha256": binding["nvdisasm"]["sha256"],
            "cuobjdump_sha256": binding["cuobjdump"]["sha256"],
            "mapping_validation": "NOT_PROVEN",
            "same_retained_buffer": True,
        },
    }


class BindingTests(unittest.TestCase):
    def test_exact_binding_and_files_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path, expected = binding_for(Path(directory))
            observed, paths = target.load_binding(path)
            frozen = target.verify_binding_files(observed, paths)
            self.assertEqual(observed, expected)
            self.assertEqual(set(frozen), set(paths))

    def test_bound_file_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _expected = binding_for(Path(directory))
            observed, paths = target.load_binding(path)
            paths["cubin"].write_bytes(b"other")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                target.verify_binding_files(observed, paths)

    def test_missing_bound_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _expected = binding_for(Path(directory))
            observed, paths = target.load_binding(path)
            paths["nvdisasm"].unlink()
            with self.assertRaises(FileNotFoundError):
                target.verify_binding_files(observed, paths)

    def test_extra_binding_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path, value = binding_for(Path(directory))
            value["unreviewed"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema mismatch"):
                target.load_binding(path)


class IntervalTests(unittest.TestCase):
    def test_measured_intervals_are_derived_from_timestamps(self):
        observed = target.derive_intervals(record(prework=12_000))
        self.assertEqual(observed["prework_from_anchor_ns"], 12_000)
        self.assertEqual(observed["work_ns"], 4_000)
        self.assertEqual(observed["issue_after_anchor_ns"], 1_000)

    def test_disordered_timestamps_are_rejected(self):
        invalid = record()
        invalid["work_end_ns"] = invalid["work_begin_ns"] - 1
        with self.assertRaisesRegex(ValueError, "not ordered"):
            target.derive_intervals(invalid)

    def test_issue_covered_delay_is_non_identifying(self):
        observed = target.classify_future_d([record(prework=20_000) for _ in range(32)])
        self.assertEqual(observed["state"], "NON_IDENTIFYING_ISSUE_OVERHEAD_COVERS_DELAY")
        self.assertEqual(observed["lanes_with_delay_expired_before_work"], 32)
        self.assertFalse(observed["g5_scored"])

    def test_short_prework_only_reports_unvalidated_window(self):
        observed = target.classify_future_d([record(prework=10_000) for _ in range(32)])
        self.assertEqual(observed["state"], "IDENTIFYING_WINDOW_OBSERVED_UNVALIDATED")
        self.assertFalse(observed["overlap_claim"])


class RawValidationTests(unittest.TestCase):
    def test_complete_fixed_matrix_remains_unvalidated(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, binding = binding_for(Path(directory))
            analysis = target.validate_raw(valid_raw(binding), binding)
            self.assertEqual(analysis["status"], "TIMING_INTERVALS_CAPTURED_UNVALIDATED")
            self.assertEqual(len(analysis["future_d_samples"]), 20)
            self.assertFalse(analysis["g5_closed"])

    def test_missing_launch_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, binding = binding_for(Path(directory))
            raw = valid_raw(binding)
            raw["launches"].pop()
            with self.assertRaisesRegex(ValueError, "launch count mismatch"):
                target.validate_raw(raw, binding)

    def test_integer_false_cannot_impersonate_claim_boolean(self):
        with tempfile.TemporaryDirectory() as directory:
            _path, binding = binding_for(Path(directory))
            raw = valid_raw(binding)
            raw["scientific_claim"] = 0
            with self.assertRaisesRegex(ValueError, "scientific_claim"):
                target.validate_raw(raw, binding)


class FinalizeTests(unittest.TestCase):
    def test_pending_signal_vetoes_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signals = {"signal": 15}
            status = {"state": "RUNNING_UNVALIDATED"}
            final = target.finalize_attempt(
                root, {"schema_version": 1}, status,
                "CAPTURED_UNVALIDATED", signals,
                inventory_fn=lambda _path: ({}, []),
                atomic_fn=lambda path, value: path.write_text(json.dumps(value)),
            )
            self.assertEqual(final, "INTERRUPTED")
            self.assertEqual(json.loads((root / "status.json").read_text())["state"],
                             "INTERRUPTED")


if __name__ == "__main__":
    unittest.main()

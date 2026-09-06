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

import run_c6_future_correctness_candidate as target


class FinalizeSignalTests(unittest.TestCase):
    def seal(self, signal_at=None, rejected=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            signals = {"signal": None}
            writes = []

            def inventory(_out):
                if signal_at == "inventory":
                    signals["signal"] = 15
                return {}, ["unsafe"] if rejected else []

            def atomic(path, value):
                path.write_text(json.dumps(value), encoding="utf-8")
                writes.append(path.name)
                if signal_at == "manifest" and path.name == "manifest.json":
                    signals["signal"] = 15
                if (signal_at == "status" and path.name == "status.json" and
                        writes.count("status.json") == 1):
                    signals["signal"] = 15

            if signal_at == "before":
                signals["signal"] = 15
            status = {"state": "RUNNING_UNVALIDATED"}
            final = target.finalize_attempt(
                root, {"schema_version": 1}, status,
                "CAPTURED_UNVALIDATED", signals,
                inventory_fn=inventory, atomic_fn=atomic,
            )
            persisted = json.loads((root / "status.json").read_text())
            return final, persisted, writes

    def test_no_signal_retains_unvalidated_capture(self):
        final, persisted, _writes = self.seal()
        self.assertEqual(final, "CAPTURED_UNVALIDATED")
        self.assertEqual(persisted["state"], "CAPTURED_UNVALIDATED")

    def test_signal_before_or_during_seal_cannot_succeed(self):
        for point in ("before", "inventory", "manifest", "status"):
            with self.subTest(point=point):
                final, persisted, writes = self.seal(point)
                self.assertEqual(final, "INTERRUPTED")
                self.assertEqual(persisted["state"], "INTERRUPTED")
                self.assertEqual(persisted["signal"], 15)
                if point == "status":
                    self.assertEqual(writes.count("status.json"), 2)

    def test_rejected_artifact_cannot_succeed(self):
        final, persisted, _writes = self.seal(rejected=True)
        self.assertEqual(final, "INVALID_DIAGNOSTIC")
        self.assertEqual(persisted["state"], "INVALID_DIAGNOSTIC")


if __name__ == "__main__":
    unittest.main()

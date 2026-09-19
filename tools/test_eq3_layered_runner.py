"""Fixed driver checks for the default-off layered RC runner.

Set EQ3_LAYERED_RC_RUNNER to an explicitly compiled runner binary.  Without
that opt-in these tests skip, so ordinary Python discovery cannot start a
solver accidentally.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent

MODEL = """HBFSIM_EQ3_THERMAL_MODEL 1
coupling on
node n0 hbf capacity_memory hbf0 0 2 300 0 0.5 295
"""

EVENTS = """HBFSIM_EQ3_THERMAL_EVENTS 1
activity 1 request-1 external_heat external 0 -1 -1 -1 0 0.1 0.1 n0 1
"""


class LayeredRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.environ.get("EQ3_LAYERED_RC_RUNNER")
        if not configured:
            raise unittest.SkipTest("EQ3_LAYERED_RC_RUNNER is not set")
        cls.runner = Path(configured).resolve()
        if not cls.runner.is_file() or not os.access(cls.runner, os.X_OK):
            raise unittest.SkipTest("EQ3_LAYERED_RC_RUNNER is not an executable file")

    def invoke(self, cwd, model, events, mode, *extra):
        command = [
            str(self.runner), "--model", str(model), "--events", str(events),
            "--step-s", "0.01", "--slot-s", "0.1", "--end-s", "0.1",
            "--sample-s", "0.1", "--min-k", "250", "--max-k", "500",
            mode, *extra,
        ]
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)

    def test_fixed_tiny_inspect_and_run_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            model = output / "model.txt"
            events = output / "events.txt"
            model.write_text(MODEL)
            events.write_text(EVENTS)

            inspected = self.invoke(output, model, events, "--inspect-only")
            self.assertEqual(0, inspected.returncode, inspected.stderr)
            receipt = json.loads(inspected.stdout)
            self.assertFalse(receipt["solver_constructed"])
            self.assertEqual(1, receipt["nodes"])
            self.assertEqual(10, receipt["total_steps"])
            self.assertEqual(1.0, receipt["activity_declared_energy_j"])
            self.assertFalse((output / "rc_energy_receipt.json").exists())

            executed = self.invoke(output, model, events, "--run")
            self.assertEqual(0, executed.returncode, executed.stderr)
            rows = executed.stdout.splitlines()
            self.assertEqual("time_s,node_id,temperature_k", rows[0])
            self.assertEqual(3, len(rows))  # header, declared t0, sample at end_s
            self.assertTrue(rows[1].startswith("0,n0,"))
            energy = json.loads((output / "rc_energy_receipt.json").read_text())
            self.assertTrue(energy["t0_emitted"])
            self.assertEqual(10, energy["total_steps"])
            self.assertEqual(energy["static_dt_key_upper_bound"], energy["factorization_count"])
            self.assertAlmostEqual(1.0, energy["activity_applied_energy_j"], places=12)
            self.assertAlmostEqual(0.0, energy["activity_energy_difference_j"], places=12)
            self.assertAlmostEqual(0.0, energy["energy_residual_j"], places=10)

            refused = self.invoke(output, model, events, "--run")
            self.assertEqual(2, refused.returncode)
            self.assertIn("refusing to overwrite", refused.stderr)

    def test_generated_candidate_inspect_only_when_available(self):
        generated = Path(os.environ.get("EQ3_LAYERED_INPUTS", WORKSPACE / "generated/layered-v3/train-4mm-20ms"))
        if not generated.is_dir():
            self.skipTest("generated first-example input is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            command = [
                str(self.runner), "--model", str(generated / "model.txt"),
                "--events", str(generated / "events.txt"), "--step-s", "0.005",
                "--slot-s", "0.5", "--end-s", "100", "--sample-s", "0.1",
                "--min-k", "250", "--max-k", "500", "--inspect-only",
            ]
            result = subprocess.run(
                command, cwd=directory, text=True, capture_output=True, check=False
            )
            self.assertEqual(0, result.returncode, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertFalse(receipt["solver_constructed"])
            self.assertEqual(3087, receipt["nodes"])
            self.assertEqual(20_000, receipt["total_steps"])
            self.assertLess(receipt["estimated_factor_cache_bytes"], 12 * 1024**3)
            self.assertFalse((Path(directory) / "rc_energy_receipt.json").exists())


if __name__ == "__main__":
    unittest.main()

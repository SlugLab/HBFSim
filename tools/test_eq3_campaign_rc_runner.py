"""Opt-in equivalence checks for the isolated campaign sparse RC runner.

The ordinary suite skips unless both binaries are explicitly supplied.  This
prevents test discovery from starting numerical work without a resource slot.
"""

import csv
import io
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
node a hbf capacity_memory stack0 0 2 300 1 0.5 295
node b hbm fast_memory stack1 0 3 301 0.5 0.2 296
edge a b 1 component
"""

EVENTS = """HBFSIM_EQ3_THERMAL_EVENTS 1
activity 1 request-a external_heat external 0 -1 -1 -1 0 0.2 0.2 a 1
activity 2 request-b external_heat external 0 -1 -1 -1 0.1 0.2 0.2 b 2
"""


class CampaignSparseRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sparse = os.environ.get("EQ3_CAMPAIGN_RC_RUNNER")
        dense = os.environ.get("EQ3_LAYERED_RC_RUNNER")
        if not sparse or not dense:
            raise unittest.SkipTest(
                "EQ3_CAMPAIGN_RC_RUNNER and EQ3_LAYERED_RC_RUNNER are required"
            )
        cls.sparse = Path(sparse).resolve()
        cls.dense = Path(dense).resolve()
        for binary in (cls.sparse, cls.dense):
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise unittest.SkipTest(f"runner is not executable: {binary}")

    def command(self, binary, model, events, mode, end="0.2"):
        return [
            str(binary), "--model", str(model), "--events", str(events),
            "--step-s", "0.01", "--slot-s", "0.1", "--end-s", end,
            "--sample-s", "0.1", "--min-k", "250", "--max-k", "500", mode,
        ]

    @staticmethod
    def execute(command, directory):
        return subprocess.run(
            command, cwd=directory, text=True, capture_output=True, check=False
        )

    @staticmethod
    def csv_values(text):
        rows = list(csv.DictReader(io.StringIO(text)))
        return {
            (float(row["time_s"]), row["node_id"]): float(row["temperature_k"])
            for row in rows
        }

    def test_sparse_matches_existing_dense_equation_on_fixed_fixture(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            model = root / "model.txt"
            events = root / "events.txt"
            model.write_text(MODEL)
            events.write_text(EVENTS)
            dense_dir = root / "dense"
            sparse_dir = root / "sparse"
            dense_dir.mkdir()
            sparse_dir.mkdir()

            dense = self.execute(
                self.command(self.dense, model, events, "--run"), dense_dir
            )
            sparse = self.execute(
                self.command(self.sparse, model, events, "--run"), sparse_dir
            )
            self.assertEqual(0, dense.returncode, dense.stderr)
            self.assertEqual(0, sparse.returncode, sparse.stderr)
            dense_values = self.csv_values(dense.stdout)
            sparse_values = self.csv_values(sparse.stdout)
            self.assertEqual(dense_values.keys(), sparse_values.keys())
            for key in dense_values:
                self.assertAlmostEqual(dense_values[key], sparse_values[key], places=10)

            dense_energy = json.loads((dense_dir / "rc_energy_receipt.json").read_text())
            sparse_energy = json.loads((sparse_dir / "rc_energy_receipt.json").read_text())
            self.assertEqual("Eigen::SimplicialLDLT_AMD", sparse_energy["backend"])
            self.assertEqual(1, sparse_energy["factorization_count"])
            for field in (
                "activity_applied_energy_j", "static_input_energy_j",
                "stored_energy_change_j", "boundary_loss_j", "energy_residual_j",
            ):
                self.assertAlmostEqual(dense_energy[field], sparse_energy[field], places=9)

    def test_inspect_does_not_create_solver_or_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            model = root / "model.txt"
            events = root / "events.txt"
            model.write_text(MODEL)
            events.write_text(EVENTS)
            result = self.execute(
                self.command(self.sparse, model, events, "--inspect-only"), root
            )
            self.assertEqual(0, result.returncode, result.stderr)
            inspected = json.loads(result.stdout)
            self.assertFalse(inspected["solver_constructed"])
            self.assertEqual("Eigen::SimplicialLDLT_AMD", inspected["backend"])
            self.assertEqual(4, inspected["matrix_structural_nnz"])
            self.assertTrue(inspected["factor_fill_memory_unmeasured_until_pilot"])
            self.assertFalse((root / "rc_energy_receipt.json").exists())

    def test_zero_source_equilibrium_exact_with_nonhardcoded_origin(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            model, events = root/'model.txt', root/'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 0.00001 312 0 0.01 312\n'
                             'node b hbf capacity_memory s1 0 20 312 0 2 312\n'
                             'edge a b 10000 component\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n')
            command = self.command(self.sparse,model,events,'--run')
            command[command.index('--min-k')+1] = '312'
            result = self.execute(command,root)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(all(value==312 for value in self.csv_values(result.stdout).values()))
            receipt=json.loads((root/'rc_energy_receipt.json').read_text())
            self.assertEqual(receipt['temperature_origin_k'],312)
            self.assertFalse(receipt['temperature_clamping'])
            for field in ('stored_energy_change_j','boundary_loss_j','energy_residual_j'):
                self.assertEqual(receipt[field],0)

    def test_real_domain_violation_not_clamped(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            model,events=root/'model.txt',root/'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 1 300 0 1 290\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n')
            command=self.command(self.sparse,model,events,'--run')
            command[command.index('--min-k')+1]='300'
            result=self.execute(command,root)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('temperature left required domain',result.stderr)

    def test_candidate_equilibrium_diagnostic_when_available(self):
        generated=WORKSPACE/'generated/layered-v3/train-4mm-20ms'
        if not generated.is_dir():
            self.skipTest('generated candidate unavailable')
        with tempfile.TemporaryDirectory() as root:
            command=self.command(self.sparse,generated/'model.txt',generated/'events.txt',
                                 '--equilibrium-diagnostic',end='100')
            command[command.index('--step-s')+1]='0.005'
            command[command.index('--slot-s')+1]='0.5'
            result=self.execute(command,root)
            self.assertEqual(result.returncode,0,result.stderr)
            receipt=json.loads(result.stdout)
            self.assertFalse(receipt['workload_executed'])
            self.assertEqual(receipt['theta_max_abs_k'],0)
            self.assertLess(receipt['absolute_max_equilibrium_error_k'],1e-8)

    def test_generated_candidate_inspect_when_available(self):
        generated = WORKSPACE / "generated/layered-v1/train-4mm-20ms"
        if not generated.is_dir():
            self.skipTest("generated candidate is unavailable")
        with tempfile.TemporaryDirectory() as root:
            command = [
                str(self.sparse), "--model", str(generated / "model.txt"),
                "--events", str(generated / "events.txt"), "--step-s", "0.005",
                "--slot-s", "0.5", "--end-s", "100", "--sample-s", "0.1",
                "--min-k", "250", "--max-k", "500", "--inspect-only",
            ]
            result = self.execute(command, root)
            self.assertEqual(0, result.returncode, result.stderr)
            inspected = json.loads(result.stdout)
            self.assertFalse(inspected["solver_constructed"])
            self.assertEqual(3087, inspected["nodes"])
            self.assertEqual(20_000, inspected["total_steps"])
            self.assertLess(inspected["matrix_structural_nnz"], 20_000)


if __name__ == "__main__":
    unittest.main()

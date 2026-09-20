"""Opt-in equivalence checks for the isolated campaign sparse RC runner.

The ordinary suite skips unless both binaries are explicitly supplied.  This
prevents test discovery from starting numerical work without a resource slot.
"""

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

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
        generated = os.environ.get("EQ3_GENERATED_ROOT")
        cls.generated_root = Path(generated).resolve() if generated else None
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

    def test_two_node_one_step_matches_independent_hand_solution(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);model,events=root/'model.txt',root/'events.txt'
            model.write_text(MODEL)
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n')
            command=self.command(self.sparse,model,events,'--run',end='.01')
            command[command.index('--slot-s')+1]='.01'
            command[command.index('--sample-s')+1]='.01'
            result=self.execute(command,root)
            self.assertEqual(result.returncode,0,result.stderr)
            values=self.csv_values(result.stdout)
            a=2/.01+.5+1;d=3/.01+.2+1
            rhs_a=2/.01*300+1+.5*295
            rhs_b=3/.01*301+.5+.2*296
            determinant=a*d-1
            expected_a=(d*rhs_a+rhs_b)/determinant
            expected_b=(rhs_a+a*rhs_b)/determinant
            self.assertAlmostEqual(values[(.01,'a')],expected_a,places=11)
            self.assertAlmostEqual(values[(.01,'b')],expected_b,places=11)

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

    def test_steady_envelope_solves_fixed_and_all_source_cap_rhs(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            model, events = root / 'model.txt', root / 'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 1 300 1 2 300\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n'
                              'activity 1 cap external_heat external 0 -1 -1 -1 0 1 1 a 4\n')
            command = self.command(self.sparse, model, events,
                                   '--steady-envelope', end='1')
            command += ['--envelope-limit-k', '302']
            result = self.execute(command, root)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt['status'], 'PREDICTED_ENVELOPE')
            self.assertEqual(receipt['selected_alpha'], .75)
            self.assertEqual(receipt['cap_total_w'], 4)
            self.assertAlmostEqual(receipt['candidates'][0]['max_k'], 302.5)
            self.assertLess(receipt['fixed_residual_inf'], 1e-12)
            self.assertLess(receipt['cap_residual_inf'], 1e-12)

    def test_steady_envelope_rejects_network_without_heat_outlet(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            model, events = root / 'model.txt', root / 'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 1 300 0 0 300\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n'
                              'activity 1 cap external_heat external 0 -1 -1 -1 0 1 1 a 4\n')
            command = self.command(self.sparse, model, events,
                                   '--steady-envelope', end='1')
            command += ['--envelope-limit-k', '380']
            result = self.execute(command, root)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('at least one heat-rejection boundary', result.stderr)

    def test_steady_envelope_does_not_search_below_frozen_alpha_set(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            model, events = root / 'model.txt', root / 'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 1 300 0 2 300\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n'
                              'activity 1 cap external_heat external 0 -1 -1 -1 0 1 1 a 400\n')
            command = self.command(self.sparse, model, events,
                                   '--steady-envelope', end='1')
            command += ['--envelope-limit-k', '320']
            result = self.execute(command, root)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt['status'], 'DOMAIN_REDESIGN_REQUIRED')
            self.assertIsNone(receipt['selected_alpha'])
            self.assertEqual([item['alpha'] for item in receipt['candidates']],
                             [1, .75, .5, .25])

    def test_node_reordering_preserves_small_model_solution(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);events=root/'events.txt'
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n'
                              'activity 1 q external_heat external 0 -1 -1 -1 0 0.2 0.2 a 1\n')
            models=[
                'node a hbf capacity_memory s0 0 2 300 0.1 0.5 295\n'
                'node b hbm fast_memory s1 0 3 301 0.2 0.2 296\n'
                'node c gpu compute gpu 0 4 302 0.3 0.4 297\n'
                'edge a b 1 component\nedge b c 2 component\n',
                'node c gpu compute gpu 0 4 302 0.3 0.4 297\n'
                'node a hbf capacity_memory s0 0 2 300 0.1 0.5 295\n'
                'node b hbm fast_memory s1 0 3 301 0.2 0.2 296\n'
                'edge a b 1 component\nedge b c 2 component\n']
            values=[];receipts=[]
            for number,body in enumerate(models):
                directory=root/str(number);directory.mkdir();model=directory/'model.txt'
                model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'+body)
                result=self.execute(self.command(self.sparse,model,events,'--run'),directory)
                self.assertEqual(result.returncode,0,result.stderr)
                values.append(self.csv_values(result.stdout))
                receipts.append(json.loads((directory/'rc_energy_receipt.json').read_text()))
            self.assertEqual(values[0].keys(),values[1].keys())
            for key in values[0]:
                self.assertAlmostEqual(values[0][key],values[1][key],places=11)
            for field in ('total_input_energy_j','stored_energy_change_j',
                          'boundary_loss_j','energy_residual_j'):
                self.assertAlmostEqual(receipts[0][field],receipts[1][field],places=11)

    def test_real_domain_violation_not_clamped(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            model,events=root/'model.txt',root/'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 1 300 0 0 300\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n'
                              'activity 1 q external_heat external 0 -1 -1 -1 0 0.2 0.2 a 30\n')
            command=self.command(self.sparse,model,events,'--run')
            command[command.index('--max-k')+1]='302'
            model_sha=hashlib.sha256(model.read_bytes()).hexdigest()
            events_sha=hashlib.sha256(events.read_bytes()).hexdigest()
            source_sha=hashlib.sha256((ROOT/'tools/eq3_campaign_rc_runner.cpp').read_bytes()).hexdigest()
            command += ['--model-sha256',model_sha,
                        '--events-sha256',events_sha,
                        '--runner-source-sha256',source_sha,
                        '--domain-version','fixture-domain-v1']
            result=self.execute(command,root)
            print('DOMAIN_REPRO_STDERR:',result.stderr.strip())
            self.assertNotEqual(result.returncode,0)
            self.assertIn('temperature left required domain',result.stderr)
            diagnostic_path=root/'rc_failure_diagnostic.json'
            if diagnostic_path.exists():
                print('DOMAIN_FAILURE_DIAGNOSTIC:',diagnostic_path.read_text())
                print('DOMAIN_LAST_VALID_STATE:',(root/'rc_failure_last_valid.csv').read_text())
                print('DOMAIN_TRIAL_STATE:',(root/'rc_failure_trial.csv').read_text())
            diagnostic=json.loads(diagnostic_path.read_text())
            self.assertEqual(diagnostic['status'],'DOMAIN_FAILURE')
            self.assertTrue(diagnostic['failure_returned_to_caller'])
            self.assertFalse(diagnostic['temperature_clamping'])
            self.assertEqual(diagnostic['last_valid_time_s'],.01)
            self.assertEqual(diagnostic['trial_target_time_s'],.02)
            self.assertEqual(diagnostic['completed_steps'],1)
            self.assertEqual(diagnostic['trial_step_1_based'],2)
            self.assertEqual(diagnostic['input_interval_start_s'],0)
            self.assertEqual(diagnostic['input_interval_end_s'],.1)
            self.assertEqual(diagnostic['model_sha256'],model_sha)
            self.assertEqual(diagnostic['events_sha256'],events_sha)
            self.assertEqual(diagnostic['runner_source_sha256'],source_sha)
            self.assertEqual(diagnostic['domain_version'],'fixture-domain-v1')
            self.assertTrue(diagnostic['offending_nodes'])
            self.assertEqual(diagnostic['offending_nodes'][0]['id'],'a')
            self.assertEqual(diagnostic['failed_trial_step_energy']['status'],
                             'UNKNOWN_NOT_INTEGRATED')
            self.assertIsNone(diagnostic['failed_trial_step_energy']['energy_residual_j'])
            completed=diagnostic['completed_interval_energy']
            self.assertAlmostEqual(completed['activity_input_j'],1.5)
            self.assertAlmostEqual(completed['stored_energy_change_j'],1.5)
            self.assertAlmostEqual(completed['energy_residual_j'],0)
            self.assertTrue((root/'rc_failure_last_valid.csv').is_file())
            self.assertTrue((root/'rc_failure_trial.csv').is_file())

    def test_nonfinite_input_is_rejected_before_trial(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);model,events=root/'model.txt',root/'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 nan 300 0 1 290\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n')
            result=self.execute(self.command(self.sparse,model,events,'--run'),root)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('malformed node record',result.stderr)
            self.assertFalse((root/'rc_failure_diagnostic.json').exists())

    def test_nonfinite_trial_is_recorded_as_numerical_failure(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);model,events=root/'model.txt',root/'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 1 300 0 0 300\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n'
                              'activity 1 q external_heat external 0 -1 -1 -1 0 0.2 0.2 a 1e308\n')
            result=self.execute(self.command(self.sparse,model,events,'--run'),root)
            self.assertNotEqual(result.returncode,0)
            diagnostic=json.loads((root/'rc_failure_diagnostic.json').read_text())
            self.assertEqual(diagnostic['status'],'NUMERICAL_FAILURE')
            self.assertGreater(diagnostic['nonfinite_trial_nodes'],0)
            self.assertFalse(diagnostic['trial_extrema_available'])
            self.assertIsNone(diagnostic['trial_max_k'])

    def test_failure_evidence_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);model,events=root/'model.txt',root/'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 1 300 0 1 290\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n')
            marker=root/'rc_failure_diagnostic.json';marker.mkdir()
            result=self.execute(self.command(self.sparse,model,events,'--run'),root)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('refusing to overwrite RC output',result.stderr)
            self.assertTrue(marker.is_dir())

    def test_failure_evidence_write_error_still_returns_failure(self):
        if not Path('/proc').is_dir():
            self.skipTest('/proc read-only pseudo-filesystem is unavailable')
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);model,events=root/'model.txt',root/'events.txt'
            model.write_text('HBFSIM_EQ3_THERMAL_MODEL 1\ncoupling on\n'
                             'node a hbf capacity_memory s0 0 1 300 0 1 290\n')
            events.write_text('HBFSIM_EQ3_THERMAL_EVENTS 1\n')
            command=self.command(self.sparse,model,events,'--run')
            command[command.index('--min-k')+1]='300'
            result=self.execute(command,Path('/proc'))
            self.assertNotEqual(result.returncode,0)
            self.assertIn('cannot create failure state',result.stderr)

    def test_candidate_equilibrium_diagnostic_when_available(self):
        if self.generated_root is None:
            self.skipTest('EQ3_GENERATED_ROOT was not explicitly supplied')
        generated=self.generated_root/'layered-v3/train-4mm-20ms'
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
        if self.generated_root is None:
            self.skipTest('EQ3_GENERATED_ROOT was not explicitly supplied')
        generated = self.generated_root / "layered-v1/train-4mm-20ms"
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

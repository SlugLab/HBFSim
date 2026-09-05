#!/usr/bin/env python3
"""Real CPU replay integration; synthetic controls are never measured data."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import replay_arrivals
from replay_arrivals import run_replay, validate_raw

ROOT = Path(__file__).resolve().parents[2]
BINARY = ROOT / 'build-eval-implementation/hbf_concurrent_trace_timing'


class ReplayManifestTests(unittest.TestCase):
    def setUp(self):
        if not BINARY.is_file():
            self.skipTest('optional CPU concurrent replay binary has not been built')
        directory = ROOT / 'results/gold/replay-wrapper/tmp'
        directory.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.profile = self.directory / 'profile.json'
        profile = json.loads((ROOT / 'configs/profiles/nominal.json').read_text())
        profile.update(capacity_bytes=16 << 30, hbm_cache_bytes=64 << 20,
                       queue_depth=2, time_scale=1)
        self.profile.write_text(json.dumps(profile))
        self.rows = [dict(request_id=i+1, issue_ns=0, consume_deadline=50000,
                          logical_address=i*16384, bytes=16384, operation='read',
                          layer=0, step=0, sequence=i % 2,
                          resource='mqsim_media', channel='profile') for i in range(4)]
        self.arrivals = self.directory / 'arrivals.jsonl'
        self.arrivals.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        self.out = self.directory / 'replay'

    def run_control(self, **kwargs):
        return run_replay(profile=self.profile, arrivals=self.arrivals, binary=BINARY,
                          out=self.out, source_kind='synthetic_control', **kwargs)

    def test_frozen_inputs_raw_hash_and_evidence_boundary(self):
        manifest = self.run_control()
        self.assertEqual(manifest['provenance'], 'PROJECTED')
        self.assertEqual(manifest['resource_class'], 'CPU_ONLY')
        self.assertFalse(manifest['hardware_validated'])
        self.assertEqual(manifest['status'], 'VALIDATED_RAW')
        self.assertFalse((self.out / 'status.json').exists(), 'scheduler owns DONE')
        self.profile.write_text('mutated original input')
        for item in manifest['artifacts'].values():
            path = self.out / item['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item['sha256'])
        raw = json.loads((self.out / 'raw.json').read_text())
        self.assertEqual(raw['summary']['completed'], 4)
        self.assertEqual(Path(manifest['argv'][0]), BINARY)
        self.assertEqual(len(manifest['git']['git_sha']), 40)

    def test_existing_destination_is_preserved(self):
        self.out.mkdir()
        sentinel = self.out / 'sentinel'
        sentinel.write_text('keep')
        with self.assertRaises(FileExistsError):
            self.run_control()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_failed_replay_keeps_diagnostics_and_has_no_validated_receipt(self):
        with self.assertRaises(RuntimeError):
            self.run_control(parallel_units=7)
        receipt = json.loads((self.out / 'replay-manifest.json').read_text())
        self.assertEqual(receipt['status'], 'FAILED')
        self.assertFalse((self.out / 'raw.json').exists())
        self.assertIn('PROJECTED_ANALYTICAL', (self.out / 'stderr.log').read_text())

    def test_closed_loop_policy_recorded_separately(self):
        manifest = self.run_control(arrival_mode='closed_loop_qd')
        raw = json.loads((self.out / 'raw.json').read_text())
        self.assertEqual(manifest['arrival_process'], 'closed_loop_qd')
        self.assertGreater(raw['requests'][-1]['issue_ns'], 0)

    def test_validator_rejects_input_and_byte_mismatch(self):
        self.run_control()
        raw = json.loads((self.out / 'raw.json').read_text())
        raw['requests'][0]['logical_address'] += 512
        with self.assertRaisesRegex(ValueError, 'identity'):
            validate_raw(raw, self.rows, 'fixed_arrival_trace')
        raw = json.loads((self.out / 'raw.json').read_text())
        raw['summary']['completed_bytes'] -= 512
        with self.assertRaisesRegex(ValueError, 'conservation'):
            validate_raw(raw, self.rows, 'fixed_arrival_trace')

    def test_validator_rejects_changed_closed_loop_policy(self):
        self.run_control(arrival_mode='closed_loop_qd')
        raw = json.loads((self.out / 'raw.json').read_text())
        row = raw['requests'][0]
        row.update(issue_ns=1, queue_enter=1, service_start=1)
        row['service_delay'] -= 1
        with self.assertRaisesRegex(ValueError, 'closed-loop'):
            validate_raw(raw, self.rows, 'closed_loop_qd')

    def test_validator_rejects_impossible_and_mismatched_qd_histogram(self):
        self.run_control()
        raw = json.loads((self.out / 'raw.json').read_text())
        horizon = raw['qd_distribution']['end_ns']-raw['qd_distribution']['start_ns']
        for durations in ({'999': horizon+1, '-1': -1}, {'0': horizon}):
            with self.subTest(durations=durations):
                raw['qd_distribution']['duration_ns'] = durations
                with self.assertRaisesRegex(ValueError, 'queue occupancy'):
                    validate_raw(raw, self.rows, 'fixed_arrival_trace')

    def test_frozen_profile_mutation_cannot_get_validated_receipt(self):
        original_run = replay_arrivals.subprocess.run
        def mutate_then_run(command, **kwargs):
            if command[0] == str(BINARY):
                frozen = self.out / 'profile.json'
                profile = json.loads(frozen.read_text())
                profile['queue_depth'] = 1
                frozen.write_text(json.dumps(profile))
            return original_run(command, **kwargs)
        with mock.patch.object(replay_arrivals.subprocess, 'run', side_effect=mutate_then_run):
            with self.assertRaisesRegex(ValueError, 'frozen input'):
                self.run_control()
        self.assertEqual(json.loads((self.out / 'replay-manifest.json').read_text())['status'], 'FAILED')

    def test_peak_qd_cannot_contradict_observed_occupancy(self):
        self.run_control()
        raw = json.loads((self.out / 'raw.json').read_text())
        self.assertEqual(raw['summary']['peak_device_qd'], 2)
        raw['summary']['peak_device_qd'] = 1
        for row in raw['requests']:
            row['qd'] = 1
        with self.assertRaisesRegex(ValueError, 'queue occupancy peak'):
            validate_raw(raw, self.rows, 'fixed_arrival_trace')

    def test_raw_validation_and_hash_bind_the_same_bytes(self):
        original_validate = replay_arrivals.validate_raw
        def mutate_after_validation(raw, inputs, mode):
            original_validate(raw, inputs, mode)
            raw['requests'][0]['logical_address'] += 512
            (self.out / 'raw.json').write_text(json.dumps(raw))
        with mock.patch.object(replay_arrivals, 'validate_raw', side_effect=mutate_after_validation):
            with self.assertRaisesRegex(ValueError, 'raw artifact changed'):
                self.run_control()
        self.assertEqual(json.loads((self.out / 'replay-manifest.json').read_text())['status'], 'FAILED')


if __name__ == '__main__':
    unittest.main()

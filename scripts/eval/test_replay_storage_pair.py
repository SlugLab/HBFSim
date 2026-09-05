"""CPU-only metadata and native MQSim controls; never storage payload I/O."""
import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import freeze_storage_split
from freeze_storage_split import freeze_profile, prepare_split
from run_manifest import atomic_json, sha256

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/eval/replay_storage_pair.py'
BINARY = ROOT / 'build-eval-implementation/hbf_concurrent_trace_timing'


class StoragePairTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'storage pairing implementation is missing')
        spec = importlib.util.spec_from_file_location('replay_storage_pair', SCRIPT)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.temp = tempfile.TemporaryDirectory(prefix='.storage-pair-test-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.profile = self.base / 'profile.json'
        profile = json.loads((ROOT / 'configs/profiles/nominal.json').read_text())
        profile.update(capacity_bytes=16 << 30, hbm_cache_bytes=64 << 20, queue_depth=1, time_scale=1)
        atomic_json(self.profile, profile)
        with (ROOT / 'docs/49-eval-audit/run-matrix.csv').open() as stream:
            rows = [r for r in csv.DictReader(stream) if r['group'] == 'flash_fidelity'
                    and r['request_bytes'] in ('4096', '8192') and r['qd'] == '1'
                    and r['workload'] == 'sequential_read']
        matrix = self.base / 'matrix.csv'
        with matrix.open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
        self.rows = rows
        entries = {}
        for size, family in ((s, f) for s in (4096, 8192) for f in ('closed_loop_qd', 'fixed_arrival_trace')):
            requests = [dict(request_id=i, logical_address=i * size, bytes=size, operation='read') for i in range(3)]
            metadata = dict(schema_version=1, seed=17, request_bytes=size, qd=1,
                            workload='sequential_read', arrival_process=family)
            path = self.base / (str(size) + '-' + family + '.input')
            if family == 'closed_loop_qd':
                atomic_json(path, dict(metadata, replenish='on_completion', file_span_bytes=size * 3,
                                       alignment_bytes=4096, requests=requests))
                entry = dict(kind=family, path=str(path), sha256=sha256(path))
            else:
                for i, r in enumerate(requests): r['issue_ns'] = i * 10
                path.write_text(''.join(json.dumps(r) + '\n' for r in requests))
                meta = self.base / (str(size) + '-arrival-metadata.json'); atomic_json(meta, metadata)
                entry = dict(kind=family, path=str(path), sha256=sha256(path),
                             metadata_path=str(meta), metadata_sha256=sha256(meta))
            entries.update({r['cell_id']: entry for r in rows if r['arrival_process'] == family and r['request_bytes'] == str(size)})
        self.device = self.base / 'device.json'
        atomic_json(self.device, dict(state='BLOCKED_STORAGE_BUSY', test_only=True))
        index = self.base / 'index.json'; atomic_json(index, dict(schema_version=1, cells=entries))
        self.split = self.base / 'split'
        self.frozen = prepare_split(matrix, index, self.profile, self.device, self.split)

    def collection(self, family='fixed_arrival_trace', size=4096):
        source = self.base / ('collection-' + family); source.mkdir()
        cell = next(c for c in self.frozen['cells'] if c['backend'] == 'physical'
                    and c['condition']['arrival_process'] == family and c['condition']['request_bytes'] == str(size))
        pair = next(p for p in self.frozen['pairs'] if p['pair_id'] == cell['pair_id'])
        records = []
        for i, actual in enumerate([100, 90, 200] if family == 'fixed_arrival_trace' else [100, 300, 500]):
            requested = i * 10 if family == 'fixed_arrival_trace' else None
            records.append(dict(event='issue', request_id=i, offset=i * size, bytes=size,
                                operation='read', requested_issue_ns=requested,
                                scheduled_ns=i * 10 if requested is not None else max(0, actual - 90),
                                provenance='TEST_ONLY'))
            records.append(dict(event='completion', request_id=i, offset=i * size, bytes=size,
                                requested_bytes=size, operation='read', requested_issue_ns=requested,
                                actual_submit_ns=actual, completion_ns=actual + 100,
                                observed_completion_ns=actual + 110, latency_ns=100,
                                admission_lag_ns=actual - requested if requested is not None else None,
                                in_steady=True, provenance='TEST_ONLY'))
        (source / 'raw.requests.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records))
        summary = dict(provenance='TEST_ONLY', measurement_scope='test_fixture', mode='pilot',
                       issued=3, completed=3, completed_bytes=size * 3, outstanding=0,
                       steady_completed=3, steady_completed_bytes=size * 3,
                       warmup_seconds=0, steady_seconds=.001, throughput_bytes_per_second=size * 3000.,
                       iops=3000., latency_p50_ns=100, latency_p99_ns=100,
                       percentile_method='nearest_rank_completed_in_steady',
                       formal_acquisition_bounds_met=False, scientific_validation_passed=False)
        atomic_json(source / 'raw.summary.json', summary)
        (source / 'frozen.input').write_bytes((self.split / pair['input_path']).read_bytes())
        plan = dict(kind=family, qd=1, condition=cell['condition'], cell_id=cell['cell_id'], role=cell['role'],
                    seed=17, replicate=1, input_sha256=pair['input_sha256'],
                    split_id=self.frozen['split_id'], split_manifest_sha256=sha256(self.split / 'manifest.json'),
                    matrix_sha256=self.frozen['matrix_sha256'], profile_sha256=sha256(self.profile),
                    device_manifest_sha256=sha256(self.device), validation_id=None)
        manifest = dict(schema_version=1, test_only=True, complete=True, state='TEST_ONLY_DONE',
                        cell_id=cell['cell_id'], run_id='TEST_ONLY-physical', replicate=1,
                        split=str(self.split), profile_freeze=None, plan=plan, request_count=3,
                        settings=dict(mode='pilot', warmup_seconds=0, steady_seconds=.001))
        self.seal_source(source, manifest)
        return source

    def seal_source(self, source, manifest):
        manifest['artifact_hashes'] = {p.name: sha256(p) for p in source.iterdir()
                                     if p.name not in ('manifest.json', 'status.json')}
        atomic_json(source / 'manifest.json', manifest)
        atomic_json(source / 'status.json', dict(state=manifest['state'], manifest_sha256=sha256(source / 'manifest.json')))

    def run_pair(self, source, mode='observed_arrivals', **kwargs):
        self.assertTrue(BINARY.is_file(), 'required native CPU binary is missing')
        return self.api.run_pair(collection=source, profile=self.profile, binary=BINARY,
                                 out=self.base / 'pair', mode=mode, cell_id=json.loads((source / 'manifest.json').read_text())['cell_id'],
                                 replicate=1, **kwargs)

    def test_observed_native_replay_exact_actual_stream_and_mock_boundary(self):
        source = self.collection()
        receipt = self.run_pair(source)
        result = self.api.validate_pair(self.base / 'pair')
        self.assertEqual(receipt['validation'], 'VALIDATED_PAIR')
        self.assertEqual(result['provenance'], 'MOCK')
        self.assertFalse(result['scientific_validation_passed'])
        self.assertEqual([r['physical_issue_ns'] for r in result['requests']], [90, 100, 200])
        self.assertEqual([r['model_issue_ns'] for r in result['requests']], [90, 100, 200])
        self.assertEqual([r['collector_request_id'] for r in result['requests']], [1, 0, 2])
        self.assertEqual([r['offset'] for r in result['requests']], [4096, 0, 8192])
        self.assertFalse((self.base / 'pair/status.json').exists())
        self.assertEqual(result['physical']['p50_us'], .1)

    def test_closed_loop_policy_uses_order_and_native_replenishment(self):
        result = self.run_pair(self.collection('closed_loop_qd'), mode='closed_loop_policy')
        result = self.api.validate_pair(self.base / 'pair')
        self.assertEqual(result['replay_arrival_mode'], 'closed_loop_qd')
        self.assertFalse(result['observed_closed_loop_diagnostic'])
        rows = result['requests']
        self.assertEqual([r['offset'] for r in rows], [0, 4096, 8192])
        self.assertEqual(rows[0]['model_issue_ns'], 0)
        self.assertEqual(rows[1]['model_issue_ns'], rows[0]['model_completion_ns'])
        self.assertNotEqual(rows[1]['model_issue_ns'], rows[1]['physical_issue_ns'])

    def test_closed_source_observed_mode_is_explicit_diagnostic(self):
        self.run_pair(self.collection('closed_loop_qd'))
        result = self.api.validate_pair(self.base / 'pair')
        self.assertTrue(result['observed_closed_loop_diagnostic'])
        self.assertEqual(result['replay_arrival_mode'], 'fixed_arrival_trace')

    def test_fixed_source_cannot_be_reinterpreted_as_closed_loop(self):
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.run_pair(self.collection(), mode='closed_loop_policy')

    def test_half_open_windows_independent_populations_empty_and_signed_errors(self):
        rows = [dict(request_id=1, bytes=512, issue_ns=50, completion_ns=100),
                dict(request_id=2, bytes=512, issue_ns=100, completion_ns=200),
                dict(request_id=3, bytes=512, issue_ns=110, completion_ns=199)]
        stats = self.api.steady_metrics(rows, 100, 200)
        self.assertEqual(stats['request_ids'], [1, 3])
        self.assertEqual((stats['p50_us'], stats['p99_us']), (.05, .089))
        self.assertEqual(stats['throughput_gbs'], 10.24)
        self.assertEqual(stats['iops'], 20000000.)
        empty = self.api.steady_metrics(rows, 201, 300)
        self.assertEqual(empty['completed'], 0)
        self.assertIsNone(empty['p50_us']); self.assertIsNone(empty['p99_us'])
        self.assertIsNone(self.api.relative_error(1, 0))
        self.assertIsNone(self.api.relative_error(None, 1))
        self.assertEqual(self.api.relative_error(1, 2), -50)

    def test_wrong_profile_and_replicate_reject(self):
        source = self.collection()
        original = self.profile.read_bytes()
        self.profile.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'profile|source'):
            self.run_pair(source)
        self.profile.write_bytes(original)
        with self.assertRaisesRegex(ValueError, 'replicate'):
            self.api.run_pair(collection=source, profile=self.profile, binary=BINARY,
                              out=self.base / 'wrong-replicate', mode='observed_arrivals',
                              cell_id=json.loads((source / 'manifest.json').read_text())['cell_id'], replicate=2)

    def test_native_qd_mismatch_rejects_without_python_profile_parser(self):
        profile = json.loads(self.profile.read_text()); profile['queue_depth'] = 2
        self.profile = self.base / 'qd2-profile.json'; atomic_json(self.profile, profile)
        self.split = self.base / 'qd2-split'
        self.frozen = prepare_split(self.base / 'matrix.csv', self.base / 'index.json', self.profile,
                                    self.device, self.split)
        source = self.collection('closed_loop_qd')
        with self.assertRaisesRegex(ValueError, 'QD|queue depth'):
            self.run_pair(source, mode='closed_loop_policy')

    def test_mock_formal_paths_reject_before_creation_including_alias(self):
        source = self.collection()
        simulated = self.base / 'checkout'; (simulated / 'results/runs').mkdir(parents=True)
        alias = simulated / 'alias'; alias.symlink_to(simulated / 'results/runs', target_is_directory=True)
        for parent in (simulated / 'results/runs', alias):
            with patch.object(self.api, 'ROOT', simulated):
                with self.assertRaisesRegex(ValueError, 'formal'):
                    self.api.run_pair(collection=source, profile=self.profile, binary=BINARY,
                                      out=parent / 'forbidden', mode='observed_arrivals', cell_id='x', replicate=1)
            self.assertFalse((parent / 'forbidden').exists())

    def test_failed_cpu_execution_retains_failure_manifest_and_logs(self):
        source = self.collection()
        with self.assertRaises(RuntimeError):
            self.api.run_pair(collection=source, profile=self.profile, binary=Path('/usr/bin/false').resolve(),
                              out=self.base / 'pair', mode='observed_arrivals',
                              cell_id=json.loads((source / 'manifest.json').read_text())['cell_id'], replicate=1)
        receipt = json.loads((self.base / 'pair/pair-manifest.json').read_text())
        self.assertEqual(receipt['validation'], 'FAILED')
        self.assertTrue((self.base / 'pair/replay/stderr.log').is_file())
        self.assertFalse((self.base / 'pair/status.json').exists())

    def test_independent_validator_rejects_resealed_false_metrics(self):
        self.run_pair(self.collection())
        out = self.base / 'pair'
        result = json.loads((out / 'raw.pair.json').read_text())
        result['physical']['p50_us'] = 12345
        atomic_json(out / 'raw.pair.json', result)
        receipt = json.loads((out / 'pair-manifest.json').read_text())
        receipt['artifacts']['raw.pair.json'] = sha256(out / 'raw.pair.json')
        atomic_json(out / 'pair-manifest.json', receipt)
        with self.assertRaisesRegex(ValueError, 'recomputed|comparison'):
            self.api.validate_pair(out)

    def test_failed_final_semantics_cannot_leave_validated_manifest(self):
        source = self.collection()
        original = self.api.atomic_json
        def corrupt_after_write(path, value):
            original(path, value)
            if path.name == 'raw.pair.json':
                value = copy.deepcopy(value); value['physical']['p99_us'] += 1
                original(path, value)
        with patch.object(self.api, 'atomic_json', corrupt_after_write):
            with self.assertRaisesRegex(ValueError, 'recomputed|comparison'):
                self.run_pair(source)
        receipt = json.loads((self.base / 'pair/pair-manifest.json').read_text())
        self.assertEqual(receipt['validation'], 'FAILED')

    def test_empty_model_window_keeps_physical_population_and_null_percentiles(self):
        source = self.collection()
        manifest = json.loads((source / 'manifest.json').read_text())
        manifest['settings']['steady_seconds'] = .000001
        summary = json.loads((source / 'raw.summary.json').read_text())
        summary.update(steady_seconds=.000001, throughput_bytes_per_second=12288000000., iops=3000000.)
        atomic_json(source / 'raw.summary.json', summary); self.seal_source(source, manifest)
        self.run_pair(source)
        result = self.api.validate_pair(self.base / 'pair')
        self.assertEqual(result['physical']['completed'], 3)
        self.assertEqual(result['model']['completed'], 0)
        self.assertIsNone(result['model']['p50_us']); self.assertIsNone(result['model']['p99_us'])
        self.assertIsNone(result['signed_relative_error_pct']['p99_us'])
        self.assertEqual(result['model']['throughput_gbs'], 0)
        self.assertEqual(len(result['requests']), 3)

    def test_fractional_collector_interval_reconciles_its_original_iops_formula(self):
        source = self.collection()
        manifest = json.loads((source / 'manifest.json').read_text())
        manifest['settings']['steady_seconds'] = .9
        summary = json.loads((source / 'raw.summary.json').read_text())
        summary.update(steady_seconds=.9, throughput_bytes_per_second=12288 / .9, iops=3 / .9)
        atomic_json(source / 'raw.summary.json', summary); self.seal_source(source, manifest)
        self.assertNotEqual(summary['iops'], 3e9 / 900000000)
        self.run_pair(source)
        result = self.api.validate_pair(self.base / 'pair')
        self.assertEqual(result['window']['end_ns'], 900000000)
        self.assertEqual(result['physical']['iops'], 3e9 / 900000000)

    def test_final_validator_uses_frozen_inputs_after_originals_disappear(self):
        source = self.collection(); self.run_pair(source)
        self.profile.unlink()
        (source / 'raw.requests.jsonl').unlink()
        (self.split / 'split.json').unlink()
        self.assertEqual(self.api.validate_pair(self.base / 'pair')['provenance'], 'MOCK')

    def test_resealed_wrong_frozen_extent_or_identity_rejects(self):
        for field in ('offset', 'cell_id', 'replicate', 'split_id'):
            with self.subTest(field=field):
                source = self.collection()
                manifest = json.loads((source / 'manifest.json').read_text())
                if field == 'offset':
                    rows = [json.loads(line) for line in (source / 'raw.requests.jsonl').read_text().splitlines()]
                    for row in rows: row['offset'] += 4096
                    (source / 'raw.requests.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
                else:
                    manifest['plan'][field] = 2 if field == 'replicate' else 'wrong'
                self.seal_source(source, manifest)
                with self.assertRaises(ValueError): self.run_pair(source)
                # Each subcase has an independent immutable output and source.
                source.rename(source.with_name('done-' + field))
                (self.base / 'pair').rename(self.base / ('failed-' + field))

    def test_heldout_requires_matching_frozen_profile_validation_identity(self):
        source = self.collection(size=8192)
        manifest = json.loads((source / 'manifest.json').read_text())
        with self.assertRaisesRegex(ValueError, 'heldout'):
            self.run_pair(source)
        (self.base / 'pair').rename(self.base / 'no-profile')
        fit = self.base / 'TEST_ONLY-fit.json'
        atomic_json(fit, dict(schema_version=1, status='FIT_COMPLETED', calibration_id='TEST_ONLY-no-fit-run',
                             split_id=self.frozen['split_id'], profile_sha256=sha256(self.profile),
                             fit_input_sha256=sha256(self.split / 'calibration/fit-inputs.json'),
                             calibration_cell_ids=[c['cell_id'] for c in self.frozen['cells'] if c['role'] == 'calibration']))
        frozen_path = self.base / 'profile-freeze'
        frozen = freeze_profile(self.split, self.profile, fit, frozen_path)
        manifest['profile_freeze'] = str(frozen_path)
        manifest['plan']['validation_id'] = frozen['validation_id']
        self.seal_source(source, manifest); self.run_pair(source)
        result = self.api.validate_pair(self.base / 'pair')
        self.assertEqual(result['identity']['validation_id'], frozen['validation_id'])
        self.assertEqual(result['provenance'], 'MOCK')
        (self.base / 'pair').rename(self.base / 'valid-heldout')
        manifest['plan']['validation_id'] = 'different-fit'
        self.seal_source(source, manifest)
        with self.assertRaisesRegex(ValueError, 'profile freeze identity'):
            self.run_pair(source)

    def test_boolean_replicate_or_qd_cannot_match_integer_identity(self):
        for field in ('replicate', 'qd'):
            source = self.collection()
            manifest = json.loads((source / 'manifest.json').read_text())
            manifest['plan'][field] = True
            self.seal_source(source, manifest)
            with self.assertRaises(ValueError): self.run_pair(source)
            source.rename(source.with_name('bool-source-' + field))
            (self.base / 'pair').rename(self.base / ('bool-' + field))

    def test_raw_snapshots_do_not_inherit_small_profile_metadata_limit(self):
        raw = self.base / 'raw'; raw.mkdir(); (raw / 'raw.json').write_bytes(b'x' * 128)
        with patch.object(freeze_storage_split, 'MAX_METADATA_BYTES', 64):
            self.assertEqual(self.api.tree_snapshot(raw)['raw.json'], b'x' * 128)

    def test_existing_output_and_source_corruption_are_preserved_and_rejected(self):
        source = self.collection()
        out = self.base / 'pair'; out.mkdir(); (out / 'sentinel').write_text('keep')
        with self.assertRaises(FileExistsError): self.run_pair(source)
        self.assertEqual((out / 'sentinel').read_text(), 'keep')
        (source / 'raw.requests.jsonl').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.api.run_pair(collection=source, profile=self.profile, binary=BINARY,
                              out=self.base / 'corrupt', mode='observed_arrivals',
                              cell_id=json.loads((source / 'manifest.json').read_text())['cell_id'], replicate=1)


if __name__ == '__main__':
    unittest.main()

"""CPU metadata fixtures: no device payloads, fitting, or performance samples."""
import copy
import csv
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from run_manifest import sha256

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/eval/freeze_storage_split.py'
MATRIX = ROOT / 'docs/49-eval-audit/run-matrix.csv'


class StorageSplitTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'storage split implementation is missing')
        spec = importlib.util.spec_from_file_location('freeze_storage_split', SCRIPT)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.temp = tempfile.TemporaryDirectory(prefix='.storage-split-test-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        with MATRIX.open() as stream:
            self.all_rows = list(csv.DictReader(stream))
        self.rows = [r for r in self.all_rows if r['group'] == 'flash_fidelity'
                     and r['request_bytes'] in ('4096', '8192') and r['qd'] == '1'
                     and r['workload'] == 'sequential_read']
        self.matrix = self.base / 'matrix.csv'
        self.write_matrix(self.rows)
        self.profile = self.write_json('initial-profile.json', {'tR_ns': 1000})
        self.device = self.write_json('device.json', {'state': 'BLOCKED_STORAGE_BUSY',
                                                    'reason': 'no authorized dedicated file'})
        self.entries = {}
        for size in (4096, 8192):
            for family in ('closed_loop_qd', 'fixed_arrival_trace'):
                metadata = dict(schema_version=1, seed=17, request_bytes=size, qd=1,
                                workload='sequential_read', arrival_process=family)
                records = [dict(logical_address=i * size, bytes=size, operation='read')
                           for i in range(3)]
                if family == 'fixed_arrival_trace':
                    for i, record in enumerate(records):
                        record.update(issue_ns=i * 1000, request_id=i)
                    path = self.base / f'{size}-arrival.jsonl'
                    path.write_text(''.join(json.dumps(r) + '\n' for r in records))
                    meta = self.write_json(f'{size}-metadata.json', metadata)
                    entry = dict(kind=family, path=str(path), sha256=sha256(path),
                                 metadata_path=str(meta), metadata_sha256=sha256(meta))
                else:
                    path = self.write_json(f'{size}-policy.json', dict(metadata,
                                           replenish='on_completion', file_span_bytes=size * 3,
                                           alignment_bytes=4096, requests=records))
                    entry = dict(kind=family, path=str(path), sha256=sha256(path))
                for row in self.rows:
                    if row['request_bytes'] == str(size) and row['arrival_process'] == family:
                        self.entries[row['cell_id']] = copy.deepcopy(entry)
        self.index = self.write_json('index.json', dict(schema_version=1, cells=self.entries))
        self.out = self.base / 'split'

    def write_json(self, name, value):
        path = self.base / name
        path.write_text(json.dumps(value, sort_keys=True) + '\n')
        return path

    def write_matrix(self, rows):
        with self.matrix.open('w') as stream:
            writer = csv.DictWriter(stream, fieldnames=self.all_rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def prepare(self, out=None):
        return self.api.prepare_split(self.matrix, self.index, self.profile, self.device,
                                      out or self.out)

    def alter_input(self, family, transform):
        entry = next(e for e in self.entries.values() if e['kind'] == family)
        path = Path(entry['path'])
        transform(path)
        for e in self.entries.values():
            if e['path'] == str(path):
                e['sha256'] = sha256(path)
        self.write_json('index.json', dict(schema_version=1, cells=self.entries))

    def calibrated(self, suffix='one'):
        split = self.api.load_split(self.out)
        profile = self.write_json(f'calibrated-{suffix}.json', {'tR_ns': 2000, 'id': suffix})
        ids = [c['cell_id'] for c in split['cells'] if c['role'] == 'calibration']
        receipt = self.write_json(f'receipt-{suffix}.json', dict(
            schema_version=1, status='FIT_COMPLETED', calibration_id=suffix,
            split_id=split['split_id'], profile_sha256=sha256(profile),
            fit_input_sha256=sha256(self.out / 'calibration/fit-inputs.json'),
            calibration_cell_ids=ids))
        return profile, receipt

    def test_actual_matrix_roles_and_strata(self):
        pairs = self.api.load_pairs(MATRIX)
        self.assertEqual(len(pairs), 256)
        cells = [c for pair in pairs for c in pair['cells']]
        self.assertEqual(len(cells), 512)
        self.assertEqual(sum(c['split'] == 'calibration' for c in cells), 128)
        strata = {(c['workload'], c['arrival_process'], c['split']) for c in cells}
        self.assertEqual(len(strata), 8)
        for c in cells:
            calibration = int(c['request_bytes']) in (4096, 16384, 65536, 262144) and int(c['qd']) in (1, 4, 16, 64)
            self.assertEqual(c['split'], 'calibration' if calibration else 'heldout')

    def test_matrix_conflicting_split_duplicate_and_unpaired_rejected(self):
        variants = [self.rows + [self.rows[0]], self.rows[1:]]
        wrong = copy.deepcopy(self.rows)
        wrong[0]['split'] = 'heldout'
        variants.append(wrong)
        logical_duplicate = copy.deepcopy(self.rows[0])
        logical_duplicate['cell_id'] = 'new-id'
        variants.append(self.rows + [logical_duplicate])
        for rows in variants:
            with self.subTest(rows=len(rows)):
                self.write_matrix(rows)
                with self.assertRaises(ValueError):
                    self.api.load_pairs(self.matrix)

    def test_pair_scientific_dimensions_cannot_disagree(self):
        rows = copy.deepcopy(self.rows)
        rows[0]['time_scale'] = '99'
        self.write_matrix(rows)
        with self.assertRaises(ValueError):
            self.api.load_pairs(self.matrix)

    def test_split_freezes_pair_hashes_and_preserves_blocked_device(self):
        self.prepare()
        split = self.api.load_split(self.out)
        self.assertEqual(len(split['cells']), 8)
        self.assertEqual(split['matrix_sha256'], sha256(self.matrix))
        self.assertEqual(split['profile_sha256'], sha256(self.profile))
        self.assertEqual(split['device_manifest_sha256'], sha256(self.device))
        self.assertEqual(split['device_state'], 'BLOCKED_STORAGE_BUSY')
        self.assertFalse(split['hardware_ready'])
        self.assertTrue(split['frozen_at'].endswith('+00:00'))
        for pair in split['pairs']:
            cells = [c for c in split['cells'] if c['pair_id'] == pair['pair_id']]
            self.assertEqual(len(cells), 2)
            self.assertEqual(cells[0]['input_sha256'], cells[1]['input_sha256'])
            self.assertEqual(cells[0]['seed'], cells[1]['seed'])
            expected = 'arrival_sha256' if pair['kind'] == 'fixed_arrival_trace' else 'policy_sha256'
            self.assertIn(expected, pair)
            self.assertNotIn('arrival_sha256' if expected == 'policy_sha256' else 'policy_sha256', pair)

    def test_missing_extra_and_mismatched_pair_inputs_rejected(self):
        original = copy.deepcopy(self.entries)
        variants = [dict(list(original.items())[1:]), dict(original, unknown=next(iter(original.values())))]
        mismatch = copy.deepcopy(original)
        fixed = [k for k, v in mismatch.items() if v['kind'] == 'fixed_arrival_trace']
        mismatch[fixed[0]] = mismatch[fixed[-1]]
        variants.append(mismatch)
        for entries in variants:
            self.write_json('index.json', dict(schema_version=1, cells=entries))
            with self.assertRaises(ValueError):
                self.prepare()
            self.assertFalse(self.out.exists())

    def test_same_size_pair_different_timestamps_or_seed_rejected(self):
        original = copy.deepcopy(self.entries)
        cell = next(k for k, e in original.items() if e['kind'] == 'fixed_arrival_trace')
        for mismatch in ('timestamp', 'seed'):
            self.entries = copy.deepcopy(original)
            entry = self.entries[cell]
            if mismatch == 'timestamp':
                records = [json.loads(line) for line in Path(entry['path']).read_text().splitlines()]
                records[-1]['issue_ns'] += 1
                path = self.base / 'other-trace.jsonl'
                path.write_text(''.join(json.dumps(r) + '\n' for r in records))
                entry.update(path=str(path), sha256=sha256(path))
            else:
                metadata = json.loads(Path(entry['metadata_path']).read_text())
                metadata['seed'] += 1
                path = self.write_json('other-meta.json', metadata)
                entry.update(metadata_path=str(path), metadata_sha256=sha256(path))
            self.write_json('index.json', dict(schema_version=1, cells=self.entries))
            with self.assertRaisesRegex(ValueError, 'pair must share'):
                self.prepare()

    def test_frozen_trace_copies_keep_replay_fields_and_exact_bytes(self):
        self.prepare()
        split = self.api.load_split(self.out)
        for pair in split['pairs']:
            cell = next(c['cell_id'] for c in split['cells'] if c['pair_id'] == pair['pair_id'])
            original = Path(self.entries[cell]['path']).read_bytes()
            self.assertEqual((self.out / pair['input_path']).read_bytes(), original)
            if pair['kind'] == 'fixed_arrival_trace':
                self.assertIn('request_id', json.loads(original.splitlines()[0]))

    def test_fixed_arrival_requires_read_bytes_time_offset_and_seed(self):
        entry = next(e for e in self.entries.values() if e['kind'] == 'fixed_arrival_trace')
        original = Path(entry['path']).read_text()
        records = [json.loads(line) for line in original.splitlines()]
        variants = []
        for field, value in [('operation', 'write'), ('bytes', 1), ('issue_ns', -1), ('logical_address', -1)]:
            variant = copy.deepcopy(records)
            variant[0][field] = value
            variants.append(variant)
        no_time = copy.deepcopy(records)
        del no_time[0]['issue_ns']
        variants.append(no_time)
        variants.append(list(reversed(records)))
        for rows in variants:
            self.alter_input('fixed_arrival_trace', lambda p: p.write_text(''.join(json.dumps(r) + '\n' for r in rows)))
            with self.assertRaises(ValueError):
                self.prepare()
        self.alter_input('fixed_arrival_trace', lambda p: p.write_text(original))
        meta = Path(entry['metadata_path'])
        content = json.loads(meta.read_text())
        del content['seed']
        meta.write_text(json.dumps(content))
        for e in self.entries.values():
            if e.get('metadata_path') == str(meta):
                e['metadata_sha256'] = sha256(meta)
        self.write_json('index.json', dict(schema_version=1, cells=self.entries))
        with self.assertRaises(ValueError):
            self.prepare()

    def test_closed_loop_requires_exact_ordered_offsets_and_no_timestamps(self):
        entry = next(e for e in self.entries.values() if e['kind'] == 'closed_loop_qd')
        original = json.loads(Path(entry['path']).read_text())
        variants = []
        for field in ('requests', 'file_span_bytes', 'alignment_bytes'):
            variant = copy.deepcopy(original)
            del variant[field]
            variants.append(variant)
        for field, value in [('issue_ns', 0), ('logical_address', 1), ('operation', 'write')]:
            variant = copy.deepcopy(original)
            variant['requests'][0][field] = value
            variants.append(variant)
        for policy in variants:
            self.alter_input('closed_loop_qd', lambda p: p.write_text(json.dumps(policy)))
            with self.assertRaises(ValueError):
                self.prepare()

    def test_fit_view_excludes_heldout_and_explicitly_is_not_a_sandbox(self):
        self.prepare()
        split = self.api.load_split(self.out)
        view = self.api.fit_inputs(self.out)
        calibration = {c['cell_id'] for c in split['cells'] if c['role'] == 'calibration'}
        self.assertEqual({c['cell_id'] for c in view['cells']}, calibration)
        heldout = {c['cell_id'] for c in split['cells'] if c['role'] == 'heldout'}
        self.assertFalse(any(cell in json.dumps(view) for cell in heldout))
        self.assertFalse(view['sandbox_enforced'])
        self.assertIn('does not sandbox', view['boundary'])

    def test_split_destination_cannot_be_overwritten(self):
        self.prepare()
        before = sha256(self.out / 'manifest.json')
        with self.assertRaises((ValueError, FileExistsError)):
            self.prepare()
        self.assertEqual(before, sha256(self.out / 'manifest.json'))

    def test_sources_and_frozen_artifacts_are_verified_on_every_use(self):
        self.prepare()
        paths = [self.matrix, self.index, self.profile, self.device,
                 Path(next(iter(self.entries.values()))['path']),
                 self.out / 'calibration/fit-inputs.json']
        for path in paths:
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.api.fit_inputs(self.out)
            path.write_bytes(original)
        self.api.load_split(self.out)

    def test_split_decoder_uses_bytes_that_passed_verification(self):
        self.prepare()
        expected = self.api.load_split(self.out)
        original_verify = self.api.verify_bundle
        changed = dict(expected, device_state='UNVERIFIED_REPLACEMENT')

        def mutate_after_verification(*args):
            snapshot = original_verify(*args)
            (self.out / 'split.json').write_text(json.dumps(changed))
            return snapshot

        with patch.object(self.api, 'verify_bundle', side_effect=mutate_after_verification):
            self.assertEqual(self.api.load_split(self.out), expected)
        with self.assertRaises(ValueError):
            self.api.load_split(self.out)

    def test_fit_view_cannot_add_heldout_after_verification(self):
        self.prepare()
        expected = self.api.fit_inputs(self.out)
        heldout = next(c for c in self.api.load_split(self.out)['cells'] if c['role'] == 'heldout')
        changed = copy.deepcopy(expected)
        changed['cells'].append(heldout)
        original_verify = self.api.verify_bundle

        def mutate_after_verification(*args):
            snapshot = original_verify(*args)
            (self.out / 'calibration/fit-inputs.json').write_text(json.dumps(changed))
            return snapshot

        with patch.object(self.api, 'verify_bundle', side_effect=mutate_after_verification):
            view = self.api.fit_inputs(self.out)
        self.assertEqual(view, expected)
        self.assertNotIn(heldout['cell_id'], [c['cell_id'] for c in view['cells']])
        with self.assertRaises(ValueError):
            self.api.fit_inputs(self.out)

    def test_profile_decoder_uses_bytes_that_passed_verification(self):
        self.prepare()
        profile, receipt = self.calibrated()
        target = self.base / 'frozen'
        expected = self.api.freeze_profile(self.out, profile, receipt, target)
        original_verify = self.api.verify_bundle

        def mutate_after_verification(path, kind):
            snapshot = original_verify(path, kind)
            if kind == 'storage_profile':
                (target / 'profile-freeze.json').write_text(json.dumps(
                    dict(expected, validation_id='UNVERIFIED_REPLACEMENT')))
            return snapshot

        with patch.object(self.api, 'verify_bundle', side_effect=mutate_after_verification):
            self.assertEqual(self.api.load_profile_freeze(target), expected)
        with self.assertRaises(ValueError):
            self.api.load_profile_freeze(target)

    def test_profile_receipt_cannot_bind_unverified_fit_view(self):
        self.prepare()
        profile, receipt = self.calibrated()
        path = self.out / 'calibration/fit-inputs.json'
        original_bytes = path.read_bytes()
        changed = json.loads(original_bytes)
        changed['cells'].append(next(c for c in self.api.load_split(self.out)['cells'] if c['role'] == 'heldout'))
        changed_bytes = json.dumps(changed).encode()
        receipt_data = json.loads(receipt.read_text())
        receipt_data['fit_input_sha256'] = self.api.digest(changed_bytes)
        receipt.write_text(json.dumps(receipt_data))
        original_verify = self.api.verify_bundle
        calls = 0

        def briefly_replace_fit_view(bundle, kind):
            nonlocal calls
            if kind == 'storage_split':
                calls += 1
                if calls > 1:
                    path.write_bytes(original_bytes)
            snapshot = original_verify(bundle, kind)
            if kind == 'storage_split' and calls == 1:
                path.write_bytes(changed_bytes)
            return snapshot

        target = self.base / 'frozen'
        with patch.object(self.api, 'verify_bundle', side_effect=briefly_replace_fit_view):
            with self.assertRaises(ValueError):
                self.api.freeze_profile(self.out, profile, receipt, target)
        self.assertFalse(target.exists())

    def test_profile_gate_receipt_and_new_validation_identity(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.api.heldout_inputs(self.out, None)
        identities = []
        for suffix in ('one', 'two'):
            profile, receipt = self.calibrated(suffix)
            target = self.base / f'frozen-{suffix}'
            self.api.freeze_profile(self.out, profile, receipt, target)
            view = self.api.heldout_inputs(self.out, target)
            self.assertEqual(len(view['cells']), 4)
            self.assertTrue(all(c['role'] == 'heldout' for c in view['cells']))
            self.assertEqual(view['profile_sha256'], sha256(profile))
            self.assertFalse(view['hardware_ready'])
            self.assertEqual(view['device_state'], 'BLOCKED_STORAGE_BUSY')
            identities.append(view['validation_id'])
            with self.assertRaises((ValueError, FileExistsError)):
                self.api.freeze_profile(self.out, profile, receipt, target)
        self.assertNotEqual(*identities)

    def test_receipt_must_bind_calibration_only_and_frozen_profile(self):
        self.prepare()
        profile, receipt = self.calibrated()
        original = json.loads(receipt.read_text())
        heldout = next(c['cell_id'] for c in self.api.load_split(self.out)['cells'] if c['role'] == 'heldout')
        for key, value in [('calibration_cell_ids', [heldout]), ('profile_sha256', '0' * 64),
                           ('fit_input_sha256', '0' * 64), ('split_id', 'wrong'), ('status', 'PLANNED')]:
            receipt.write_text(json.dumps(dict(original, **{key: value})))
            with self.assertRaises(ValueError):
                self.api.freeze_profile(self.out, profile, receipt, self.base / 'frozen')

    def test_changed_calibrated_profile_or_receipt_invalidates_use(self):
        self.prepare()
        profile, receipt = self.calibrated()
        target = self.base / 'frozen'
        self.api.freeze_profile(self.out, profile, receipt, target)
        for path in (profile, receipt, target / 'profile-freeze.json'):
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            with self.assertRaises(ValueError):
                self.api.heldout_inputs(self.out, target)
            path.write_bytes(original)

    def test_profile_from_another_split_does_not_open_heldout(self):
        self.prepare()
        second = self.base / 'other-split'
        self.prepare(second)
        profile, receipt = self.calibrated()
        target = self.base / 'frozen'
        self.api.freeze_profile(self.out, profile, receipt, target)
        with self.assertRaises(ValueError):
            self.api.heldout_inputs(second, target)

    def test_stale_input_hash_rejected_before_destination_creation(self):
        path = Path(next(iter(self.entries.values()))['path'])
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'source hash mismatch'):
            self.prepare()
        self.assertFalse(self.out.exists())

    def test_closed_loop_rejects_more_qd_than_requests_and_out_of_span(self):
        entry = next(e for e in self.entries.values() if e['kind'] == 'closed_loop_qd')
        original = json.loads(Path(entry['path']).read_text())
        for key, value in [('requests', []), ('file_span_bytes', 4096)]:
            policy = dict(original, **{key: value})
            self.alter_input('closed_loop_qd', lambda p: p.write_text(json.dumps(policy)))
            with self.assertRaises(ValueError):
                self.prepare()

    def test_duplicate_index_json_keys_are_rejected(self):
        cell = next(iter(self.entries))
        entry = json.dumps(self.entries[cell])
        self.index.write_text('{"schema_version":1,"cells":{' + json.dumps(cell) + ':' + entry + ',' + json.dumps(cell) + ':' + entry + '}}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON key'):
            self.prepare()

    def test_symlink_and_fifo_source_metadata_are_rejected_without_reads(self):
        path = self.base / 'special-profile'
        path.symlink_to(self.profile)
        with self.assertRaises(ValueError):
            self.api.prepare_split(self.matrix, self.index, path, self.device, self.out)
        path.unlink()
        os.mkfifo(path)
        result = subprocess.run([sys.executable, str(SCRIPT), 'prepare', '--matrix', str(self.matrix),
                                 '--trace-index', str(self.index), '--profile', str(path),
                                 '--device-manifest', str(self.device), '--out', str(self.out)],
                                text=True, capture_output=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.out.exists())

    def test_full_matrix_freeze_uses_all_512_original_cells(self):
        self.write_matrix(self.all_rows)
        entries = {}
        for pair in self.api.load_pairs(self.matrix):
            dims = pair['dimensions']
            size, qd = int(dims['request_bytes']), int(dims['qd'])
            family = dims['arrival_process']
            metadata = dict(schema_version=1, seed=31, request_bytes=size, qd=qd,
                            workload=dims['workload'], arrival_process=family)
            records = [dict(offset=i * size, bytes=size, operation='read') for i in range(qd)]
            name = pair['pair_id']
            if family == 'closed_loop_qd':
                path = self.write_json(name + '.json', dict(metadata, replenish='on_completion',
                                       requests=records, file_span_bytes=qd * size, alignment_bytes=4096))
                entry = dict(kind=family, path=str(path), sha256=sha256(path))
            else:
                for i, record in enumerate(records):
                    record['issue_ns'] = i * 1000
                path = self.base / (name + '.jsonl')
                path.write_text(''.join(json.dumps(r) + '\n' for r in records))
                meta = self.write_json(name + '-meta.json', metadata)
                entry = dict(kind=family, path=str(path), sha256=sha256(path),
                             metadata_path=str(meta), metadata_sha256=sha256(meta))
            for cell in pair['cells']:
                entries[cell['cell_id']] = entry
        self.write_json('index.json', dict(schema_version=1, cells=entries))
        self.prepare()
        split = self.api.load_split(self.out)
        actual = {c['cell_id']: c['condition'] for c in split['cells']}
        expected = {r['cell_id']: r for r in self.all_rows if r['group'] == 'flash_fidelity'}
        self.assertEqual(actual, expected)
        self.assertEqual(len(split['pairs']), 256)
        self.assertEqual(len(self.api.fit_inputs(self.out)['cells']), 128)

    def test_special_metadata_file_rejected_without_blocking(self):
        self.prepare()
        path = self.out / 'calibration/fit-inputs.json'
        path.unlink()
        os.mkfifo(path)
        result = subprocess.run([sys.executable, str(SCRIPT), 'verify', '--split', str(self.out)],
                                text=True, capture_output=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)

    def test_cli_metadata_preparation_and_verification(self):
        result = subprocess.run([sys.executable, str(SCRIPT), 'prepare', '--matrix', str(self.matrix),
                                 '--trace-index', str(self.index), '--profile', str(self.profile),
                                 '--device-manifest', str(self.device), '--out', str(self.out)],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(SCRIPT), 'verify', '--split', str(self.out)],
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['hardware_ready'])


if __name__ == '__main__':
    unittest.main()

"""CPU tests for the fixed seven-file startup source contract."""
from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess
import unittest
from unittest import mock

import hf_startup_sources as startup
import hf_runtime_sources as runtime


class StartupSourceTests(unittest.TestCase):
    def setUp(self):
        from test_hf_runtime_sources import RuntimeSourceTests
        fixture = RuntimeSourceTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.base, self.site, self.stdlib = fixture.base, fixture.site, fixture.stdlib
        for name in startup.STARTUP_SOURCE_FILES:
            path = self.site / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# TEST_ONLY startup source; never execute\n')
        fixture.prepare_tuning()
        self.primary = fixture.collect_tuning()
        self.snapshot = startup.collect_startup_sources(self.primary)
        self.assertEqual(startup.validate_startup_sources(self.snapshot, self.primary)['source_file_count'], 7)

    def _manifest(self):
        return json.loads(self.snapshot.manifest_bytes)

    def _changed(self, **changes):
        document = self._manifest()
        document.update(changes)
        return replace(self.snapshot, manifest_bytes=runtime.metadata.canonical(document))

    def _write_selected(self, root, raw=b'FOREIGN startup payload; must not be read\n'):
        for name in startup.STARTUP_SOURCE_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def _assert_rejected_without_payload_io(self, operation, forbidden_root, before_open=None):
        opened = []
        payload_reads = []
        forbidden_fds = set()
        original_open, original_pread = os.open, os.pread

        def observed_open(path, *args, **kwargs):
            if before_open is not None:
                before_open(path, kwargs)
            fd = original_open(path, *args, **kwargs)
            target = Path('/proc/self/fd/' + str(fd)).resolve()
            if target.is_relative_to(forbidden_root):
                forbidden_fds.add(fd)
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    opened.append(str(target))
            return fd

        def observed_pread(fd, *args, **kwargs):
            if fd in forbidden_fds:
                payload_reads.append(str(Path('/proc/self/fd/' + str(fd)).resolve()))
            return original_pread(fd, *args, **kwargs)

        with mock.patch.object(os, 'open', side_effect=observed_open), \
             mock.patch.object(os, 'pread', side_effect=observed_pread):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(opened, [], 'redirected selected leaf was opened')
        self.assertEqual(payload_reads, [], 'redirected selected payload was read')

    def test_valid_collect_validate_recheck_and_reordered_artifacts(self):
        report = startup.validate_startup_sources(self.snapshot, self.primary)
        self.assertEqual(report['startup_contract'], startup.STARTUP_CONTRACT)
        self.assertEqual(report['provenance'], 'MOCK')
        self.assertTrue(report['test_only'])
        self.assertFalse(report['scientific_validation_passed'])
        reordered = replace(self.snapshot, artifacts=tuple(reversed(self.snapshot.artifacts)))
        self.assertEqual(startup.validate_startup_sources(reordered, self.primary), report)
        status = startup.recheck_startup_sources(reordered, self.primary)
        self.assertEqual(status, dict(status='STARTUP_SOURCES_UNCHANGED',
                                       manifest_sha256=runtime.metadata.digest(self.snapshot.manifest_bytes),
                                       provenance='MOCK', scientific_validation_passed=False))

    def test_frozen_validation_never_reads_original_files(self):
        with mock.patch.object(os, 'open', side_effect=AssertionError('frozen validation opened a path')):
            startup.validate_startup_sources(self.snapshot, self.primary)

    def test_recheck_detects_selected_file_and_real_ancestor_replacement(self):
        (self.site / 'vllm/env_override.py').write_bytes(b'changed')
        with self.assertRaises(ValueError): startup.recheck_startup_sources(self.snapshot, self.primary)

    def test_recheck_detects_real_ancestor_replacement_from_clean_baseline(self):
        self.assertEqual(startup.recheck_startup_sources(self.snapshot, self.primary)['status'], 'STARTUP_SOURCES_UNCHANGED')
        original = self.site / 'vllm'; moved = self.base / 'vllm-original'
        original.rename(moved); original.mkdir()
        for name in startup.STARTUP_SOURCE_FILES:
            if name.startswith('vllm/'):
                path = self.site / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'# TEST_ONLY startup source; never execute\n')
        with self.assertRaises(ValueError): startup.recheck_startup_sources(self.snapshot, self.primary)

    def test_collect_and_recheck_reject_redirected_primary_root_before_selected_io(self):
        owned = self.base / 'owned-site-packages'
        foreign = self.base / 'foreign-site-packages'
        self.site.rename(owned)
        self._write_selected(foreign)
        self.site.symlink_to(foreign, target_is_directory=True)
        for operation in (lambda: startup.collect_startup_sources(self.primary),
                          lambda: startup.recheck_startup_sources(self.snapshot, self.primary)):
            with self.subTest(operation=operation.__code__.co_firstlineno):
                self._assert_rejected_without_payload_io(operation, foreign)

    def test_collect_and_recheck_reject_same_path_root_replacement_before_selected_io(self):
        owned = self.base / 'owned-site-packages'
        self.site.rename(owned)
        self.site.mkdir()
        self._write_selected(self.site)
        for operation in (lambda: startup.collect_startup_sources(self.primary),
                          lambda: startup.recheck_startup_sources(self.snapshot, self.primary)):
            with self.subTest(operation=operation.__code__.co_firstlineno):
                self._assert_rejected_without_payload_io(operation, self.site)

    def test_directory_check_open_race_never_reads_redirected_leaf(self):
        owned = self.site / 'vllm'
        moved = self.base / 'owned-vllm'
        foreign = self.base / 'foreign-site-packages'
        self._write_selected(foreign)
        for operation in (lambda: startup.collect_startup_sources(self.primary),
                          lambda: startup.recheck_startup_sources(self.snapshot, self.primary)):
            triggered = False

            def replace_at_open(path, kwargs):
                nonlocal triggered
                text = os.fspath(path)
                component_open = kwargs.get('dir_fd') is not None and text == 'vllm'
                legacy_leaf_open = kwargs.get('dir_fd') is None and Path(text).is_relative_to(owned)
                if not triggered and (component_open or legacy_leaf_open):
                    triggered = True
                    owned.rename(moved)
                    owned.symlink_to(foreign / 'vllm', target_is_directory=True)

            try:
                with self.subTest(operation=operation.__code__.co_firstlineno):
                    self._assert_rejected_without_payload_io(operation, foreign, replace_at_open)
                    self.assertTrue(triggered, 'race hook did not reach the directory open boundary')
            finally:
                if owned.is_symlink():
                    owned.unlink()
                if moved.exists():
                    moved.rename(owned)

    def test_descriptor_capture_closes_fds_on_success_and_read_failure(self):
        original_open, original_close = os.open, os.close
        for fail_read in (False, True):
            live = set()

            def tracked_open(*args, **kwargs):
                fd = original_open(*args, **kwargs)
                live.add(fd)
                return fd

            def tracked_close(fd):
                live.discard(fd)
                return original_close(fd)

            read = mock.patch.object(startup.metadata, 'read_exact', side_effect=ValueError('injected read failure')) \
                if fail_read else mock.patch.object(startup.metadata, 'read_exact', wraps=startup.metadata.read_exact)
            with mock.patch.object(os, 'open', side_effect=tracked_open), \
                 mock.patch.object(os, 'close', side_effect=tracked_close), read:
                if fail_read:
                    with self.assertRaises(ValueError):
                        startup.recheck_startup_sources(self.snapshot, self.primary)
                else:
                    self.assertEqual(startup.recheck_startup_sources(self.snapshot, self.primary)['status'],
                                     'STARTUP_SOURCES_UNCHANGED')
            self.assertEqual(live, set(), 'selected-source capture leaked a file descriptor')

    def test_recheck_rejects_selected_symlink_before_open(self):
        path = self.site / 'torch/cuda/__init__.py'
        path.unlink()
        path.symlink_to(self.site / 'torch/__init__.py')
        with self.assertRaises(ValueError): startup.recheck_startup_sources(self.snapshot, self.primary)

    def test_missing_extra_duplicate_and_malformed_artifacts_fail(self):
        rows = list(self.snapshot.artifacts)
        mutations = (
            tuple(rows[:-1]),
            tuple(rows[:6] + [rows[0]]),
            tuple([('site-packages/unknown-startup.py', rows[0][1])] + rows[1:]),
            tuple([list(rows[0])] + rows[1:]),
            tuple([rows[0][0], *rows[1:]]),
        )
        for artifacts in mutations:
            with self.subTest(artifacts=type(artifacts).__name__), self.assertRaises(ValueError):
                startup.validate_startup_sources(replace(self.snapshot, artifacts=artifacts), self.primary)
        changed = list(rows); changed[0] = (rows[0][0], 'bad bytes')
        with self.assertRaises(ValueError): startup.validate_startup_sources(replace(self.snapshot, artifacts=tuple(changed)), self.primary)

    def test_manifest_types_hashes_states_ranges_identity_root_and_ancestor_fail(self):
        document = self._manifest(); mutations = []
        for key, value in (
            ('source_file_count', True), ('startup_source_bytes', -1),
            ('primary_manifest_sha256', '0' * 64), ('source_root', str(self.base / 'other')),
        ):
            changed = dict(document); changed[key] = value; mutations.append(runtime.metadata.canonical(changed))
        changed = dict(document); changed['artifacts'] = dict(document['artifacts']); changed['artifacts'][next(iter(changed['artifacts']))] = '0' * 64
        mutations.append(runtime.metadata.canonical(changed))
        state_key = next(iter(document['source_states']))
        for field in ('path', 'read_ranges', 'file_identity'):
            changed = self._manifest(); state = changed['source_states'][state_key]
            if field == 'path': state['path'] = str(self.base / 'wrong.py')
            elif field == 'read_ranges': state['read_ranges'][0][0] = 1
            else: state['file_identity']['size'] += 1
            mutations.append(runtime.metadata.canonical(changed))
        changed = self._manifest(); changed['source_states'][state_key]['chain'][0]['inode'] += 1
        mutations.append(runtime.metadata.canonical(changed))
        changed = self._manifest(); changed['source_states'] = []
        mutations.append(runtime.metadata.canonical(changed))
        changed = self._manifest(); changed['source_states'][state_key]['read_ranges'] = 'invalid'
        mutations.append(runtime.metadata.canonical(changed))
        changed = self._manifest(); changed['source_states'][state_key]['file_identity'] = []
        mutations.append(runtime.metadata.canonical(changed))
        for raw in mutations:
            with self.assertRaises(ValueError):
                startup.validate_startup_sources(replace(self.snapshot, manifest_bytes=raw), self.primary)

    def test_contract_revision_primary_extension_and_provenance_cannot_change(self):
        for key, value in (
            ('startup_contract', 'SINGLE_GPU_NO_ADDON_V2'),
            ('test_only', False), ('provenance', 'STARTUP_SOURCE_METADATA'),
            ('scientific_validation_passed', True), ('weight_payload_rehashed', True),
        ):
            with self.subTest(key=key):
                with self.assertRaises(ValueError): startup.validate_startup_sources(self._changed(**{key: value}), self.primary)
        base = self.fixture.collect()
        with self.assertRaises(ValueError): startup.validate_startup_sources(self.snapshot, base)

    def test_per_file_combined_budget_and_manifest_bounds_apply_to_collect_and_frozen_validate(self):
        primary_bytes = self._manifest()['primary_source_metadata_bytes']
        startup_bytes = self._manifest()['startup_source_bytes']
        with mock.patch.object(startup, 'MAX_TOTAL_BYTES', primary_bytes + startup_bytes - 1):
            with self.assertRaises(ValueError): startup.collect_startup_sources(self.primary)
            with self.assertRaises(ValueError): startup.validate_startup_sources(self.snapshot, self.primary)
        with mock.patch.object(startup, 'MAX_FILE_BYTES', 1):
            with self.assertRaises(ValueError): startup.collect_startup_sources(self.primary)
            with self.assertRaises(ValueError): startup.validate_startup_sources(self.snapshot, self.primary)
        oversized = replace(self.snapshot, manifest_bytes=b'x' * ((8 << 20) + 1))
        with self.assertRaises(ValueError): startup.validate_startup_sources(oversized, self.primary)

    def test_mutation_sensitivity_combined_budget_and_ancestor_join(self):
        primary_bytes = self._manifest()['primary_source_metadata_bytes']
        startup_bytes = self._manifest()['startup_source_bytes']
        limit = primary_bytes + startup_bytes - 1
        with mock.patch.object(startup, 'MAX_TOTAL_BYTES', limit):
            with self.assertRaises(ValueError): startup.collect_startup_sources(self.primary)
            # Test-only mutant: a fresh full budget ignores primary bytes and would read all seven files.
            budget = {'remaining': limit}; paths = startup._paths(self.primary_manifest_root())
            for name, path in sorted(paths.items()):
                runtime.metadata.snapshot(path, header=False, budget=budget,
                    limit=startup.MAX_FILE_BYTES, confined_to=self.primary_manifest_root())
            self.assertGreaterEqual(budget['remaining'], 0)
        original = self.site / 'vllm'; moved = self.base / 'vllm-original'; original.rename(moved); original.mkdir()
        for name in startup.STARTUP_SOURCE_FILES:
            if name.startswith('vllm/'):
                path = self.site / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'# TEST_ONLY startup source; never execute\n')
        with self.assertRaises(ValueError): startup.collect_startup_sources(self.primary)
        # Test-only mutant: replacing the primary join with freshly observed selected
        # ancestors accepts the new root and demonstrates this check's sensitivity.
        mutant_bindings = {}
        for path in startup._paths(self.primary_manifest_root()).values():
            for row in runtime.metadata.path_state(path)['chain']:
                mutant_bindings[row['path']] = (row['device'], row['inode'])
        with mock.patch.object(startup, '_ancestor_bindings', return_value=mutant_bindings):
            mutant_snapshot = startup.collect_startup_sources(self.primary)
        self.assertEqual(json.loads(mutant_snapshot.manifest_bytes)['startup_source_bytes'], startup_bytes)

    def primary_manifest_root(self):
        return Path(self.primary_report()['source_root'])

    def primary_report(self):
        return runtime.validate_runtime_sources(self.primary)

    def test_no_inference_imports(self):
        code = 'import sys;sys.path.insert(0,sys.argv[1]);import hf_startup_sources;assert not any(n in sys.modules for n in ("torch","vllm","flashinfer","numpy"))'
        result = subprocess.run(['/opt/miniconda3/bin/python3.13', '-B', '-c', code, str(Path(__file__).parent)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

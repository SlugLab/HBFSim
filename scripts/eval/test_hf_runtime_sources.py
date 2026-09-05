"""CPU source identity controls; no inference imports or checkpoint reads."""
from dataclasses import replace
from contextlib import contextmanager
from importlib.metadata import Distribution
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import hf_runtime_sources as sources

ROOT = Path(__file__).resolve().parents[2]


class RuntimeSourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='.runtime-source-test-', dir=ROOT)
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.site = self.base/'site-packages'; self.stdlib = self.base/'stdlib'
        for name in sources.SOURCE_FILES:
            path = self.site/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# TEST_ONLY source fixture\n')
        for name in sources.STDLIB_FILES:
            path = self.stdlib/name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# TEST_ONLY stdlib fixture\n')
        for name, version in sources.DISTRIBUTIONS.items():
            path = self.site/(name.replace('-', '_')+'-'+version+'.dist-info'); path.mkdir()
            (path/'METADATA').write_text(f'Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n')
            for file in ('WHEEL', 'INSTALLER', 'RECORD'):
                (path/file).write_bytes(b'TEST_ONLY metadata; never follow RECORD paths\n')
        for name in sources.REQUIRED_DIRECTORIES:
            (self.site/name).mkdir(parents=True, exist_ok=True)
        self.interpreter = self.base/'python-fixture'; self.interpreter.write_bytes(b'TEST_ONLY executable bytes')

    def collect(self):
        return sources.collect_runtime_sources(source_root=self.site, stdlib_root=self.stdlib,
                                                interpreter=self.interpreter)

    def test_finite_snapshot_is_immutable_mock_and_record_is_not_binary_authentication(self):
        snapshot = self.collect(); report = sources.validate_runtime_sources(snapshot)
        self.assertEqual(report['provenance'], 'MOCK')
        self.assertTrue(report['test_only']); self.assertFalse(report['scientific_validation_passed'])
        self.assertFalse(report['all_runtime_binaries_authenticated'])
        self.assertEqual(len(snapshot.artifacts), len(sources.SOURCE_FILES)+len(sources.STDLIB_FILES)+40)
        self.assertIn('vllm/model_executor/model_loader/weight_utils.py', sources.SOURCE_FILES)
        self.assertIn('vllm/v1/worker/worker_base.py', sources.SOURCE_FILES)
        self.assertEqual(report['distributions']['torch']['version'], '2.9.1')
        report['distributions']['torch']['version'] = 'mutated'
        self.assertEqual(sources.validate_runtime_sources(snapshot)['distributions']['torch']['version'], '2.9.1')

    def test_missing_or_changed_pinned_distribution_and_extra_direct_url_fail(self):
        metadata = self.site/'vllm-0.15.1.dist-info/METADATA'
        original = metadata.read_bytes()
        for raw in (original.replace(b'0.15.1', b'0.15.2'), original+b'Version: 0.15.1\n'):
            metadata.write_bytes(raw)
            with self.assertRaises(ValueError): self.collect()
        metadata.write_bytes(original)
        (metadata.parent/'direct_url.json').write_bytes(b'{}')
        with self.assertRaises(ValueError): self.collect()

    def test_package_cache_presence_must_match_pinned_read_only_boundary(self):
        (self.site/'flashinfer/data/aot').mkdir()
        with self.assertRaises(ValueError): self.collect()
        (self.site/'flashinfer/data/aot').rmdir()
        (self.site/'flashinfer_jit_cache.py').write_bytes(b'raise AssertionError("never import")')
        with self.assertRaises(ValueError): self.collect()

    def test_source_symlink_escape_rejected_before_opening_target(self):
        path = self.site/'vllm/envs.py'; path.unlink()
        target = self.base/'forbidden-source'; target.write_bytes(b'forbidden')
        path.symlink_to(target); opened=[]; original=os.open
        def observed(path, *args, **kwargs):
            opened.append(str(path)); return original(path, *args, **kwargs)
        with mock.patch.object(os, 'open', side_effect=observed):
            with self.assertRaises(ValueError): self.collect()
        self.assertNotIn(str(target), opened)

    def test_oversized_file_and_aggregate_budget_reject(self):
        path = self.site/'vllm/envs.py'
        with path.open('wb') as file: file.truncate((16 << 20)+1)
        with self.assertRaises(ValueError): self.collect()
        path.write_bytes(b'fixture')
        with mock.patch.object(sources, 'MAX_TOTAL_BYTES', 10):
            with self.assertRaises(ValueError): self.collect()

    def test_frozen_only_validation_does_not_reopen_sources_and_current_recheck_detects_drift(self):
        snapshot = self.collect()
        path = self.site/'vllm/envs.py'; path.write_bytes(b'changed')
        with mock.patch.object(os, 'open', side_effect=AssertionError('frozen validator must not open')):
            sources.validate_runtime_sources(snapshot)
        with self.assertRaises(ValueError): sources.recheck_runtime_sources(snapshot)

    def test_resealed_snapshot_cannot_change_file_set_versions_or_fixture_provenance(self):
        snapshot = self.collect()
        report = json.loads(snapshot.manifest_bytes)
        for mutation in ('version', 'origin', 'missing'):
            changed = json.loads(snapshot.manifest_bytes); artifacts = dict(snapshot.artifacts)
            if mutation == 'version': changed['distributions']['vllm']['version'] = '0.15.2'
            elif mutation == 'origin': changed['test_only']=False; changed['provenance']='RUNTIME_SOURCE_METADATA'
            else:
                key = 'site-packages/vllm/envs.py'; del artifacts[key]; del changed['artifacts'][key]
            tampered = replace(snapshot, manifest_bytes=json.dumps(changed).encode(), artifacts=tuple(artifacts.items()))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): sources.validate_runtime_sources(tampered)

    def test_collection_never_imports_inference_packages(self):
        code = 'import sys;sys.path.insert(0,sys.argv[1]);import hf_runtime_sources;assert not any(n in sys.modules for n in ("torch","vllm","flashinfer","numpy"))'
        result = subprocess.run([sys.executable, '-c', code, str(ROOT/'scripts/eval')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_interpreter_override_cannot_be_relabelled_even_with_default_source_roots(self):
        with mock.patch.object(sources, 'DEFAULT_SOURCE_ROOT', self.site), \
             mock.patch.object(sources, 'DEFAULT_STDLIB_ROOT', self.stdlib):
            snapshot = sources.collect_runtime_sources(interpreter=self.interpreter)
            report = json.loads(snapshot.manifest_bytes)
            report.update(test_only=False, evidence='RUNTIME_SOURCE_METADATA', provenance='RUNTIME_SOURCE_METADATA')
            changed = replace(snapshot, manifest_bytes=json.dumps(report).encode())
            with self.assertRaises(ValueError): sources.validate_runtime_sources(changed)
            with self.assertRaises(ValueError): sources.recheck_runtime_sources(changed)

    def test_distribution_ambiguity_uses_normalized_case_and_separator_names(self):
        for name in ('VLLM-0.15.2.dist-info', 'flashinfer.python-0.6.2.dist-info'):
            extra = self.site/name; extra.mkdir()
            with self.subTest(name=name), self.assertRaises(ValueError): self.collect()
            extra.rmdir()

    def test_frozen_file_identity_cannot_use_boolean_instead_of_integer(self):
        snapshot = self.collect(); report = json.loads(snapshot.manifest_bytes)
        report['source_states']['site-packages/vllm/envs.py']['file_identity']['device'] = True
        with self.assertRaises(ValueError):
            sources.validate_runtime_sources(replace(snapshot, manifest_bytes=json.dumps(report).encode()))

    def test_frozen_chain_range_and_interpreter_identity_types_are_strict(self):
        snapshot = self.collect()
        for change in ('chain', 'range', 'interpreter'):
            report = json.loads(snapshot.manifest_bytes)
            state = report['source_states']['site-packages/vllm/envs.py']
            if change == 'chain': state['chain'][0]['device'] = True
            elif change == 'range': state['read_ranges'][0][0] = False
            else: report['interpreter']['file_identity']['device'] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                sources.validate_runtime_sources(replace(snapshot, manifest_bytes=json.dumps(report).encode()))

    def test_directory_inventory_stops_before_materializing_unbounded_entry_names(self):
        consumed = 0; scandir = os.scandir
        @contextmanager
        def entries(path):
            nonlocal consumed
            if Path(path) != self.site:
                with scandir(path) as iterator: yield iterator
                return
            def iterator():
                nonlocal consumed
                for index in range(5000):
                    consumed += 1
                    name = 'unrelated_'+str(index)
                    yield SimpleNamespace(name=name, path=str(self.site/name))
            yield iterator()
        with mock.patch.object(os, 'scandir', side_effect=entries):
            with self.assertRaises(ValueError): self.collect()
        self.assertEqual(consumed, 4097)

    def test_duplicate_distributions_match_stdlib_first_hyphen_and_egg_info_discovery(self):
        for name in ('vllm-0.15.2-extra.dist-info', 'vllm-0.15.2.egg-info', 'VLLM.egg-info'):
            extra = self.site/name; extra.mkdir()
            file = extra/('PKG-INFO' if name.endswith('.egg-info') else 'METADATA')
            file.write_text('Metadata-Version: 2.4\nName: vllm\nVersion: 0.15.2\n')
            self.assertEqual(len(list(Distribution.discover(name='vllm', path=[str(self.site)]))), 2)
            with self.subTest(name=name), self.assertRaises(ValueError): self.collect()
            file.unlink(); extra.rmdir()

    def test_common_ancestor_identity_cannot_contradict_another_frozen_file(self):
        snapshot = self.collect(); report = json.loads(snapshot.manifest_bytes)
        report['source_states']['site-packages/vllm/envs.py']['chain'][0]['inode'] += 1
        with self.assertRaises(ValueError):
            sources.validate_runtime_sources(replace(snapshot, manifest_bytes=json.dumps(report).encode()))


if __name__ == '__main__': unittest.main()

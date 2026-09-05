"""Fake namespace/descriptor tests; no POSIX segment or CUDA runtime is opened."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'adapters/vllm_capacity'))
from hf_owned_routes import OwnedRouteMemory


class OwnedRouteMemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='.route-memory-test-', dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.namespace = {}; self.handles = {}; self.calls = []; self.next_fd = 10
        self.partial = False
        outer = self
        class SharedMemory:
            _fd = -1; _name = None; _buf = None; _mmap = None
            def __init__(self, name=None, create=False, size=0):
                outer.calls.append(('open', name, create, size))
                if create:
                    if name in outer.namespace: raise FileExistsError(name)
                    outer.namespace[name] = (1, outer.next_fd, size)
                elif name not in outer.namespace: raise FileNotFoundError(name)
                self._fd = outer.next_fd; outer.next_fd += 1
                self._name = name; self.name = name
                self.identity = outer.namespace[name]; self.size = self.identity[2]
                self._buf = self._mmap = object(); outer.handles[self._fd] = self
                if outer.partial: raise RuntimeError('failure after exclusive allocation')
            def close(self):
                outer.calls.append(('close', self._name))
                self._fd = -1; self._buf = self._mmap = None
            def unlink(self):
                outer.calls.append(('unlink', self._name))
                del outer.namespace[self._name]
        self.original = NS(SharedMemory=SharedMemory, untouched=object())
        self.module = NS(shared_memory=self.original, _TMP_DIR=str(self.path),
                         _LOCK_FILE_PREFIX=str(self.path/'vllm_routed_experts'))
        self.name = 'vllm_routed_experts_buffer_1234567890_0'
        self.fd_patch = mock.patch('hf_owned_routes._fd_identity', side_effect=lambda h: h.identity)
        self.ns_patch = mock.patch('hf_owned_routes._namespace_identity', side_effect=lambda name: self.namespace.get(name))
        self.fd_patch.start(); self.ns_patch.start()
        self.addCleanup(self.fd_patch.stop); self.addCleanup(self.ns_patch.stop)

    def scope(self): return OwnedRouteMemory(self.module, self.path, max_bytes=4096)

    def test_collision_never_attaches_unlinks_or_changes_preexisting_object(self):
        for size in (1024, 2048):
            self.namespace[self.name] = (99, 99, size); self.calls.clear()
            with self.scope():
                with self.assertRaises(RuntimeError) as caught:
                    self.module.shared_memory.SharedMemory(self.name, create=True, size=1024)
                self.assertNotIsInstance(caught.exception, FileExistsError)
            self.assertEqual(self.namespace[self.name], (99, 99, size))
            self.assertEqual(self.calls, [('open', self.name, True, 1024)])
            self.assertIs(self.module.shared_memory, self.original)

    def test_native_reader_default_is_allowed_only_after_one_owned_creator(self):
        with self.scope() as scope:
            proxy = self.module.shared_memory
            with self.assertRaises(ValueError): proxy.SharedMemory(self.name)
            creator = proxy.SharedMemory(self.name, create=True, size=1024)
            reader = proxy.SharedMemory(self.name)
            self.assertIs(type(creator), self.original.SharedMemory)
            self.assertIs(type(reader), self.original.SharedMemory)
            self.assertIs(proxy.untouched, self.original.untouched)
            with self.assertRaises(ValueError): proxy.SharedMemory(self.name, create=True, size=1024)
            with self.assertRaises(ValueError): proxy.SharedMemory(self.name)
            scope.reconcile('1234567890', 0)
        self.assertNotIn(self.name, self.namespace)
        self.assertEqual(creator._fd, -1); self.assertEqual(reader._fd, -1)
        self.assertEqual(scope.report['status'], 'OWNED_MEMORY_CLOSED')

    def test_invalid_names_sizes_and_reader_names_reject_before_delegation(self):
        cases = [(self.name, True, True), (self.name, 1, 1024), (self.name, True, 0),
                 (self.name, True, 4097), ('../foreign', True, 10)]
        for name, create, size in cases:
            with self.scope() as scope:
                with self.assertRaises(ValueError):
                    self.module.shared_memory.SharedMemory(name, create=create, size=size)
        self.assertEqual(self.calls, [])
        with self.scope():
            self.module.shared_memory.SharedMemory(self.name, create=True, size=1024)
            with self.assertRaises(ValueError):
                self.module.shared_memory.SharedMemory('vllm_routed_experts_buffer_9_0')

    def test_partial_initializer_keeps_exclusively_created_handle_for_cleanup(self):
        self.partial = True
        with self.scope():
            with self.assertRaisesRegex(RuntimeError, 'failure after'):
                self.module.shared_memory.SharedMemory(self.name, create=True, size=1024)
        self.assertNotIn(self.name, self.namespace)
        self.assertTrue(all(handle._fd == -1 for handle in self.handles.values()))

    def test_cleanup_attempts_reader_creator_and_engine_even_if_native_cleanup_raises(self):
        with self.scope() as scope:
            creator = self.module.shared_memory.SharedMemory(self.name, create=True, size=1024)
            reader = self.module.shared_memory.SharedMemory(self.name)
            order = []
            def bad_reader(): order.append('reader'); raise RuntimeError('reader failed')
            def bad_creator(): order.append('creator'); raise RuntimeError('creator failed')
            result = scope.finish(NS(_shm=creator, cleanup=bad_creator),
                NS(_shm=reader, cleanup=bad_reader), NS(shutdown=lambda: order.append('engine')))
        self.assertEqual(order, ['reader', 'creator', 'engine'])
        self.assertEqual(result['status'], 'FAILED_MEMORY_CLEANUP')
        self.assertNotIn(self.name, self.namespace)
        self.assertIs(self.module.shared_memory, self.original)

    def test_replacement_namespace_is_never_unlinked_by_fallback(self):
        with self.scope() as scope:
            creator = self.module.shared_memory.SharedMemory(self.name, create=True, size=1024)
            self.namespace[self.name] = (7, 77, 1024)
            owner = NS(_shm=creator, cleanup=lambda: creator.unlink())
            result = scope.finish(owner)
        self.assertEqual(self.namespace[self.name], (7, 77, 1024))
        self.assertIsNone(owner._shm)
        self.assertEqual(creator._fd, -1)
        self.assertEqual(result['status'], 'FAILED_MEMORY_CLEANUP')
        self.assertFalse(any(call[0] == 'unlink' for call in self.calls))

    def test_wrong_imported_tmp_prefix_refuses_before_binding(self):
        self.module._TMP_DIR = '/tmp'
        with self.assertRaises(ValueError):
            with self.scope(): pass
        self.assertIs(self.module.shared_memory, self.original)

    def test_missing_native_cleanup_entry_does_not_skip_owned_fallback(self):
        with self.scope() as scope:
            creator = self.module.shared_memory.SharedMemory(self.name, create=True, size=1024)
            report = scope.finish(NS(_shm=creator), client=NS())
        self.assertEqual(report['status'], 'FAILED_MEMORY_CLEANUP')
        self.assertNotIn(self.name, self.namespace)
        self.assertEqual(creator._fd, -1)

    def test_unobserved_descriptor_identity_is_failed_cleanup_not_name_ownership(self):
        with self.scope() as scope:
            with mock.patch('hf_owned_routes._fd_identity', side_effect=OSError('identity unavailable')):
                with self.assertRaises(OSError):
                    self.module.shared_memory.SharedMemory(self.name, create=True, size=1024)
            report = scope.finish()
        self.assertEqual(report['status'], 'FAILED_MEMORY_CLEANUP')
        self.assertIn(self.name, self.namespace)
        self.assertFalse(any(call[0] == 'unlink' for call in self.calls))

    def test_cleanup_interrupt_is_recorded_and_remaining_owned_cleanup_runs(self):
        with self.scope() as scope:
            creator = self.module.shared_memory.SharedMemory(self.name, create=True, size=1024)
            reader = self.module.shared_memory.SharedMemory(self.name)
            order = []
            def interrupted(): order.append('reader'); raise KeyboardInterrupt()
            def close_creator():
                order.append('creator'); creator.close(); creator.unlink()
            report = scope.finish(NS(_shm=creator, cleanup=close_creator),
                NS(_shm=reader, cleanup=interrupted), NS(shutdown=lambda: order.append('engine')))
        self.assertEqual(order, ['reader', 'creator', 'engine'])
        self.assertEqual(report['status'], 'FAILED_MEMORY_CLEANUP')
        self.assertNotIn(self.name, self.namespace)
        self.assertEqual(creator._fd, -1); self.assertEqual(reader._fd, -1)

    def test_namespace_interrupt_never_publishes_success_and_still_closes_handles(self):
        with self.scope() as scope:
            creator = self.module.shared_memory.SharedMemory(self.name, create=True, size=1024)
            calls = 0
            def interrupted_once(name):
                nonlocal calls
                calls += 1
                if calls == 1: raise KeyboardInterrupt()
                return self.namespace.get(name)
            with mock.patch('hf_owned_routes._namespace_identity', side_effect=interrupted_once):
                report = scope.finish(NS(_shm=creator, cleanup=lambda: creator.unlink()))
        self.assertEqual(report['status'], 'FAILED_MEMORY_CLEANUP')
        self.assertEqual(creator._fd, -1)
        self.assertNotIn(self.name, self.namespace)


if __name__ == '__main__': unittest.main()

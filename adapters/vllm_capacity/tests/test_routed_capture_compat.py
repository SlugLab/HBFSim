"""CPU fake capturers only; optional vLLM/torch imports must remain deferred."""
import importlib
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]


class RecordingCapturer:
    def __init__(self):
        self.calls = []
    def capture(self, layer, ids):
        self.calls.append((layer, ids))


def fake_class():
    class Capturer:
        instance = None
        @staticmethod
        def get_instance():
            return Capturer.instance
    return Capturer


class RoutedCaptureCompatTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'adapters/vllm_capacity/routed_capture_compat.py').is_file(), 'deferred capture closure is missing')
        self.api = importlib.import_module('routed_capture_compat')

    def test_proxy_forwards_exact_ids_after_singleton_exists(self):
        cls = fake_class()
        with self.api.install_vllm_routed_experts_deferred_binding(cls) as binding:
            proxy = cls.get_instance()
            proxy.capture(0, 'TEST_ONLY dummy')
            cls.instance = RecordingCapturer()
            ids = [[3, 1]]
            proxy.capture(7, ids)
            self.assertEqual(cls.instance.calls, [(7, ids)])
            self.assertIs(cls.instance.calls[0][1], ids)
            self.assertIs(cls.get_instance(), cls.instance)
            self.assertEqual(binding.summary()['ignored_before_init'], 1)
            self.assertEqual(binding.summary()['forwarded_calls'], 1)
            self.assertFalse(binding.summary()['scientific_validation_passed'])

    def test_idempotent_binding_restores_exact_original_getter(self):
        cls = fake_class()
        original = cls.__dict__['get_instance']
        binding = self.api.install_vllm_routed_experts_deferred_binding(cls)
        self.assertIs(self.api.install_vllm_routed_experts_deferred_binding(cls), binding)
        binding.close()
        self.assertIs(cls.__dict__['get_instance'], original)
        self.assertIsNone(cls.get_instance())

    def test_closed_early_proxy_cannot_silently_drop_future_capture(self):
        cls = fake_class()
        binding = self.api.install_vllm_routed_experts_deferred_binding(cls)
        proxy = cls.get_instance()
        binding.close()
        with self.assertRaises(RuntimeError):
            proxy.capture(0, 'TEST_ONLY')

    def test_existing_instance_is_preserved_and_never_called_gold(self):
        cls = fake_class()
        cls.instance = RecordingCapturer()
        with self.api.install_vllm_routed_experts_deferred_binding(cls) as binding:
            self.assertIs(cls.get_instance(), cls.instance)
            self.assertEqual(binding.summary()['status'], 'BINDING_INSTALLED_UNVALIDATED')
            self.assertEqual(binding.summary()['forwarded_calls'], 0)

    def test_close_does_not_overwrite_another_in_process_hook(self):
        cls = fake_class()
        binding = self.api.install_vllm_routed_experts_deferred_binding(cls)
        replacement = staticmethod(lambda: 'OTHER_HOOK')
        cls.get_instance = replacement
        binding.close()
        self.assertIs(cls.__dict__['get_instance'], replacement)

    def test_imports_do_not_load_optional_gpu_dependencies_or_change_environment(self):
        code = "import os,sys;before=dict(os.environ);import trace_collector,routed_capture_compat;assert dict(os.environ)==before;assert not any(k=='torch' or k.startswith('vllm') for k in sys.modules)"
        result = subprocess.run([sys.executable, '-c', code], cwd=ROOT / 'adapters/vllm_capacity', capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

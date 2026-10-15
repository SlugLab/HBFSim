import contextlib
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from prepare_overlay import transform
from report_layers import summarize

class SwitchTests(unittest.TestCase):
    def adapter(self):
        ns = {}
        exec(transform('adapter', 'def register():\n    return "installed"\n'), ns)
        return ns['register']

    def test_default_off_never_installs(self):
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(self.adapter()())

    def test_strict_off_refuses(self):
        with patch.dict(os.environ, {'HBFSIM_INSTRUMENTATION_POLICY':'strict'}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'strict'):
                self.adapter()()

    def test_on_missing_agent_refuses(self):
        with patch.dict(os.environ, {'HBFSIM_ENABLE_SASS_LIFTER':'1'}, clear=True), patch('ctypes.CDLL', return_value=object()):
            with self.assertRaisesRegex(RuntimeError, 'switch-aware'):
                self.adapter()()

    def test_invalid_refuses(self):
        with patch.dict(os.environ, {'HBFSIM_ENABLE_SASS_LIFTER':'yes'}, clear=True):
            with self.assertRaisesRegex(RuntimeError, '0 or 1'):
                self.adapter()()

    def test_agent_actual_predicate_gate(self):
        text = transform('agent', 'bool qkv_enabled() {\n return true;\n}\n')
        self.assertIn('if (!hbfsim_sass_enabled()) return false;', text)
        self.assertNotIn('bpftime_nv_bind_ptx_variant', text)

    def test_source_mismatch_refuses(self):
        with self.assertRaises(ValueError):
            transform('agent', 'unknown source')

    def test_registration_is_not_coverage(self):
        with self.assertRaises(ValueError):
            summarize({'status':'MODEL_CONNECTED_PASS', 'target_pid':1, 'target_start_ticks':2, 'validated_plan_sha256':'x', 'registered_storage_count':147})

    def test_layer_summary_requires_completed_modules(self):
        r = dict(status='MODEL_CONNECTED_PASS', target_pid=1, target_start_ticks=2, validated_plan_sha256='x',
                 by_storage=[dict(alias='model.layers.3.mlp.weight', base=4096, bytes=16, active_complete_modules=['ptx:sha256:abc'])])
        self.assertEqual(summarize(r)['layers'], [3])
        r['by_storage'][0]['active_complete_modules'] = []
        with self.assertRaises(ValueError):
            summarize(r)

if __name__ == '__main__':
    unittest.main()

class LauncherContractTests(unittest.TestCase):
    def test_no_filename_blacklist_and_research_isolation_remains(self):
        runner = Path(__file__).resolve().parents[2]/'scripts/run_with_bpftime.sh'
        text = runner.read_text()
        self.assertNotIn('*bpftime-agent*|*provider_router*', text)
        self.assertIn('original_preload=${LD_PRELOAD:-}', text)
        self.assertIn('${original_preload:+:$original_preload}', text)
        self.assertIn('[[ -n ${LD_PRELOAD:-} || -n ${HBFSIM_TARGET_EXTRA_LD_PRELOAD:-}', text)

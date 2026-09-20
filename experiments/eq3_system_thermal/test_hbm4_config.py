import json
from pathlib import Path
import unittest

from prepare_causal_campaign import make_config
from prepare_causal_main import make_main


class Hbm4ConfigTests(unittest.TestCase):
    def test_actual_service_supply_uses_frozen_hbm4_profile(self):
        profile = json.loads((Path(__file__).resolve().parents[2] /
            'configs/eq3_thermal/research/candidate_profile.json').read_text())['device_selection']['hbm']
        for topology in ('mixed_direct', 'relay', 'dash'):
            c = make_config('fixed', topology, 'guard_only', 'Qwen/Qwen2.5-7B-Instruct', 1, 1)
            for stack in c['service']['fabric']['hbm']:
                self.assertEqual(sum(c['service']['channels'][stack].values()), profile['raw_bandwidth_Bps'])
            self.assertEqual(c['evidence']['hbm_device']['identity'], 'Micron HBM4')
            self.assertEqual(c['evidence']['hbm_device']['nominal_capacity_label'], '36 GB')
            self.assertEqual(c['evidence']['hbm_device']['dies_per_stack'], 12)
            for stack in c['service']['fabric']['hbf']:
                self.assertEqual(sum(c['service']['channels'][stack].values()), 1_536_000_000_000)

    def test_cache_sizes_are_allocations_not_device_replacements(self):
        for size in (4, 16):
            c = make_main('mixed_direct', 'read_rate_feedback_thermal_guard_v1', '7B', 384, f'cache{size}GiB')
            self.assertEqual(c['executor']['cache_capacity_bytes'], size * 1024**3)
            self.assertEqual(c['evidence']['hbm_device']['nominal_capacity_label'], '36 GB')
            self.assertEqual(len(c['service']['fabric']['hbm']), 4)
            self.assertTrue(all(sum(v.values()) == 2_048_000_000_000 for k, v in c['service']['channels'].items() if k.startswith('hbm')))


if __name__ == '__main__':
    unittest.main()

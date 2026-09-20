import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest

from experiments.eq3_maintenance.run_point import (
    FROZEN_INPUT_FILES, load_frozen_inputs, workload_burst_period_ns)
from experiments.eq3_maintenance.campaign_inputs import workload


SOURCE = (Path(__file__).resolve().parents[4]/'plans'/'isolated-maintenance-campaign-v1'/
          'points'/'Q1-WEIGHT-MAINT-PILOT03')


def args(**changes):
    values=dict(mode='mixed_direct',workload='W1',policy='guard_only',active_s=8.0,
                recovery_s=2.0,weight_model='Qwen/Qwen2.5-7B-Instruct',
                geometry='legacy16k',capacity_scope='working-region',maintenance=True,
                gpu_external_w=200.0)
    values.update(changes)
    return SimpleNamespace(**values)


class FrozenRunPointTests(unittest.TestCase):
    def test_pilot03_frozen_contract_loads_exact_inputs(self):
        source,manifest,values=load_frozen_inputs(SOURCE,args())
        self.assertEqual(source,SOURCE.resolve())
        self.assertEqual(manifest['request_count'],4032)
        self.assertEqual(len(values['maintenance-input.json']),64)
        for name in FROZEN_INPUT_FILES:
            self.assertEqual(hashlib.sha256((source/name).read_bytes()).hexdigest(),
                             manifest['input_sha256'][name])

    def test_frozen_contract_rejects_scientific_or_power_drift(self):
        for changed in (dict(active_s=7.0),dict(geometry='ocp4k16bank'),
                        dict(gpu_external_w=100.0)):
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                load_frozen_inputs(SOURCE,args(**changed))

    def test_workload_shapes_are_explicit_and_distinct(self):
        self.assertEqual(workload_burst_period_ns('W1'),20_000_000)
        self.assertEqual(workload_burst_period_ns('W2'),100_000_000)
        w1=workload('mixed_direct','W1',total_hbf_rps=100,active_ns=1_000_000_000,
                    burst_period_ns=workload_burst_period_ns('W1'),hbm_rps_per_stack=0)
        w2=workload('mixed_direct','W2',total_hbf_rps=100,active_ns=1_000_000_000,
                    burst_period_ns=workload_burst_period_ns('W2'),hbm_rps_per_stack=0)
        self.assertEqual(len(w1),len(w2))
        self.assertGreater(len({row['arrival_ns'] for row in w1}),
                           len({row['arrival_ns'] for row in w2}))
        self.assertTrue(all(row['arrival_ns']%100_000_000==0 for row in w2))
        with self.assertRaises(ValueError):
            workload_burst_period_ns('other')


if __name__=='__main__':
    unittest.main()

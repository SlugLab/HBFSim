"""Fixed integration checks, no numerical thermal solve or research matrix."""
import io
import json
from copy import deepcopy
from pathlib import Path
import unittest
from run_causal_point import execute
from topology_service import default_config
from test_run_causal_point import normalized, FakeThermal, trace, energy_profile

PROFILE=json.loads((Path(__file__).parent/'ecc_proxy/profile_v1.json').read_text())

class RunnerIntegrationTests(unittest.TestCase):
    def run_case(self, strength):
        service=default_config('mixed_direct')
        baseline={s:10**12 for s in service['channels']}
        p=deepcopy(PROFILE);p['transfer_strength']=strength
        config={
            'point_id':'ecc-fixed','active_ns':20_000_000,'recovery_ns':20_000_000,
            'window_ns':20_000_000,'strategy':'guard_only','service':service,
            'energy':energy_profile(),'target_bytes_per_s_by_stack':{s:1 for s in baseline},
            'trace':{'total_batches':1,'max_active_batches':1,'batch_interval_ns':100},
            'executor':{'cache_mode':'disabled','cache_capacity_bytes':0,
                'coalescing_enabled':True,'prefetch_wait_mode':'wait_at_consumption',
                'stripe_unit_bytes':4096,'migration_mode':'fixed','retry_count_per_source_read':0,
                'stripe_targets':[{'stack':'hbf0','channel':'0','route':'direct'}]},
            'hbf_read_cost_proxy':{'mode':'conditional_nand_history_v1','profile':p,
                'initial_by_stack':{s:{'equivalent_age_days_30c':90,'pe_cycles':1000,
                    'temperature_k':300} for s in service['fabric']['hbf']}}}
        sink=io.StringIO()
        result=execute(config,normalized(service),FakeThermal(sorted(baseline),baseline),sink,
                       initial_trace=trace(0),trace_factory=trace)
        return result,[json.loads(x) for x in sink.getvalue().splitlines()]

    def test_real_consumer_changes_physical_work_energy_not_useful_payload(self):
        base,brows=self.run_case(0);cost,rows=self.run_case(.1)
        self.assertEqual(base['completed_tokens'],cost['completed_tokens'])
        self.assertEqual(base['delivered_useful_bytes_by_stack'],cost['delivered_useful_bytes_by_stack'])
        self.assertGreater(cost['energy_j'],base['energy_j'])
        activities=[a for w in rows for a in w['service']['activities']]
        media=sum(a['bytes'] for a in activities if a['phase']=='media_read')
        self.assertGreater(media,4096)
        self.assertAlmostEqual(cost['energy_j'],media*50e-12)
        decisions=rows[0]['hbf_read_cost_proxy']['admission_cost_decisions']
        self.assertEqual(len(decisions),1)
        self.assertEqual(decisions[0]['attempt_work_milli'],1900)
        self.assertGreater(rows[0]['service']['completions'][0]['completion_ns'],
                           brows[0]['service']['completions'][0]['completion_ns'])
        self.assertEqual(rows[-1]['hbf_read_cost_proxy']['state']['states']['hbf0']['last_ns'],40_000_000)
        self.assertEqual(rows[-1]['hbf_read_cost_proxy']['admission_cost_decisions'],[])

if __name__=='__main__':unittest.main()

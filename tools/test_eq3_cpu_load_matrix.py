import json,tempfile,unittest
from pathlib import Path
from eq3_cpu_load_compare import compare
from eq3_cpu_load_matrix import POLICIES,RATES,make_load_case
from eq3_cpu_load_review import percentile,queue_trace

class SustainedLoadMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.code=Path(__file__).resolve().parents[1]
    def test_exact_matrix_and_policy_fairness(self):
        for rate in RATES:
            cases=[make_load_case(self.code,rate,p) for p in POLICIES]
            self.assertEqual({len(x['requests']) for x in cases},{rate*20*8})
            self.assertEqual(len({x['workload_identity_sha256'] for x in cases}),1)
            self.assertTrue(all(x['end_ns']==30_000_000_000 and max(r['arrival_ns'] for r in x['requests'])<20_000_000_000 for x in cases))
        self.assertAlmostEqual(make_load_case(self.code,25,'none')['screening']['foreground_base_utilization_per_stack'],.5)
        self.assertEqual(make_load_case(self.code,25,'none')['screening']['light_capacity_rps'],12.5)
    def test_review_helpers_include_censoring(self):
        jobs=[{'maintenance':False,'arrival_ns':0,'start_ns':5,'end_ns':10},
              {'maintenance':False,'arrival_ns':0},{'maintenance':True,'arrival_ns':0,'start_ns':0,'end_ns':20}]
        trace=queue_trace(jobs,20,10);self.assertEqual(trace[0]['foreground_queued'],2);self.assertEqual(trace[1]['foreground_queued'],1)
        self.assertEqual(percentile([1,2,3],.5),2);self.assertAlmostEqual(percentile([1,3],.95),2.9)
    def test_aggregate_retains_negative_result(self):
        rows=[]
        for rate in RATES:
            for i,policy in enumerate(POLICIES):
                rows.append({'rate_per_stack_rps':rate,'policy':policy,'workload_identity_sha256':str(rate),
                  'foreground_complete':100-i,'foreground_unfinished':i,'completed_logical_bytes':4096*(100-i),
                  'max_temperature_k':300-i,'latency_completed_s':{'p95':.1+i},'maintenance':{'queued':i,'overdue_cohorts':i},
                  'max_data_age_s':i,'package_dynamic_energy_j':10-i,'external_energy_j':1})
        result=compare(rows);self.assertTrue(result['rates']['5']['arms']['hysteresis']['negative_flags']['lower_temperature_with_more_backlog'])
        self.assertNotIn('winner',result)
        self.assertTrue(all('winner' not in group for group in result['rates'].values()))
if __name__=='__main__':unittest.main()

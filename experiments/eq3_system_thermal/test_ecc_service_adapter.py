from copy import deepcopy
import unittest
from causal_service import CausalTopologyService
from ecc_service_adapter import ReliabilityCausalService
from topology_service import default_config

class FixedProvider:
 def __init__(self,attempts):
  self.states={'hbf0':{}};self.attempts=attempts
  self.profile={'ecc_decoder_headroom_over_fresh_media':2}
 def cost(self,stack,now):return {'attempt_work_milli':self.attempts,'expected_retry_steps':self.attempts/1000-1}

def run(attempts,topology='mixed_direct'):
 c=default_config(topology);p=FixedProvider(attempts);s=ReliabilityCausalService(c,p)
 s.begin_window(0,20_000_000,{k:10**12 for k in c['channels']},{k:'normal' for k in c['channels']})
 s.submit_jobs([{'job_id':'read1','stack':'hbf0','channel':'0','route':'relay' if topology=='relay' else 'direct',
                 'operation':'read','bytes':96_000_000,'arrival_ns':0}])
 return s,s.advance_to(20_000_000)

class AdapterTests(unittest.TestCase):
 def test_retry_consumes_media_and_delays_unique_completion(self):
  _,base=run(1000);_,retry=run(2000)
  self.assertEqual(retry['completion_ids'],['read1'])
  self.assertGreater(retry['job_progress'][0]['completion_ns'],base['job_progress'][0]['completion_ns'])
  self.assertEqual(sum(x['bytes'] for x in retry['activities'] if x['phase']=='media_read'),192_000_000)
  self.assertEqual(sum(x['bytes'] for x in retry['activities'] if x['phase']=='direct_gpu_link'),96_000_000)
 def test_relay_success_payload_is_not_retransmitted_for_internal_retry(self):
  _,receipt=run(2500,'relay')
  self.assertEqual(sum(x['bytes'] for x in receipt['activities'] if x['phase']=='media_read'),240_000_000)
  self.assertEqual(sum(x['bytes'] for x in receipt['activities'] if x['phase']=='relay_send'),96_000_000)
  self.assertEqual(sum(x['bytes'] for x in receipt['activities'] if x['phase']=='partner_gpu_drain'),96_000_000)
 def test_null_effort_matches_unmodified_service(self):
  wrapped,receipt=run(1000);c=default_config('mixed_direct');s=CausalTopologyService(c)
  s.begin_window(0,20_000_000,{k:10**12 for k in c['channels']},{k:'normal' for k in c['channels']})
  s.submit_jobs([{'job_id':'read1','stack':'hbf0','channel':'0','route':'direct','operation':'read','bytes':96_000_000,'arrival_ns':0}])
  original=s.advance_to(20_000_000)
  self.assertEqual(receipt['job_progress'][0]['completion_ns'],original['job_progress'][0]['completion_ns'])
  self.assertEqual(receipt['completion_ids'],original['completion_ids'])
 def test_active_cost_frozen_when_temperature_feedback_changes_provider(self):
  c=default_config('mixed_direct');p=FixedProvider(2000);s=ReliabilityCausalService(c,p)
  s.begin_window(0,20_000_000,{k:10**12 for k in c['channels']},{k:'normal' for k in c['channels']})
  s.submit_jobs([{'job_id':'r','stack':'hbf0','channel':'0','route':'direct','operation':'read','bytes':96_000_000,'arrival_ns':0}])
  s.advance_to(500_000);p.attempts=1000;r=s.advance_to(20_000_000)
  self.assertEqual(r['job_progress'][0]['metadata']['reliability_cost_proxy']['attempt_work_milli'],2000)
if __name__=='__main__':unittest.main()

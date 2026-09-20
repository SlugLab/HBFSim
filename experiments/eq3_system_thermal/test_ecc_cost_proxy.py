import json
from copy import deepcopy
from pathlib import Path
import unittest
from ecc_cost_proxy import ReadCostProxy,ProxyDomainError,DAY_NS
PROFILE=json.loads((Path(__file__).parent/'ecc_proxy/profile_v1.json').read_text())

def proxy(age=0,pe=0,temp=303.15,strength=1):
 p=deepcopy(PROFILE);p['transfer_strength']=strength
 return ReadCostProxy(p,{'hbf0':{'equivalent_age_days_30c':age,'pe_cycles':pe,'temperature_k':temp}})

class CostTests(unittest.TestCase):
 def test_published_anchor_and_separate_scenario_bounds(self):
  self.assertEqual(proxy().cost('hbf0',0)['expected_retry_steps'],0)
  self.assertAlmostEqual(proxy(365,2000).cost('hbf0',0)['expected_retry_steps'],19.9)
  self.assertGreater(proxy(90,0).cost('hbf0',0)['expected_retry_steps'],3)
  self.assertGreaterEqual(proxy(90,1000).cost('hbf0',0)['expected_retry_steps'],8)
  self.assertGreaterEqual(proxy(180,0).cost('hbf0',0)['expected_retry_steps'],.544*7)
 def test_history_hotter_accumulates_more_without_changing_wall_time(self):
  cold,hot=proxy(temp=303.15),proxy(temp=358.15)
  for p in (cold,hot):p.observe(0,20_000_000_000,{'hbf0':303.15})
  self.assertGreater(hot.cost('hbf0',20_000_000_000)['expected_retry_steps'],cold.cost('hbf0',20_000_000_000)['expected_retry_steps'])
  self.assertEqual(hot.states['hbf0']['last_ns'],20_000_000_000)
 def test_cooling_never_clears_previous_damage_or_instantly_increases_cost(self):
  p=proxy(age=10,temp=358.15);before=p.cost('hbf0',0)
  p.observe(0,0,{'hbf0':303.15});after=p.cost('hbf0',0)
  self.assertEqual(before['expected_retry_steps'],after['expected_retry_steps'])
 def test_null_transfer_and_domain_failures(self):
  self.assertEqual(proxy(365,2000,strength=0).cost('hbf0',0)['attempt_work_milli'],1000)
  with self.assertRaises(ProxyDomainError):proxy(366)
  with self.assertRaises(ProxyDomainError):proxy(pe=2001)
  with self.assertRaises(ValueError):ReadCostProxy(PROFILE,{'hbm0':{'equivalent_age_days_30c':0,'pe_cycles':0,'temperature_k':300}})
if __name__=='__main__':unittest.main()

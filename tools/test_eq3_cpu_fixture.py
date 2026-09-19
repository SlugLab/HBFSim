"""Fixed static generator tests: never execute a solver or open research blind."""
import unittest
from pathlib import Path
from eq3_cpu_fixture import make_case

class FixtureTests(unittest.TestCase):
    def test_policy_inputs_identical_except_policy(self):
        code=Path(__file__).resolve().parents[1]
        for scenario in ('Safe','Near','Stress'):
            a=make_case(code,scenario,'none');b=make_case(code,scenario,'hysteresis')
            b['config']['policy']='none';self.assertEqual(a,b)
            self.assertEqual(1000,len(a['requests']))
            nodes=[x for x in a['config']['thermal_model_text'].splitlines() if x.startswith('node ')]
            self.assertEqual(123,len(nodes));self.assertEqual(8,sum('_base ' in x for x in nodes))
            for s in a['config']['stacks']:
                self.assertEqual(set(range(s['die_count'])),{r['die'] for r in a['requests'] if r['stack']==s['id']})

if __name__=='__main__':unittest.main()

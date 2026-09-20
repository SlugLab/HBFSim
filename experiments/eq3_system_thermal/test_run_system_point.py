import io
import unittest
from prepare_stage import config
from run_system_point import execute


class ThermalFixture:
    limits={'hbf':(353.15,363.15,378.15),'hbm':(353.15,363.15,378.15),'gpu':(363.15,373.15,383.15)}
    def __init__(self, stacks):
        self.stacks=stacks;self.total=0
    def advance(self,start,end,energy):
        self.total+=sum(energy.values())
        return {'temperatures':{s:300.01 for s in self.stacks},
                'stack_states':{s:'normal' for s in self.stacks},
                'hysteresis_budget_bytes':{s:1_536_000_000_000*20_000_000//10**9 for s in self.stacks},
                'energy_j':{'cumulative':{'total_input_j':self.total}}}


class RunnerTests(unittest.TestCase):
    def test_four_topology_energy_and_recovery_drain(self):
        for topology in ('mixed_direct','all_hbf_direct','relay','dash'):
            c=config('fixed',topology,1_920_000_000_000,'guard_only',.04,.04)
            rows=[{'id':'gpu','device_id':'gpu','role':'compute_die'}]
            for stack in c['service']['channels']:
                rows.append({'id':stack+'.base','device_id':stack,'role':'base'})
                for i in range(16):
                    rows.append({'id':f'{stack}.die{i}','device_id':stack,'role':'array_die',
                                 'die_index':i,'physical_type':stack[:3].upper()})
            result=execute(c,{'components':rows},ThermalFixture(c['service']['channels']),io.StringIO())
            self.assertEqual(result['offered_bytes'],result['delivered_bytes'])
            self.assertEqual(result['backlog_bytes'],0)
            coefficient={'mixed_direct':50e-12,'all_hbf_direct':50e-12,
                         'relay':54e-12,'dash':52e-12}[topology]
            self.assertAlmostEqual(result['energy_j'],result['delivered_bytes']*coefficient,places=8)


if __name__=='__main__':unittest.main()

import unittest
from thermal_client import ThermalService

class GuardContractTests(unittest.TestCase):
    def client(self,delay=0):
        x=ThermalService.__new__(ThermalService)
        x.now=0;x.window=20;x.action_delay=delay;x.dwell=100;x.hysteresis_k=2.
        x.limits={'gpu':(310,320,330),'hbf':(310,320,330)}
        x.states={};x.pending={};x.last_change={};x.baseline_budgets={'hbf0':100};x.light_fraction=.5
        return x
    def test_gpu_separate_temperature_propagates_guard_without_extra_stack(self):
        x=self.client();x.command=lambda _:dict(entity_temperatures_k={'gpu':{'hotspot_k':325},'hbf0.die0':{'hotspot_k':300}})
        result=x.advance(0,20,{})
        self.assertEqual(result['stack_states'],{'hbf0':'severe'})
        self.assertEqual(result['temperatures']['gpu'],325)
    def test_gpu_light_propagates_half_budget(self):
        x=self.client();x.command=lambda _:dict(entity_temperatures_k={'gpu':{'hotspot_k':315},'hbf0.die0':{'hotspot_k':300}})
        result=x.advance(0,20,{})
        self.assertEqual(result['stack_states'],{'hbf0':'light'})
        self.assertEqual(result['hysteresis_budget_bytes'],{'hbf0':50})
    def test_escalation_delay_and_recovery_dwell(self):
        x=self.client(20)
        def sample(temp,t):return x._guard({'hbf0.die0':{'hotspot_k':temp}},t)[1]['hbf0']
        self.assertEqual(sample(325,20),'normal')
        self.assertEqual(sample(325,40),'severe')
        self.assertEqual(sample(300,60),'severe')
        self.assertEqual(sample(300,100),'severe')
        self.assertEqual(sample(300,140),'normal')
    def test_cancel_obsolete_pending(self):
        x=self.client(20)
        x._guard({'hbf0.die0':{'hotspot_k':325}},20)
        x._guard({'hbf0.die0':{'hotspot_k':300}},30)
        self.assertEqual(x.pending,{})

if __name__=='__main__':unittest.main()

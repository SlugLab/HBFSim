import unittest
from energy_ledger import ActivityEnergyLedger

class EnergyTests(unittest.TestCase):
    def ledger(self):
        return ActivityEnergyLedger(dict(nand_media_w={'0':2.,'1':3.,'2':4.},nand_data_out_w=1.,
              hbm_array_j_per_byte=.1,fabric_endpoint_j_per_byte=.01,gpu_external_w=0.,evidence='FIXTURE'),
              {'dies_per_channel':2,'stacks':[{'id':'hbf0','channels':[0]}]},
              {'gpu','hbf0.die0','hbf0.die1','hbf0.base','hbm0.base','hbm0.die0','hbm0.die1'},
              hbm_dies={'hbm0':['hbm0.die0','hbm0.die1']})
    def event(self,phase,time):
        return dict(command_id=1,phase=phase,time_ns=time,transactions=[dict(channel=0,chip=0,die=1,
                    type=0,bytes=16,transaction_id=1,external_request_id=1,stack='hbf0')])
    def test_media_cross_window_no_dispatch_heat(self):
        x=self.ledger();x.native(self.event(0,0));self.assertEqual(x.flush(10),{'gpu':0.})
        x.native(self.event(1,10));self.assertAlmostEqual(x.flush(20)['hbf0.die1'],2e-8)
        x.native(self.event(2,25));self.assertAlmostEqual(x.flush(30)['hbf0.die1'],1e-8)
        self.assertAlmostEqual(x.total_j,3e-8)
    def test_same_die_multiplane_not_double_power(self):
        x=self.ledger();e=self.event(1,0);e['transactions'].append(dict(e['transactions'][0],transaction_id=2,plane=1))
        x.native(e);self.assertAlmostEqual(x.flush(100)['hbf0.die1'],2e-7)
    def test_relay_no_hbm_array_heat(self):
        x=self.ledger();x.fabric(dict(kind='start',stage='HBF_RELAY',request_id='a',bytes=10,start_ns=0,end_ns=100),
                                 dict(stack='hbf0',partner='hbm0'))
        result=x.flush(100)
        self.assertAlmostEqual(result['hbf0.base'],.1);self.assertAlmostEqual(result['hbm0.base'],.1)
        self.assertNotIn('hbm0.die0',result)
    def test_explicit_hbm_distribution_and_duplicate_rejected(self):
        x=self.ledger();e=dict(phase='media_start',stack_id='hbm0',time_ns=0,media_end_ns=100,bytes=10,request_id='a')
        x.hbm(e);r=x.flush(50)
        self.assertAlmostEqual(r['hbm0.die0'],.25);self.assertAlmostEqual(r['hbm0.die1'],.25)
        with self.assertRaises(ValueError):x.hbm(e)
    def test_unknown_die_rejected_not_address_guessed(self):
        x=self.ledger();e=self.event(1,0);e['transactions'][0]['die']='UNKNOWN'
        with self.assertRaises(ValueError):x.native(e)
    def test_end_without_begin_rejected(self):
        with self.assertRaises(ValueError):self.ledger().native(self.event(2,10))

if __name__=='__main__':unittest.main()

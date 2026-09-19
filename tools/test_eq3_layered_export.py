"""Hand-computable fixed software fixtures; no research solver execution."""
import copy
import tempfile
import unittest
from pathlib import Path

from eq3_layered_export import discretize, network, rc_files, reference_files, sampled_power, generate


def slab():
    return {'package_size_m':[.002,.001,.003],
            'components':[{'id':'bottom','device':'package','role':'passive','xyz_m':[0,0,0],
                           'size_m':[.002,.001,.001],'material':'m','powered':False},
                          {'id':'base','device':'hbf_test','role':'base_die','xyz_m':[0,0,.001],
                           'size_m':[.002,.001,.002],'material':'m','powered':True}],
            'materials':{'m':{'k_xyz_w_m_k':[2,3,4],'cv_j_m3_k':1e6}},'background':None,
            'boundaries':{'initial_temperature_k':300,'top':{'h_w_m2_k':100,'ambient_k':310},
                          'bottom':{'h_w_m2_k':50,'ambient_k':290},'sides':'adiabatic'},
            'power':{'intervals':[{'start_s':0,'end_s':.5,'power_w':{'base':2}},
                                   {'start_s':.5,'end_s':1,'power_w':{'base':0}}],
                     'total_energy_j':1},'sensors':[{'id':'base','reduction':'weighted_mean',
                         'weights':[{'component_id':'base','weight':1}]}]}


class LayeredExportTests(unittest.TestCase):
    def test_four_topology_fixtures_export_without_thermal_solves(self):
        from test_eq3_layered_ir import _profile, _per_component_power
        from eq3_layered_ir import normalize
        for mode in ('mixed_direct','all_hbf_direct','relay','dash'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                profile=_profile(mode)
                ir=normalize(profile,_per_component_power(profile),'t')
                receipt=generate(ir,Path(tmp)/'out',.0001,.01,.05)
                self.assertFalse(receipt['research_solver_started'])
                self.assertEqual(len(receipt['base_die_mapping']),8)
                self.assertNotEqual(receipt['entity_count'],255)
                self.assertNotEqual(receipt['powered_components'],121)
                if mode=='all_hbf_direct':
                    self.assertFalse(any(c['physical_type']=='GDDR' for c in ir['components']))

    def test_independent_hand_capacity_resistance_boundary(self):
        # Two full-area slabs: A=2e-6; C=0.002/0.004 J/K,
        # R=(.001/2+.002/2)/(4*2e-6)=187.5 K/W.
        ir=slab(); grid=discretize(ir); edges,bounds=network(ir,grid)
        self.assertEqual(len(grid['cells']),2)
        self.assertAlmostEqual(grid['cells'][0]['capacity_j_k'],.002)
        self.assertAlmostEqual(grid['cells'][1]['capacity_j_k'],.004)
        self.assertAlmostEqual(edges[0][2],1/187.5)
        self.assertAlmostEqual(bounds[0][0],2e-6/(.0005/4+1/50))
        self.assertAlmostEqual(bounds[1][0],2e-6/(.001/4+1/100))
        self.assertEqual(bounds[1][1],310)

    def test_base_energy_partition_not_replicated(self):
        ir=slab(); grid=discretize(ir,.001)
        files,receipt=rc_files(ir,grid)
        self.assertAlmostEqual(receipt['emitted_energy_j'],1)
        line=files['events.txt'].splitlines()[1].split()
        self.assertEqual(len(line[12:]),4) # 2 node-energy pairs, not 2 J
        self.assertAlmostEqual(float(line[-1]),.5)
        self.assertEqual(sum(sampled_power(ir,.1)['base'])*.1,1)

    def test_reference_units_anisotropy_and_all_layers(self):
        ir=slab(); grid=discretize(ir,.001)
        files,mapping=reference_files(ir,grid,.02,.1)
        self.assertIn('thermal conductivity 1.9999999999999999e-06',files['package.stk'])
        self.assertIn('source L1',files['package.stk'])
        self.assertLess(files['package.stk'].index('die S1'),files['package.stk'].index('die S0'))
        self.assertAlmostEqual(sum(x['source_weight'] for row in mapping for x in row),1)
        self.assertIn('step );',files['package.stk'])

    def test_id_order_does_not_change_grid(self):
        ir=slab(); other=copy.deepcopy(ir); other['components'].reverse()
        self.assertEqual(discretize(ir),discretize(other))

    def test_no_snapping_and_no_hole(self):
        with self.assertRaisesRegex(ValueError,'no snapping'): discretize(slab(),.0007)
        ir=slab(); ir['components'].pop()
        with self.assertRaisesRegex(ValueError,'unfilled'): discretize(ir)

    def test_stock_single_layer_double_boundary_rejected(self):
        ir=slab(); ir['components']=ir['components'][1:]
        ir['components'][0]['xyz_m'][2]=0; ir['package_size_m'][2]=.002
        with self.assertRaisesRegex(ValueError,'single z layer'):
            reference_files(ir,discretize(ir,.001),.02,.1)

    def test_non_aligned_power_transition_rejected(self):
        ir=slab(); ir['power']['intervals'][0]['end_s']=.47
        with self.assertRaisesRegex(ValueError,'transition'): sampled_power(ir,.1)

    def test_generate_is_static_and_budget_never_removes_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'generated'
            receipt=generate(slab(),p,.001,.02,rc_node_budget=1)
            self.assertEqual(receipt['rc_nodes'],2)
            self.assertEqual(receipt['rc_execution_readiness'],'BLOCKED_NODE_BUDGET')
            self.assertFalse(receipt['research_solver_started'])
            self.assertFalse(list(p.glob('field_*.txt')))


if __name__=='__main__': unittest.main()

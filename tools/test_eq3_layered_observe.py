import io
import unittest
from eq3_layered_observe import frames, sensor_mapping, readings, rc_frames
from eq3_layered_export import discretize
from test_eq3_layered_export import slab


class ObserveTests(unittest.TestCase):
    def test_field_grid_max_is_not_max_of_region_means(self):
        ir=slab(); grid=discretize(ir,.001)
        ir['sensors']=[{'id':'base_mean','reduction':'weighted_mean',
                        'weights':[{'component_id':'base','weight':1}]},
                       {'id':'base_hot','reduction':'max','components':['base']}]
        values=[300,300,310,330]
        rows=readings(sensor_mapping(ir,grid),values,grid)
        self.assertEqual(rows[0][1],320)
        self.assertEqual(rows[1][1],330)
        self.assertEqual(rows[1][2],grid['cells'][3]['id'])

    def test_field_parser_rejects_truncation_and_nan(self):
        self.assertEqual(list(frames(io.StringIO('% comment\n300 301\n\n'),2,1)),[[300,301]])
        for text in ('300\n','300 nan\n'):
            with self.assertRaises(ValueError): list(frames(io.StringIO(text),2,1))

    def test_rc_rejects_duplicate_and_missing_nodes(self):
        grid=discretize(slab()); header='time_s,record_type,location,value_k\n'
        for body in ('0.1,node,n0_0_0,300\n',
                     '0.1,node,n0_0_0,300\n0.1,node,n0_0_0,300\n'):
            with self.assertRaises(ValueError): list(rc_frames(io.StringIO(header+body),grid,.1))

    def test_new_runner_initial_and_sampled_frames(self):
        grid=discretize(slab())
        text='time_s,node_id,temperature_k\n0,n0_0_0,300\n0,n1_0_0,300\n0.1,n0_0_0,301\n0.1,n1_0_0,302\n'
        self.assertEqual(list(rc_frames(io.StringIO(text),grid,.1)),[[301,302]])


if __name__=='__main__': unittest.main()

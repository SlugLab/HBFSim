import io
import gzip
import os
import json
import tempfile
from pathlib import Path
import unittest
from eq3_layered_observe import frames, sensor_mapping, readings, rc_frames, open_field, observe
from eq3_layered_export import discretize,generate
from test_eq3_layered_export import slab


class ObserveTests(unittest.TestCase):
    def test_domain_audit_retains_failure_without_weakening_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ir=slab();ir['temperature_domain_k']=[300,400]
            generated=root/'generated';generate(ir,generated,.001,.02)
            run=root/'raw';run.mkdir()
            for z in range(2):
                (run/f'field_{z}.txt').write_text(('401.000  401.000  \n\n')*50)
            with self.assertRaisesRegex(ValueError,'DOMAIN_FAILED'):
                observe(run,generated,root/'strict')
            result=observe(run,generated,root/'audit',audit_invalid_domain=True)
            self.assertEqual(result['status'],'DOMAIN_FAILED_AUDIT_ONLY')
            self.assertFalse(result['domain_valid'])
            self.assertEqual(result['domain_failure_frame_count'],50)
            self.assertEqual(result['first_domain_failure_s'],.02)
            self.assertEqual(result['frames'],50)

    @unittest.skipUnless(os.environ.get('EQ3_CAMPAIGN_NATIVECODEC'), 'native codec opt-in')
    def test_native_transport_hash_verified_after_full_decode(self):
        from eq3_campaign_nativecodec import Encoder
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'field_0.txt';encoded=Path(str(p)+'.tmk')
            enc=Encoder(encoded,1,1,1);enc.feed(b'300.000  \n');r=enc.finish()
            receipt={'status':'PASS','fields':[{'path':encoded.name,'uncompressed_sha256':r['native_sha256'],'uncompressed_bytes':r['native_bytes']}]}
            (Path(tmp)/'stream_receipt.json').write_text(json.dumps(receipt))
            with open_field(p) as f:self.assertEqual(list(f),['300.000  \n'])
            receipt['fields'][0]['uncompressed_sha256']='0'*64
            (Path(tmp)/'stream_receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                with open_field(p) as f:list(f)

    def test_retained_field_selection_rejects_ambiguity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'field.txt'
            with self.assertRaisesRegex(ValueError,'missing or ambiguous'):
                with open_field(p): pass
            p.write_text('300.000\n')
            with open_field(p) as stream:
                self.assertEqual(list(stream),['300.000\n'])
            compressed=Path(str(p)+'.gz')
            compressed.write_bytes(gzip.compress(b'300.000\n'))
            with self.assertRaisesRegex(ValueError,'missing or ambiguous'):
                with open_field(p): pass
            p.unlink()
            with open_field(p) as stream:
                self.assertEqual(list(stream),['300.000\n'])

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

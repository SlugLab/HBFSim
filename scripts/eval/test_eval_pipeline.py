"""Fail-closed result/renderer tests; all synthetic artifacts stay in temporary dirs."""
import copy
import csv
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from validate_results import COLUMNS,load_validated,read_rows,validate_rows,validate_manifest
from render_figures import metric_points,summarize,plot_lines,FIGURES
import matplotlib.pyplot as plt
ROOT=pathlib.Path(__file__).resolve().parents[2]
MOCK=ROOT/'results/mock/eval.csv'

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.rows=read_rows(MOCK)
    def row(self): return copy.deepcopy(self.rows[0])
    def invalid(self,key,value):
        r=self.row();r[key]=value
        with self.assertRaises(ValueError):validate_rows([r])
    def test_mock_valid(self): self.assertGreater(len(validate_rows(self.rows)),1000)
    def test_strict_mock_rejects_every_row(self):
        r=self.row();r['provenance']='PROJECTED';r['run_id']='test-projection'
        with self.assertRaisesRegex(ValueError,'MOCK forbidden'):validate_rows([r,self.row()],True)
    def test_provenance_closed_enum(self):
        for p in ['measured','MEASURED ','INFERRED','UNKNOWN']: self.invalid('provenance',p)
    def test_numeric_nonfinite(self):
        for v in ['nan','inf','-inf','not-a-number']: self.invalid('value',v)
    def test_wrong_unit(self): self.invalid('unit','ns')
    def test_nonpositive_count_dimension(self): self.invalid('qd','0')
    def test_fraction(self):
        r=self.row();r.update(metric='coverage_fraction',unit='ratio',value='1.2')
        with self.assertRaises(ValueError):validate_rows([r])
    def test_negative_raw_time(self):
        r=self.row();r.update(metric='p50_us',unit='us',value='-1')
        with self.assertRaises(ValueError):validate_rows([r])
    def test_parity_does_not_mix_profiles(self):
        rows=[copy.deepcopy(r) for r in self.rows if r['figure']=='fig-e2-async-semantics' and r['panel']=='parity']
        changed_run=rows[0]['run_id']
        for row in rows:
            if row['run_id']==changed_run:
                row['profile']='other-profile'
        fig,ax=plt.subplots()
        try:
            with self.assertRaisesRegex(ValueError,'heterogeneous'):
                plot_lines(ax,rows,FIGURES['fig-e2-async-semantics'][2])
        finally:plt.close(fig)
    def test_missing_column(self):
        r=self.row();del r['provenance']
        with self.assertRaises(ValueError):validate_rows([r])
    def test_duplicate(self):
        r=self.row()
        with self.assertRaises(ValueError):validate_rows([r,r])
    def test_invalid_overlap(self):
        r=self.row();r.update(delay_us='2',independent_work_us='1',overlap_ratio='1.5')
        with self.assertRaises(ValueError):validate_rows([r])
    def test_missing_sibling_metric(self):
        r=self.row();r.update(metric='delta_sim_us',value='1',unit='us')
        with self.assertRaisesRegex(ValueError,'paired x'):metric_points([r],'delta_sim_us','delta_hw_us')
    def test_context_mixing(self):
        a=self.row();b=self.row();b.update(profile='different',delay_us='2',run_id='different')
        with self.assertRaisesRegex(ValueError,'heterogeneous'):summarize(metric_points([a,b],'critical_delta_us','delay_us'),'delay_us')
    def test_json_input(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'rows.json';p.write_text(json.dumps([self.row()]))
            self.assertEqual(load_validated(p),[self.row()])
    def command(self,input_path,out,*args):
        return subprocess.run([sys.executable,str(ROOT/'scripts/eval/render_figures.py'),'--input',str(input_path),'--out',str(out),*args],text=True,capture_output=True)
    def write_csv(self,path,rows):
        with path.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=COLUMNS);w.writeheader();w.writerows(rows)
    def projected_fixture(self,d):
        # These are explicitly fabricated projected test records, never MEASURED.
        rows=[dict(r,provenance='PROJECTED',run_id='test-'+r['run_id']) for r in self.rows if r['figure']=='fig-e1-hardware-fidelity']
        raw=d/'synthetic-test-only.json';raw.write_text(json.dumps({'kind':'SYNTHETIC TEST FIXTURE ONLY','rows':rows}))
        runs={}
        for r in rows:
            runs[r['run_id']]={k:r[k] for k in ['provenance','git_sha','branch']}
            runs[r['run_id']].update(raw_artifact=raw.name,raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),assumptions=['Fabricated unit-test values. Not real measurements or scientific predictions.'])
        manifest=d/'manifest.json';manifest.write_text(json.dumps({'schema_version':1,'runs':runs}))
        return rows,manifest,raw
    def test_manifest_hash_and_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            d=pathlib.Path(td);rows,m,raw=self.projected_fixture(d)
            validate_manifest(rows,m)
            rows[0]['provenance']='MEASURED'
            with self.assertRaisesRegex(ValueError,'mismatch'):validate_manifest(rows,m)
            rows[0]['provenance']='PROJECTED';raw.write_text('modified')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):validate_manifest(rows,m)
    def test_final_guard_implicit_and_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            for dirname,args in [('final',[]),('paper',['--strict-no-mock'])]:
                out=pathlib.Path(td)/dirname
                r=self.command(MOCK,out,*args)
                self.assertNotEqual(r.returncode,0);self.assertIn('MOCK forbidden',r.stderr)
                self.assertFalse(out.exists())
    def test_automatic_watermark(self):
        with tempfile.TemporaryDirectory() as td:
            out=pathlib.Path(td)/'preview'
            r=self.command(MOCK,out,'--figure','fig-e1-hardware-fidelity')
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertIn('MOCK DATA', (out/'fig-e1-hardware-fidelity.svg').read_text())
    def test_same_renderer_accepts_data_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            d=pathlib.Path(td);rows,manifest,raw=self.projected_fixture(d)
            p=d/'eval.csv';self.write_csv(p,rows);out=d/'final'
            r=self.command(p,out,'--strict-no-mock','--figure','fig-e1-hardware-fidelity')
            self.assertEqual(r.returncode,0,r.stderr)
            text=(out/'fig-e1-hardware-fidelity.svg').read_text()
            self.assertIn('PROJECTED',text);self.assertNotIn('MOCK DATA',text)
            self.assertTrue((out/'fig-e1-hardware-fidelity.pdf').is_file())
    def test_incomplete_grid_leaves_no_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            d=pathlib.Path(td)
            rows=[r for r in self.rows if not (r['figure']=='fig-e3-hbf-feasibility' and r['panel']=='tr1' and r['rho']=='0.0625' and r['parallel_units']=='256')]
            p=d/'eval.csv';self.write_csv(p,rows);out=d/'preview'
            result=self.command(p,out,'--figure','fig-e3-hbf-feasibility')
            self.assertNotEqual(result.returncode,0);self.assertIn('incomplete',result.stderr)
            self.assertFalse(out.exists())

if __name__=='__main__':unittest.main()

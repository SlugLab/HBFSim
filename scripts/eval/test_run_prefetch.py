"""CPU fixture end-to-end controls, never measured routes or GPU compute."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import gguf
import numpy as np

from budget_fast_tier import budget_fast_tier
from inventory_checkpoint import inventory_checkpoint
from routing_metrics import analyze_routes

ROOT=Path(__file__).resolve().parents[2]


class PrefetchCliTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='.prefetch-cli-test-',dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        checkpoint=self.base/'TEST_ONLY.gguf'
        writer=gguf.GGUFWriter(checkpoint,'qwen3moe')
        writer.add_block_count(2);writer.add_expert_count(2);writer.add_expert_used_count(1)
        writer.add_head_count_kv(1);writer.add_key_length(2);writer.add_value_length(2);writer.add_context_length(32)
        writer.add_tensor('token_embd.weight',np.zeros((4,2),dtype=np.float16))
        for layer in range(2):
            for projection in ('up','gate','down'):
                writer.add_tensor(f'blk.{layer}.ffn_{projection}_exps.weight',np.zeros((2,2,2),dtype=np.float16))
            writer.add_tensor(f'blk.{layer}.attn_q.weight',np.zeros((2,2),dtype=np.float16))
        writer.write_header_to_file();writer.write_kv_data_to_file();writer.write_tensors_to_file();writer.close()
        inv=inventory_checkpoint(checkpoint,page_bytes=512)
        inventory_hash=self.write('inventory.json',inv)
        budget=budget_fast_tier(inv,fast_bytes=1024+32+128,active_sequences=1,context_tokens=8,
                                kv_element_bytes=2,workspace_bytes=0,safety_bytes=0)
        self.write('budget.json',budget)
        members={'a':[dict(phase='decode',token_step=s,layer_id=l,topk_expert_ids=[0])
                      for s in range(3) for l in range(2)]}
        result=analyze_routes(members,experts=2,top_k=1,layers=2,seed=3)
        routes_hash=self.write('real.json',dict(series='real',provenance='MOCK',**result['real']))
        manifest_hash=self.write('routing-manifest.json',dict(schema_version=1,concurrency_kind='TRACE_COMPOSED',
                 inventory_sha256=inventory_hash,E=2,k=1,layers=2,provenance='MOCK',input_source_kind='SYNTHETIC_CONTROL',
                 outputs=dict(real=dict(path='real.json',sha256=routes_hash))))
        self.write('compute.json',dict(schema_version=1,inventory_sha256=inventory_hash,
                    routing_manifest_sha256=manifest_hash,routing_series='real',source_kind='SYNTHETIC_CONTROL',
                    timing_semantics='COMPUTE_ONLY_NO_MEDIA_STALL',prompt_tokens=1,
                    nodes=[dict(step=s,layer=l,compute_ns=20000) for s in range(3) for l in range(2)]))
        profile=json.loads((ROOT/'configs/profiles/nominal.json').read_text())
        profile.update(capacity_bytes=16<<30,hbm_cache_bytes=64<<20,queue_depth=2,time_scale=1)
        self.write('profile.json',profile)

    def write(self,name,value):
        payload=json.dumps(value).encode()
        (self.base/name).write_bytes(payload)
        return hashlib.sha256(payload).hexdigest()

    def invoke(self,name='attempt',extra=()):
        command=[sys.executable,str(ROOT/'scripts/eval/run_prefetch.py'),
                 '--binary',str(ROOT/'build-eval-implementation/hbf_mqsim_service'),
                 '--out',str(self.base/name)]
        for flag,file in [('inventory','inventory.json'),('budget','budget.json'),('routes','real.json'),
                          ('routing-manifest','routing-manifest.json'),('compute','compute.json'),('profile','profile.json')]:
            command+=['--'+flag,str(self.base/file)]
        return subprocess.run(command+list(extra),capture_output=True,text=True,timeout=30)

    def test_three_native_policies_preserve_mock_boundary_and_conservation(self):
        process=self.invoke(extra=('--initial-residency','cold'))
        self.assertEqual(process.returncode,0,process.stderr)
        manifest=json.loads((self.base/'attempt/manifest.json').read_text())
        self.assertEqual(manifest['provenance'],'MOCK')
        self.assertFalse(manifest['hardware_validated'])
        self.assertEqual(set(manifest['policies']),{'none','on_demand','one_layer_ahead'})
        for policy in manifest['policies']:
            raw=json.loads((self.base/'attempt'/policy/'raw.json').read_text())
            self.assertEqual(raw['service_source'],'MQSIM_SIMULATED')
            self.assertEqual(raw['cache_hits']+raw['cache_misses'],6)
            self.assertEqual(raw['traffic_bytes'],sum(r['bytes'] for r in raw['requests']))
            self.assertEqual(raw['gross_extra_bytes'],sum(r['extra_bytes'] for r in raw['requests']))
            self.assertEqual(raw['traffic_delta_vs_on_demand_bytes'],raw['gross_extra_bytes']-raw['saved_bytes'])
            self.assertTrue(all(r['ready_ns'] is not None for r in raw['requests']))
        self.assertFalse((self.base/'attempt/status.json').exists())
        second=self.invoke()
        self.assertNotEqual(second.returncode,0)

    def test_input_identity_and_budget_mismatch_reject_before_attempt(self):
        budget=json.loads((self.base/'budget.json').read_text())
        budget['rho']=999
        self.write('budget.json',budget)
        process=self.invoke()
        self.assertNotEqual(process.returncode,0)
        self.assertFalse((self.base/'attempt').exists())

    def test_missing_compute_node_rejects_before_attempt(self):
        compute=json.loads((self.base/'compute.json').read_text())
        compute['nodes'].pop()
        self.write('compute.json',compute)
        process=self.invoke()
        self.assertNotEqual(process.returncode,0)
        self.assertFalse((self.base/'attempt').exists())

    def test_declared_synthetic_routes_cannot_be_relabelled_projected(self):
        routes=json.loads((self.base/'real.json').read_text())
        routes['provenance']='PROJECTED'
        routes_hash=self.write('real.json',routes)
        manifest=json.loads((self.base/'routing-manifest.json').read_text())
        manifest['provenance']='PROJECTED'
        manifest['outputs']['real']['sha256']=routes_hash
        manifest_hash=self.write('routing-manifest.json',manifest)
        compute=json.loads((self.base/'compute.json').read_text())
        compute.update(source_kind='EXTERNAL_UNVERIFIED',routing_manifest_sha256=manifest_hash)
        self.write('compute.json',compute)
        process=self.invoke()
        self.assertNotEqual(process.returncode,0)
        self.assertFalse((self.base/'attempt').exists())


if __name__=='__main__':
    unittest.main()

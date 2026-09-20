#!/usr/bin/env python3
"""A1 cross-source ablation: keep C/G/boundaries and each domain's self response."""
import argparse,csv,json,os,pathlib,resource,time
from thermal_client import ThermalService
p=argparse.ArgumentParser();p.add_argument('--baseline',type=pathlib.Path,required=True);p.add_argument('--model-dir',type=pathlib.Path,required=True);p.add_argument('--binary',type=pathlib.Path,required=True);p.add_argument('--artifact-root',type=pathlib.Path,required=True);p.add_argument('--output',type=pathlib.Path,required=True);p.add_argument('--domain',required=True);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False);begin=time.monotonic()
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[name]='1'
os.environ['CUDA_VISIBLE_DEVICES']='';resource.setrlimit(resource.RLIMIT_AS,(4*1024**3,4*1024**3))
manifest=dict(kind='SOURCE_ISOLATED_DOMAIN_RESPONSE',domain=a.domain,source_point=str(a.baseline.resolve()),model=str(a.model_dir.resolve()),input='same immutable WINDOW_TOTAL, keep this source owner only',limits=dict(wall_s=600,address_gib=4,cpu=1,gpu=0),scope='Preserves same linear network/self cooling; eliminates other source contributions. Not a disconnected physical network or a single global mosaic field; open loop only.')
(a.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
try:
 with ThermalService(a.binary,a.model_dir,a.output/'thermal-process',artifact_root=a.artifact_root) as service,(a.baseline/'energy.csv').open() as source,(a.output/'observations.jsonl').open('w') as output:
  count=0
  for row in csv.DictReader(source):
   if row['kind']!='WINDOW_TOTAL':continue
   energies=json.loads(row['component_energy_j'])
   keep={k:v for k,v in energies.items() if k.split('.')[0]==a.domain}
   result=service.advance(int(row['start_ns']),int(row['end_ns']),keep)
   output.write(json.dumps(result)+'\n');count+=1
 (a.output/'DONE.json').write_text(json.dumps(dict(execution_status='COMPLETED',windows=count,wall_s=time.monotonic()-begin,peak_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,scientific_scope=manifest['scope']),indent=2))
except BaseException as error:
 (a.output/'FAILED.json').write_text(json.dumps(dict(error=repr(error),wall_s=time.monotonic()-begin),indent=2));raise

#!/usr/bin/env python3
"""Deterministic artificial layout fixtures. NO VALUES ARE HARDWARE MEASUREMENTS."""
import csv
import itertools
import json
import math
import pathlib
import subprocess
from validate_results import COLUMNS,UNITS,validate_rows
ROOT=pathlib.Path(__file__).resolve().parents[2]
SHA=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
RHO=[.0625,.125,.25,.5,.75,1.0]
N=[256,1024,1536,3000,4883,15000]
TR=[1,2,4,5,10,20]
rows=[]
sequence=0

def add(eq,fig,panel,series,metrics,rep=1,**dims):
    global sequence
    sequence+=1
    r={k:'' for k in COLUMNS}
    r.update(provenance='MOCK',schema_version='1',eq=eq,figure=fig,panel=panel,
        run_id=f'mock-{sequence:06d}',git_sha=SHA,branch='eval_base',
        hardware='MOCK-layout-substrate',model='MOCK-not-a-checkpoint',workload='synthetic-layout',
        mode='mock',backend='synthetic',operation='read',profile='layout-only',series=series,
        replicate=str(rep),source_file='scripts/eval/generate_mock.py',
        source_function='main fixture construction',source_line_start='1',source_line_end=str(len(pathlib.Path(__file__).read_text().splitlines())))
    r.update({k:str(v) for k,v in dims.items()})
    for metric,value in metrics.items():
        rows.append(dict(r,metric=metric,value=f'{value:.10g}',unit=UNITS[metric]))

f='fig-e1-hardware-fidelity'
for d,rep in itertools.product([0,.5,1,2,5,10,20],range(1,6)):
    noise=(rep-3)*.012
    add('EQ1',f,'gpu','HBF logical range',{'critical_delta_us':d*1.035+noise,'event_delta_us':d*1.06+noise+.03},rep,delay_us=d,warps=1,occupancy='low')
for size,series in itertools.product([4096,8192,16384,32768,65536,131072,262144,1048576],['Physical device fixture','MQSim fixture']):
    add('EQ1',f,'ssd_p50',series,{'p50_us':(12+size/3500)*(1.06 if 'MQSim' in series else 1)},request_bytes=size,qd=1,split='heldout')
for q,series in itertools.product([1,2,4,8,16,32,64,128],['Physical device fixture','MQSim fixture']):
    bw=6.4*q/(q+7)*(1.04 if 'MQSim' in series else 1)
    for panel,metric,value in [('ssd_p99','p99_us',18+q*2.1),('ssd_rate','throughput_gbs',bw),('ssd_iops','iops',bw*1e9/16384)]:
        add('EQ1',f,panel,series,{metric:value},qd=q,request_bytes=16384,split='heldout')
for i in range(1,13):
    x=i*17
    add('EQ1',f,'three_arm','Paired fixture',{'delta_hw_us':x,'delta_sim_us':x*(1+.06*math.sin(i))})
f='fig-e2-async-semantics'
for ratio,series in itertools.product([0,.25,.5,.75,1,1.5,2],['Native','Old issue-stall','New future/TMA','Analytical oracle']):
    residual=max(0,1-ratio)
    if series=='Native': residual=0
    if series=='Old issue-stall': residual=0
    if series=='New future/TMA': residual+=.025
    total=1 if series=='Old issue-stall' else residual
    for panel,metric,v in [('residual','residual_norm',residual),('total','total_stall_norm',total)]:
        add('EQ2',f,panel,series,{metric:v},delay_us=5,overlap_ratio=ratio,independent_work_us=ratio*5,operation='tma_g2s')
for op,d,ratio in itertools.product(['TMA G->S','TMA S->G','cp.async conditional','Ordinary future load'],[1,2,5,10,20],[0,.25,.5,.75,1,1.5,2]):
    oracle=max(0,d*(1-ratio))
    add('EQ2',f,'parity',op,{'oracle_residual_us':oracle,'residual_us':oracle*1.02+.03},delay_us=d,overlap_ratio=ratio,independent_work_us=d*ratio,operation=op)
f='fig-e3-hbf-feasibility'
for tr,n,rho in itertools.product(TR,N,RHO):
    array=n*4096/tr/1000
    service=(array*3072/(array+3072))*.72
    decode=1+(.9*(1-rho))/(1+service/160)
    add('EQ3',f,f'tr{tr}','MoE fixture',{'service_gbs':service,'decode_norm':decode,'throughput_norm':1/decode,'hbf_bytes_per_token':3.6e9*(1-rho),'hit_rate':rho,'miss_rate':1-rho,'utilization':.72},rho=rho,tR_us=tr,parallel_units=n,active_sequences=1,prefetch_policy='on_demand')
# Deliberately hypothetical E/k; no hard-coded Qwen expert count or real routing claim.
config={'num_experts':96,'top_k':6,'description':'MOCK layout fixture; not Qwen checkpoint metadata'}
E,k=config['num_experts'],config['top_k']
f='fig-e4-expert-union'
for b,series in itertools.product([1,2,4,8,16,32,64,128],['Routing trace fixture','Shuffled trace fixture','Uniform null']):
    null=1-(1-k/E)**b
    union=null if series=='Uniform null' else null*(.83 if series=='Routing trace fixture' else .97)
    for panel,metric,v in [('union','union_fraction',union),('entropy','routing_entropy',math.log2(E)*union),('reuse','jaccard',min(.98,.15+union*.65))]:
        add('EQ4',f,panel,series,{metric:v},active_sequences=b,num_experts=E,top_k=k)
f='fig-e5-workload-boundary'
policies=['no_prefetch','on_demand','one_layer_ahead']
for panel,factor in [('moe',.32),('dense_capacity',.9),('dense_compute',.48)]:
    for rho,policy in itertools.product(RHO,policies):
        gain={'no_prefetch':1.12,'on_demand':1,'one_layer_ahead':.62}[policy]
        add('EQ4',f,panel,policy,{'decode_norm':1+factor*(1-rho)*gain},rho=rho,active_sequences=8,prefetch_policy=policy,model='MOCK-'+panel,tR_us=5,parallel_units=1024)
for b,policy in itertools.product([1,2,4,8,16,32,64,128],policies):
    add('EQ4',f,'concurrency',policy,{'min_service_gbs':(60+55*math.log2(b+1))*(.7 if policy=='one_layer_ahead' else 1)},active_sequences=b,rho=.25,prefetch_policy=policy)
f='fig-e6-robustness'
for tr,series in itertools.product(TR,['Lower service assumption','Nominal assumption','Upper service assumption']):
    factor={'Lower service assumption':.04,'Nominal assumption':.025,'Upper service assumption':.015}[series]
    add('APPENDIX',f,'sensitivity',series,{'decode_norm':1+factor*tr},tR_us=tr,rho=.25,parallel_units=1024)
for b in [1,2,4,8,16,32,64,128]:
    add('APPENDIX',f,'coverage','Eligible bytes fixture',{'coverage_fraction':max(.65,.99-.025*math.log2(b))},active_sequences=b)
    for policy in ['on_demand','one_layer_ahead']:
        add('APPENDIX',f,'traffic',policy,{'extra_traffic_ratio':0 if policy=='on_demand' else .06+.02*math.log2(b)},active_sequences=b,prefetch_policy=policy)
validate_rows(rows)
out=ROOT/'results/mock'; out.mkdir(parents=True,exist_ok=True)
with (out/'eval.csv').open('w',newline='') as stream:
    w=csv.DictWriter(stream,fieldnames=COLUMNS); w.writeheader(); w.writerows(rows)
(out/'model-fixture.json').write_text(json.dumps(config,indent=2)+'\n')
print(f'{len(rows)} MOCK metric rows; no measured results created')

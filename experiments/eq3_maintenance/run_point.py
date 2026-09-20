#!/usr/bin/env python3
"""Explicit CPU-only experiment entry. No default-backend fallback or auto sweep."""
import argparse
from dataclasses import asdict
import csv
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
for p in (ROOT,ROOT/'tools',HERE,HERE/'backend/client'):
    sys.path.insert(0,str(p))
from campaign_inputs import configuration,workload,energy_profile
from closed_loop import ClosedLoopCoordinator
from energy_ledger import ActivityEnergyLedger
from thermal_client import ThermalService
from read_rate_policy import EngineeringProfile,ReadRatePolicy
from maintenance_service import MaintenanceMqsimService
from weight_workloads import weight_extent,generate_weight_requests
from eq3_basic_hbm import BasicHbm
from eq3_basic_fabric import BasicFabric


def write_json(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def write_csv(path,rows):
    keys=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list,tuple)) else v
                             for k,v in row.items()})


def run(args):
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    n=8 if args.mode=='all_hbf_direct' else 4
    weight=weight_extent(args.weight_model,stacks=n) if args.weight_model else None
    capacity_extent=weight_extent(args.capacity_model,stacks=n) if weight else None
    pages_per_stack=max(32768,((capacity_extent['global_page_count']+n-1)//n+4095)//4096*4096) if weight else 65536
    if weight and weight['global_page_count']>pages_per_stack*n:raise ValueError('weight model exceeds fixed modeled capacity')
    if args.scan_period_s is not None:
        if not weight or args.scan_period_s<=0:raise ValueError('scan period requires model and positive seconds')
        args.total_hbf_rps=max(1,int(weight['global_page_count']/args.scan_period_s))
    cfg=configuration(args.mode,pages_per_stack=pages_per_stack)
    active_ns=round(args.active_s*1e9);end_ns=active_ns+round(args.recovery_s*1e9)
    window_ns=20000000
    if active_ns%window_ns or end_ns%window_ns:
        raise ValueError('duration must align to thermal20ms')
    requests=workload(args.mode,args.workload,total_hbf_rps=args.total_hbf_rps,active_ns=active_ns)
    weight_input=None
    if weight:
        region='model.layers.9' if args.workload=='W2' else None
        start_page=next(r['first_global_page'] for r in weight['regions'] if r['id'].startswith(region+'.')) if region else 0
        weight_input=generate_weight_requests(args.weight_model,args.total_hbf_rps*active_ns//1000000000,start_page,
                     max(1,1000000000//args.total_hbf_rps),stacks=n,region=region)
        hbf=[]
        for row in weight_input['requests']:
            route='relay' if args.mode=='relay' or args.mode=='dash' and row['local_page']%2 else 'direct'
            hbf.append(dict(row,request_id='weight-'+str(row['request_id']),stack_local_page=row['local_page'],
                            route=route,arrival_ns=row['arrival_ns']//window_ns*window_ns))
        requests=sorted(hbf+[r for r in requests if r['stack'].startswith('hbm')],key=lambda r:(r['arrival_ns'],r['request_id']))
    coefficient=energy_profile()
    # Same GPU load in every arm: demand scenarios never modify an operation coefficient.
    stacks=list(cfg['fabric']['hbf'])+list(cfg['fabric']['hbm'])
    budget=16384*2048 # Allows102400 pages/s/stack, limited further by actual service.
    budgets={s:budget for s in stacks}
    endpoint_caps={f'{s}:gpu-link':budget for s in stacks}
    for stack,item in cfg['fabric']['hbf'].items():
        if item['pair']:
            endpoint_caps[f'{stack}->{item["pair"]}:relay-link']=budget
    policy_profile=EngineeringProfile(profile_id='eq3-pilot-v1',enabled=True,strategy=args.policy,
          window_ns=window_ns,target_bytes_per_s=max(1,args.total_hbf_rps//len(cfg['fabric']['hbf']))*16384,
          target_latency_p95_ns=window_ns,step_bytes=16384,minimum_budget_bytes=16384,
          maximum_budget_bytes=budget,severe_budget_bytes=0)
    maintenance=[]
    if args.maintenance:
        due=round(args.maintenance_due_s*1e9) if args.maintenance_due_s is not None else active_ns//2//window_ns*window_ns
        if due>=end_ns or due<0:raise ValueError('maintenance due outside experiment')
        first_global=weight_input['selected_global_page_range'][0] if weight_input else 0
        for offset in range(64):
            page=first_global+offset
            maintenance.append(dict(request_id=1000000+offset,stack=f'hbf{page%n}',
                  stack_local_page=page//n,due_ns=due,deadline_ns=due+window_ns,
                  initial_age_s=86400-due*1e-9,bytes=16384,reclaim_source_block=True,trigger_reason='RETENTION_AGE_DUE'))
    meminfo=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
    available=int(meminfo['MemAvailable'].split()[0])*1024
    free=shutil.disk_usage(output).free
    if available<32*1024**3 or free<100*1024**3:
        raise RuntimeError('host reserve insufficient; no backend started')
    source=subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()
    meta=dict(task='EQ3-ISOLATED-MAINTENANCE-CAMPAIGN-v1',kind='PILOT_CONDITIONAL_ENGINEERING_USE',
          environment_id='eq3-thermal-cpu-v1',source_head=source,
          source_status=subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True),
          mode=args.mode,workload=args.workload,policy=args.policy,active_ns=active_ns,end_ns=end_ns,
          total_hbf_rps=args.total_hbf_rps,request_count=len(requests),maintenance_count=len(maintenance),
          thermal_model_dir=str(args.model_dir.resolve()),binary=str(args.binary.resolve()),
          thermal_binary=str(args.thermal_binary.resolve()),observed_memory_available_bytes=available,
          observed_disk_available_bytes=free,limits=dict(process_address_gib=12,cpu=1,gpu=0,
          wall_s=600,expected_output_gib=1,host_memory_reserve_gib=32,host_disk_reserve_gib=100),
          resource_reason='First medium-event pilot; source tests and complete-model paired factorRSS<0.4GiB; two owned serial CPU processes plus coordinator.',
          scientific_scope='Finite working region, true16die MQSim topology, original complete2mm thermal model; no product throughput/capacity/energy calibration or token causality')
    for name,obj in [('configuration.json',cfg),('profile.json',cfg['profile']),('stack-map.json',cfg['stack_map']),
                     ('requests-input.json',requests),('maintenance-input.json',maintenance),
                     ('energy-profile.json',coefficient),('policy-profile.json',asdict(policy_profile))]:
        write_json(output/name,obj)
    if weight_input:
        write_json(output/'weight-model-extent.json',weight)
        write_json(output/'weight-workload.json',weight_input)
        meta['weight_model']=args.weight_model
        meta['capacity_model']=args.capacity_model
        meta['scan_period_s']=args.scan_period_s
        meta['requested_weight_bytes_per_s']=weight['tensor_payload_bytes']/args.scan_period_s if args.scan_period_s else None
        meta['effective_page_scan_period_s']=weight['global_page_count']/args.total_hbf_rps
        meta['read_only_weight_workload']=True
        meta['weight_coverage']=weight_input['coverage']
    meta['executable_sha256']={str(p.resolve()):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.binary,args.thermal_binary)}
    meta['input_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.json')}
    write_json(output/'manifest.json',meta)
    resource.setrlimit(resource.RLIMIT_AS,(12*1024**3,12*1024**3))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        os.environ[key]='1'
    os.environ['CUDA_VISIBLE_DEVICES']=''
    normalized=json.loads((args.model_dir/'normalized.json').read_text())
    components=[c['id'] for c in normalized['components']]
    hbm_dies={stack:[c['id'] for c in normalized['components']
                    if c['device_id']==stack and c['role']=='array_die'] for stack in cfg['fabric']['hbm']}
    energy=ActivityEnergyLedger(coefficient,cfg['stack_map'],components,hbm_dies=hbm_dies,gpu_stop_ns=active_ns)
    fabric=BasicFabric(cfg['fabric']);hbm=BasicHbm(cfg['hbm']) if cfg['hbm'] else None
    def resource_probe(start,end):
        state=fabric.resource_state();answer={}
        for stack in stacks:
            rows=[r for r in energy.rows if start<=r['start_ns']<end and
                  r['component'].startswith(stack+'.die') and
                  r['scope'] in ('NAND_MEDIA','HBM_ARRAY_UNIFORM_SPATIAL_ASSUMPTION')]
            elapsed=sum(r['end_ns']-r['start_ns'] for r in rows)
            dies=16 if stack.startswith('hbf') else len(hbm_dies[stack])
            utilization=min(1.0,elapsed/(end-start)/dies)
            own=state['hbf' if stack.startswith('hbf') else 'hbm'][stack]
            busy=any(v is not None for v in own['banks']) or any(v is not None for k,v in own.items() if k!='banks')
            answer[stack]=dict(backend_busy_fraction=utilization,resource_busy=busy,
                               retry_count=None,uecc_count=None)
        return answer
    try:
        with MaintenanceMqsimService(args.binary,output/'profile.json',output/'mqsim',timeout=180,
                 artifact_root=args.artifact_root,stack_map=output/'stack-map.json',native_observations=True) as mq:
            with ThermalService(args.thermal_binary,args.model_dir,output/'thermal-process',
                  artifact_root=args.artifact_root,baseline_budgets=budgets) as thermal:
                engine=ClosedLoopCoordinator(args.mode,mq,hbm,fabric,thermal,energy,ReadRatePolicy(policy_profile),
                         thermal_window_ns=window_ns,experiment_end_ns=end_ns,
                         initial_stack_budget_bytes=budgets,shared_endpoint_caps_bytes=endpoint_caps,
                         resource_probe=resource_probe)
                result=engine.run(requests,maintenance)
                result['thermal_engine_identity']=dict(header=thermal.header,lock=thermal.lock)
        write_json(output/'result.json',result)
        write_csv(output/'requests.csv',result['requests'])
        write_csv(output/'maintenance.csv',result['maintenance'])
        for name,rows in result['timeline'].items():write_csv(output/(name+'.csv'),rows)
        write_csv(output/'energy-activity.csv',energy.rows)
        summary=result['summary']
        summary.update(mode=args.mode,workload=args.workload,policy=args.policy,
              maintenance_enabled=args.maintenance,peak_k=max(max(r['temperature_range_k']) for r in result['timeline']['thermal']),
              energy_activity_j=energy.total_j,per_stack={s:dict(offered=sum(r['stack']==s for r in requests),
              completed=sum(r['stack']==s and r['state']=='COMPLETE' for r in result['requests'])) for s in stacks},
              weight_model=args.weight_model,weight_coverage=weight_input['coverage'] if weight_input else 'NOT_MODEL_SIZED',
              token_per_s='UNAVAILABLE',external_gddr=cfg['external_gddr'],coefficient_evidence=coefficient['evidence'])
        write_json(output/'summary.json',summary)
        receipt=dict(execution_status='COMPLETED',capability_status='CONDITIONAL_ENGINEERING_LOOP',
                     numerical_status='SEE_THERMAL_BALANCE_NOT_P2_FREEZE',wall_s=time.monotonic()-started,
                     max_child_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        write_json(output/'DONE.json',receipt);print(json.dumps(dict(receipt,summary=summary)))
    except BaseException as exc:
        if 'engine' in locals():
            write_json(output/'unfinished-requests.json',list(engine._records.values()))
            write_json(output/'unfinished-maintenance.json',list(engine._maintenance.values()))
        write_json(output/'FAILED.json',dict(execution_status='FAILED',error=repr(exc),wall_s=time.monotonic()-started))
        raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('binary','thermal-binary','model-dir','output','artifact-root'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--mode',choices=('mixed_direct','relay','dash','all_hbf_direct'),default='mixed_direct')
    p.add_argument('--workload',choices=('W1','W2'),default='W1')
    p.add_argument('--policy',choices=('guard_only','thermal_hysteresis_guard','read_rate_feedback_thermal_guard_v1'),default='guard_only')
    p.add_argument('--total-hbf-rps',type=int,default=4000)
    p.add_argument('--active-s',type=float,default=.4);p.add_argument('--recovery-s',type=float,default=.2)
    p.add_argument('--maintenance',action='store_true')
    p.add_argument('--maintenance-due-s',type=float,default=None)
    p.add_argument('--weight-model',default=None)
    p.add_argument('--capacity-model',default='Qwen/Qwen2.5-72B-Instruct')
    p.add_argument('--scan-period-s',type=float,default=None)
    run(p.parse_args())

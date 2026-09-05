#!/usr/bin/env python3
"""Expand the proposed campaign, not an experiment runner. Costs are planning estimates."""
import csv
import itertools
import pathlib
import subprocess
ROOT=pathlib.Path(__file__).resolve().parents[2]
SHA=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
SM='f4dc28b2671c01939d98e4a968e6fb37b2e364d9'
rows=[]

def add(eq,group,gate,repeats,gpu_s,cpu_s,**d):
    cell=f'{group}-{len(rows)+1:05d}'
    resource_class = {
        'gpu_delay':'GPU_EXCLUSIVE', 'async_overlap':'GPU_EXCLUSIVE',
        'async_correctness':'GPU_EXCLUSIVE', 'live_confirmation':'GPU_EXCLUSIVE',
        'mode_cost':'GPU_EXCLUSIVE', 'three_arm':'GPU_STORAGE_EXCLUSIVE',
        'routing_capture':'GPU_SHARED_SAFE', 'feasibility':'CPU_ONLY',
        'workload_boundary':'CPU_ONLY', 'robustness':'CPU_ONLY',
    }.get(group)
    if group == 'flash_fidelity':
        resource_class = 'STORAGE_EXCLUSIVE' if d['backend'] == 'physical' else 'CPU_ONLY'
    if resource_class is None:
        raise ValueError('Resource class must be explicitly assigned: ' + group)
    rows.append(dict(cell_id=cell,eq=eq,group=group,branch='eval_base',git_sha=SHA,
        implementation_status='PLANNED',blocking_gate=gate,repeats=repeats,resource_class=resource_class,
        gpu_seconds_per_repeat_estimate=gpu_s,cpu_core_seconds_per_repeat_estimate=cpu_s,
        estimated_gpu_hours=repeats*gpu_s/3600,estimated_cpu_core_hours=repeats*cpu_s/3600,
        cost_provenance='PROJECTED_PLANNING_ESTIMATE',**dict({'minimum_configuration':'no'},**d)))
for d,w,o,r in itertools.product([0,.5,1,2,5,10,20],[1,2,4,8,16],['low','high'],['native','fast_logical','hbf_logical']):
    add('EQ1','gpu_delay','G1-GPU;G2-known-delay',10,2,2,delay_us=d,warps=w,occupancy=o,profile=r,operation='dependent_ld_global',minimum_configuration='yes')
sizes=[4096,8192,16384,32768,65536,131072,262144,1048576]
qds=[1,2,4,8,16,32,64,128]
for size,q,pattern,arrival,arm in itertools.product(sizes,qds,['sequential_read','uniform_random_read'],['closed_loop_qd','fixed_arrival_trace'],['physical','mqsim']):
    split='calibration' if size in sizes[::2] and q in qds[::2] else 'heldout'
    add('EQ1','flash_fidelity','G3-frozen-device-profile;G4-arrival-replay',10,0,60,
        request_bytes=size,qd=q,workload=pattern,arrival_process=arrival,backend=arm,split=split,hardware='CD8P-identity-to-freeze',minimum_configuration='yes')
for size,q,arm in itertools.product(sizes,[1,4,16,64],['T0','THW','TSIM']):
    add('EQ1','three_arm','G1-GPU;G3-backing-cache-control',10,5,5,request_bytes=size,qd=q,backend=arm,minimum_configuration='yes')
for d,ratio,op,mode in itertools.product([1,2,5,10,20],[0,.25,.5,.75,1,1.5,2],['tma_g2s','tma_s2g','cp_async','ordinary_future_load'],['native','old_issue_stall','new_future_tma']):
    add('EQ2','async_overlap','G5-async-correctness;cp_async-conditional' if op=='cp_async' else 'G5-async-correctness',10,3,3,
        delay_us=d,overlap_ratio=ratio,independent_work_us=d*ratio,operation=op,mode=mode,
        candidate_branch='feature/sm120-exact-stage1',candidate_sha=SM,minimum_configuration='conditional' if op=='cp_async' else 'yes')
for case in ['predicate_false_then_use','cfg_diamond','loop_reissue','two_bulk_groups_wait1','wait_read_source_reuse','barrier_phase_reuse','replace_address_acquire','copy_descriptor_acquire','daemon_loss','queue_full','unsupported_cp_async']:
    add('EQ2','async_correctness','G5-async-correctness',10,2,2,workload=case,candidate_branch='feature/sm120-exact-stage1',candidate_sha=SM,minimum_configuration='yes')
for tr,n,rho in itertools.product([1,2,4,5,10,20],[256,1024,1536,3000,4883,15000],[.0625,.125,.25,.5,.75,1.]):
    add('EQ3','feasibility','G6-concurrent-timing;G7-budget-accounting',5,0,60,
        tR_us=tr,parallel_units=n,parallel_unit_scope='per_cube',page_bytes=4096,rho=rho,
        active_sequences=1,model='Qwen3-30B-A3B',prefetch_policy='on_demand',minimum_configuration='yes',
        provenance_after_gate='PROJECTED')
for model,b in itertools.product(['Qwen3-30B-A3B','dense_capacity_match','dense_active_compute_match'],[1,2,4,8,16,32,64,128]):
    add('EQ4','routing_capture','G1-GPU;G8-model-inventory;G9-trace-capture',5,360,360,
        model=model,active_sequences=b,prompt_count=16,concurrency_kind='trace-composed unless scheduler timestamps present',minimum_configuration='yes' if b in [1,8,32] else 'no')
for model,b,policy,rho,tr,n in itertools.product(['Qwen3-30B-A3B','dense_capacity_match','dense_active_compute_match'],[1,2,4,8,16,32,64,128],['no_prefetch','on_demand','one_layer_ahead'],[.125,.25,.5],[4,20],[256,1536,4883]):
    add('EQ4','workload_boundary','G6-concurrent-timing;G7-budget;G9-trace;G10-prefetch',5,0,60,
        model=model,active_sequences=b,prefetch_policy=policy,rho=rho,tR_us=tr,parallel_units=n,
        minimum_configuration='yes' if b in [1,8,32] and tr==4 and n==1536 else 'no',provenance_after_gate='PROJECTED')
for model,b,policy,rho in itertools.product(['Qwen3-30B-A3B','dense_capacity_match','dense_active_compute_match'],[1,8,32],['on_demand','one_layer_ahead'],[.125,.5]):
    add('EQ4','live_confirmation','G1;G5;G7;G8;G9;G10',5,180,180,model=model,active_sequences=b,prefetch_policy=policy,rho=rho,tR_us=4,parallel_units=1536,minimum_configuration='no',provenance_after_gate='PROJECTED')
for tr,scale in itertools.product([1,2,4,5,10,20],[.8,1.,1.2]):
    add('APPENDIX','robustness','G6;G7;G9;G10',5,0,60,tR_us=tr,service_multiplier=scale,rho=.25,minimum_configuration='yes')
for mode,workload in itertools.product(['reference','fast','hybrid'],['dependent_load','capacity_replay','qwen_decode']):
    add('APPENDIX','mode_cost','G1 for live; matched stream and time_scale=1',5,60,60,mode=mode,workload=workload,time_scale=1,minimum_configuration='yes')
keys=list(dict.fromkeys(k for r in rows for k in r))
with (ROOT/'docs/49-eval-audit/run-matrix.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=keys); w.writeheader(); w.writerows(rows)
for group in dict.fromkeys(r['group'] for r in rows):
    subset=[r for r in rows if r['group']==group]
    print(group,len(subset),'cells',sum(r['repeats'] for r in subset),'runs',round(sum(r['estimated_gpu_hours'] for r in subset),2),'GPU-h',round(sum(r['estimated_cpu_core_hours'] for r in subset),2),'CPU-core-h')

#!/usr/bin/env python3
"""Prepare bounded model-derived causal pilots; never launch a solver."""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from prepare_stage import config as base_config, TOPOLOGIES, STRATEGIES
from causal_workload import build_architecture_trace

HERE=Path(__file__).resolve().parent

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+"\n")

def make_config(point_id,topology,strategy,model_id,active_s,recovery_s,rate=1_536_000_000_000):
    base=base_config(point_id,topology,rate,strategy,active_s,recovery_s)
    service=base['service'];groups={};targets=[]
    for stack in service['fabric']['hbf']:
        channels=['direct','relay'] if topology=='dash' else ['uniform']
        service['channels'][stack]={c:1_536_000_000_000//len(channels) for c in channels}
        groups[stack]={c:{'resource_id':stack+':uniform16-media',
                           'bandwidth_bytes_per_s':1_536_000_000_000} for c in channels}
        for c in channels:
            route=c if topology=='dash' else ('relay' if topology=='relay' else 'direct')
            targets.append({'stack':stack,'channel':c,'route':route})
    if topology=='dash':
        service['dash_routes']={s:{'direct':'direct','relay':'relay'} for s in service['fabric']['hbf']}
    for stack, channels in service['channels'].items():
        if stack not in groups:
            groups[stack]={c:{'resource_id':stack+':ch'+c,
                              'bandwidth_bytes_per_s':rate} for c,rate in channels.items()}
    service['causal_channel_groups']=groups
    trace={'model_id':model_id,'batch_intervals':1,'batch_size':1,'batch_interval_ns':1,
           'prefetch_layers':0,'attention_compute_ns_per_token':1000,
           'mlp_compute_ns_per_token':1000,'output_compute_ns_per_token':1000,
           'embedding_access':'selected_token_rows','max_active_batches':4}
    trace_path=HERE/'stage/traces/tiny_qwen2_cpu_trace_v1/trace.json'
    trace.update(dependency_mode='tiny_cpu_template',
                 tiny_trace_path=str(trace_path.relative_to(HERE.parents[1])),
                 tiny_trace_sha256=json.loads(trace_path.read_text())['trace_sha256'],
                 projection_context_tokens=4096)
    probe=build_architecture_trace(trace)
    read_bytes=sum(t['tensor']['bytes'] for t in probe['batches'][0]['tasks'] if t['type']=='storage')
    n=len(service['fabric']['hbf'])
    trace['batch_interval_ns']=max(1,read_bytes*10**9//(n*rate))
    trace['total_batches']=(int(active_s*1e9)-1)//trace['batch_interval_ns']+1
    return {'schema_version':'eq3-causal-point-v1','point_id':point_id,'topology':topology,
      'strategy':strategy,'active_ns':int(active_s*1e9),'recovery_ns':int(recovery_s*1e9),
      'service':service,'causal_channel_granularity':{'mode':'uniform_stack_group_16ch',
        'physical_channels_per_stack':16,'semantics':'UNIFORM_EQUIVALENT_GROUP_WITH_SHARED_DASH_MEDIA_NOT_ONE_PHYSICAL_CHANNEL'},
      'trace':trace,'executor':{'cache_mode':'disabled','cache_capacity_bytes':0,
        'coalescing_enabled':True,'prefetch_wait_mode':'wait_at_consumption','stripe_unit_bytes':4096,
        'migration_mode':'fixed','retry_count_per_source_read':0,'stripe_targets':targets},
      'target_bytes_per_s_by_stack':{s:rate for s in service['channels']},
      'gpu_compute_w':0,'gpu_external_w':0,'thermal_limits_k':base['thermal_limits_k'],
      'resource_limits':{'address_space_gib':8,'watchdog_s':900},
      'maintenance':{'mode':'disabled'},'evidence':{
        'read_pressure':'MODEL_REGENERATED_PAYLOAD_PER_BATCH_TIMES_EXOGENOUS_BATCH_ARRIVALS',
        'nominal_per_batch_read_bytes':read_bytes,'nominal_per_stack_input_Bps':rate,
        'hbm_policy_target':'NOMINAL_ENDPOINT_PROFILE_ONLY_NO_HBM_REQUEST_GENERATION',
        'coalescing':'ACTUAL_PHYSICAL_DEMAND_MAY_BE_LOWER_THAN_NOMINAL_LOGICAL_DEMAND',
        'compute':'1US_PER_BUNDLE_EXPLICIT_MEMORY_BOUND_SCENARIO_NOT_GPU_CALIBRATION',
        'gpu_power':'UNKNOWN_SELF_HEAT_EXCLUDED_NOT_MEASURED_ZERO',
        'grain':'UNIFORM_CHANNEL_GROUP_EQUIVALENCE_FIXED_TESTED; NONUNIFORM_USES_SEPARATE_16CH_RATE_POINTS',
        'token':'ONLY_FINAL_CAUSAL_COMPUTE_COMPLETION; NOT_REAL_MODEL_THROUGHPUT'}}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--stage',type=Path,required=True)
    ap.add_argument('--kind',choices=['pilot','main'],default='pilot')
    a=ap.parse_args();stage=a.stage.resolve();dest=stage/'causal-v1'
    index=json.loads((stage/'RUN_INDEX.json').read_text());templates={r['topology']:r for r in index['points']}
    points=[]
    specs=[(t,STRATEGIES[2],'Qwen/Qwen2.5-72B-Instruct',8,4) for t in TOPOLOGIES] if a.kind=='pilot' else [
        (t,s,'Qwen/Qwen2.5-72B-Instruct',20,10) for t in TOPOLOGIES for s in STRATEGIES]
    for t,s,m,active,recovery in specs:
        pid=f"causal-{a.kind}-{t}-{m.split(chr(47))[-1]}-{STRATEGIES.index(s)}-01"
        cfg=make_config(pid,t,s,m,active,recovery)
        path=dest/'inputs'/(pid+'.json')
        if path.exists():raise FileExistsError(path)
        save(path,cfg);row=deepcopy(templates[t]);row.update(point_id=pid,kind=a.kind,
            config=str(path),config_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            output=str(dest/'points'/pid));points.append(row)
    save(dest/(a.kind.upper()+'_INDEX_CANDIDATE.json'),{'status':'CANDIDATE_NOT_FROZEN',
      'authorization':'USER_EXPLICIT_FOUR_TOPOLOGY_PLAN','points':points,
      'resources':{'point_wall_s':900,'stage_wall_s':14400,'point_output_gib':1,
        'stage_output_gib':20,'parent_combined_output_gib':100,'host_ram_reserve_gib':32,'host_disk_reserve_gib':100},
      'pending':'Bind actual tiny trace consumer and source locks before execution; pilots reviewed before main.'})

if __name__=='__main__':main()

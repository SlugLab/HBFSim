#!/usr/bin/env python3
"""Generate bounded causal comparisons only after actual pilot receipt review."""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
from analyze_extensions import analyze_point
from prepare_causal_campaign import make_config
from prepare_stage import STRATEGIES, TOPOLOGIES
from freeze_extension import freeze, save, HERE


def specs():
    result=[]
    for topology in TOPOLOGIES:
        for strategy in STRATEGIES:
            result.append((topology,strategy,'72B',1536,'baseline'))
    for topology in ('mixed_direct','relay','dash'):
        result += [(topology,STRATEGIES[2],'7B',384,'baseline'),
                   (topology,STRATEGIES[2],'72B',1536,'prefetch1'),
                   (topology,STRATEGIES[2],'72B',1536,'prefetch1_issue_stall'),
                   (topology,STRATEGIES[2],'72B',1536,'no_coalescing'),
                   (topology,STRATEGIES[2],'72B',1536,'hot_static'),
                   (topology,STRATEGIES[2],'72B',1536,'hot_adaptive'),
                   (topology,STRATEGIES[2],'7B',384,'cache16GiB')]
    result += [('mixed_direct',STRATEGIES[2],'7B',384,'cache4GiB'),
               ('mixed_direct',STRATEGIES[2],'72B',1536,'retry1')]
    return result


def make_main(topology,strategy,model,rate,arm):
    pid=f'causal-main-{topology}-{model}-{rate}-{STRATEGIES.index(strategy)}-{arm}-01'
    c=make_config(pid,topology,strategy,f'Qwen/Qwen2.5-{model}-Instruct',20,10,rate*10**9)
    c['resource_limits']['watchdog_s']=1800
    c['comparison_arm']=arm
    if arm.startswith('prefetch1'):
        c['trace'].update(prefetch_layers=1,prefetch_mode='layer_lookahead')
        if arm.endswith('issue_stall'):c['executor']['prefetch_wait_mode']='stall_at_issue'
    if arm=='no_coalescing':c['executor']['coalescing_enabled']=False
    if arm in ('hot_static','hot_adaptive'):
        original=deepcopy(c['executor']['stripe_targets'])
        c['executor']['stripe_targets']=[{**r,'stack':'hbf0'} for r in original]
        c['evidence']['placement_input']='ALL_INITIAL_SOURCE_WEIGHT_STRIPES_ON_HBF0; SAME_LOGICAL_MODEL_ARRIVALS_AS_BASELINE'
        if arm=='hot_adaptive':
            c['executor'].update(migration_mode='basic',migration_access_threshold=2,
                migration_capacity_bytes=160*1024**3,fast_stripe_targets=original)
            c['evidence']['migration_capacity']='FINITE_160GIB_DESTINATION_RESERVATION_ACROSS_EXISTING_HBF_STACKS; PROGRAM_ERASE_COST_COUNTED'
    if arm.startswith('cache'):
        capacity=int(arm[len('cache'):-len('GiB')])*1024**3
        targets=c['executor']['stripe_targets']
        for stack in c['service']['fabric']['hbm']:
            capacity_Bps=sum(c['service']['channels'][stack].values())
            c['service']['channels'][stack]={'uniform':capacity_Bps}
            c['service']['causal_channel_groups'][stack]={'uniform':{
                'resource_id':stack+':uniform16-media','bandwidth_bytes_per_s':capacity_Bps}}
        fast=[{'stack':f'hbm{i%4}','channel':'uniform','route':'direct'} for i,_ in enumerate(targets)]
        c['executor'].update(cache_mode='external_hbm',cache_capacity_bytes=capacity,
                             fast_stripe_targets=fast)
        c['evidence']['cache_capacity']='TOTAL_4_OR_16GIB_SCENARIO_ALLOCATION_WITHIN_FOUR_36GB_HBM_LABELS; ACTUAL_FILL_LRU_HIT_SERVICE'
    if arm=='retry1':c['executor']['retry_count_per_source_read']=1
    c['evidence']['coalescing_scope']='IDENTICAL_WHOLE_TENSOR_READS_SHARE_ALL_THEIR_PAGES; ARBITRARY_PARTIAL_PAGE_OVERLAP_UNSUPPORTED'
    return c


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--pilot-index',type=Path,required=True);a=p.parse_args()
    stage=a.stage.resolve();index=json.loads(a.pilot_index.read_text())
    if len(index['points'])!=4:raise ValueError('four actual topology pilots required')
    costs=[]
    for row in index['points']:
        point=Path(row['output']);analysis=analyze_point(point)
        if analysis['analysis_status']!='VALIDATED_COMPLETE_RECEIPTS':raise ValueError('pilot not validated')
        done=json.loads((point/'DONE.json').read_text());costs.append(done['wall_s'])
    # Six-hour extension is finite. Stop at review rather than repeatedly launch
    # points whose pilot already predicts missing the whole-stage budget.
    predicted=sum(costs)/len(costs)*2.5*len(specs())
    if predicted>21600:raise RuntimeError('measured pilot projects >6h causal extension; resource/identifiability review required')
    dest=stage/'causal-main-v1'
    if dest.exists():raise FileExistsError(dest)
    dest.mkdir();paths=[]
    for spec in specs():
        c=make_main(*spec);path=dest/'inputs'/(c['point_id']+'.json')
        path.parent.mkdir(parents=True,exist_ok=True);save(path,c);paths.append(path)
    frozen=freeze(stage,dest,paths,HERE/'run_causal_point.py',point_wall_s=1800,
                  dependency=a.pilot_index.parent/'DONE.json')
    frozen['pilot_review']={'actual_wall_s':costs,'linear_30s_projection_s':predicted,
                            'interpretation':'UNCERTAIN_LINEAR_PROJECTION; MAIN_WATCHDOGS_STILL_ENFORCED'}
    frozen['design']={'core_72b_policy_topology_points':12,'total_points':len(specs()),
        'secondary_7b_rate_per_stack_TBps':.384,'primary_72b_rate_per_stack_TBps':1.536,
        'cross_model_comparison':'DIFFERENT_DEMAND_NOT_ISOLATED_MODEL_SIZE_EFFECT',
        'paired_arms':'same model/rate/topology/policy/age and initial state within each ablation',
        'unavailable':'QUALIFIED_THERMAL_FAST_PATH; REAL_GPU_TIMING; ARBITRARY_PARTIAL_PAGE_COALESCING'}
    save(dest/'MAIN_INDEX.json',frozen)

if __name__=='__main__':main()

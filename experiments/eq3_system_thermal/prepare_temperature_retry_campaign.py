"""Freeze transparent exploratory retry scenarios without changing device inputs."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from freeze_extension import freeze, save, HERE
from prepare_causal_campaign import make_config
from prepare_stage import TOPOLOGIES


def make_point(topology, probability, arm, active_s, recovery_s, phase):
    name=f'tempretry-{phase}-{topology}-p{probability:g}-{arm}-01'
    strategy='thermal_hysteresis_guard' if arm=='hysteresis' else 'guard_only'
    config=make_config(name,topology,strategy,'Qwen/Qwen2.5-72B-Instruct',active_s,recovery_s)
    profile=json.loads((HERE/'ecc_temperature_proxy/candidate_family_v1.json').read_text())
    profile['recoverable_read_probability_at_85c']=probability
    config['hbf_read_cost_proxy']={
        'mode':'conditional_temperature_retry_v1','profile':profile,
        'initial_by_stack':{s:{'temperature_k':300.0} for s in config['service']['fabric']['hbf']}}
    config['comparison_arm']=arm
    config['resource_limits']['watchdog_s']=2400 if phase=='main' else 900
    config['evidence']['purpose']='EXPLORATORY_CONTROLLER_FAVORABLE_SCENARIO_SEARCH_ALL_CANDIDATES_RETAINED_NOT_HBF_RBER'
    if arm=='feedback':
        config['temperature_retry_feedback']={
            'mode':'conditional_temperature_retry_feedback_v1',
            'observation_window_ns':20_000_000,'evaluation_interval_ns':200_000_000,
            'rollback_interval_ns':1_000_000_000,'step_fraction':.05,
            'near_light_temperature_k':348.15,'minimum_budget_fraction':.1}
    return config


def prepare(stage,destination,phase,probability=None,selection=None,dependency=None):
    if phase=='main' and (probability not in (.01,.1,.3) or not selection):
        raise ValueError('main needs a bounded selected non-null probability and recorded pilot review')
    if destination.exists():raise FileExistsError(destination)
    destination.mkdir(parents=True);(destination/'inputs').mkdir()
    specs=([('mixed_direct',p,arm) for p in (0,.01,.1,.3) for arm in ('guard','feedback')]
           if phase=='pilot' else [(t,probability,arm) for t in TOPOLOGIES for arm in ('guard','hysteresis','feedback')])
    paths=[]
    for topology,p,arm in specs:
        config=make_point(topology,p,arm,4 if phase=='pilot' else 20,2 if phase=='pilot' else 10,phase)
        path=destination/'inputs'/(config['point_id']+'.json');save(path,config);paths.append(path)
    index=freeze(stage,destination,paths,HERE/'run_causal_point.py',
                 point_wall_s=900 if phase=='pilot' else 2400,dependency=dependency)
    index['resources'].update(stage_wall_s=7200 if phase=='pilot' else 21600,
        point_output_gib=4,sensitivity_output_gib=20 if phase=='pilot' else 60,parent_combined_output_gib=200)
    index['authorization']='USER_EXPLICIT_CONTROLLER_FAVORABLE_CURVE_EXPLORATION_WITH_DOCUMENT_PRIORITY'
    index['selection_review']=selection
    index['selection_contract']={
        'candidates_p85':[0,.01,.1,.3],
        'primary':'positive relative cumulative useful delivery gain over matched guard at common horizon',
        'secondary':'report backlog, queue p95, stability, peaktemperature and all failed/negativearms; no guarantee ofimprovement',
        'main':'new20+10s fourtopology matched runs;selected scenario remains exploratory,not blind hardware validation',
        'no_positive_candidate':'report NO_BENEFIT_IN_BOUNDED_FAMILY; do not silently extend curve or alter hardware'}
    save(destination/'EXECUTION_INDEX.json',index)
    save(destination/'PREFLIGHT.json',dict(index=index,environment_id='eq3-thermal-cpu-v1',
        source_priority='TARGET_DOCUMENTS_THEN_RELATED_DEVICE_PROXY_THEN_EXPLICIT_SCENARIO',
        physical_invariants='HBM4 frozen profile;HBF1.536TBps;40+10pJ/B;same full thermal networks;300..400Kdomain;original guards',
        source_gaps='No numeric target HBF temperature retry curve; p85 andtwoextraattempts are scenario assumptions; Ea retention-to-instantaneous transfer itself is assumed',
        scope='CONDITIONAL_RECOVERABLE_COST_NOT_RBER_UBER_OR_DATA_LOSS_CERTIFICATION',
        safety='serialCPU1/BLAS1/GPU0;32GiBRAM100GiBdiskhostreserves;retainraw;finitepoint/stagebudgets',
        duration_s=[4,2] if phase=='pilot' else [20,10]))
    return index


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',type=Path,required=True);p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--phase',choices=['pilot','main'],required=True);p.add_argument('--probability',type=float)
    p.add_argument('--selection');p.add_argument('--dependency',type=Path)
    a=p.parse_args();prepare(a.stage.resolve(),a.destination.resolve(),a.phase,a.probability,a.selection,a.dependency)

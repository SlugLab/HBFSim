#!/usr/bin/env python3
"""Prepare paired bounded software-integration thermal pilots; never launch."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from prepare_causal_campaign import make_config
from prepare_stage import TOPOLOGIES
from freeze_extension import freeze,save

HERE=Path(__file__).resolve().parent

def prepare(stage,destination):
    if destination.exists():raise FileExistsError(destination)
    destination.mkdir(parents=True)
    inputs=destination/'inputs';inputs.mkdir()
    profile=json.loads((HERE/'ecc_proxy/profile_v1.json').read_text())
    configs=[]
    for topology in TOPOLOGIES:
        for strength in (0,.1):
            name=f'ecc-pilot-{topology}-s{strength:g}-01'
            config=make_config(name,topology,'guard_only','Qwen/Qwen2.5-72B-Instruct',1,1)
            p=deepcopy(profile);p['transfer_strength']=strength
            config['hbf_read_cost_proxy']={'mode':'conditional_nand_history_v1','profile':p,
                'initial_by_stack':{s:{'equivalent_age_days_30c':90,'pe_cycles':1000,
                    'temperature_k':300} for s in config['service']['fabric']['hbf']}}
            config['evidence']['purpose']='PAIRED_PROXY_SERVICE_ENERGY_DAG_INTEGRATION_NOT_CONTROLLER_BENEFIT'
            path=inputs/(name+'.json');save(path,config);configs.append(path)
    index=freeze(stage,destination,configs,HERE/'run_causal_point.py',point_wall_s=600,
                 dependency=stage/'maintenance-v1/DONE.json')
    index['authorization']='USER_FOUR_TOPOLOGY_PLAN_PLUS_HBF_NAND_OCP_SANDISK_CONDITIONAL_PROXY_CLARIFICATION'
    index['resources'].update(stage_wall_s=3600,sensitivity_output_gib=8)
    save(destination/'PILOT_INDEX.json',index)
    preflight={
        'status':'FROZEN_WAITING_SERIAL_CPU_RELEASE','question':'Does optional HBF retry cost consume media/decoder resources and energy while preserving unique useful delivery and four-topology routing?',
        'classification':'CONDITIONAL_SIMULATED_ENGINEERING_PILOT_NOT_HBF_CALIBRATION',
        'environment_id':'eq3-thermal-cpu-v1','points':8,'repetitions':1,
        'duration':'1s input +1s drain; backlog may remain','nominal_demand_TBps_per_HBF':1.536,
        'model':'Qwen2.5-72B regenerated shapes using recorded tiny same-architecture dependency structure',
        'initial_age':'90 equivalent days at30C uniformly perHBF; NOT90 wall days at85C',
        'initial_PE':1000,'initial_temperature_K':300,
        'comparison':'same input, transfer factor0 versus0.1; bothguard_only, no static retries/maintenance',
        'expected_effect':'roughly1.9 media effort atinitial age inweak proxy; actual goodput limitedby allresources; lower useful work maylower ornotchange temperature',
        'confounders':['decoder throughput assumption','uniform stack age conservatively driven hottestarraydie','initialwearfixed','shortagecurve assumed','DAGcoalescingchangesphysicaldemand'],
        'minimum_validation':['resource service slows eligible HBF reads','physicalretryenergy present onlyenabled','usefuluniquecompletion','causal token status','no HBM NANDcost','nonnegative conserved energy','no future temperatures used'],
        'stop':['source/model/input mismatch','400K domain failure retains evidence','600s point or3600s stage','8GiB addressspace','2GiB pointoutput','host32GiB RAM/100GiB diskreserve'],
        'output':'immutable perwindowraw, source manifest, costdecisionreceipt, DONE/FAILED, pairedanalysis andtimepanels',
        'interpretation':'No controller benefit or realHBFerror prediction. Refresh→ECC coupling stillunsupported untilmatchingdataextentconsumer; do notreset whole stack.',
        'next_gate':'strict pilot audit and cost review before new policy matrix; existing base retained as noECCbaseline',
    }
    save(destination/'PREFLIGHT.json',preflight)
    (destination/'PREFLIGHT.md').write_text('# ECC paired integration pilots\n\n'+json.dumps(preflight,indent=2,ensure_ascii=False)+'\n')

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--stage',type=Path,required=True);ap.add_argument('--destination',type=Path,required=True)
    a=ap.parse_args();prepare(a.stage.resolve(),a.destination.resolve())

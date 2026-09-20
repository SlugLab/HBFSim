"""Generate frozen mainline engineering cases, no thermal solve or launch.

Reuse audit: P1 generate() supports stable stack/base/die names and independent
thermal/data graphs. Device/profile defaults are explicitly overridden together.
The old GDDR board-proxy node is deliberately not generated for package-only
scope. No research-layered geometry or physical accuracy is inherited.
"""
import argparse,hashlib,json
from pathlib import Path
from eq3_thermal_config import generate,load

def make_case(code,scenario,policy):
    folder=code/'configs/eq3_thermal'
    topology=load(folder/'topologies/mixed_direct_8.json')
    topology.update(hbm_count=4,hbf_count=4,hbm_profile='micron_hbm4_36gb_12hi',hbf_profile='hbf_assumed_16hi',external_fast_memory_profile=None)
    model,_,graph=generate(topology,load(folder/'devices.json'),load(folder/'thermal_fixture.json'),load(folder/'power_fixture.json'))
    array={'Safe':.02,'Near':.5,'Stress':3.0}[scenario]
    stacks=[{'id':s['stack_id'],'physical_kind':s['physical_kind'],'die_count':s['die_count']} for s in graph['stacks']]
    config={'evidence':'ENGINEERING_FIXTURE','topology':'mixed_direct','policy':policy,'thermal_model_text':model,
      'stacks':stacks,'thermal_step_ns':10000000,'sample_ns':20000000,'page_bytes':4096,'initial_age_ns':0,
      'hbf_maintenance_period_ns':2000000000,'hbm_refresh_period_ns':1000000000,
      'duration_ns':{'read':20000000,'program':40000000,'erase':60000000,'write':20000000,'dram_refresh':5000000},
      'power_w':{'read_array':array,'program_array':2*array,'erase_array':2.5*array,'write_array':1.5*array,'refresh_array':.25*array,
        'base':.02,'relay_base':.02,'gpu_phy':.01,'gddr':1.,'gpu_external':2.},
      'control':{s['id']:{'light_k':299.15,'severe_k':300.15,'shutdown_k':302.15,'hysteresis_k':.25,
         'action_delay_ns':20000000,'min_dwell_ns':100000000,'light_gap_ns':80000000} for s in stacks}}
    requests=[]
    for i in range(125):
        for stack in stacks:
            requests.append({'id':f'{stack["id"]}:{i}','stack':stack['id'],'die':i%stack['die_count'],'route':'direct','op':'read',
              'arrival_ns':i*40000000,'logical_bytes':4096,'physical_bytes':4096,'link_bytes':4096,'fail_fraction':0})
    return {'scenario_label':scenario,'evidence':'ENGINEERING_FIXTURE','end_ns':10000000000,'config':config,'requests':requests,
      'parameter_sources':{'C_G':'existing P1 thermal_fixture.json UNCALIBRATED_TEST_FIXTURE',
        'device_counts':'4HBM4 12hi+4HBF assumed16hi; not research geometry validation',
        'service_power_control_periods':'explicit engineering choices, not vendor specifications; 2s is NOT a conversion of OCP 24h',
        'temperature':'SIMULATED; engineering thresholds not 85C retention condition'},
      'expected_invariants':['unique actual completions','all submitted foreground accounted including queued/inflight',
        'no overlapping resource bookings','only successful maintenance resets age','program/erase only actual operations',
        'package energy residual <=0.001','mapping error <=1e-8J','same offered workload for policies'],
      'predictions':{'single_isolated_read_adiabatic_delta_k':array*.02/.1,
        'foreground_stack_utilization_before_maintenance':.5,'HBF_maintenance_stack_utilization_at_period':16*.06/2,
        'HBM_maintenance_stack_utilization_at_period':12*.005/1,
        'interpretation':'HBF total nominal demand .98 permits bursts/backlog; control can reduce service, not external GPU heat; no required benefit or forced state coverage'},
      'claim_limits':['scenario labels are input candidates, achieved temperature states determined after run',
        'no Ea/ECC/endurance calibration','not P5 or physical conditional model','no token metric','all 112 die plus 8 base nodes retained']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);code=Path(__file__).resolve().parents[1]
    receipts=[]
    for scenario in ('Safe','Near','Stress'):
        for policy in ('none','hysteresis'):
            case=make_case(code,scenario,policy);path=a.output/f'{scenario}-{policy}.json'
            path.write_text(json.dumps(case,indent=2)+'\n')
            receipts.append({'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'requests':len(case['requests']),
              'nodes':len(case['config']['thermal_model_text'].splitlines())-2-sum(x.startswith('edge ') for x in case['config']['thermal_model_text'].splitlines())})
    (a.output/'generation_receipt.json').write_text(json.dumps({'kind':'STATIC_INPUT_ONLY','cases':receipts},indent=2)+'\n')
if __name__=='__main__':main()

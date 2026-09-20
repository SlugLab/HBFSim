"""Generate the approved nine-point sustained-load CPU engineering matrix.

This reuses the frozen Near engineering fixture. It changes only request arrival
rate, observation horizon, and the explicitly selected controller policy.
Generation performs no simulation.
"""
import argparse,hashlib,json
from pathlib import Path
from eq3_cpu_fixture import make_case

RATES=(5,10,25)
POLICIES=('none','hysteresis','hysteresis_escalation_priority_v2')
ARRIVAL_S=20
END_S=30

def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def make_load_case(code,rate,policy):
    if rate not in RATES or policy not in POLICIES:raise ValueError('unapproved matrix coordinate')
    case=make_case(code,'Near',policy);stacks=case['config']['stacks'];interval=1_000_000_000//rate
    requests=[]
    for i in range(rate*ARRIVAL_S):
        for stack in stacks:
            requests.append({'id':f'{stack["id"]}:{i}','stack':stack['id'],'die':i%stack['die_count'],
              'route':'direct','op':'read','arrival_ns':i*interval,'logical_bytes':4096,
              'physical_bytes':4096,'link_bytes':4096,'fail_fraction':0})
    case.update(matrix_id='EQ3-D4-SUSTAINED-LOAD-v2',scenario_label=f'rate{rate:02d}',rate_per_stack_rps=rate,
      arrival_window_ns=ARRIVAL_S*1_000_000_000,recovery_window_ns=(END_S-ARRIVAL_S)*1_000_000_000,
      end_ns=END_S*1_000_000_000,requests=requests)
    identity_config=dict(case['config']);identity_config.pop('policy')
    case['workload_identity_sha256']=canonical_hash({'config_without_policy':identity_config,'requests':requests,
      'arrival_window_ns':case['arrival_window_ns'],'end_ns':case['end_ns']})
    rho=rate*case['config']['duration_ns']['read']*1e-9
    case['predictions']['foreground_stack_utilization_before_maintenance']=rho
    case['predictions']['interpretation']='offered load varies only by the approved per-stack rate; backlog and maintenance interference are valid outcomes'
    case['screening']={'foreground_base_utilization_per_stack':rho,'light_capacity_rps':1e9/case['config']['control'][stacks[0]['id']]['light_gap_ns'],
      'hbf_maintenance_base_utilization':16*(case['config']['duration_ns']['read']+case['config']['duration_ns']['program'])*1e-9/2,
      'hbm_maintenance_base_utilization':12*case['config']['duration_ns']['dram_refresh']*1e-9/1,
      'prediction':'25rps exceeds the 12.5rps Light admission ceiling before maintenance; backlog is an allowed negative result'}
    case['claim_limits'].extend(['fixed 20s arrival plus 10s recovery window; not steady state',
      'rate labels are offered load, not promised thermal states','one deterministic run per arm; no confidence interval'])
    return case

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);code=Path(__file__).resolve().parents[1];receipts=[]
    for rate in RATES:
        identities=set()
        for policy in POLICIES:
            case=make_load_case(code,rate,policy);identities.add(case['workload_identity_sha256'])
            path=a.output/f'rate{rate:02d}-{policy}.json';path.write_text(json.dumps(case,indent=2)+'\n')
            receipts.append({'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'rate_per_stack_rps':rate,'policy':policy,'requests':len(case['requests']),
              'workload_identity_sha256':case['workload_identity_sha256']})
        assert len(identities)==1
    (a.output/'generation_receipt.json').write_text(json.dumps({'kind':'STATIC_INPUT_ONLY','matrix_id':'EQ3-D4-SUSTAINED-LOAD-v2',
      'cases':receipts,'rates_per_stack_rps':RATES,'policies':POLICIES,'arrival_s':ARRIVAL_S,'recovery_s':END_S-ARRIVAL_S,
      'interpretation':'ENGINEERING_FIXTURE; no physical/product/P5 claim'},indent=2)+'\n')
if __name__=='__main__':main()

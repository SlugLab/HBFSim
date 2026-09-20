"""Aggregate exactly nine reviewed arms without manufacturing a winner."""
import argparse,json
from pathlib import Path
from eq3_cpu_load_matrix import POLICIES,RATES

def compare(rows):
    keyed={(x['rate_per_stack_rps'],x['policy']):x for x in rows}
    if set(keyed)!={(r,p) for r in RATES for p in POLICIES}:raise ValueError('exact approved nine-point matrix required')
    output={'matrix_id':'EQ3-D4-SUSTAINED-LOAD-v2','evidence':'ENGINEERING_FIXTURE','rates':{},
      'interpretation':'descriptive deterministic comparison; negative results retained; no winner or product claim'}
    for rate in RATES:
        arms={p:keyed[(rate,p)] for p in POLICIES};assert len({x['workload_identity_sha256'] for x in arms.values()})==1
        baseline=arms['none'];table={}
        for policy,x in arms.items():
            p95=x['latency_completed_s']['p95'];base_p95=baseline['latency_completed_s']['p95']
            table[policy]={'foreground_complete':x['foreground_complete'],'foreground_unfinished':x['foreground_unfinished'],
              'completed_logical_bytes':x['completed_logical_bytes'],'max_temperature_k':x['max_temperature_k'],
              'latency_completed_p95_s':p95,'maintenance_queued':x['maintenance']['queued'],
              'maintenance_overdue_cohorts':x['maintenance']['overdue_cohorts'],'max_data_age_s':x['max_data_age_s'],
              'package_dynamic_energy_j':x['package_dynamic_energy_j'],'external_energy_j':x['external_energy_j'],
              'delta_vs_none':{'foreground_complete':x['foreground_complete']-baseline['foreground_complete'],
                'foreground_unfinished':x['foreground_unfinished']-baseline['foreground_unfinished'],
                'max_temperature_k':x['max_temperature_k']-baseline['max_temperature_k'],
                'latency_completed_p95_s':None if p95 is None or base_p95 is None else p95-base_p95,
                'maintenance_queued':x['maintenance']['queued']-baseline['maintenance']['queued']},
              'negative_flags':{'lower_temperature_with_more_backlog':x['max_temperature_k']<baseline['max_temperature_k'] and x['foreground_unfinished']>baseline['foreground_unfinished'],
                'no_temperature_improvement':policy!='none' and x['max_temperature_k']>=baseline['max_temperature_k'],
                'maintenance_backlog_increase':x['maintenance']['queued']>baseline['maintenance']['queued']}}
        output['rates'][str(rate)]={'workload_identity_sha256':baseline['workload_identity_sha256'],'arms':table}
    return output

def main():
    p=argparse.ArgumentParser();p.add_argument('--reviews',type=Path,nargs='+',required=True);a=p.parse_args()
    print(json.dumps(compare([json.loads(x.read_text()) for x in a.reviews]),indent=2))
if __name__=='__main__':main()

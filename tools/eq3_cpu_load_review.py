"""Independent detailed review for one sustained-load CpuService result."""
import argparse,collections,hashlib,json,math
from pathlib import Path
from eq3_cpu_review import review as base_review

def percentile(values,q):
    if not values:return None
    x=sorted(values);position=(len(x)-1)*q;lo=math.floor(position);hi=math.ceil(position)
    return x[lo] if lo==hi else x[lo]*(hi-position)+x[hi]*(position-lo)

def queue_trace(jobs,end_ns,step_ns=1_000_000_000):
    rows=[]
    for t in range(0,end_ns+1,step_ns):
        row={'time_ns':t}
        for maintenance,label in ((False,'foreground'),(True,'maintenance')):
            selected=[j for j in jobs if j['maintenance']==maintenance and j['arrival_ns']<=t]
            row[label+'_queued']=sum('start_ns' not in j or j['start_ns']>t for j in selected)
            row[label+'_inflight']=sum(j.get('start_ns',t+1)<=t<j.get('end_ns',0) for j in selected)
        rows.append(row)
    return rows

def sampled_threshold_exposure(case,result):
    end=result['now'];by_stack=collections.defaultdict(list)
    for row in result['samples']:by_stack[row['stack']].append(row)
    out={}
    for stack,rows in by_stack.items():
        rows.sort(key=lambda x:x['time_ns']);limits=case['config']['control'][stack];summary={}
        for state,key in (('Light','light_k'),('Severe','severe_k'),('Shutdown','shutdown_k')):
            count=0;duration=0
            for i,row in enumerate(rows):
                if row['temperature_k']>=limits[key]:
                    count+=1;duration+=max(0,(rows[i+1]['time_ns'] if i+1<len(rows) else end)-row['time_ns'])
            summary[state]={'sample_count_at_or_above':count,'sampled_interval_s':duration*1e-9,'threshold_k':limits[key]}
        out[stack]=summary
    return out

def detailed_review(case,result):
    summary=base_review(case,result);jobs=result['done']+result['active']+result['queue']
    foreground=[j for j in jobs if not j['maintenance']];done=[j for j in result['done'] if not j['maintenance']]
    failed=[j for j in done if j.get('status')=='FAILED'];complete=[j for j in done if j.get('status')=='COMPLETE']
    latencies=[(j['end_ns']-j['arrival_ns'])*1e-9 for j in complete]
    backend_wait=[(j['start_ns']-j['arrival_ns'])*1e-9 for j in complete]
    unfinished=[j for j in foreground if j not in done]
    trace=queue_trace(jobs,result['now']);periods={'HBF':case['config']['hbf_maintenance_period_ns'],'HBM4':case['config']['hbm_refresh_period_ns']}
    overdue=[]
    kinds={s['id']:s['physical_kind'] for s in case['config']['stacks']}
    for key,c in result['cohorts'].items():
        period=periods[kinds[c['stack']]];overdue.append(max(0,c['age_ns']-period))
    maintenance_done=[j for j in result['done'] if j['maintenance']]
    summary.update(matrix_id=case['matrix_id'],rate_per_stack_rps=case['rate_per_stack_rps'],
      workload_identity_sha256=case['workload_identity_sha256'],offered_foreground=len(foreground),
      foreground_complete=len(complete),foreground_failed=len(failed),foreground_unfinished=len(unfinished),
      offered_logical_bytes=sum(j['logical_bytes'] for j in foreground),completed_logical_bytes=sum(j['logical_bytes'] for j in complete),
      latency_completed_s={f'p{int(q*100):02d}':percentile(latencies,q) for q in (.5,.95,.99)}|{'max':max(latencies) if latencies else None},
      backend_queue_wait_completed_s={f'p{int(q*100):02d}':percentile(backend_wait,q) for q in (.5,.95,.99)}|{'max':max(backend_wait) if backend_wait else None},
      external_wait_s=0.0,unfinished_wait_at_horizon_s={'max':max((result['now']-j['arrival_ns'])*1e-9 for j in unfinished) if unfinished else 0.0,
        'sum':sum((result['now']-j['arrival_ns'])*1e-9 for j in unfinished)},
      completion_by_arrival_window=sum(j['end_ns']<=case['arrival_window_ns'] for j in complete),
      completion_by_final_horizon=len(complete),queue_trace_1s=trace,
      queue_peaks={'foreground':max(x['foreground_queued'] for x in trace),'maintenance':max(x['maintenance_queued'] for x in trace)},
      sampled_threshold_exposure=sampled_threshold_exposure(case,result),
      control_entries=dict(collections.Counter(x['to'] for x in result['log'] if x['kind']=='control')),
      maintenance={'done':len(maintenance_done),'failed':sum(j.get('status')=='FAILED' for j in maintenance_done),
        'queued':sum(j['maintenance'] for j in result['queue']),'inflight':sum(j['maintenance'] for j in result['active']),
        'overdue_cohorts':sum(x>0 for x in overdue),'max_overdue_s':max(overdue)*1e-9},
      component_energy_j=result['energy_j'],package_dynamic_energy_j=sum(result['energy_j'].values()),
      external_energy_j=result['external_energy_j'],negative_results_are_valid=True)
    assert summary['offered_foreground']==len(case['requests'])
    assert summary['foreground_complete']+summary['foreground_failed']+summary['foreground_unfinished']==summary['offered_foreground']
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--raw',type=Path,required=True);a=p.parse_args()
    case=json.loads(a.input.read_text());raw=json.loads(a.raw.read_text());result=detailed_review(case,raw)
    result['input_sha256']=hashlib.sha256(a.input.read_bytes()).hexdigest();result['raw_sha256']=hashlib.sha256(a.raw.read_bytes()).hexdigest()
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()

"""Independent interval-energy and service conservation audit; no solver."""
import argparse,collections,hashlib,json,math
from pathlib import Path

def review(case,r):
    c=case['config'];assert c['topology']=='mixed_direct' # current frozen pilot scope only
    assert r['evidence']=='ENGINEERING_FIXTURE' and r['temperature_source']=='SIMULATED'
    assert r['now']==case['end_ns'] and r['policy']==c['policy']
    jobs=r['done']+r['active']+r['queue'];ids=[j['id'] for j in jobs]
    assert len(ids)==len(set(ids))
    offered={j['id'] for j in case['requests']}
    assert {j['id'] for j in jobs if not j['maintenance']}==offered
    energy=collections.defaultdict(float);energy['gpu']=c['power_w']['gpu_external']*r['now']*1e-9
    bookings=collections.defaultdict(list);programs=collections.Counter();erases=collections.Counter();commits=collections.Counter()
    powers=c['power_w'];done_ids={j['id'] for j in r['done']}
    for j in r['done']+r['active']:
        assert j['arrival_ns']<=j['start_ns']<j['end_ns']
        if j['id'] in done_ids:assert j['end_ns']<=r['now']
        else:assert j['start_ns']<=r['now']<j['end_ns']
        sid=j['stack'];die=j['die'];cohort=f'{sid}:{die}'
        expected={f'{sid}:die:{die}',f'{sid}:base',f'{sid}:gpu-link'}
        if sid.startswith('hbf'):expected.add(f'{sid}:upstream')
        assert set(j['resources'])==expected and len(j['resources'])==len(expected)
        end=min(j['end_ns'],r['now']);dt=(end-j['start_ns'])*1e-9
        op=j['op'];array='refresh_array' if op=='dram_refresh' else op+'_array'
        energy[f'{sid}_die{die}']+=powers[array]*dt;energy[f'{sid}_base']+=powers['base']*dt;energy['gpu']+=powers['gpu_phy']*dt
        for resource in expected:bookings[resource].append((j['start_ns'],end,j['id']))
        if op=='program':programs[cohort]+=1
        if op=='erase':erases[cohort]+=1
        if j['maintenance'] and j.get('status')=='COMPLETE' and op in ('program','dram_refresh'):commits[cohort]+=1
    for rows in bookings.values():
        rows.sort()
        for prev,cur in zip(rows,rows[1:]):assert prev[1]<=cur[0],('resource overlap',prev,cur)
    energy_error=max(abs(energy[k]-v) for k,v in r['energy_j'].items())
    assert energy_error<=1e-8 and set(energy)<=set(r['energy_j'])
    for key,x in r['cohorts'].items():
        assert x['commits']==commits[key] and x['program_attempts']==programs[key] and x['erase_attempts']==erases[key]
        assert x['age_ns']==r['now']-x['last_commit_ns']+(0 if x['commits'] else x['initial_age_ns'])
        assert x['old_data_valid']
    assert r['balance']['relative_residual']<=.001 and r['balance']['max_component_mapping_error_j']<=1e-8
    assert all(math.isfinite(t) and t>0 for t in r['temperature_k'].values())
    foreground=[j for j in r['done'] if not j['maintenance']]
    latencies=[(j['end_ns']-j['arrival_ns'])*1e-9 for j in foreground]
    temperatures=r['temperature_range_k'];hotspot=max(temperatures,key=lambda k:temperatures[k]['max'])
    return {'status':'PASS','evidence':'ENGINEERING_FIXTURE','physical_acceptance':False,
      'scenario':case['scenario_label'],'policy':c['policy'],'simulated_s':r['now']*1e-9,
      'foreground_done':len(foreground),'foreground_queued':sum(not j['maintenance'] for j in r['queue']),
      'foreground_inflight':sum(not j['maintenance'] for j in r['active']),
      'completed_foreground_mean_latency_s':sum(latencies)/len(latencies) if latencies else None,
      'latency_semantics':'completed only, censored backlog separately shown',
      'maintenance_commits':sum(commits.values()),'maintenance_backlog':sum(j['maintenance'] for j in r['queue']),
      'program_attempts':sum(programs.values()),'erase_attempts':sum(erases.values()),
      'max_data_age_s':max(x['age_ns'] for x in r['cohorts'].values())*1e-9,
      'hotspot':hotspot,'max_temperature_k':temperatures[hotspot]['max'],
      'balance':r['balance'],'independent_interval_energy_error_j':energy_error,
      'control_transitions':dict(collections.Counter(x['to'] for x in r['log'] if x['kind']=='control')),
      'known_limits':case['claim_limits']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--raw',type=Path,required=True);a=p.parse_args()
    result=review(json.loads(a.input.read_text()),json.loads(a.raw.read_text()))
    result['input_sha256']=hashlib.sha256(a.input.read_bytes()).hexdigest();result['raw_sha256']=hashlib.sha256(a.raw.read_bytes()).hexdigest()
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()

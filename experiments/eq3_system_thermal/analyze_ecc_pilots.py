#!/usr/bin/env python3
"""Replay the optional reliability receipts, then compare frozen null/weak pairs."""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from analyze_extensions import analyze_point,plot_panels
from ecc_cost_proxy import ReadCostProxy


def analyze(point):
    generic=analyze_point(point)
    config=json.loads((point/'config.json').read_text())
    option=config['hbf_read_cost_proxy']
    provider=ReadCostProxy(option['profile'],option['initial_by_stack'])
    manifest=json.loads((point/'manifest.json').read_text())
    normalized=json.loads((Path(manifest['model_dir'])/'normalized.json').read_text())
    die_ids={stack:[c['id'] for c in normalized['components']
                    if c.get('device_id')==stack and c.get('role')=='array_die']
             for stack in provider.states}
    if any(not ids for ids in die_ids.values()):raise ValueError('missing actual array mapping')
    decisions=set();physical=defaultdict(int);retry=defaultdict(int);decode=defaultdict(int)
    energy=0.;peaks={};rows=0;times=[];owner_series=defaultdict(list)
    for line in (point/'windows.jsonl').open():
        row=json.loads(line);rows+=1
        receipt=row['hbf_read_cost_proxy']
        for d in receipt['admission_cost_decisions']:
            if d['job_id'] in decisions:raise ValueError('duplicate cost sampling')
            decisions.add(d['job_id'])
            if not row['start_ns']<=d['at_ns']<=row['end_ns']:raise ValueError('noncausal decision time')
            expected=provider.cost(d['stack'],d['at_ns'])
            if expected['attempt_work_milli']!=d['attempt_work_milli']:raise ValueError('unreproducible retry effort')
            if not math.isclose(expected['equivalent_age_days_30c'],d['equivalent_age_days_30c'],rel_tol=1e-12):
                raise ValueError('retention age differs from previous-temperature replay')
        for a in row['service']['activities']:
            s=a['stack']
            if a['phase']=='media_read' and s in provider.states:
                physical[s]+=a['bytes']
            if a['operation']=='retry_internal':
                if s not in provider.states or a['phase']!='media_read':raise ValueError('retry leaks out of HBF media scope')
                retry[s]+=a['bytes']
            if a['phase']=='ecc_decode':decode[s]+=a['bytes']
        energy+=sum(v for k,v in row['energy']['scope_energy_j'].items() if k.startswith('retry_internal:'))
        entities=row['thermal']['entity_temperatures_k']
        temperatures={s:max(entities[k]['hotspot_k'] for k in die_ids[s])
                      for s in provider.states}
        provider.observe(row['start_ns'],row['end_ns'],temperatures)
        for s,v in provider.states.items():
            observed=receipt['state']['states'][s]
            if not math.isclose(v['equivalent_age_ns'],observed['equivalent_age_ns'],rel_tol=1e-12):
                raise ValueError('window age mismatch')
        times.append(row['end_ns']/1e9)
        for s,v in row['thermal']['temperatures'].items():
            peaks[s]=max(peaks.get(s,v),v);owner_series[s].append(v)
    if dict(physical)!=dict(decode):raise ValueError('physical media/decoder activity byte mismatch')
    if not math.isclose(energy,sum(retry.values())*50e-12,rel_tol=1e-10,abs_tol=1e-12):
        raise ValueError('retry phase energy must match original50pJ/B once')
    strength=option['profile']['transfer_strength']
    if strength==0 and sum(retry.values()):raise ValueError('null transfer emitted retry work')
    summary=json.loads((point/'DONE.json').read_text())['summary']
    return generic,{'point_id':config['point_id'],'topology':config['topology'],'strength':strength,
        'status':'PASS_FIXED_INPUT_CAUSAL_REPLAY_AND_ENERGY','sampled_jobs':len(decisions),
        'windows':rows,'physical_read_bytes':dict(physical),'internal_retry_bytes':dict(retry),
        'retry_energy_j':energy,'total_energy_j':summary['energy_j'],
        'useful_bytes':summary['delivered_useful_bytes_by_stack'],
        'completed_simulated_tokens':summary['completed_tokens'],'peak_k_by_owner':peaks,
        'final_age_state':provider.snapshot()['states'],'temperature_series':{'time_s':times,'owners_k':dict(owner_series)},'scope':'CONDITIONAL_PROXY_NOT_HBF_MEASUREMENT_OR_POLICY_BENEFIT'}


def owner_plot(result,path):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    fig,axes=plt.subplots(3,1,figsize=(10,8),sharex=True)
    data=result['temperature_series']
    for axis,prefix in zip(axes,('gpu','hbm','hbf')):
        count=0
        for owner,values in data['owners_k'].items():
            if owner.startswith(prefix):axis.plot(data['time_s'],values,label=owner);count+=1
        axis.set_ylabel(prefix.upper()+' hotspot K');axis.grid(alpha=.2)
        if count:axis.legend(ncol=4,fontsize=8)
        else:axis.text(.1,.5,'UNAVAILABLE / NOT PRESENT',transform=axis.transAxes)
    axes[-1].set_xlabel('Simulation time (s)')
    fig.suptitle(result['point_id']+' — conditional proxy')
    fig.tight_layout();fig.savefig(path,dpi=140);plt.close(fig)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--index',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    index=json.loads(a.index.read_text());results=[];pairs=defaultdict(dict)
    for p in index['points']:
        path=Path(p['output']);generic,result=analyze(path)
        (a.output/(p['point_id']+'.json')).write_text(json.dumps(result,indent=2)+'\n')
        plot_panels(generic,a.output/(p['point_id']+'.png'))
        owner_plot(result,a.output/(p['point_id']+'-owners.png'))
        results.append(result);pairs[result['topology']][result['strength']]=result
    comparisons=[]
    for topology,pair in pairs.items():
        if set(pair)!={0,.1}:raise ValueError('incomplete paired topology')
        null,weak=pair[0],pair[.1]
        comparable=[]
        for strength in (0,.1):
            source=next(Path(p['output']) for p in index['points'] if p['point_id']==pair[strength]['point_id'])
            cfg=json.loads((source/'config.json').read_text());cfg.pop('point_id')
            cfg['hbf_read_cost_proxy']['profile'].pop('transfer_strength')
            comparable.append(cfg)
        if comparable[0]!=comparable[1]:raise ValueError('paired scientific inputs differ beyond proxy strength')
        if sum(weak['internal_retry_bytes'].values())<=0:raise ValueError('enabled proxy has no retry consumer')
        comparisons.append({'topology':topology,
            'null_useful_bytes':sum(null['useful_bytes'].values()),'weak_useful_bytes':sum(weak['useful_bytes'].values()),
            'null_tokens':null['completed_simulated_tokens'],'weak_tokens':weak['completed_simulated_tokens'],
            'null_peak_HBF_K':max(v for s,v in null['peak_k_by_owner'].items() if s.startswith('hbf')),
            'weak_peak_HBF_K':max(v for s,v in weak['peak_k_by_owner'].items() if s.startswith('hbf')),
            'weak_retry_energy_j':weak['retry_energy_j']})
    (a.output/'SUMMARY.json').write_text(json.dumps({'status':'PASS_PAIRED_INTEGRATION',
        'points':results,'pairs':comparisons,'policy_benefit':'NOT_TESTED'},indent=2)+'\n')

if __name__=='__main__':main()

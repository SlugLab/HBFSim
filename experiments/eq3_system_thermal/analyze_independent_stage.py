"""Reuse strict per-point audit for independent rate diagnostics/sensitivity.

These stages are not the 60-point base design: comparison identities include
the explicit distribution/model/physical settings rather than just rate/policy.
"""
import argparse
import csv
import json
from pathlib import Path

from analyze_campaign import analyze_point


def load(path):
    return json.loads(Path(path).read_text())


def metrics(point):
    totals = point['totals']
    return dict(delivered_TB=totals['delivered_effective_bytes']/1e12,
                backlog_TB=totals['final_backlog_effective_bytes']/1e12,
                peak_K=totals['peak_temperature_k'], energy_J=totals['energy_j'])


def compare(left, right, label):
    if left['totals']['offered_effective_bytes'] != right['totals']['offered_effective_bytes']:
        raise ValueError('paired total offered demand mismatch')
    return dict(comparison=label, left=left['point_id'], right=right['point_id'],
                delta_right_minus_left={k:metrics(right)[k]-v for k,v in metrics(left).items()})


def run(index_path, output):
    index = load(index_path)
    output.mkdir(parents=True, exist_ok=False)
    points, failures, configs = {}, [], {}
    for row in index['points']:
        path = Path(row['output'])
        if not (path/'DONE.json').is_file():
            if not (path/'FAILED.json').is_file():
                raise ValueError(f'unfinished point: {path}')
            failures.append(dict(point_id=row['point_id'], failure=load(path/'FAILED.json')))
            continue
        point = analyze_point(path)
        if point['point_id'] != row['point_id'] or point['input_sha256'] != row['config_sha256']:
            raise ValueError('point identity differs from frozen execution index')
        points[point['point_id']] = point
        configs[point['point_id']] = load(path/'config.json')
    comparisons = []
    baseline_cache = {}
    def baseline(row):
        pid=row['point_id']
        if pid not in baseline_cache:
            baseline_cache[pid]=analyze_point(Path(row['output']))
            if baseline_cache[pid]['input_sha256'] != row['config_sha256']:
                raise ValueError('historical baseline identity differs')
        return baseline_cache[pid]
    for kind in ('nonuniform_vs_uniform','no_coupling_vs_full'):
        for pair in index.get('comparisons',{}).get(kind,[]):
            if pair['diagnostic_point_id'] not in points:
                continue
            left=baseline(pair.get('uniform_base',pair.get('full_coupling_base')))
            comparisons.append(compare(left,points[pair['diagnostic_point_id']],kind))
    for kind in ('same_total_offered_existing_base','same_per_stack_existing_base'):
        for pair in index.get('comparisons',{}).get(kind,[]):
            left=baseline(pair['mixed_4x1536'])
            right=baseline(pair.get('all_hbf_8x768',pair.get('all_hbf_8x1536')))
            if kind=='same_total_offered_existing_base':
                comparisons.append(compare(left,right,kind))
            else:
                comparisons.append(dict(comparison=kind,left=left['point_id'],right=right['point_id'],
                    left_metrics=metrics(left),right_metrics=metrics(right),
                    warning='DIFFERENT_TOTAL_DEMAND_AND_STACK_COUNT_NO_ISOLATED_TOPOLOGY_EFFECT'))
    groups={}
    for pid, config in configs.items():
        if 'sensitivity' in config:
            key=(config['topology'],json.dumps(config['sensitivity']['values'],sort_keys=True))
            groups.setdefault(key,{})[config['strategy']]=points[pid]
    for (topology,values), group in groups.items():
        if 'guard_only' in group and 'read_rate_feedback_thermal_guard_v1' in group:
            comparison=compare(group['guard_only'],group['read_rate_feedback_thermal_guard_v1'],'controlled_sensitivity')
            comparison.update(topology=topology,values=json.loads(values));comparisons.append(comparison)
    # Scientific figures are derived only after each point passed the raw audit.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for pid, point in points.items():
        trace=point['_trace']; x=[n/1e9 for n in trace['end_ns']]
        fig,axes=plt.subplots(4,1,figsize=(10,9),sharex=True,layout='constrained')
        axes[0].plot(x,[v/1e12 for v in trace['offered_Bps']],label='demand')
        axes[0].plot(x,[v/1e12 for v in trace['delivered_Bps']],label='effective delivery');axes[0].set_ylabel('TB/s')
        for owner,values in trace['temperatures_k_by_owner'].items():axes[1].plot(x,values,label=owner)
        axes[1].set_ylabel('K');axes[1].legend(ncol=5,fontsize=6)
        axes[2].step(x,trace['worst_state_rank'],where='post');axes[2].set_ylabel('state rank')
        axes[3].plot(x,[v/1e12 for v in trace['backlog_bytes']]);axes[3].set_ylabel('backlog TB');axes[3].set_xlabel('s')
        for axis in axes:axis.grid(alpha=.2);axis.axvline(point['active_ns']/1e9,color='gray',linestyle='--')
        axes[0].legend();fig.suptitle(pid+' | CONDITIONAL_SIMULATED; ECC off; token/s unavailable')
        fig.savefig(output/(pid+'.png'),dpi=120);plt.close(fig)
    summaries=[dict(point_id=p['point_id'],topology=p['topology'],strategy=p['strategy'],**metrics(p)) for p in points.values()]
    with (output/'summary.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=['point_id','topology','strategy','delivered_TB','backlog_TB','peak_K','energy_J'])
        writer.writeheader();writer.writerows(summaries)
    result=dict(status='AUDITED_WITH_RETAINED_FAILURES' if failures else 'AUDITED_COMPLETE',
                point_count=len(index['points']),completed=len(points),failures=failures,
                comparisons=comparisons,points=summaries,claim='CONDITIONAL_NO_ECC_NOT_HARDWARE_CALIBRATION')
    (output/'RESULT.json').write_text(json.dumps(result,indent=2)+'\n')
    (output/'RESULT.md').write_text('# Independent stage evidence\n\n'+f'{len(points)}/{len(index["points"])} complete receipts audited; {len(failures)} retained failures.\n\n'
        +'All point summaries, declared paired comparisons and derived figures are in this directory. Failures remain in the denominator. Lower temperature is not automatically a performance gain.\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--index',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.index,args.output)

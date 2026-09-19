#!/usr/bin/env python3
"""Read-only, same-grid timestep convergence check; no solver launch."""
import argparse
import json
import math
from pathlib import Path
import eq3_reference as ref


def verified(run, power):
    manifest = ref.load(run/'input_manifest.json')
    receipt = ref.load(run/'reference_receipt.json')
    if receipt['exit_code'] != 0 or manifest['trace'] != 'train':
        raise ValueError('requires completed fixed training-reference runs')
    if not receipt['outputs_sha256']:
        raise ValueError('missing raw output bindings')
    for name, expected in receipt['outputs_sha256'].items():
        if ref.sha256(run/name) != expected:
            raise ValueError('changed output: '+name)
    series = ref.reference_series(run, power['slot_s'])
    maps = ref.map_hotspots(run)
    expected_n = len(power['traces']['train']['slots'])
    if len(maps) != expected_n or any(len(s)!=expected_n for s in series.values()):
        raise ValueError('truncated reference')
    return manifest, receipt, series, maps


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ['coarse', 'middle', 'fine', 'power']:
        ap.add_argument('--'+name, type=Path, required=True)
    args = ap.parse_args()
    power = ref.load(args.power)
    runs = [verified(p,power) for p in [args.coarse,args.middle,args.fine]]
    first = runs[0][0]
    if first['input_sha256'].get(args.power.name) != ref.sha256(args.power):
        raise ValueError('wrong power input')
    for current in runs[1:]:
        if current[0]['input_sha256'] != first['input_sha256'] or current[0]['mesh'] != first['mesh']:
            raise ValueError('physical input or mesh changed')
        if current[1]['executable_sha256'] != runs[0][1]['executable_sha256']:
            raise ValueError('solver changed')
    steps = [r[0]['step_s'] for r in runs]
    if not (math.isclose(steps[0],2*steps[1]) and math.isclose(steps[1],2*steps[2])):
        raise ValueError('requires successively halved steps')
    deltas = []
    for a,b in zip(runs,runs[1:]):
        deltas.append(dict(region=ref.series_delta(a[2],b[2]),
                           grid_hotspot=ref.hotspot_delta(a[3],b[3])))
    comparisons = {}
    for kind, metric in [('region','aggregate_mae_k'),('region','max_error_k'),
                         ('grid_hotspot','mae_k'),('grid_hotspot','max_error_k')]:
        old,new = (d[kind][metric] for d in deltas)
        comparisons[kind+'.'+metric] = dict(previous=old,current=new,
            ratio=None if old==0 else new/old,
            improves=(new<=.8*old) if old>1e-12 else new<=1e-12)
    print(json.dumps(dict(classification='BOUNDED_ENGINEERING_CONVERGENCE',
        step_s=steps, fixed_inputs=first['input_sha256'], comparisons=comparisons,
        preregistered_rule='All four deltas shrink to <=80% of previous delta; '
                           'otherwise stop and diagnose. Not absolute accuracy proof.',
        improvement_observed=all(v['improves'] for v in comparisons.values()),
        physical_validation=False, rc_validation=False),indent=2))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Summarize layers from a hash-pinned completed validation receipt, not a scope list."""
import argparse
import hashlib
import json
from pathlib import Path
import re

def summarize(report):
    if report.get('status') not in ('MODEL_CONNECTED_PASS', 'MODEL_CONNECTED_PASS_MANIFEST_FULLWEIGHTS', 'MODEL_CONNECTED_PASS_CANDIDATE_ONLY_MEASUREMENT'):
        raise ValueError('completed model validation required')
    if not report.get('target_pid') or not report.get('target_start_ticks') or not report.get('validated_plan_sha256'):
        raise ValueError('actual run and plan identity required')
    rows = report.get('by_storage')
    if not isinstance(rows, list) or not rows:
        raise ValueError('address-matched completed storage evidence required; registration is insufficient')
    layers = set()
    common = set()
    joined = []
    for row in rows:
        if not isinstance(row.get('base'), int) or row['base'] <= 0 or not isinstance(row.get('bytes'), int) or row['bytes'] <= 0:
            raise ValueError('actual storage address/extent missing')
        modules = row.get('active_complete_modules')
        if not isinstance(modules, list) or not modules or not all(isinstance(x, str) and x.startswith('ptx:sha256:') for x in modules):
            raise ValueError('active completed modules missing')
        alias = row['alias']
        match = re.fullmatch(r'model\.layers\.(\d+)\..+', alias)
        if match:
            layers.add(int(match[1]))
        else:
            common.add(alias)
        joined.append(dict(alias=alias, base=row['base'], bytes=row['bytes'], active_complete_modules=modules))
    return dict(schema='hbfsim.validated_layer_summary.v1', target_pid=report['target_pid'],
                target_start_ticks=report['target_start_ticks'], validated_plan_sha256=report['validated_plan_sha256'],
                layers=sorted(layers), common_weights=sorted(common), storage_evidence=joined,
                limit='Summary of the pinned validator receipt. Each listed layer has evidenced weights; this does not assert every weight in that layer was covered. Native versus recovered provenance remains in the original binding records.')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--validation', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    a = p.parse_args()
    raw = a.validation.read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.sha256:
        p.error('validation receipt changed')
    result = summarize(json.loads(raw))
    result['validation_sha256'] = a.sha256
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()

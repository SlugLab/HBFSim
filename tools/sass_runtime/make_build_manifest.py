#!/usr/bin/env python3
"""Bind successful independent research build records and selected artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
from verify_build import verify

def row(path):
    path = path.resolve(strict=True)
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--overlay', type=Path, required=True)
    p.add_argument('--source-input', type=Path, action='append', default=[])
    p.add_argument('--build-receipt', type=Path, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    for role in ('agent', 'provider', 'server', 'gate', 'pass', 'cudart12'):
        p.add_argument('--'+role, type=Path, required=True)
    a = p.parse_args()
    overlay_path = a.overlay/'SASS_SWITCH_OVERLAY.json'
    overlay = json.loads(overlay_path.read_bytes())
    sources = [a.overlay/r['output'] for r in overlay['files']] + a.source_input
    data = dict(schema='hbfsim.sass_research_build.v1', overlay_manifest=row(overlay_path), source_inputs=[row(x) for x in sources], build_receipts=[row(x) for x in a.build_receipt], artifacts={role:row(getattr(a,role)) for role in ('agent','provider','server','gate','pass','cudart12')})
    # Validate with the same launch gate before publishing the requested path.
    temporary = a.output.with_name(a.output.name+'.unverified')
    with temporary.open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    pin = row(temporary)['sha256']
    verify(temporary.resolve(), pin, {role:getattr(a,role) for role in data['artifacts']})
    if a.output.exists():
        raise FileExistsError(a.output)
    temporary.rename(a.output)
    print(json.dumps(row(a.output)))

if __name__ == '__main__':
    main()

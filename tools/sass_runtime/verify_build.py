#!/usr/bin/env python3
"""Verify separately recorded research build inputs and selected artifacts.

Research overlays are not the clean public bpftime build and must never borrow
its provenance stamp. This verifies recorded identities, not compiler honesty.
"""
import argparse
import hashlib
import json
from pathlib import Path

def checked(row):
    path = Path(row['path'])
    if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
        raise ValueError('research build identity mismatch: '+str(path))
    return path

def verify(manifest, pin, selected):
    path = checked({'path':str(manifest), 'sha256':pin})
    data = json.loads(path.read_bytes())
    if data.get('schema') != 'hbfsim.sass_research_build.v1':
        raise ValueError('separate research build schema required')
    overlay_path = checked(data['overlay_manifest'])
    overlay = json.loads(overlay_path.read_bytes())
    if overlay.get('schema') != 'hbfsim.sass_switch_overlay.v1':
        raise ValueError('switch overlay input missing')
    if not data.get('source_inputs') or not data.get('build_receipts'):
        raise ValueError('source identities and actual build receipts required')
    for row in data['source_inputs']:
        checked(row)
    input_identities = {(str(Path(row['path']).resolve()), row['sha256']) for row in data['source_inputs']}
    for row in overlay['files']:
        output = (overlay_path.parent/row['output']).resolve()
        if not output.is_relative_to(overlay_path.parent.resolve()):
            raise ValueError('overlay output escapes its directory')
        checked(dict(path=str(output), sha256=row['output_sha256']))
        if (str(output), row['output_sha256']) not in input_identities:
            raise ValueError('overlay consumer missing from recorded source inputs: '+row['role'])
    built = set()
    for row in data['build_receipts']:
        receipt = json.loads(checked(row).read_bytes())
        if receipt.get('status') != 'PASS' or not receipt.get('commands'):
            raise ValueError('successful build commands required')
        for command in receipt['commands']:
            if not isinstance(command.get('argv'), list) or not command['argv'] or command.get('returncode') != 0:
                raise ValueError('actual successful build argv required')
        for output in receipt.get('outputs', []):
            checked(output)
            built.add((str(Path(output['path']).resolve()), output['sha256']))
    for role, configured in selected.items():
        row = data['artifacts'][role]
        actual = checked(row)
        if actual.resolve() != Path(configured).resolve():
            raise ValueError('selected '+role+' differs from research build')
        if role in ('agent', 'provider') and (str(actual.resolve()), row['sha256']) not in built:
            raise ValueError('rebuilt '+role+' lacks successful output receipt')
    return data

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    for role in ('agent', 'provider', 'server', 'gate', 'pass', 'cudart12'):
        p.add_argument('--'+role, required=True)
    a = p.parse_args()
    verify(a.manifest, a.sha256, {role:getattr(a,role) for role in ('agent','provider','server','gate','pass','cudart12')})
    print('HBFSim: separate research build identities verified; runtime coverage remains pending.')

if __name__ == '__main__':
    main()

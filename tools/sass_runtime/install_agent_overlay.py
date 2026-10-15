#!/usr/bin/env python3
"""Install a reviewed overlay into an explicitly supplied isolated bpftime checkout.

Run before building. Never point this at an active experiment source tree.
"""
import argparse
import hashlib
import json
from pathlib import Path

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--overlay', type=Path, required=True)
    p.add_argument('--agent-source', type=Path, required=True)
    p.add_argument('--version-script', type=Path, required=True)
    a = p.parse_args()
    receipt = json.loads((a.overlay/'SASS_SWITCH_OVERLAY.json').read_bytes())
    for item in receipt['files']:
        candidate = (a.overlay/item['output']).resolve()
        if not candidate.is_relative_to(a.overlay.resolve()) or hashlib.sha256(candidate.read_bytes()).hexdigest() != item['output_sha256']:
            p.error('overlay input bytes changed or escaped output directory')
    row = next(r for r in receipt['files'] if r['role']=='agent')
    source = a.overlay/row['output']
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row['output_sha256']:
        p.error('overlay agent bytes changed')
    version = a.version_script.read_text()
    if version.count('global:') != 1 or 'hbfsim_sass_switch_abi_v1' in version:
        p.error('unexpected or already modified export version script')
    if not a.agent_source.is_file():
        p.error('explicit agent source target must already exist')
    # The original checkout and immutable evidence remain separately available.
    a.agent_source.write_bytes(raw)
    for name in ('qkv_exact_abi_adapter.hpp', 'qkv_live_identity_v1.hpp'):
        (a.agent_source.parent/name).write_bytes((a.overlay/'source'/name).read_bytes())
    symbols = ('hbfsim_sass_switch_abi_v1', 'bpftime_nv_qkv_select_weight_storage_v1', 'bpftime_nv_qkv_end_selected_call_v1')
    additions = ''.join(' ' + s + ';' for s in symbols if s not in version)
    a.version_script.write_text(version.replace('global:', 'global:' + additions, 1))
    print(json.dumps({'installed_agent_sha256':row['output_sha256'], 'compiled':False,
                      'next':'Rebuild this isolated checkout; verify exported ABI symbol and record artifact hashes.'}))

if __name__ == '__main__':
    main()

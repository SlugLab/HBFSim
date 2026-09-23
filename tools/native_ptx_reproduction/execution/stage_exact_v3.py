#!/usr/bin/env python3
"""CPU-only exact-entry staging; each raw module runs in a fresh pass process."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PROOF = Path(__file__).with_name('entry_proof.py')

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

def write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n')

def module_child(spec_path):
    spec = json.loads(Path(spec_path).read_text())
    source, plugin, preparer, out = map(Path, (spec['source'], spec['plugin'], spec['preparer'], spec['out']))
    raw = source.read_bytes()
    assert sha(source) == spec['raw']
    prep_spec = importlib.util.spec_from_file_location('hbfsim_auto_prepare', preparer)
    auto = importlib.util.module_from_spec(prep_spec)
    sys.modules[prep_spec.name] = auto
    prep_spec.loader.exec_module(auto)
    proof_spec = importlib.util.spec_from_file_location('hbfsim_entry_proof', PROOF)
    proof = importlib.util.module_from_spec(proof_spec)
    sys.modules[proof_spec.name] = proof
    proof_spec.loader.exec_module(proof)
    entries = list(auto._entry_names(raw))
    for entry in spec['entries']:
        assert entries.count(entry) == 1, 'entry not unique in raw'
    evidence = {'raw_sha256': spec['raw'], 'full_entry_count': len(entries), 'selected_entries': spec['entries']}
    if spec['kind'] == 'embedding':
        evidence['entry_proof'] = proof.prove_static_assertfail(raw, spec['entries'][0])
    elif spec['kind'] == 'fill':
        _, body, span = proof.entry_body(raw, spec['entries'][0])
        calls = proof.CALL_RE.findall(body)
        assert not calls, 'Fill selected entry has a call; no scoped proof'
        evidence['entry_proof'] = {'proof': 'EXACT_ENTRY_NO_CALL', **span, 'call_count': 0}
    elif spec['kind'] == 'norm':
        assert len(spec['entries']) == 2
        evidence['entry_proof'] = {'proof': 'EXACT_TWO_ENTRIES', 'entries': [proof.entry_body(raw, e)[2] for e in spec['entries']]}
    else:
        assert spec['kind'] == 'moe' and len(spec['entries']) == 1
        evidence['entry_proof'] = {'proof': 'EXACT_FUSED_MOE_ENTRY', 'entries': [proof.entry_body(raw, e)[2] for e in spec['entries']]}
    write(out/'ENTRY_PROOF.json', evidence)
    result = auto._transform_module_child(source, plugin, out/'pass-manifest.raw-attempts.jsonl', spec['entries'],
                                          'NO_UNRESOLVED_MEMORY_FUNCTION', [])
    payload = base64.b64decode(result.pop('output_ptx_base64'), validate=True)
    staged = out/(spec['raw'] + '.ptx')
    staged.write_bytes(payload)
    assert sha(staged) == result['staged_sha256']
    write(out/'TRANSFORM_RESULT.json', result)
    assert len(result['entry_results']) == len(spec['entries'])
    assert all(row['status'] == 'SUPPORTED_TRANSFORMED' for row in result['entry_results'])
    rows = result['manifest_records']
    assert {(r['module_id'], r['kernel']) for r in rows} == {('ptx:sha256:' + spec['raw'], e) for e in spec['entries']}
    for row in rows:
        assert row['instrumented'] and row['rewritten_instructions'] > 0
        assert not row['unsupported_parameters'] and row['unsupported_instructions'] == 0
        # ld/st.param, local, shared and const are non-HBF memory operations.
        relevant = [x for x in row['unsupported_opcodes'] if not auto.NON_HBF_UNSUPPORTED.search(str(x))]
        assert not relevant, f'HBF-relevant unsupported opcode: {relevant}'
    write(out/'SELECTED_ROWS.json', rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', type=Path)
    ap.add_argument('--output', type=Path)
    ap.add_argument('--child', type=Path)
    args = ap.parse_args()
    if args.child:
        module_child(args.child)
        return
    cfg = json.loads(args.input.read_text())
    out = args.output.absolute()
    assert not out.exists(), 'new independent result directory required'
    out.mkdir(parents=True)
    plugin, preparer = Path(cfg['pass_library']), Path(cfg['auto_prepare_ptx'])
    aggregate_plugin = Path(cfg['aggregate_pass_library'])
    assert sha(plugin) == cfg['pass_sha256'] and sha(preparer) == cfg['auto_prepare_sha256']
    assert sha(aggregate_plugin) == cfg['aggregate_pass_sha256']
    bindings = json.loads(Path(cfg['native_binding_manifest']).read_text())['bindings']
    assert len(bindings) == 4
    grouped = {}
    for b in bindings:
        raw = b['member_sha256']
        assert sha(b['raw_ptx_path']) == raw
        group = grouped.setdefault(raw, {'source': b['raw_ptx_path'], 'entries': [], 'kind': None})
        assert group['source'] == b['raw_ptx_path'] or sha(group['source']) == sha(b['raw_ptx_path'])
        group['entries'].append(b['kernel'])
        group['kind'] = 'embedding' if 'Indexing_cu' in b['kernel'] else 'fill' if 'FillFunctor' in b['kernel'] else 'norm'
    for row in cfg['moe_raw']:
        assert sha(row['path']) == row['sha256'] and row['sha256'] not in grouped
        grouped[row['sha256']] = {'source': row['path'], 'entries': ['fused_moe_kernel'], 'kind': 'moe'}
    assert len(grouped) == 7 and sum(len(x['entries']) for x in grouped.values()) == 8
    start = {'time_unix_ns': time.time_ns(), 'pass_sha256': sha(plugin),
             'aggregate_pass_sha256': sha(aggregate_plugin), 'preparer_sha256': sha(preparer), 'modules': grouped}
    write(out/'START.json', start)
    all_rows = []
    for raw, group in sorted(grouped.items()):
        mod_out = out/raw
        mod_out.mkdir()
        selected_plugin = aggregate_plugin if group['kind'] in ('embedding', 'fill') else plugin
        spec = {'raw': raw, 'source': group['source'], 'entries': group['entries'], 'kind': group['kind'],
                'plugin': str(selected_plugin), 'plugin_sha256': sha(selected_plugin),
                'preparer': str(preparer), 'out': str(mod_out)}
        write(mod_out/'SPEC.json', spec)
        with (mod_out/'stdout').open('xb') as stdout, (mod_out/'stderr').open('xb') as stderr:
            cp = subprocess.run([sys.executable, __file__, '--child', str(mod_out/'SPEC.json')],
                                stdout=stdout, stderr=stderr, timeout=300)
        write(mod_out/'PROCESS.json', {'returncode': cp.returncode, 'time_unix_ns': time.time_ns()})
        if cp.returncode:
            raise RuntimeError(f'module {raw} transform failed rc={cp.returncode}; raw logs preserved')
        all_rows.extend(json.loads((mod_out/'SELECTED_ROWS.json').read_text()))
    stage = out/'stage'
    stage.mkdir()
    stages = {}
    for raw in sorted(grouped):
        src = out/raw/(raw+'.ptx')
        dst = stage/(raw+'.ptx')
        shutil.copyfile(src, dst)
        stages[raw] = {'path': str(dst), 'sha256': sha(dst), 'bytes': dst.stat().st_size}
    with (out/'pass-manifests.jsonl').open('x') as f:
        for row in all_rows:
            f.write(json.dumps(row, sort_keys=True, separators=(',', ':'))+'\n')
    write(out/'STAGE_RECEIPT.json', {'status': 'CPU_STAGED_NOT_GPU_VALIDATED', 'time_unix_ns': time.time_ns(),
                                    'pass_sha256': sha(plugin), 'aggregate_pass_sha256': sha(aggregate_plugin),
                                    'plugin_selection': {raw: 'aggregate' if grouped[raw]['kind'] in ('embedding','fill') else 'base'
                                                         for raw in grouped},
                                    'stage_count': len(stages), 'pass_row_count': len(all_rows),
                                    'stages': stages, 'pass_manifest_sha256': sha(out/'pass-manifests.jsonl')})
    print(json.dumps({'status': 'CPU_STAGED', 'stage_count': len(stages), 'pass_row_count': len(all_rows)}))

if __name__ == '__main__':
    main()

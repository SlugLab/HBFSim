#!/usr/bin/env python3
"""Build exact new-stage provenance for the four native Torch/vLLM entries."""
import hashlib
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''): h.update(chunk)
    return h.hexdigest()

def write(path,obj):
    Path(path).write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')

def one_group(name,bindings,auto,stage_run,output,join_tool):
    out=output/name; out.mkdir()
    stage=out/'stage'; stage.mkdir()
    source_sidecar=json.loads(Path(bindings[0]['provenance_sidecar']).read_text())
    collector=source_sidecar['collector']['manifest_path']
    assert all(json.loads(Path(b['provenance_sidecar']).read_text())['collector']['manifest_path']==collector for b in bindings)
    grouped={}
    for b in bindings:
        raw=b['member_sha256']; group=grouped.setdefault(raw,{'source':b['raw_ptx_path'],'entries':[]})
        assert sha(b['raw_ptx_path'])==raw and sha(group['source'])==raw
        group['entries'].append(b['kernel'])
    variants=[]; passrows=[]; artifact={}
    for raw,g in grouped.items():
        result=json.loads((stage_run/raw/'TRANSFORM_RESULT.json').read_text())
        copy=stage/(raw+'.ptx'); shutil.copyfile(stage_run/'stage'/(raw+'.ptx'),copy)
        assert sha(copy)==result['staged_sha256']
        artifact[str(copy)]=sha(copy)
        selected_rows=json.loads((stage_run/raw/'SELECTED_ROWS.json').read_text())
        assert {r['kernel'] for r in selected_rows}==set(g['entries'])
        passrows.extend(selected_rows)
        all_entries=list(auto._entry_names(Path(g['source']).read_bytes()))
        assert all(all_entries.count(entry)==1 for entry in g['entries'])
        variants.append({'call_graph_status':'KNOWN_SYSTEM_CALL_DEPENDENCY_ASSERTFAIL' if name=='torch' and raw.startswith('91046') else 'EXACT_ENTRY_NO_CALL' if name=='torch' else 'NO_UNRESOLVED_MEMORY_FUNCTION',
            'entry_results':result['entry_results'],'kernel_entries':all_entries,
            'module_id':'ptx:sha256:'+raw,'raw_attempt_records':selected_rows,
            'raw_bytes':Path(g['source']).stat().st_size,'raw_sha256':raw,
            'selected_entries':g['entries'],'sources':[g['source']],
            'staged_bytes':copy.stat().st_size,'staged_path':str(copy),
            'staged_sha256':sha(copy),'status':'PARTIAL_READY','unresolved_functions':[]})
    pass_path=stage/'pass-manifests.jsonl'
    pass_path.write_text(''.join(json.dumps(r,sort_keys=True,separators=(',',':'))+'\n' for r in passrows))
    manifest={'schema_version':1,'cache_root':str(stage_run),'staging_dir':str(stage),'policy':'partial',
              'status':'PARTIAL_READY','variants':variants,'kernel_patterns':[],
              'completion_marker':str(stage/'COMPLETE.json')}
    write(stage/'ptx-staging-manifest.json',manifest)
    write(stage/'COMPLETE.json',{'status':'PARTIAL_READY','manifest_sha256':sha(stage/'ptx-staging-manifest.json'),
          'pass_manifest_path':str(pass_path),'pass_manifest_sha256':sha(pass_path),'artifact_sha256':artifact})
    sidecar=out/'native-provenance-scoped.json'
    argv=[sys.executable,str(join_tool),'--collector-manifest',str(collector),'--staging-dir',str(stage),
          '--pass-manifest',str(pass_path)]
    for b in bindings: argv+=['--selected-entry',b['kernel']]
    argv+=['--output',str(sidecar)]
    cp=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    (out/'join.stdout').write_bytes(cp.stdout); (out/'join.stderr').write_bytes(cp.stderr)
    write(out/'JOIN_PROCESS.json',{'argv':argv,'returncode':cp.returncode})
    if cp.returncode: raise RuntimeError(f'{name} scoped join rc={cp.returncode}')
    joined=json.loads(sidecar.read_text())
    assert joined['status']=='SCOPED_READY' and set(joined['selected_entry_memberships'])=={b['kernel'] for b in bindings}
    return sidecar

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--stage-run',required=True,type=Path)
    ap.add_argument('--bindings',required=True,type=Path)
    ap.add_argument('--auto-prepare',required=True,type=Path)
    ap.add_argument('--join-tool',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    a=ap.parse_args()
    output=a.output.absolute(); assert not output.exists(); output.mkdir()
    spec=importlib.util.spec_from_file_location('hbfsim_auto_prepare',a.auto_prepare)
    auto=importlib.util.module_from_spec(spec); sys.modules[spec.name]=auto; spec.loader.exec_module(auto)
    bindings=json.loads(a.bindings.read_text())['bindings']; assert len(bindings)==4
    torch=[b for b in bindings if b['container_sha256'].startswith('e3bdd')]
    norm=[b for b in bindings if b['container_sha256'].startswith('4cb19')]
    assert len(torch)==2 and len(norm)==2
    sidecars={'torch':one_group('torch',torch,auto,a.stage_run,output,a.join_tool),
              'norm':one_group('norm',norm,auto,a.stage_run,output,a.join_tool)}
    updated=[]
    for b in bindings:
        group='torch' if b in torch else 'norm'
        c=dict(b); c['staged_path']=str(output/group/'stage'/(b['member_sha256']+'.ptx'))
        c['staged_sha256']=sha(c['staged_path'])
        c['provenance_sidecar']=str(sidecars[group]); c['provenance_sha256']=sha(sidecars[group])
        updated.append(c)
    write(output/'native-bindings-new.json',{'schema_version':1,'status':'READY','bindings':updated})
    write(output/'JOIN_RECEIPT.json',{'status':'CPU_SCOPED_JOIN_NOT_GPU_VALIDATED',
          'binding_sha256':sha(output/'native-bindings-new.json'),
          'sidecars':{k:{'path':str(v),'sha256':sha(v)} for k,v in sidecars.items()}})
    print(json.dumps({'status':'CPU_JOINED','bindings':len(updated)}))

if __name__=='__main__': main()

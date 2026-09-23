#!/usr/bin/env python3
"""Compose an immutable runtime view from explicit, hash-bound component inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import time

REQUIRED = {
    'hbfsim-bpftime.provenance', 'hbfsim_bpftime_attach_loader', 'hbfsimd',
    'libhbfsim.so', 'libhbfsim.so.0', 'libhbfsim.so.0.1.0',
    'libhbfsim_launch_gate.so', 'libptxpass_hbf.so',
    'runtime/agent/libbpftime-agent.so',
    'runtime/syscall-server/libbpftime-syscall-server.so',
    'vllm_fused_moe_probe.bpf.o',
}

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''): h.update(chunk)
    return h.hexdigest()

def checked(row):
    source=Path(row['path']).absolute()
    if not source.is_file() or sha(source)!=row['sha256']:
        raise ValueError(f'source absent or SHA mismatch: {source}')
    return source

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--inputs',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    a=ap.parse_args()
    data=json.loads(a.inputs.read_text())
    if set(data['files'])!=REQUIRED:
        raise ValueError(f'bundle file set differs: missing={REQUIRED-set(data["files"])} extra={set(data["files"])-REQUIRED}')
    # Check all inputs before creating output.
    sources={name:checked(row) for name,row in data['files'].items()}
    compiler=checked(data['compiler'])
    receipts={name:checked(row) for name,row in data.get('receipts',{}).items()}
    out=a.output.absolute(); out.mkdir(parents=True,exist_ok=False)
    files={}
    for name,source in sorted(sources.items()):
        link=out/name; link.parent.mkdir(parents=True,exist_ok=True); link.symlink_to(source)
        files[name]={'path':str(link),'resolved':str(source.resolve()),
                     'sha256':sha(link),'bytes':link.stat().st_size}
    manifest={'schema':'hbfsim.source_built_runtime_bundle.v1',
              'status':'CPU_COMPOSED_NOT_GPU_VALIDATED','time_unix_ns':time.time_ns(),
              'files':files,'compiler':{'path':str(compiler),'sha256':sha(compiler)},
              'receipts':{name:{'path':str(path),'sha256':sha(path)} for name,path in receipts.items()},
              'note':data.get('note','Explicit source-built cohort; no GPU proof from composition.')}
    path=out/'BUNDLE_MANIFEST.json'
    path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'bundle':str(path),'sha256':sha(path),'file_count':len(files)}))

if __name__=='__main__': main()

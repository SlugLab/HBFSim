#!/usr/bin/env python3
"""Reproduce the pinned future-analysis counterexample without modifying production."""
import hashlib
import io
import json
import pathlib
import subprocess
import tarfile
import tempfile
ROOT=pathlib.Path(__file__).resolve().parents[2]
SHA='f4dc28b2671c01939d98e4a968e6fb37b2e364d9'
OUT=ROOT/'docs/49-eval-audit/evidence'
with tempfile.TemporaryDirectory(prefix='hbfsim-async-audit-') as directory:
    work=pathlib.Path(directory)
    archive=subprocess.check_output(['git','archive',SHA,'src/ptxpass_hbf'],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar: tar.extractall(work,filter='data')
    probe=(OUT/'future-predicate-probe.cpp').read_bytes()
    (work/'probe.cpp').write_bytes(probe)
    command=['c++','-std=c++20','-O2','-I.','-Isrc/ptxpass_hbf','probe.cpp']+[f'src/ptxpass_hbf/{s}.cpp' for s in ['future_transform','ptx_ir','ptx_analysis','ptx_async_op']]+['-o','probe']
    subprocess.run(command,cwd=work,check=True,capture_output=True)
    output=subprocess.check_output([str(work/'probe')],text=True)
    (OUT/'predicated-consumer-output.ptx').write_text(output)
    reproduced=(output.startswith('modified=1 reject=\n') and output.count('// HBFSim future wait ')==1 and '@!%p bra' in output and 'add.u32 %r4, %r1, 2;\nret;' in output)
    record={'branch':'feature/sm120-exact-stage1','git_sha':SHA,'evidence_kind':'STATIC_REPRODUCED' if reproduced else 'NOT_REPRODUCED',
        'gpu_executed':False,'case':'predicate false at first consumer, unguarded second consumer has no wait',
        'compile_command':command,'probe_sha256':hashlib.sha256(probe).hexdigest(),
        'output_sha256':hashlib.sha256(output.encode()).hexdigest(),'production_modified':False}
    (OUT/'async-probe.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps(record))
    if not reproduced: raise SystemExit('Pinned counterexample changed; inspect before updating the audit.')

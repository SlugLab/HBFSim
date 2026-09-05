#!/usr/bin/env python3
"""Cache representative PTX/cubin/disassembly evidence without claiming gold.

The build receipt must bind ptx_sha256 and cubin_sha256. This tool verifies that
binding and saves both cuobjdump and nvdisasm output once per immutable bundle.
Presence of instructions cannot prove async issue/wait/consume ordering, so
mapping_validation remains NOT_PROVEN until a separate scoped semantic review.
No optimization flags are changed, and no CUDA context or kernel is launched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

from freeze_storage_split import regular_bytes
from collect_storage import hash_regular
from replay_arrivals import identity, regular_file
from run_manifest import atomic_json, canonical_hash, git_snapshot, sha256

ROOT=Path(__file__).resolve().parents[2]


def execute(argv,stdout,stderr,timeout):
    return subprocess.run(argv,stdout=stdout,stderr=stderr,timeout=timeout).returncode


def collect_mapping_artifacts(cubin,ptx,build_manifest,out,*,cuda_bin,runner=None):
    out=Path(out).resolve()
    test_only=runner is not None
    if not out.is_relative_to(ROOT) or (test_only and out.is_relative_to((ROOT/'results/runs').resolve())):
        raise ValueError('audit output must stay in checkout; test bundles cannot enter formal runs')
    inputs={name:regular_bytes(Path(path).absolute()) for name,path in
            (('kernel.cubin',cubin),('source.ptx',ptx),('build-manifest.json',build_manifest))}
    hashes={name:hashlib.sha256(data).hexdigest() for name,data in inputs.items()}
    build=json.loads(inputs['build-manifest.json'])
    if (build['schema_version']!=1 or build['ptx_sha256']!=hashes['source.ptx']
            or build['cubin_sha256']!=hashes['kernel.cubin']):
        raise ValueError('PTX/cubin identity differs from build receipt')
    tools={name:regular_file((Path(cuda_bin)/name).resolve(strict=True)) for name in ('cuobjdump','nvdisasm')}
    identities={str(path):identity(path) for path in [*tools.values(),Path(__file__)]}
    tool_hashes={name:sha256(path) for name,path in tools.items()}
    key=canonical_hash(dict(inputs=hashes,tools=tool_hashes,test_only=test_only,collector=sha256(Path(__file__))))
    if any(identity(Path(path))!=before for path,before in identities.items()):
        raise ValueError('tool/collector changed while binding identity')
    expected_artifacts=set(inputs)|{name+suffix for name in tools for suffix in ('.sass.txt','.stderr.log')}
    if out.exists():
        result=json.loads(regular_bytes(out/'manifest.json'))
        if (result.get('collection')!='COMPLETE' or result.get('cache_key')!=key
                or result.get('mapping_validation')!='NOT_PROVEN'
                or result.get('provenance')!=('MOCK' if test_only else 'PROJECTED')
                or result.get('scientific_validation_passed') is not False or result.get('gpu_executed') is not False
                or result.get('inputs')!=hashes or result.get('tool_hashes')!=tool_hashes
                or set(result.get('artifact_hashes',{}))!=expected_artifacts
                or {p.name for p in out.iterdir()}!=expected_artifacts|{'manifest.json'}
                or any(result['artifact_hashes'][name]!=expected for name,expected in hashes.items())):
            raise ValueError('existing audit bundle is incomplete or belongs to different inputs/build/tools')
        for name,expected in result['artifact_hashes'].items():
            if Path(name).name!=name or hash_regular(out/name)!=expected:
                raise ValueError('cached audit artifact changed')
        return result
    out.mkdir(parents=True,exist_ok=False)
    result=dict(schema_version=1,collection='FAILED',mapping_validation='NOT_PROVEN',
                scope='REPRESENTATIVE_COMPILED_ARTIFACTS_NOT_ASYNC_SEMANTIC_GOLD',
                provenance='MOCK' if test_only else 'PROJECTED',gpu_executed=False,
                scientific_validation_passed=False,cache_key=key,inputs=hashes,
                tool_hashes=tool_hashes,git=git_snapshot(ROOT),commands=[],artifact_hashes={})
    run=runner or execute
    try:
        for name,data in inputs.items():
            with (out/name).open('xb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
        for name,option in (('cuobjdump','--dump-sass'),('nvdisasm','--print-line-info-ptx')):
            command=[str(tools[name]),option,str(out/'kernel.cubin')]
            stdout_path=out/(name+'.sass.txt');stderr_path=out/(name+'.stderr.log')
            with stdout_path.open('xb') as stdout,stderr_path.open('xb') as stderr:
                code=run(command,stdout,stderr,30)
                stdout.flush();stderr.flush();os.fsync(stdout.fileno());os.fsync(stderr.fileno())
            result['commands'].append(dict(argv=command,exit_code=code))
            if code or not stdout_path.stat().st_size:
                raise ValueError('disassembly failed or returned empty output: '+name)
        for name,expected in hashes.items():
            if hashlib.sha256(regular_bytes(out/name)).hexdigest()!=expected:
                raise ValueError('frozen audit input changed during disassembly')
        if any(identity(Path(path))!=before for path,before in identities.items()):
            raise ValueError('tool/collector identity changed during disassembly')
        if {path.name for path in out.iterdir()}!=expected_artifacts:
            raise ValueError('unexpected audit artifacts before completion')
        result['artifact_hashes']={name:hash_regular(out/name) for name in sorted(expected_artifacts)}
        result['collection']='COMPLETE'
    except BaseException as error:
        result['error']=str(error)
        raise
    finally:
        atomic_json(out/'manifest.json',result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('cubin','ptx','build-manifest','out','cuda-bin'):
        parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    try:
        result=collect_mapping_artifacts(args.cubin,args.ptx,args.build_manifest,args.out,cuda_bin=args.cuda_bin)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        parser.exit(2,f'audit_sass_mapping: {error}\n')
    print(json.dumps(dict(output=str(args.out),collection=result['collection'],mapping_validation=result['mapping_validation'])))


if __name__=='__main__':main()

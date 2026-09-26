#!/usr/bin/env python3
"""Read only the exact sm120 Li7 ELF from the preserved discovery image."""

import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time

ROOT = Path('/root/hbfsim-exp/rebuttal_20260921/sass-lifter-20260924')
TASK = ROOT / 'task/router-li7-cpu-extract-v1'
OUT = TASK / 'output-v2'
SOURCE = ROOT / 'runs/all-weights-discovery-v2/result'
SHA = 'd15cf2497649902226341d260eee82160e485d38e76cdcb9cb581eca82167eb0'
IMAGE = SOURCE / f'provider/pid-1961849/modules/{SHA}.bin'
INDEX = SOURCE / 'module-files.json'
SYMBOL = '_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi7ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_'
TOOL = '/usr/local/cuda-13.1/bin/cuobjdump'


def bounded():
    os.sched_setaffinity(0, {45})
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))


def run(argv, name, seconds):
    target = OUT / name
    err = OUT / (name + '.stderr')
    started = time.monotonic()
    with target.open('xb') as stdout, err.open('xb') as stderr:
        p = subprocess.run(argv, cwd=OUT, stdout=stdout, stderr=stderr,
                           preexec_fn=bounded, timeout=seconds)
    return {'argv': argv, 'rc': p.returncode, 'elapsed': time.monotonic() - started,
            'stdout': str(target), 'bytes': target.stat().st_size,
            'stderr': str(err), 'stderr_bytes': err.stat().st_size}


def main():
    index = json.loads(INDEX.read_text())
    rows = [r for r in index['files'] if r['sha256'] == SHA]
    if len(rows) != 1 or rows[0]['remote_path'] != str(IMAGE) or rows[0]['bytes'] != 176152136:
        raise RuntimeError('preserved image index mismatch')
    if IMAGE.stat().st_size != 176152136:
        raise RuntimeError('preserved image byte length mismatch')
    listing = (TASK / 'output-v1/list-elf.txt').read_text()
    needle = f'{SHA}.7.sm_120.cubin'
    if listing.count(f': {needle}') != 1:
        raise RuntimeError('exact ELF 7 missing or ambiguous in saved official listing')
    OUT.mkdir(parents=True, exist_ok=False)
    deadline = time.monotonic() + 600
    jobs = [run([TOOL, '--extract-elf', '.7.sm_120.cubin', str(IMAGE)],
                'extract.stdout', deadline - time.monotonic())]
    files = [p for p in OUT.glob('*.cubin') if p.is_file()]
    if jobs[0]['rc'] != 0 or len(files) != 1 or files[0].name != needle:
        raise RuntimeError(f'exact cubin extraction failed: {jobs[0]["rc"]}, files={[p.name for p in files]}')
    cubin = files[0]
    jobs.append(run(['/usr/bin/readelf', '-W', '-S', str(cubin)],
                    'sections.txt', deadline - time.monotonic()))
    jobs.append(run(['/usr/bin/readelf', '-W', '-s', str(cubin)],
                    'symbols.txt', deadline - time.monotonic()))
    sections = (OUT / 'sections.txt').read_text(errors='replace')
    symbols = (OUT / 'symbols.txt').read_text(errors='replace')
    if any(j['rc'] != 0 for j in jobs) or SYMBOL not in symbols:
        raise RuntimeError('readelf exact symbol not found or tool failed')
    nvinfo = '.nv.info.' + SYMBOL
    if nvinfo not in sections:
        raise RuntimeError('exact Li7 nv.info section not found')
    jobs.append(run(['/usr/bin/readelf', '-W', '-x', nvinfo, str(cubin)],
                    'li7-nvinfo.hex', deadline - time.monotonic()))
    if jobs[-1]['rc'] != 0:
        raise RuntimeError('exact Li7 nv.info dump failed')
    receipt = {'status': 'CPU_METADATA_EXTRACTED', 'input': str(IMAGE),
               'input_sha_from_provider': SHA, 'input_bytes_verified': IMAGE.stat().st_size,
               'cubin': str(cubin), 'cubin_bytes': cubin.stat().st_size,
               'cubin_sha256': hashlib.sha256(cubin.read_bytes()).hexdigest(),
               'symbol': SYMBOL, 'nvinfo_section': nvinfo,
               'jobs': jobs, 'total_budget_seconds': 600,
               'cpu': 45, 'memory_limit_bytes': 2*1024**3,
               'gpu_used': False}
    (OUT / 'CPU_RECEIPT.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': receipt['status'], 'cubin_bytes': receipt['cubin_bytes'],
                      'receipt': str(OUT / 'CPU_RECEIPT.json')}))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Bounded official cuobjdump read of the actual discovery-v2 Li7 image."""

import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import time

ROOT = Path('/root/hbfsim-exp/rebuttal_20260921/sass-lifter-20260924')
OUT = ROOT / 'task/router-li7-cpu-extract-v1/output-v1'
INDEX = ROOT / 'runs/all-weights-discovery-v2/result/module-files.json'
IMAGE_SHA = 'd15cf2497649902226341d260eee82160e485d38e76cdcb9cb581eca82167eb0'
IMAGE = ROOT / f'runs/all-weights-discovery-v2/result/provider/pid-1961849/modules/{IMAGE_SHA}.bin'
SYMBOL = '_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi7ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_'
TOOL = Path('/usr/local/cuda-13.1/bin/cuobjdump')
CPU = 45
MAX_SECONDS = 600


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded_child():
    os.sched_setaffinity(0, {CPU})
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))


def main():
    index = json.loads(INDEX.read_text())
    matches = [row for row in index['files'] if row['sha256'] == IMAGE_SHA]
    if len(matches) != 1 or matches[0]['remote_path'] != str(IMAGE):
        raise RuntimeError('actual discovery module index mismatch')
    if IMAGE.stat().st_size != matches[0]['bytes'] or matches[0]['bytes'] != 176152136:
        raise RuntimeError('actual discovery module length mismatch')
    if not TOOL.is_file():
        raise RuntimeError('official cuobjdump unavailable')
    OUT.mkdir(parents=True, exist_ok=False)
    deadline = time.monotonic() + MAX_SECONDS
    jobs = [
        ('list-elf.txt', ['--list-elf']),
        ('li7-sm120.sass', ['--dump-sass', '--gpu-architecture', 'sm_120', '--function', SYMBOL]),
        ('li7-sm120.resource.txt', ['--dump-resource-usage', '--gpu-architecture', 'sm_120', '--function', SYMBOL]),
        ('li7-sm120.symbols.txt', ['--dump-elf-symbols', '--gpu-architecture', 'sm_120', '--function', SYMBOL]),
        ('li7-sm120.elf.txt', ['--dump-elf', '--gpu-architecture', 'sm_120', '--function', SYMBOL]),
    ]
    receipts = []
    for name, args in jobs:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('600-second total CPU budget exhausted')
        path = OUT / name
        error = OUT / (name + '.stderr')
        started = time.monotonic()
        with path.open('xb') as out, error.open('xb') as err:
            result = subprocess.run([str(TOOL), *args, str(IMAGE)], stdout=out, stderr=err,
                                    preexec_fn=bounded_child, timeout=remaining, cwd=OUT)
        receipt = {'argv': [str(TOOL), *args, str(IMAGE)], 'returncode': result.returncode,
                   'elapsed_seconds': time.monotonic() - started,
                   'stdout': str(path), 'stdout_bytes': path.stat().st_size,
                   'stderr': str(error), 'stderr_bytes': error.stat().st_size}
        receipts.append(receipt)
        if result.returncode:
            break
    sass = OUT / 'li7-sm120.sass'
    sass_text = sass.read_text(errors='replace') if sass.is_file() else ''
    state = 'CPU_EXTRACT_PASS' if len(receipts) == len(jobs) and all(r['returncode'] == 0 for r in receipts) and SYMBOL in sass_text else 'CPU_EXTRACT_INCOMPLETE'
    report = {'status': state, 'source': 'actual native discovery-v2 provider module',
              'image_path': str(IMAGE), 'image_sha256_from_provider_identity': IMAGE_SHA,
              'image_bytes_verified_against_stat_index': matches[0]['bytes'],
              'index_sha256': digest(INDEX), 'tool_path': str(TOOL),
              'symbol_exact': SYMBOL, 'architecture': 'sm_120',
              'cpu_affinity': CPU, 'address_space_limit_bytes': 2*1024**3,
              'total_budget_seconds': MAX_SECONDS, 'jobs': receipts,
              'sass_exact_symbol_present': SYMBOL in sass_text,
              'gpu_api_or_model_called': False}
    path = OUT / 'CPU_RECEIPT.json'
    with path.open('x') as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps({'status': state, 'receipt': str(path), 'jobs': len(receipts)}))
    return 0 if state == 'CPU_EXTRACT_PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())

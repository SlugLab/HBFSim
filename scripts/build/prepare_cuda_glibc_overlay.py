#!/usr/bin/env python3
"""Copy CUDA includes and align only rsqrt declarations with recent glibc.

Optional build-local workaround for CUDA13.1/new glibc. Never modifies the
installed toolkit. Use returned directory with NVCC_PREPEND_FLAGS=-I<directory>.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--include', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
source = (a.include / 'crt/math_functions.h').read_bytes()
text = source.decode()
for function, signature in [('rsqrt', 'double x'), ('rsqrtf', 'float x')]:
    pattern = rf'(?m)^(extern __DEVICE_FUNCTIONS_DECL__ __device_builtin__[^\n]+\b{function}\({signature}\));$'
    text, count = re.subn(pattern, r'\1 noexcept(true);', text)
    if count != 1:
        raise SystemExit(f'unsupported header: expected one unqualified {function} declaration, got {count}')
if a.output.exists():
    raise SystemExit('output must be new; preserve existing build evidence')
shutil.copytree(a.include, a.output)
(a.output / 'crt/math_functions.h').write_text(text)
receipt = {'source': str(a.include.resolve()),
           'original_sha256': hashlib.sha256(source).hexdigest(),
           'patched_sha256': hashlib.sha256(text.encode()).hexdigest(),
           'change': 'Only rsqrt/rsqrtf declarations receive noexcept(true), matching this glibc',
           'scope': 'BUILD_LOCAL_WORKAROUND_NOT_INSTALLED_TOOLKIT_CHANGE',
           'reference': 'https://forums.developer.nvidia.com/t/cuda-headers-in-crt-math-functions-h-still-broken-in-debian-13-repo/362708'}
(a.output.parent / (a.output.name + '.json')).write_text(json.dumps(receipt, indent=2)+'\n')
print(a.output.resolve())

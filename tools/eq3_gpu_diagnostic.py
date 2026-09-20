#!/usr/bin/env python3
"""Read-only enumeration, never launches kernels or changes device settings.

Receipts contain local host identity; do not publish them without review.
"""
import argparse
import ctypes
import glob
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time


def command(argv):
    start = time.time()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        return dict(argv=argv, start_unix_s=start, elapsed_s=time.time()-start,
                    stdout=p.stdout, stderr=p.stderr, exit_code=p.returncode,
                    timeout=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return dict(argv=argv, start_unix_s=start, elapsed_s=time.time()-start,
                    error=str(e), exit_code=None,
                    timeout=isinstance(e, subprocess.TimeoutExpired))


def read(path):
    try:
        return Path(path).read_text()
    except OSError as e:
        return {'error': str(e)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--context', required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('refusing to overwrite an existing receipt')
    names = ['PATH', 'LD_LIBRARY_PATH', 'LD_PRELOAD', 'CUDA_VISIBLE_DEVICES',
             'NVIDIA_VISIBLE_DEVICES', 'NVIDIA_DRIVER_CAPABILITIES']
    data = dict(context=args.context, started_unix_s=time.time(),
                hostname=platform.node(), kernel=platform.release(),
                uid=os.getuid(), gid=os.getgid(), cwd=os.getcwd(),
                boot_id=read('/proc/sys/kernel/random/boot_id'),
                cgroup=read('/proc/self/cgroup'),
                namespaces={p: os.readlink('/proc/self/ns/'+p)
                            for p in ['pid', 'mnt', 'user']},
                environment={n: os.environ.get(n, 'UNSET') for n in names},
                driver=read('/proc/driver/nvidia/version'),
                gpu_compute_authorization='NOT_APPROVED; zero compute workload',
                framework={n: bool(importlib.util.find_spec(n))
                           for n in ['torch', 'tensorflow', 'jax']})
    data['devices'] = command(['bash', '-c', 'ls -l /dev/nvidia*'])
    data['binary_resolution'] = command(['bash', '-c',
        'type -a nvidia-smi; command -v nvidia-smi; readlink -f /usr/bin/nvidia-smi'])
    data['smi'] = command(['nvidia-smi'])
    data['smi_list'] = command(['nvidia-smi', '-L'])
    data['nvcc'] = command(['nvcc', '--version'])
    data['nvcc_path'] = shutil.which('nvcc')
    data['dynamic_libraries'] = command(['ldconfig', '-p'])
    if 'stdout' in data['dynamic_libraries']:
        data['dynamic_libraries']['stdout'] = '\n'.join(
            s for s in data['dynamic_libraries']['stdout'].splitlines()
            if any(k in s for k in ['libcuda', 'libnvidia-ml', 'libcudart']))
    for name, libname, init in [('nvml', 'libnvidia-ml.so.1', 'nvmlInit_v2'),
                              ('cuda', 'libcuda.so.1', 'cuInit')]:
        result = {}
        try:
            lib = ctypes.CDLL(libname)
            result['library_opened'] = libname
            fn = getattr(lib, init)
            fn.restype = ctypes.c_int
            fn.argtypes = [] if name == 'nvml' else [ctypes.c_uint]
            code = fn() if name == 'nvml' else fn(0)
            result['initialization_code'] = code
            if code == 0:
                count = ctypes.c_int()
                get_count = getattr(lib, 'nvmlDeviceGetCount_v2' if name == 'nvml'
                                    else 'cuDeviceGetCount')
                get_count.argtypes = [ctypes.POINTER(ctypes.c_int)]
                get_count.restype = ctypes.c_int
                result['enumeration_code'] = get_count(ctypes.byref(count))
                result['device_count'] = count.value
                if name == 'nvml':
                    lib.nvmlShutdown()
        except (OSError, AttributeError) as e:
            result['error'] = str(e)
        data[name] = result
    data['loaded_gpu_libraries'] = [s for s in read('/proc/self/maps').splitlines()
                                   if any(k in s for k in ['libcuda', 'libnvidia-ml'])]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    print(json.dumps({k: data[k] for k in ['context', 'cuda', 'nvml', 'framework']}))


if __name__ == '__main__':
    main()

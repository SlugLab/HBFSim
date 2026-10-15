#!/usr/bin/env python3
"""Exercise the real bootstrap argument construction without downloads or CUDA."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as temp:
        scratch = Path(temp)
        copied = scratch / 'project with spaces' / 'scripts' / 'bootstrap.sh'
        copied.parent.mkdir(parents=True)
        shutil.copyfile(root / 'scripts/bootstrap.sh', copied)
        fake = scratch / 'bin'
        fake.mkdir()
        calls = scratch / 'calls.jsonl'
        stub = ('#!/usr/bin/env python3\nimport json,os,sys\n'
                'with open(os.environ["CALLS"],"a") as f: '
                'f.write(json.dumps([os.path.basename(sys.argv[0]),*sys.argv[1:]])+"\\n")\n')
        for name in ('git', 'cmake'):
            file = fake / name
            file.write_text(stub)
            file.chmod(0o755)
        cuda = scratch / 'cuda with spaces'
        (cuda / 'bin').mkdir(parents=True)
        nvcc = cuda / 'bin/nvcc'
        nvcc.write_text(stub)
        nvcc.chmod(0o755)
        env = dict(os.environ, CALLS=str(calls), PATH=str(fake)+os.pathsep+os.environ['PATH'],
                   HBFSIM_ENABLE_CUDA='ON', HBFSIM_CUDA_ROOT=str(cuda), HBFSIM_CUDA_ARCHITECTURES='121')
        subprocess.run(['bash', str(copied)], env=env, check=True, capture_output=True)
        rows = [json.loads(x) for x in calls.read_text().splitlines()]
        assert rows[0][-2:] == ['third_party/bpftime', 'third_party/mqsim']
        assert rows[1] == ['nvcc', '--version']
        assert '-DCMAKE_CUDA_COMPILER='+str(nvcc) in rows[-1]
        assert '-DCUDAToolkit_ROOT='+str(cuda) in rows[-1]
        assert '-DCMAKE_CUDA_ARCHITECTURES=121' in rows[-1]
        calls.unlink()
        env.update(HBFSIM_ENABLE_CUDA='OFF', HBFSIM_CUDA_ROOT='/does/not/exist')
        subprocess.run(['bash', str(copied)], env=env, check=True, capture_output=True)
        rows = [json.loads(x) for x in calls.read_text().splitlines()]
        assert len(rows) == 2 and not any('CUDAToolkit_ROOT' in x for x in rows[-1])
        env.update(HBFSIM_ENABLE_CUDA='ON')
        failed = subprocess.run(['bash', str(copied)], env=env, capture_output=True, text=True)
        assert failed.returncode == 64 and 'HBFSIM_CUDA_ROOT' in failed.stderr
    print('PASS: selected toolkit/architecture, paths with spaces, CPU-only, invalid root')


if __name__ == '__main__':
    main()

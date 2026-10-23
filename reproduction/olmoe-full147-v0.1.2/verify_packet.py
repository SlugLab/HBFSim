"""Verify the frozen publication packet; this does not validate a GPU model."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu', action='store_true', help='Compile/run the retained CPU accounting fixture')
    parser.add_argument('--cxx', default='c++', help='C++20 compiler for --cpu')
    args = parser.parse_args()
    packet = Path(__file__).resolve().parent
    manifest = json.loads((packet / 'FROZEN_SOURCE_MANIFEST.json').read_text())
    if manifest['version'] != '0.1.2':
        raise ValueError('Unexpected packet version')
    expected = {}
    for row in manifest['files']:
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or row['path'] in expected:
            raise ValueError('Invalid or duplicate packet member')
        path = packet / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(packet):
            raise ValueError('Missing, nonregular or escaping member: ' + row['path'])
        raw = path.read_bytes()
        if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('Member identity mismatch: ' + row['path'])
        expected[row['path']] = row
    observed = {str(p.relative_to(packet)) for p in packet.rglob('*') if p.is_file()}
    if observed != set(expected) | {'FROZEN_SOURCE_MANIFEST.json'}:
        raise ValueError('Packet membership mismatch')
    strategy = json.loads((packet / 'STRATEGY.json').read_text())
    if strategy['timing_mode'] != 'nominal HBF; zero injection OFF' or strategy['persistent_ELF_cache'] != 'OFF':
        raise ValueError('Frozen strategy mismatch')
    result = {
        'status': 'SOURCE_PACKET_IDENTITIES_PASS', 'version': manifest['version'],
        'checked_files': len(expected), 'checked_bytes': sum(r['bytes'] for r in expected.values()),
        'GPU_model_executed': False, 'fresh_source_GPU_reproduction_claimed': False,
        'CPU_fixture': 'NOT_REQUESTED',
    }
    if args.cpu:
        helper = packet / 'source/hbf/helper'
        with tempfile.TemporaryDirectory(prefix='hbfsim-v012-publication-check-') as directory:
            executable = str(Path(directory) / 'uniform-account-cpu')
            command = [args.cxx, '-std=c++20', '-O2',
                       '-I' + str(helper / 'source/src/cuda_runtime/device'),
                       '-I' + str(helper / 'source/include'),
                       str(helper / 'tests/test_uniform_account.cpp'), '-o', executable]
            compiled = subprocess.run(command, text=True, capture_output=True, timeout=60)
            if compiled.returncode:
                raise RuntimeError('Publication CPU fixture compile failed: ' + compiled.stderr)
            tested = subprocess.run([executable], text=True, capture_output=True, timeout=60)
            if tested.returncode:
                raise RuntimeError('Publication CPU fixture failed: ' + tested.stdout + tested.stderr)
            result['CPU_fixture'] = {'status': 'PASS', 'compile_argv': command,
                                     'returncode': tested.returncode, 'stdout': tested.stdout,
                                     'scope': 'Retained integer accounting/layout fixture only; not NAND trace, GPU or model acceptance'}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

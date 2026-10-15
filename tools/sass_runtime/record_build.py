#!/usr/bin/env python3
"""Record one actual build command and its outputs; never invent success."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--cwd', type=Path, required=True)
    p.add_argument('--output', type=Path, action='append', required=True)
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    if not command or a.receipt.exists() or not a.cwd.is_absolute() or any(not x.is_absolute() for x in a.output):
        p.error('fresh receipt, explicit command and absolute cwd/output paths required')
    # Refuse stale outputs so a no-op cannot bless an older binary.
    if any(x.exists() for x in a.output):
        p.error('build outputs must not exist before this command')
    result = subprocess.run(command, cwd=a.cwd, check=False)
    complete = result.returncode == 0 and all(x.is_file() for x in a.output)
    rows = [{'path':str(x), 'sha256':hashlib.sha256(x.read_bytes()).hexdigest()} for x in a.output if x.is_file()]
    data = dict(status='PASS' if complete else 'FAIL', commands=[dict(argv=command, cwd=str(a.cwd), returncode=result.returncode)], outputs=rows, output_sha256=[r['sha256'] for r in rows])
    with a.receipt.open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    return 0 if complete else (result.returncode or 1)

if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Retain first audit, confirm exact frozen inventory and disallow child creation."""
import sys
import json
import resource
from pathlib import Path

out = Path(__file__).resolve().parent
before = resource.getrusage(resource.RUSAGE_CHILDREN)
attempts = []

def guard(event, args):
    if event.startswith('subprocess.') or event in ('os.fork', 'os.forkpty', 'os.posix_spawn', 'os.system', 'pty.spawn'):
        attempts.append(event)
        raise RuntimeError('single-process audit blocks ' + event)

sys.addaudithook(guard)
source = (out / 'eq34-cpu-audit.py').read_text()
source = source.replace("'eq34-", "'eq34-confirm-")
source = source.replace("legacy_path = REPO / 'results/manifests/qwen3-30b-a3b-inventory.json'",
                        "legacy_path = metrics_dir / 'inventory.json'")
return_code = None
try:
    exec(compile(source, str(out / 'eq34-cpu-audit.py'), 'exec'),
         {'__name__': '__main__', '__file__': str(out / 'eq34-cpu-audit.py')})
except SystemExit as result:
    return_code = result.code
finally:
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    receipt = dict(scope='TEST_ONLY_PROCESS_CREATION_GUARD', return_code=return_code,
                   blocked_process_attempts=attempts,
                   inherited_child_cpu_seconds=before.ru_utime + before.ru_stime,
                   process_child_cpu_delta_seconds=after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime,
                   original_audit_preserved=True,
                   inventory_resolution='Exact inventory.json retained by metrics manifest, not current GGUF inventory with same basename')
    (out / 'eq34-confirm-process-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))
sys.exit(return_code)

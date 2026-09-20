"""External serial campaign continuation; never claims to wake an AI agent.

Only executes frozen commands. Failed independent stages retain their evidence;
no scientific model selection or parameter adaptation is performed here.
"""
import argparse
import fcntl
import hashlib
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def live_campaign_processes(source_root):
    found = []
    names = {'launch_sensitivity.py', 'run_endpoint_guard_point.py', 'run_causal_point.py'}
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():
            continue
        try:
            args = (p / 'cmdline').read_bytes().decode().split('\0')
            if any(Path(a).name in names and str(source_root) in a for a in args):
                found.append(int(p.name))
        except (OSError, UnicodeError):
            pass
    return found


def verify_locks(locks):
    for path, digest in locks.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError(f'frozen queue input changed: {path}')


def run(manifest_path):
    manifest = json.loads(manifest_path.read_text())
    root = manifest_path.parent
    with (root / 'queue.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (root / 'QUEUE_STATUS.json').exists():
            raise FileExistsError('preserve previous queue status; new attempt needs a new directory')
        verify_locks(manifest['locks'])
        prerequisite = Path(manifest['wait_for_stage'])
        deadline = time.monotonic() + manifest['wait_timeout_s']
        save(root / 'QUEUE_STATUS.json', {'status': 'WAITING_FOR_EXISTING_STAGE',
            'pid': os.getpid(), 'ai_wakeup': 'UNAVAILABLE', 'stage': str(prerequisite)})
        while True:
            terminal = any((prerequisite / n).exists() for n in ('DONE.json', 'FAILED.json'))
            if terminal and not live_campaign_processes(manifest['source_root']):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('predecessor completion/CPU release was not observed')
            time.sleep(manifest['check_interval_s'])
        results = []
        env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
                   MKL_NUM_THREADS='1', NUMEXPR_NUM_THREADS='1', CUDA_VISIBLE_DEVICES='',
                   MPLCONFIGDIR=str(root / 'matplotlib-cache'))
        for phase in manifest['phases']:
            verify_locks(manifest['locks'])
            if live_campaign_processes(manifest['source_root']):
                raise RuntimeError('another campaign process still owns CPU')
            save(root / 'QUEUE_STATUS.json', {'status': 'RUNNING', 'pid': os.getpid(),
                'phase': phase['id'], 'results': results, 'ai_wakeup': 'UNAVAILABLE'})
            with (root / (phase['id'] + '.log')).open('x') as log:
                child = subprocess.Popen(phase['command'], cwd=manifest['source_root'],
                    env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    code = child.wait(timeout=phase.get('analysis_timeout_s'))
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL); child.wait()
                    code = 124
            results.append({'phase': phase['id'], 'exit_code': code})
            save(root / 'PHASE_RESULTS.json', results)
            # Subsequent phases are declared independent. Never modify an input,
            # retry a failed point, or select a favorable ECC curve here.
        save(root / 'QUEUE_DONE.json', {'status': 'COMPLETED' if all(r['exit_code'] == 0 for r in results)
            else 'FINISHED_WITH_FAILURES', 'results': results, 'ai_wakeup': 'UNAVAILABLE',
            'human_or_agent_review_required': True})
        save(root / 'QUEUE_STATUS.json', json.loads((root / 'QUEUE_DONE.json').read_text()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.manifest.resolve())
    except BaseException as error:
        save(args.manifest.resolve().parent / 'QUEUE_FAILED.json', {'error': repr(error),
            'status': 'FAILED', 'raw_preserved': True, 'ai_wakeup': 'UNAVAILABLE'})
        raise

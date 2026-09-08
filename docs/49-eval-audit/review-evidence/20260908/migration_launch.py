#!/usr/bin/env python3
"""Task-local mount/space/cache guard. No job is resumed implicitly."""
import argparse,json,os,re,shutil,subprocess,sys,signal,time
from pathlib import Path


def cleanup_owned_session(child):
    """Terminate only this Popen child's newly created session, then reap it.

    The caller retains the unreaped session leader. Never signal a group after
    observing that leader has already exited, preventing PID reuse targeting.
    """
    if child.poll() is not None:
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            child.wait()
            return
        try:
            child.wait(timeout=30 if sig != signal.SIGKILL else None)
            return
        except subprocess.TimeoutExpired:
            continue


def check_runtime(root, mount, reserve_gib):
    current=json.loads(subprocess.check_output(
        ['findmnt','-J','-T',str(root),'-o','TARGET,SOURCE,UUID,OPTIONS'],
        text=True))['filesystems'][0]
    free_now=shutil.disk_usage(root).free
    if (current['uuid']!=mount['uuid'] or current['target']!='/mnt/disk0'
            or 'rw' not in current['options'].split(',')
            or free_now<reserve_gib*2**30):
        print(json.dumps({'state':'STOP_SPACE_OR_MOUNT','free_bytes':free_now,
                          'utc_unix':time.time()}),flush=True)
        raise SystemExit(74)


def monitor_child(child, check, interval=60):
    """Return normal child status; any monitoring failure cleans up and re-raises."""
    try:
        while True:
            try:
                return child.wait(timeout=interval)
            except subprocess.TimeoutExpired:
                pass
            check()
    except BaseException:
        try:
            cleanup_owned_session(child)
        except BaseException as cleanup_error:
            # Cleanup diagnostics must not replace the original failure/exit.
            print('OWNED_CLEANUP_ERROR: '+repr(cleanup_error),file=sys.stderr,flush=True)
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stack',required=True,help='source/build/environment fingerprint, not a shared stack nickname')
    p.add_argument('--check',action='store_true')
    p.add_argument('--reserve-gib',type=int,default=128)
    p.add_argument('--max-output-gib',type=int,default=64)
    p.add_argument('command',nargs=argparse.REMAINDER)
    a=p.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',a.stack): p.error('invalid stack fingerprint')
    if a.reserve_gib<128 or a.max_output_gib<0: p.error('reserve must be >=128 GiB and output bound nonnegative')
    root=Path('/mnt/disk0/hbfsim-exp'); logical=Path('/root/hbfsim-exp')
    mount=json.loads(subprocess.check_output(['findmnt','-J','-T',str(root),'-o','TARGET,SOURCE,UUID,OPTIONS'],text=True))['filesystems'][0]
    if (mount['target']!='/mnt/disk0' or mount['source']!='/dev/nvme2n1p1' or mount['uuid']!='37be6892-a8aa-4fae-885d-5feed3b28fab' or 'rw' not in mount['options'].split(',')):
        raise SystemExit('BLOCKED_MOUNT: expected writable PM1733a UUID')
    if logical.resolve()!=root or not logical.is_symlink(): raise SystemExit('BLOCKED_PREFIX: migration compatibility link absent')
    free=shutil.disk_usage(root).free
    if free<(a.reserve_gib+a.max_output_gib)*2**30: raise SystemExit('BLOCKED_SPACE: bounded output plus reserve would exceed free space')
    base=root/'.task-runtime'/a.stack
    names={'TMPDIR':'tmp','HF_HOME':'hf','XDG_CACHE_HOME':'xdg','TRITON_CACHE_DIR':'triton','CUDA_CACHE_PATH':'cuda','TORCH_EXTENSIONS_DIR':'torch-extensions','NUMBA_CACHE_DIR':'numba','PIP_CACHE_DIR':'pip'}
    env={name:str(base/sub) for name,sub in names.items()}
    result={'mount':mount,'free_bytes':free,'reserve_bytes':a.reserve_gib*2**30,'output_bound_bytes':a.max_output_gib*2**30,'environment':env,'automatic_restart':False,'state':'GUARD_PASS'}
    print(json.dumps(result,indent=2),flush=True)
    cmd=a.command[1:] if a.command[:1]==['--'] else a.command
    if a.check or not cmd: return 0
    for directory in env.values(): Path(directory).mkdir(parents=True,exist_ok=True)
    task_env=os.environ.copy();task_env.update(env)
    # Caller must separately satisfy the existing resource and scientific gates.
    child=subprocess.Popen(cmd,env=task_env,cwd=logical/'eval-base-integration',start_new_session=True)
    return monitor_child(child, lambda: check_runtime(root, mount, a.reserve_gib))


if __name__=='__main__':
    raise SystemExit(main())

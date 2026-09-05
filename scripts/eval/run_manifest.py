#!/usr/bin/env python3
"""Durable attempt manifests and Linux process identity (PID alone is insufficient)."""
from __future__ import annotations
import hashlib
import ctypes
import errno
import json
import os
import pathlib
import platform
import subprocess
import tempfile
from datetime import datetime, timezone

STATES = frozenset('PLANNED RUNNING DONE FAILED INTERRUPTED BLOCKED_GPU_BUSY BLOCKED_STORAGE_BUSY CONTAMINATED_EXTERNAL_GPU INVALID_GOLD_GATE'.split())

def now():
    return datetime.now(timezone.utc).isoformat()

def sha256(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def atomic_json(path, value):
    """Write, fsync, replace, then fsync the directory before publishing a state."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,sort_keys=True,indent=2);stream.write('\n')
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)

def read_json(path):
    return json.loads(pathlib.Path(path).read_text())

def process_identity(pid, include_zombies=False):
    """Return boot/PID/starttime plus ancestry; zombies no longer own resources."""
    try:
        fields=pathlib.Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')',1)[1].split()
        if fields[0]=='Z' and not include_zombies:return None
        return dict(pid=int(pid),boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    start_time=int(fields[19]),ppid=int(fields[1]),pgrp=int(fields[2]),session=int(fields[3]))
    except (OSError,ValueError,IndexError):return None

def same_process(left,right):
    return bool(left and right and all(left.get(k)==right.get(k) for k in ('pid','boot_id','start_time')))

def identity_alive(identity):
    return bool(identity and same_process(process_identity(identity['pid']),identity))

def process_table():
    processes={}
    for entry in pathlib.Path('/proc').iterdir():
        if entry.name.isdigit():
            identity=process_identity(int(entry.name))
            if identity:processes[identity['pid']]=identity
    return processes

def owned_processes(child):
    """Track exact identities; an existing recycled session leader is foreign.

    Persisted owned_members keep descendants verifiable after the original
    leader exits. A numeric session ID alone is never an ownership anchor.
    The supplied child record is updated for durable status snapshots.
    """
    if not child:return {}
    boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if child.get('boot_id')!=boot:return {}
    processes=process_table()
    leader=process_identity(child['pid'],include_zombies=True)
    exact_leader=same_process(leader,child)
    reused_leader=bool(leader and not exact_leader)
    known={p['pid']:p for p in child.get('owned_members',[])}
    owned={pid:processes[pid] for pid,p in known.items() if same_process(processes.get(pid),p)}
    if exact_leader and child['pid'] in processes:owned[child['pid']]=processes[child['pid']]
    anchored_session=exact_leader or (not reused_leader and any(p['session']==child['pid'] for p in owned.values()))
    if anchored_session:
        owned.update({pid:p for pid,p in processes.items() if p['session']==child['pid'] and p['start_time']>=child['start_time']})
    while True:
        new={pid:p for pid,p in processes.items() if p['ppid'] in owned}
        if not new.keys()-owned.keys():break
        owned.update(new)
    known.update(owned)
    child['owned_members']=list(known.values())
    return owned

def uncertain_session(child):
    """Unrecorded crash survivors block retries but are never signal targets."""
    if not child or child.get('boot_id')!=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip():return False
    leader=process_identity(child['pid'],include_zombies=True)
    if leader:return False  # Exact leaders are handled above; reused leaders are foreign.
    if owned_processes(child):return False
    return any(p['session']==child['pid'] and p['start_time']>=child['start_time'] for p in process_table().values())

def signal_identity(identity,number):
    """Signal an exact process through a pidfd; never fall back to PID/group kill.

    Conda builds may omit Python's pidfd wrappers, so use libc's exported API.
    If pidfds are unavailable, cleanup fails closed and retains the live identity.
    """
    libc=ctypes.CDLL(None,use_errno=True)
    if not hasattr(libc,'pidfd_open') or not hasattr(libc,'pidfd_send_signal'):
        raise OSError(errno.ENOSYS,'pidfd cleanup unavailable')
    libc.pidfd_open.argtypes=[ctypes.c_int,ctypes.c_uint];libc.pidfd_open.restype=ctypes.c_int
    libc.pidfd_send_signal.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint];libc.pidfd_send_signal.restype=ctypes.c_int
    fd=libc.pidfd_open(identity['pid'],0)
    if fd<0:
        code=ctypes.get_errno()
        if code==errno.ESRCH:return False
        raise OSError(code,os.strerror(code))
    try:
        # The fd pins the opened process. Recheck after opening it so that even
        # reuse during this call cannot redirect a signal to an unrelated PID.
        if not identity_alive(identity):return False
        if libc.pidfd_send_signal(fd,number,None,0)<0:
            code=ctypes.get_errno()
            if code==errno.ESRCH:return False
            raise OSError(code,os.strerror(code))
        return True
    finally:os.close(fd)

def git_snapshot(root):
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=root)
    patch=git('diff','HEAD','--binary')
    return dict(git_sha=git('rev-parse','HEAD').decode().strip(),branch=git('branch','--show-current').decode().strip(),
                dirty_patch_sha256=hashlib.sha256(patch).hexdigest(),dirty_status=git('status','--porcelain').decode())

def environment_snapshot():
    # Do not copy credential-bearing environment variables into public manifests.
    keys=('PATH','CUDA_VISIBLE_DEVICES','CUDA_DEVICE_ORDER','OMP_NUM_THREADS','MKL_NUM_THREADS','LANG','LC_ALL')
    return dict(timestamp=now(),hostname=platform.node(),platform=platform.platform(),python=platform.python_version(),
                environment={k:os.environ[k] for k in keys if k in os.environ},owner=process_identity(os.getpid()))

def set_status(run_dir, attempt, state, **details):
    if state not in STATES:raise ValueError('unknown state: '+state)
    status=dict(schema_version=1,state=state,updated_at=now(),attempt=str(attempt.relative_to(run_dir)),**details)
    atomic_json(attempt/'status.json',status)
    # The root status is the durable current-attempt pointer. DONE is published last.
    atomic_json(run_dir/'status.json',status)
    return status

def confined_file(base, name):
    if not isinstance(name,str) or pathlib.Path(name).is_absolute():raise ValueError('artifact needs relative path')
    candidate=pathlib.Path(base)/name
    resolved=candidate.resolve()
    if not resolved.is_relative_to(pathlib.Path(base).resolve()) or candidate.is_symlink():raise ValueError('artifact escapes attempt')
    return candidate

def artifact_inventory(attempt):
    """List regular in-attempt artifacts without following links or special files.

    Both sealing and export use this inventory. Rejected artifacts are preserved
    on disk for diagnosis, but their targets/payloads are never opened for hashing.
    """
    attempt=pathlib.Path(attempt)
    files={};rejected={}
    def walk_error(error):raise error
    for directory,dirs,names in os.walk(attempt,followlinks=False,onerror=walk_error):
        for name in dirs+names:
            path=pathlib.Path(directory)/name;relative=str(path.relative_to(attempt))
            if path.is_symlink():
                rejected[relative]='symbolic link artifact is forbidden'
                if name in dirs:dirs.remove(name)
                continue
            if path.is_dir():continue
            if not path.is_file():
                rejected[relative]='non-regular artifact is forbidden';continue
            try:confined_file(attempt,relative)
            except ValueError as error:
                rejected[relative]=str(error);continue
            if relative not in {'manifest.json','status.json'}:files[relative]=path
    return files,rejected

def verify_hashes(artifacts):
    if not isinstance(artifacts,list) or not artifacts:raise ValueError('missing frozen artifacts')
    for item in artifacts:
        path=pathlib.Path(item['path'])
        if not path.is_absolute() or not path.is_file() or sha256(path)!=item['sha256']:
            raise ValueError('artifact absent/hash mismatch: '+str(path))

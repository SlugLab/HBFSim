#!/usr/bin/env python3
"""Project-local advisory locks and read-only GPU/storage identity guards.

Locks coordinate this project's runners only. nvidia-smi sampling cannot detect
an external job wholly between samples, and does not reserve the GPU systemwide.
No resets, driver/settings changes, storage payload reads, or foreign kills occur.

Storage authorization schema (all fields required):
  {"schema_version":1,"authorized":true,"authorization_ref":"review/ticket",
   "dedicated":true,"exclusive":true,"access":"read_only",
   "file":"/dedicated-mount/read-only-file",
   "identity":<exact storage_snapshot(file) identity object>}
The manifest itself must be a hashed config artifact in the task registry. The
file must have no write mode bits, live on a dedicated non-root block filesystem,
have no block holders, and no foreign open file descriptors on that filesystem.
This is an explicit authorization and observation boundary, not a sandbox for
untrusted executables. Privileged administrators can still race advisory locks.
"""
from __future__ import annotations
import csv
import fcntl
import hashlib
import io
import json
import os
import pathlib
import stat
import subprocess
from contextlib import ExitStack
from run_manifest import now, process_identity, identity_alive, owned_processes, same_process

RESOURCE_CLASSES=frozenset('CPU_ONLY GPU_SHARED_SAFE GPU_EXCLUSIVE STORAGE_EXCLUSIVE GPU_STORAGE_EXCLUSIVE'.split())
SHARED_SAFE_METRICS=frozenset('checksum_ok stale_reads early_release missing_transactions deadlocks timeouts rewritten_instructions unsupported_instructions bypassed_accesses covered_bytes'.split())

class ResourceBusy(RuntimeError):
    def __init__(self,message,state='BLOCKED_GPU_BUSY'):
        super().__init__(message);self.state=state

class FileLock:
    def __init__(self,path,shared=False,state='BLOCKED_GPU_BUSY'):
        self.path=pathlib.Path(path);self.shared=shared;self.state=state;self.stream=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.stream=self.path.open('a+')
        try:fcntl.flock(self.stream.fileno(),(fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX)|fcntl.LOCK_NB)
        except BlockingIOError:
            self.stream.close();self.stream=None
            raise ResourceBusy('project resource lock occupied: '+self.path.name,self.state)
        return self
    def __exit__(self,*unused):
        if self.stream:
            fcntl.flock(self.stream.fileno(),fcntl.LOCK_UN);self.stream.close();self.stream=None

def gpu_snapshot(gpu_uuid):
    snapshot=dict(timestamp=now(),available=False,gpu_uuid=gpu_uuid,processes=[])
    commands=[['nvidia-smi','-i',gpu_uuid,'--query-gpu=uuid,name,driver_version,memory.total,utilization.gpu','--format=csv,noheader,nounits'],
              ['nvidia-smi','--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory','--format=csv,noheader,nounits']]
    try:
        outputs=[]
        for argv in commands:
            result=subprocess.run(argv,capture_output=True,text=True,timeout=10,check=False)
            if result.returncode:
                snapshot['error']=result.stderr.strip() or result.stdout.strip();return snapshot
            outputs.append(result.stdout)
        info=list(csv.reader(io.StringIO(outputs[0]),skipinitialspace=True))
        if len(info)!=1 or len(info[0])!=5 or info[0][0]!=gpu_uuid:
            snapshot['error']='selected GPU identity missing/mismatched';return snapshot
        snapshot['device']=dict(zip(('uuid','name','driver_version','memory_total_mib','utilization_percent'),info[0]))
        for fields in csv.reader(io.StringIO(outputs[1]),skipinitialspace=True):
            if not fields:continue
            if len(fields)!=4:raise ValueError('unparseable nvidia-smi compute list')
            if fields[0]==gpu_uuid:
                pid=int(fields[1]);snapshot['processes'].append(dict(pid=pid,name=fields[2],used_memory_mib=fields[3],identity=process_identity(pid)))
        snapshot['available']=True
    except (OSError,ValueError,subprocess.TimeoutExpired) as error:snapshot['error']=str(error)
    return snapshot

def backing_disks(device,seen=None):
    """Resolve partitions and device-mapper/LVM ancestors to physical disks."""
    device=pathlib.Path(device).resolve();seen=set() if seen is None else seen
    if device in seen:raise ValueError('cyclic/ambiguous block backing')
    seen.add(device)
    if (device/'partition').exists():return backing_disks(device.parent,seen)
    slaves=list((device/'slaves').glob('*'))
    if not slaves:return {device}
    leaves=set()
    for child in slaves:leaves.update(backing_disks(child,set(seen)))
    return leaves

def disk_device_numbers(disk):
    """Physical disk and all partition numbers share one exclusivity boundary."""
    disk=pathlib.Path(disk)
    devices={disk}
    devices.update(path for path in disk.iterdir() if (path/'partition').is_file())
    numbers=set()
    for device in devices:
        major,minor=(device/'dev').read_text().strip().split(':')
        numbers.add(os.makedev(int(major),int(minor)))
    return numbers

def storage_snapshot(file):
    """Identity and open-descriptor discovery only; never open/read file payload."""
    path=pathlib.Path(file)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():raise ValueError('storage file must be absolute regular non-symlink')
    info=path.stat()
    if info.st_mode & 0o222:raise ValueError('storage file must have read-only mode bits')
    if info.st_dev==pathlib.Path('/').stat().st_dev:raise ValueError('shared root filesystem is not dedicated storage')
    major_minor=f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'
    block=pathlib.Path('/sys/dev/block')/major_minor
    if not block.exists():raise ValueError('storage backing has no verified block identity')
    device=block.resolve()
    # Resolve partition backing to the containing disk, to catch shared root partitions.
    disk=device.parent if (device/'partition').exists() else device
    root_info=pathlib.Path('/').stat()
    root_block=pathlib.Path(f'/sys/dev/block/{os.major(root_info.st_dev)}:{os.minor(root_info.st_dev)}')
    if not root_block.exists():raise ValueError('root block backing cannot be verified; exclusivity unproven')
    if backing_disks(device)&backing_disks(root_block):raise ValueError('shared root disk is not exclusive storage')
    if backing_disks(device)!={disk}:raise ValueError('stacked storage backing requires a separate reviewed collector')
    disk_numbers=disk_device_numbers(disk)
    holders=list((device/'holders').glob('*'))+list((disk/'holders').glob('*'))
    if holders:raise ValueError('storage backing has block holders')
    result=subprocess.run(['findmnt','--json','--target',str(path),'--output','SOURCE,TARGET,FSTYPE,MAJ:MIN'],capture_output=True,text=True,timeout=10,check=True)
    mounts=json.loads(result.stdout).get('filesystems',[])
    if len(mounts)!=1 or mounts[0].get('maj:min')!=major_minor or mounts[0].get('target')=='/':raise ValueError('storage mount identity ambiguous')
    mounted=[]
    for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines():
        fields=line.split();major,minor=map(int,fields[2].split(':'))
        if os.makedev(major,minor) in disk_numbers:mounted.append(fields[2])
    if mounted!=[major_minor]:raise ValueError('storage disk has other mounts/partitions; exclusivity unproven')
    identity=dict(file=str(path.resolve()),file_inode=info.st_ino,file_size=info.st_size,major_minor=major_minor,
                  sysfs_path=str(device),disk_sysfs_path=str(disk),mount=mounts[0])
    for name in ('serial','model','wwid'):
        candidates=[disk/'device'/name,disk/name]
        identity[name]=next((p.read_text().strip() for p in candidates if p.is_file()),'')
    if not identity['serial'] and not identity['wwid']:raise ValueError('storage serial/WWID identity unavailable')
    foreign=[]
    for proc in pathlib.Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name)==os.getpid():continue
        try:
            for fd in (proc/'fd').iterdir():
                try:
                    opened=fd.stat()
                    if opened.st_dev in disk_numbers or (stat.S_ISBLK(opened.st_mode) and opened.st_rdev in disk_numbers):
                        foreign.append(int(proc.name));break
                except FileNotFoundError:continue
        except FileNotFoundError:continue
        except PermissionError:raise ValueError('cannot establish storage exclusivity: inaccessible process descriptors')
    return dict(identity=identity,foreign_pids=foreign)

class ResourceGuard:
    def __init__(self,results,attempt,task,gpu_probe=None,storage_probe=None,project_root=None):
        self.results=pathlib.Path(results);self.attempt=pathlib.Path(attempt);self.task=task
        self.lock_root=pathlib.Path(project_root or pathlib.Path(__file__).resolve().parents[2])/'results'/'locks'
        self.resource_class=task['resource_class'];self.gpu_probe=gpu_probe or gpu_snapshot;self.storage_probe=storage_probe or storage_snapshot
        self.stack=ExitStack();self.child=None;self.started=False;self.snapshots=[];self.known_owned={}
        self.has_gpu=self.resource_class.startswith('GPU_')
        self.has_storage=self.resource_class in {'STORAGE_EXCLUSIVE','GPU_STORAGE_EXCLUSIVE'}
        self.storage=None
    def _gpu(self,phase):
        self.known_owned.update(owned_processes(self.child))
        snapshot=self.gpu_probe(self.task.get('gpu_uuid',''))
        snapshot=dict(snapshot,phase=phase,timestamp=now());self.snapshots.append(snapshot)
        with (self.attempt/'raw.gpu.jsonl').open('a') as stream:
            stream.write(json.dumps(snapshot,sort_keys=True)+'\n');stream.flush();os.fsync(stream.fileno())
        self.known_owned.update(owned_processes(self.child))
        foreign=[]
        for proc in snapshot.get('processes',[]):
            observed=proc.get('identity') or process_identity(int(proc['pid']))
            known=self.known_owned.get(int(proc['pid']))
            matches=bool(observed and known and all(observed.get(k)==known.get(k) for k in ('pid','boot_id','start_time')))
            # The observed /proc identity remains valid if a short-lived owned
            # descendant exits between the compute-list sample and this check.
            exact_leader=bool(self.child and same_process(process_identity(self.child['pid'],include_zombies=True),self.child))
            same_session=bool(observed and exact_leader and observed.get('boot_id')==self.child['boot_id']
                and observed.get('session')==self.child['pid'] and observed.get('start_time',-1)>=self.child['start_time'])
            if not matches and not same_session:foreign.append(proc)
        if not snapshot.get('available') or snapshot.get('gpu_uuid')!=self.task['gpu_uuid'] or foreign:
            state='CONTAMINATED_EXTERNAL_GPU' if self.started else 'BLOCKED_GPU_BUSY'
            raise ResourceBusy(snapshot.get('error') or ('foreign GPU processes: '+str([p['pid'] for p in foreign])),state)
    def _storage(self,phase='preflight'):
        try:
            if not self.storage:
                path=self.task.get('storage_manifest')
                if not path:raise ValueError('missing frozen storage authorization manifest')
                self.storage=json.loads(pathlib.Path(path).read_text())
                for k,v in dict(schema_version=1,authorized=True,dedicated=True,exclusive=True,access='read_only').items():
                    if self.storage.get(k)!=v:raise ValueError('storage authorization field missing/invalid: '+k)
                if not self.storage.get('authorization_ref'):raise ValueError('missing storage authorization reference')
            snapshot=self.storage_probe(self.storage['file'])
            with (self.attempt/'raw.storage.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(snapshot,timestamp=now(),phase=phase),sort_keys=True)+'\n');stream.flush();os.fsync(stream.fileno())
            if snapshot['identity']!=self.storage['identity']:raise ValueError('frozen storage identity changed')
            owned=owned_processes(self.child)
            if set(snapshot['foreign_pids'])-owned.keys():raise ValueError('foreign storage users observed')
            return snapshot
        except (OSError,ValueError,KeyError,subprocess.SubprocessError) as error:
            raise ResourceBusy(str(error),'BLOCKED_STORAGE_BUSY') from error
    def __enter__(self):
        try:
            if self.has_gpu:
                gpu=self.task.get('gpu_uuid','')
                if not gpu.startswith('GPU-'):raise ResourceBusy('selected physical GPU UUID required')
                key=hashlib.sha256(gpu.encode()).hexdigest()[:24]
                self.stack.enter_context(FileLock(self.lock_root/('gpu-'+key+'.lock'),shared=self.resource_class=='GPU_SHARED_SAFE'))
                self._gpu('preflight')
            if self.has_storage:
                snapshot=self._storage()
                key=hashlib.sha256(snapshot['identity']['disk_sysfs_path'].encode()).hexdigest()[:24]
                self.stack.enter_context(FileLock(self.lock_root/('storage-'+key+'.lock'),state='BLOCKED_STORAGE_BUSY'))
                self._storage()
            return self
        except BaseException:
            self.stack.close();raise
    def check(self,phase='periodic'):
        if self.has_gpu:self._gpu(phase)
        if self.has_storage:self._storage(phase)
    def __exit__(self,*unused):
        self.stack.close()

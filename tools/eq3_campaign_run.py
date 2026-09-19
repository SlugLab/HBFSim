"""Operational limits/measurements only; never modify bound science or retry."""
import argparse, hashlib, json, os, resource, signal, subprocess, sys, time
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('mode',choices=['solve','observe']);p.add_argument('--root',type=Path,required=True);p.add_argument('--point',type=Path,required=True)
a=p.parse_args(); root=a.root.resolve(); plan=a.point.resolve()
code=root/'eq3_thermal/worktree'; launch=json.loads((plan/'launch.json').read_text());run=root/launch['output']['path']; derived=plan/'derived'
receipt=plan/(a.mode+'-operational.json')
if receipt.exists(): raise SystemExit('No operational retry/overwrite allowed')
gib=1024**3
resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,12*gib))
cpu=min(os.sched_getaffinity(0));os.sched_setaffinity(0,{cpu})
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','BLIS_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):os.environ[key]='1'
os.environ['CUDA_VISIBLE_DEVICES']=''
contract=json.loads((plan/'child.json').read_text())
if a.mode=='solve':
    argv=[sys.executable,'-B',str(code/'tools/eq3_layered_launch.py'),'run','--root',str(root),'--code-root',str(code),'--manifest',str(plan/'experiment_manifest.json'),'--launch',str(plan/'launch.json'),'--approval',str(root/'eq3_thermal/plans/campaign-v1/authorization.json')]
else:
    if not (run/'DONE.json').exists():raise SystemExit('No completed solver receipt; diagnose only')
    argv=[sys.executable,'-B',str(code/'tools/eq3_layered_observe.py'),'--run-dir',str(run),'--generated',str(root/launch['command']['cwd']),'--output',str(derived)]

if a.mode=='observe' and contract['family'] in ('rc','rc_pilot'):
    argv+=['--kind','rc']

def bytes_in(path):return sum(x.stat().st_size for x in path.rglob('*') if x.is_file())
initial_task_bytes=bytes_in(root/'eq3_thermal')
if initial_task_bytes>=20*gib:raise SystemExit('Insufficient conservative task disk allowance')
def tree():
    nodes={}
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            d={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in path.read_text().splitlines() if ':' in line}
            nodes[int(path.parent.name)]=(int(d['PPid']),int(d.get('VmRSS','0 kB').split()[0]))
        except (OSError,ValueError,KeyError):continue
    ids={os.getpid()}
    while True:
        new=ids|{pid for pid,(parent,rss) in nodes.items() if parent in ids}
        if new==ids:break
        ids=new
    return ids,sum(nodes.get(pid,(0,0))[1] for pid in ids)

def limits():
    # Parent launcher stays <=512 MiB; its already-bound child restriction raises
    # only solver address-space to12GiB. Observe is sequential and <=12GiB.
    if a.mode=='observe':resource.setrlimit(resource.RLIMIT_AS,(12*gib,12*gib))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))

started=time.monotonic(); peak=0;samples=0;reason=None
with (plan/(a.mode+'-stdout.log')).open('xb') as out,(plan/(a.mode+'-stderr.log')).open('xb') as err:
    child=subprocess.Popen(argv,stdout=out,stderr=err,preexec_fn=limits)
    while True:
        ids,rss=tree();peak=max(peak,rss);samples+=1
        size=bytes_in(plan)
        if rss>16*gib/1024:reason='TASK_RSS_LIMIT'
        if size>4*gib:reason='COMBINED_OUTPUT_LIMIT'
        if bytes_in(root/'eq3_thermal')>20*gib:reason='TASK_DISK_LIMIT'
        if a.mode=='observe' and time.monotonic()-started>600:reason='POSTPROCESS_WATCHDOG'
        if reason:
            for pid in ids-{os.getpid()}:
                try:os.kill(pid,signal.SIGKILL)
                except ProcessLookupError:pass
            child.wait();break
        if child.poll() is not None:break
        time.sleep(.5)
elapsed=time.monotonic()-started; usage=resource.getrusage(resource.RUSAGE_CHILDREN)
result={'mode':a.mode,'argv':argv,'exit_code':child.returncode,'stop_reason':reason,'wall_s':elapsed,
        'child_user_cpu_s':usage.ru_utime,'child_system_cpu_s':usage.ru_stime,
        'child_rusage_maxrss_kib':usage.ru_maxrss,'rusage_semantics':'OS maximum child-process RSS, not simultaneous aggregate',
        'sampled_task_rss_peak_kib':peak,'sample_count':samples,'sampling_semantics':'0.5s descendant-tree RSS sum including supervisor; sampled lower bound, shared pages may be double counted',
        'affinity_cpu':cpu,'affinity_is_operational_not_scientific':True,
        'initial_task_disk_bytes':initial_task_bytes,'plan_output_bytes':bytes_in(plan),
        'task_disk_bytes_after':bytes_in(root/'eq3_thermal'),'task_disk_accounting':'entire eq3_thermal tree conservative, includes historical files',
        'task_memory_mechanism':'serial phases, <=512MiB launcher + <=12GiB single solver + <=512MiB supervisor; descendant RSS monitor16GiB; no cgroup changes',
        'disk_mechanism':'bound launcher4GiB raw monitor plus outer4GiB combined plan/raw/derived monitor; sampled not filesystem quota',
        'supervisor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
with receipt.open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result));sys.exit(0 if child.returncode==0 and not reason else 1)

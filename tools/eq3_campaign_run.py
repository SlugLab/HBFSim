"""Operational limits/measurements only; never modify bound science or retry."""
import argparse, hashlib, json, os, resource, shutil, signal, subprocess, sys, time
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('mode',choices=['solve','observe','observe-audit']);p.add_argument('--root',type=Path,required=True);p.add_argument('--point',type=Path,required=True)
a=p.parse_args(); root=a.root.resolve(); plan=a.point.resolve()
if plan!=root and root not in plan.parents:raise SystemExit('Point path escapes task root')
launch=json.loads((plan/'launch.json').read_text());contract=json.loads((plan/'child.json').read_text());manifest=json.loads((plan/'experiment_manifest.json').read_text())
def local(value,must_file=False):
    path=(root/value).resolve()
    if path!=root and root not in path.parents:raise SystemExit('Bound path escapes task root')
    if must_file and not path.is_file():raise SystemExit('Bound file unavailable')
    return path
code=local(manifest['execution_context']['code_root']);run=local(launch['output']['path']); derived=plan/'derived'
if not code.is_dir():raise SystemExit('Bound code root unavailable')
if a.mode=='observe-audit':derived=plan/'derived-invalid-domain-audit'
receipt=plan/(a.mode+'-operational.json')
if receipt.exists(): raise SystemExit('No operational retry/overwrite allowed')
gib=1024**3
authorization_path=local(manifest.get('execution_context',{}).get('authorization_path','eq3_thermal/plans/campaign-v1/authorization.json'),True)
authorization=json.loads(authorization_path.read_text());scope_path=local(authorization['scope_path'],True)
if hashlib.sha256(scope_path.read_bytes()).hexdigest()!=authorization['scope_sha256']:raise SystemExit('Bound stage scope changed')
scope=json.loads(scope_path.read_text())
if contract['stage_id']!=scope['stage_id']:raise SystemExit('Child stage differs from bound scope')
if contract['limits']!=scope['limits']:raise SystemExit('Child resource limits differ from bound stage scope')
if scope.get('resource_policy')=='PER_EXPERIMENT_USER_CONFIRMED':
    limits=scope['limits'];process_ram=limits['process_ram_gib'];task_ram=limits['task_ram_gib'];watchdog=limits['watchdog_s']
    point_disk=limits['point_disk_gib'];task_disk=limits['task_disk_gib'];min_free=limits['min_free_disk_gib']
else:
    process_ram,task_ram,watchdog,point_disk,task_disk,min_free=12,16,600,4,20,0
resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,int(process_ram*gib)))
cpu=min(os.sched_getaffinity(0));os.sched_setaffinity(0,{cpu})
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','BLIS_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):os.environ[key]='1'
os.environ['CUDA_VISIBLE_DEVICES']=''
for dependency in manifest['dependencies']:
    if dependency['logical_id']=='native-field-codec':
        library=root/dependency['path']
        if hashlib.sha256(library.read_bytes()).hexdigest()!=dependency['sha256']:
            raise SystemExit('Bound codec library changed')
        os.environ['EQ3_CAMPAIGN_NATIVECODEC']=str(library)
if a.mode=='solve':
    argv=[sys.executable,'-B',str(code/'tools/eq3_layered_launch.py'),'run','--root',str(root),'--code-root',str(code),'--manifest',str(plan/'experiment_manifest.json'),'--launch',str(plan/'launch.json'),'--approval',str(authorization_path)]
else:
    if not (run/'DONE.json').exists():raise SystemExit('No completed solver receipt; diagnose only')
    argv=[sys.executable,'-B',str(code/'tools/eq3_layered_observe.py'),'--run-dir',str(run),'--generated',str(root/launch['command']['cwd']),'--output',str(derived)]

if a.mode!='solve' and contract['family'] in ('rc','rc_pilot'):
    argv+=['--kind','rc']
if a.mode=='observe-audit':argv+=['--audit-invalid-domain']

def bytes_in(path):return sum(x.stat().st_size for x in path.rglob('*') if x.is_file())
initial_task_bytes=bytes_in(root/'eq3_thermal')
if initial_task_bytes>=task_disk*gib or shutil.disk_usage(root).free<min_free*gib:raise SystemExit('Insufficient conservative task disk allowance or free-space reserve')
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
    if a.mode!='solve':resource.setrlimit(resource.RLIMIT_AS,(int(process_ram*gib),int(process_ram*gib)))
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))

collector=root/'tools/collect_experiment_metadata.py'
metadata=plan/'metadata.json'
if a.mode=='solve':
    binding=next(d for d in manifest['dependencies'] if d['logical_id']=='metadata-collector')
    if hashlib.sha256(collector.read_bytes()).hexdigest()!=binding['sha256']:
        raise SystemExit('Metadata collector changed')
    inventory=[sys.executable,str(collector),'start','--metadata',str(metadata),
        '--project-root',str(code),'--experiment-id',manifest['experiment_id'],
        '--run-id',contract['point_id'],'--environment-id',manifest['execution_context']['environment_id'],
        '--config',str(plan/'experiment_manifest.json'),'--workload',contract['trace'],
        '--input-path',launch['command']['cwd'],'--output-path',launch['output']['path'],
        '--design-version','DESIGN_FREEZE-v3','--reference-version','NEW_REFERENCE',
        '--',*argv]
    with (plan/'metadata-start.log').open('xb') as log:
        subprocess.run(inventory,stdout=log,stderr=subprocess.STDOUT,check=True)

started=time.monotonic(); peak=0;samples=0;reason=None
with (plan/(a.mode+'-stdout.log')).open('xb') as out,(plan/(a.mode+'-stderr.log')).open('xb') as err:
    child=subprocess.Popen(argv,stdout=out,stderr=err,preexec_fn=limits)
    while True:
        ids,rss=tree();peak=max(peak,rss);samples+=1
        size=bytes_in(plan)
        if rss>task_ram*gib/1024:reason='TASK_RSS_LIMIT'
        if size>point_disk*gib:reason='COMBINED_OUTPUT_LIMIT'
        if bytes_in(root/'eq3_thermal')>task_disk*gib or shutil.disk_usage(root).free<min_free*gib:reason='TASK_DISK_LIMIT'
        if time.monotonic()-started>watchdog:reason='WATCHDOG'
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
        'resource_policy':scope.get('resource_policy','LEGACY_FIXED'),'bound_limits':scope['limits'],
        'task_memory_mechanism':f'serial phases, <=512MiB launcher + <={process_ram}GiB single process; descendant RSS monitor{task_ram}GiB; no cgroup changes',
        'disk_mechanism':f'bound launcher{point_disk}GiB raw monitor plus outer{point_disk}GiB combined monitor; task{task_disk}GiB and free reserve{min_free}GiB; sampled not filesystem quota',
        'supervisor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
with receipt.open('x') as f:json.dump(result,f,indent=2)
if a.mode=='solve':
    with (plan/'metadata-finish.log').open('xb') as log:
        subprocess.run([sys.executable,str(collector),'finish','--metadata',str(metadata),
            '--exit-code',str(child.returncode),'--raw-log',str(plan/'solve-stdout.log'),
            '--raw-data',str(run)],stdout=log,stderr=subprocess.STDOUT,check=True)
print(json.dumps(result));sys.exit(0 if child.returncode==0 and not reason else 1)

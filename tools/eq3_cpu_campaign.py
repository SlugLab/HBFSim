"""Small frozen-command runner for user-authorized P3/P4 engineering work.

No scheduler, automatic retries, solver replacement or approval synthesis.
"""
import argparse,hashlib,json,os,resource,signal,subprocess,sys,time
from pathlib import Path

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def check_manifest(root,manifest):
    campaign=root/'eq3_thermal/plans/p2-p3-p4-campaign-v1'
    scope=json.loads((campaign/'campaign_manifest.json').read_text())
    if manifest['campaign_id']!=scope['campaign_id'] or manifest['authority']!='AUTHORIZED_BY_USER_CAMPAIGN_SCOPE':raise ValueError('campaign authority mismatch')
    for key,filekey,hashkey in [('taskbook','taskbook','sha256'),('adoption','user_adoption','adoption_sha256')]:
        if sha(campaign/scope['source'][filekey])!=scope['source'][hashkey]:raise ValueError('actual user source changed: '+key)
    if sha(campaign/'campaign_manifest.json')!=manifest['campaign_scope_sha256']:raise ValueError('scope binding changed')
    if manifest['phase'] not in ('P3','P4') or manifest['kind'] not in ('build','fixed_test','pilot','analysis'):raise ValueError('out of CPU engineering scope')
    if manifest['evidence']!='ENGINEERING_FIXTURE':raise ValueError('no physical model acceptance inherited')
    if manifest['limits']!=scope['limits']:raise ValueError('resource envelope changed')
    for p,h in manifest['artifacts'].items():
        path=(root/p).resolve()
        if root not in path.parents or sha(path)!=h:raise ValueError('artifact changed or outside root: '+p)
    for d in manifest['dependencies']:
        path=root/d['path']
        if sha(path)!=d['sha256'] or json.loads(path.read_text())['status'] not in d['allowed_status']:raise ValueError('dependency not satisfied')
    if manifest['phase']=='P4' and not any(x['id']=='P3_FIXED_PASS' for x in manifest['dependencies']):raise ValueError('P4 requires corresponding P3 path pass')
    if manifest['kind']=='pilot' and not any(x['id']=='P4_FIXED_PASS' for x in manifest['dependencies']):raise ValueError('pilot requires functional checks')
    code=root/'eq3_thermal/worktree'
    revision=subprocess.check_output(['git','-C',str(code),'rev-parse','HEAD'],text=True).strip()
    diff=subprocess.check_output(['git','-C',str(code),'diff','--binary','HEAD'])
    if revision!=manifest['source_revision'] or hashlib.sha256(diff).hexdigest()!=manifest['dirty_diff_sha256']:raise ValueError('code revision changed')
    return scope

def disk(path):return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
def descendants():
    rows={}
    for p in Path('/proc').glob('[0-9]*/status'):
        try:
            d={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in p.read_text().splitlines() if ':' in line}
            rows[int(p.parent.name)]=(int(d['PPid']),int(d.get('VmRSS','0 kB').split()[0]))
        except (OSError,ValueError,KeyError):pass
    ids={os.getpid()}
    while True:
        new=ids|{pid for pid,(ppid,rss) in rows.items() if ppid in ids}
        if ids==new:break
        ids=new
    return ids,sum(rows.get(pid,(0,0))[1] for pid in ids)

def run(root,plan):
    manifest=json.loads((plan/'manifest.json').read_text());check_manifest(root,manifest)
    if (plan/'result.json').exists() or (plan/'stdout.log').exists():raise ValueError('no retry/overwrite; new ID required')
    gib=1024**3
    if disk(root/'eq3_thermal')>=20*gib:raise ValueError('task disk envelope exhausted')
    resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,12*gib));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    cpu=min(os.sched_getaffinity(0));os.sched_setaffinity(0,{cpu})
    for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','BLIS_NUM_THREADS','GOTO_NUM_THREADS'):os.environ[k]='1'
    os.environ['CUDA_VISIBLE_DEVICES']=''
    argv=[x.replace('{root}',str(root)).replace('{point}',str(plan)) for x in manifest['argv']]
    cwd=Path(manifest['cwd'].replace('{root}',str(root)).replace('{point}',str(plan)))
    tracked=[Path(x.replace('{root}',str(root)).replace('{point}',str(plan))) for x in manifest.get('additional_output_dirs',[])]
    for path in [cwd,*tracked]:
        if root not in path.resolve().parents:raise ValueError('operational path outside workspace')
    collector=root/'tools/collect_experiment_metadata.py';metadata=plan/'metadata.json'
    with (plan/'metadata-start.log').open('xb') as log:
        subprocess.run([sys.executable,str(collector),'start','--metadata',str(metadata),'--project-root',str(root/'eq3_thermal/worktree'),
          '--experiment-id',manifest['campaign_id'],'--run-id',manifest['point_id'],'--environment-id',manifest['environment_id'],
          '--config',str(plan/'manifest.json'),'--workload',manifest['purpose'],'--input-path',str(plan/'manifest.json'),
          '--output-path',str(plan),'--design-version','engineering-v1','--reference-version','ENGINEERING_FIXTURE','--',*argv],stdout=log,stderr=subprocess.STDOUT,check=True)
    def limits():resource.setrlimit(resource.RLIMIT_AS,(12*gib,12*gib))
    started=time.monotonic();peak=0;reason=None
    with (plan/'stdout.log').open('xb') as out,(plan/'stderr.log').open('xb') as err:
        child=subprocess.Popen(argv,cwd=cwd,stdout=out,stderr=err,preexec_fn=limits)
        while True:
            ids,rss=descendants();peak=max(peak,rss)
            if rss>16*gib/1024:reason='TASK_RSS_LIMIT'
            if disk(plan)+sum(disk(p) for p in tracked)>4*gib:reason='POINT_DISK_LIMIT'
            if disk(root/'eq3_thermal')>20*gib:reason='TASK_DISK_LIMIT'
            if time.monotonic()-started>600:reason='WATCHDOG'
            if reason:
                for pid in ids-{os.getpid()}:
                    try:os.kill(pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                child.wait();break
            if child.poll() is not None:break
            time.sleep(.5)
    r=resource.getrusage(resource.RUSAGE_CHILDREN)
    result={'status':'PASS' if child.returncode==0 and reason is None else 'FAILED','point_id':manifest['point_id'],
      'evidence':'ENGINEERING_FIXTURE','physical_acceptance':False,'exit_code':child.returncode,'stop_reason':reason,
      'wall_s':time.monotonic()-started,'child_user_cpu_s':r.ru_utime,'child_system_cpu_s':r.ru_stime,
      'max_child_rss_kib':r.ru_maxrss,'sampled_descendant_rss_peak_kib':peak,
      'memory_semantics':'OS peak individual child versus sampled aggregate lower bound; shared pages may double count',
      'point_bytes':disk(plan)+sum(disk(p) for p in tracked),'task_retained_bytes':disk(root/'eq3_thermal'),
      'disk_semantics':'conservative whole tree logical file sizes; cumulative physical writes UNKNOWN',
      'cpu_affinity':[cpu],'manifest_sha256':sha(plan/'manifest.json'),'runner_sha256':sha(__file__),
      'stdout_sha256':sha(plan/'stdout.log'),'stderr_sha256':sha(plan/'stderr.log')}
    with (plan/'result.json').open('x') as f:json.dump(result,f,indent=2)
    with (plan/'metadata-finish.log').open('xb') as log:
        subprocess.run([sys.executable,str(collector),'finish','--metadata',str(metadata),'--exit-code',str(child.returncode),
          '--raw-log',str(plan/'stdout.log'),'--raw-data',str(plan)],stdout=log,stderr=subprocess.STDOUT,check=True)
    print(json.dumps(result));return 0 if result['status']=='PASS' else 1

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--point',type=Path,required=True)
    a=p.parse_args();sys.exit(run(a.root.resolve(),a.point.resolve()))

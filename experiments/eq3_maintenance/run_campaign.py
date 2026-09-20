#!/usr/bin/env python3
"""Execute an explicit frozen conditional campaign in owned independent processes."""
import argparse,concurrent.futures,datetime,json,os,pathlib,shutil,subprocess,time
p=argparse.ArgumentParser();p.add_argument('--stage',type=pathlib.Path,required=True);p.add_argument('--artifact-root',type=pathlib.Path,required=True);p.add_argument('--binary',type=pathlib.Path,required=True);p.add_argument('--thermal-binary',type=pathlib.Path,required=True);a=p.parse_args()
a.stage=a.stage.resolve();root=pathlib.Path(__file__).resolve().parents[2];here=pathlib.Path(__file__).resolve().parent
lock_path=a.stage/'MAIN_CAMPAIGN_LOCK_v1.json';lock=json.loads(lock_path.read_text());cpus=sorted(os.sched_getaffinity(0))[:3]
if len(cpus)!=3:raise RuntimeError('frozen3 independent CPUs unavailable')
started=time.monotonic();status=[]
queue=[]
for workload in ('W1','W2'):
 for qi,mode in enumerate(lock['topologies'],1):
  for scene,config in lock['scenarios'].items():
   for pi,policy in enumerate(lock['policies']):
    queue.append(dict(id=f'MAIN-{workload}-Q{qi}-{scene}-P{pi}',workload=workload,mode=mode,scene=scene,policy=policy,config=config,cpu=cpus[pi]))
queue_path=a.stage/'MAIN_QUEUE_v1.json'
if queue_path.exists():raise RuntimeError('create-only campaign; existing queue requires explicit resume review')
queue_path.write_text(json.dumps(dict(created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),points=queue,lock=str(lock_path)),indent=2))
def run_point(row):
 if time.monotonic()-started>lock['resources']['campaign_wall_s']:return dict(row,status='NOT_STARTED_CAMPAIGN_WALL')
 if shutil.disk_usage(a.stage).free<100*1024**3:return dict(row,status='NOT_STARTED_DISK_RESERVE')
 c=row['config'];out=a.stage/'points'/row['id'];model=(a.stage/'points/Q4-THERMAL-STATIC01/generated_2mm' if row['mode']=='all_hbf_direct' else a.artifact_root/'generated/campaign-RC2MM-train')
 cmd=['taskset','-c',str(row['cpu']),'python3',str(here/'launch_point.py'),'--receipt',str(out)+'-launch','--wall-s','600','python3',str(here/'run_point.py'),'--binary',str(a.binary.resolve()),'--thermal-binary',str(a.thermal_binary.resolve()),'--model-dir',str(model.resolve()),'--output',str(out),'--artifact-root',str(a.artifact_root.resolve()),'--mode',row['mode'],'--workload',row['workload'],'--policy',row['policy'],'--active-s',str(c['active_s']),'--recovery-s',str(c['recovery_s']),'--weight-model',c['weight_model'],'--scan-period-s',str(lock['scan_period_s']),'--maintenance','--maintenance-due-s',str(lock['maintenance']['due_s']),'--campaign-lock',str(lock_path)]
 begin=time.monotonic()
 with (a.stage/(row['id']+'-driver.log')).open('w') as f:r=subprocess.run(cmd,cwd=root,stdout=f,stderr=subprocess.STDOUT)
 return dict(row,status='COMPLETED' if r.returncode==0 else 'FAILED',exit_code=r.returncode,wall_s=time.monotonic()-begin,point=str(out))
# Each wave has same workload/topology/scenario,3policies, independent singletons.
for start in range(0,len(queue),3):
 wave=queue[start:start+3]
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  for answer in pool.map(run_point,wave):
   status.append(answer)
   (a.stage/'MAIN_STATUS_v1.json').write_text(json.dumps(dict(elapsed_s=time.monotonic()-started,points=status),indent=2))
   print(json.dumps({k:answer[k] for k in ('id','status','wall_s') if k in answer}),flush=True)
 if any(r['status']!='COMPLETED' for r in status[-3:]):
  (a.stage/'MAIN_PAUSED_v1.json').write_text(json.dumps(dict(reason='wave failure; requires causal review, independent work continues',remaining=[r['id'] for r in queue[start+3:]]),indent=2));break
 if sum(p.stat().st_size for p in (a.stage/'points').rglob('*') if p.is_file() and p.parts[-2].startswith('MAIN-'))>32*1024**3:
  raise RuntimeError('campaign output budget reached')
else:(a.stage/'MAIN_DONE_v1.json').write_text(json.dumps(dict(completed=len(status),wall_s=time.monotonic()-started),indent=2))

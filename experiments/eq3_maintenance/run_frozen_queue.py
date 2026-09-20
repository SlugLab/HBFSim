#!/usr/bin/env python3
"""Run a frozen explicit queue; isolated engines, finite resource guards, no sweep inference."""
import argparse,concurrent.futures,hashlib,json,os,pathlib,shutil,subprocess,time
p=argparse.ArgumentParser();p.add_argument('--queue',type=pathlib.Path,required=True);p.add_argument('--root',type=pathlib.Path,required=True);a=p.parse_args();a.queue=a.queue.resolve();a.root=a.root.resolve();stage=a.queue.parent;queue=json.loads(a.queue.read_text());lock=json.loads((stage/'ENGINEERING_LOCK.json').read_text());began=time.monotonic();status=[]
if (stage/'STATUS.json').exists():raise RuntimeError('create-only execution; explicit reviewed resume/new IDs required')
def run(row):
 target=stage/'points'/row['id'];cmd=row['command'];t=time.monotonic()
 with (stage/(row['id']+'-driver.log')).open('w') as f:proc=subprocess.run(cmd,cwd=a.root,stdout=f,stderr=subprocess.STDOUT)
 state='COMPLETED' if proc.returncode==0 else 'FAILED'
 if (target/'NOT_STARTED.json').exists():state='NOT_STARTED'
 return dict(row,status=state,exit_code=proc.returncode,wall_s=time.monotonic()-t,point=str(target))
for wave in queue['waves']:
 if time.monotonic()-began>lock['resources']['campaign_wall_s']:raise RuntimeError('bounded campaign wall reached before next wave')
 for relative,expected in lock['consumer_sha256'].items():
  if hashlib.sha256((a.root/relative).read_bytes()).hexdigest()!=expected:raise RuntimeError('frozen consumer changed: '+relative)
 if shutil.disk_usage(stage).free<100*1024**3:raise RuntimeError('host disk reserve before wave')
 if sum(q.stat().st_size for q in (stage/'points').rglob('*') if q.is_file())>32*1024**3:raise RuntimeError('own retained campaign budget reached')
 with concurrent.futures.ThreadPoolExecutor(max_workers=wave['concurrency']) as pool:
  for item in pool.map(run,wave['points']):
   status.append(item);print(json.dumps({k:item[k] for k in ('id','status','wall_s')}),flush=True)
   (stage/'STATUS.json').write_text(json.dumps(dict(elapsed_s=time.monotonic()-began,points=status),indent=2))
 if any(v['status']!='COMPLETED' for v in status[-len(wave['points']):]):
  (stage/'PAUSED.json').write_text(json.dumps(dict(reason='actual failure; preserve and diagnose before affected continuation',wave=wave['id'],completed=len([x for x in status if x['status']=='COMPLETED'])),indent=2));break
else:(stage/'DONE.json').write_text(json.dumps(dict(completed=len(status),wall_s=time.monotonic()-began),indent=2))

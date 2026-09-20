#!/usr/bin/env python3
"""Serial bounded stage executor; status files, no AI polling required."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
GIB=1024**3


def save(path,obj):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(obj,indent=2)+'\n');temp.replace(path)


def size(path):return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index',type=Path,required=True)
    p.add_argument('--kind',choices=['pilot','base'],required=True)
    a=p.parse_args();index=json.loads(a.index.read_text());stage=a.index.parent
    limits=index['resources'];started=time.monotonic();results=[]
    for name,expected in index['source_locks'].items():
        if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('source changed after freeze: '+name)
    env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
             NUMEXPR_NUM_THREADS='1',CUDA_VISIBLE_DEVICES='',PYTHONDONTWRITEBYTECODE='1')
    if a.kind=='base':
        review=json.loads((stage/'PILOT_REVIEW.json').read_text())
        if review.get('status')!='PASS':raise RuntimeError('pilot review not passed')
    points=[r for r in index['points'] if r['kind']==a.kind]
    for row in points:
        output=Path(row['output'])
        if output.exists():raise FileExistsError('preserve existing evidence: '+str(output))
        if hashlib.sha256(Path(row['config']).read_bytes()).hexdigest()!=row['config_sha256']:
            raise ValueError('input freeze mismatch')
        if shutil.disk_usage(stage).free<limits['host_disk_reserve_gib']*GIB:
            raise RuntimeError('host disk reserve')
        mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
        if int(mem['MemAvailable'].split()[0])*1024<limits['host_ram_reserve_gib']*GIB:
            raise RuntimeError('host memory reserve')
        if time.monotonic()-started>limits['stage_wall_s']:raise RuntimeError('stage watchdog')
        launch=stage/'launch'/row['point_id'];launch.mkdir(parents=True,exist_ok=False)
        command=[sys.executable,'-B',str(HERE/index.get('runner','run_system_point.py'))]
        for key in ('config','model_dir','thermal_binary','artifact_root','output'):
            command += ['--'+key.replace('_','-'),row[key]]
        save(launch/'launch.json',{'command':command,'point':row,'limits':limits})
        save(stage/'STATUS.json',{'status':'RUNNING','active':row['point_id'],'completed':results})
        wall=time.monotonic();reason=None
        with (launch/'stdout.log').open('w') as out,(launch/'stderr.log').open('w') as err:
            child=subprocess.Popen(command,stdout=out,stderr=err,env=env,start_new_session=True)
            while True:
                try:
                    code=child.wait(timeout=min(15,max(.1,limits['point_wall_s']-(time.monotonic()-wall))))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic()-wall>=limits['point_wall_s']:reason='POINT_WATCHDOG'
                    elif time.monotonic()-started>=limits['stage_wall_s']:reason='STAGE_WATCHDOG'
                    elif size(output)>limits['point_output_gib']*GIB:reason='POINT_OUTPUT_BUDGET'
                    elif size(stage)>limits['stage_output_gib']*GIB:reason='STAGE_OUTPUT_BUDGET'
                    elif shutil.disk_usage(stage).free<limits['host_disk_reserve_gib']*GIB:reason='HOST_DISK_RESERVE'
                    mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
                    if int(mem['MemAvailable'].split()[0])*1024<limits['host_ram_reserve_gib']*GIB:reason='HOST_RAM_RESERVE'
                    if reason:break
            if reason:
                os.killpg(child.pid,signal.SIGTERM)
                try:code=child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL);code=child.wait()
        result={'point_id':row['point_id'],'exit_code':code,'reason':reason,
                'wall_s':time.monotonic()-wall,'output_bytes':size(output)}
        save(launch/'result.json',result);results.append(result)
        if code or not (output/'DONE.json').exists():
            save(stage/'FAILED.json',{'failed':result,'completed':results});return 1
        if result['output_bytes']>limits['point_output_gib']*GIB or size(stage)>limits['stage_output_gib']*GIB:
            raise RuntimeError('retained output exceeds finite budget')
    save(stage/(a.kind.upper()+'_DONE.json'),{'status':'COMPLETED','results':results,'wall_s':time.monotonic()-started})
    save(stage/'STATUS.json',{'status':'COMPLETED_'+a.kind.upper(),'completed':results})
    return 0


if __name__=='__main__':raise SystemExit(main())

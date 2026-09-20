#!/usr/bin/env python3
"""Bound one owned experiment process group, preserving startup/failure evidence."""
import argparse
import datetime
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import time

p=argparse.ArgumentParser();p.add_argument('--receipt',type=Path,required=True);p.add_argument('--wall-s',type=float,default=600);p.add_argument('--memory-reserve-gib',type=float,default=0);p.add_argument('--disk-reserve-gib',type=float,default=0);p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
if not a.command or a.wall_s<=0:p.error('positive finite wall and explicit command required')
a.receipt.mkdir(parents=True,exist_ok=False)
(a.receipt/'launch.json').write_text(json.dumps(dict(command=a.command,wall_s=a.wall_s,
           timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
           authority='USER_CONFIRMED EQ3-ISOLATED-MAINTENANCE-CAMPAIGN-v1; point manifest supplied by consumer'),indent=2))
started=time.monotonic();reason=None
with (a.receipt/'stdout.log').open('wb') as stdout,(a.receipt/'stderr.log').open('wb') as stderr:
    child=subprocess.Popen(a.command,stdout=stdout,stderr=stderr,start_new_session=True,
                          env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    while True:
        remaining=a.wall_s-(time.monotonic()-started)
        if remaining<=0:reason='WATCHDOG';break
        try:
            code=child.wait(timeout=min(10,remaining));break
        except subprocess.TimeoutExpired:
            mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
            available=int(mem['MemAvailable'].split()[0])*1024
            if available<a.memory_reserve_gib*1024**3:reason='HOST_MEMORY_RESERVE';break
            if shutil.disk_usage(a.receipt).free<a.disk_reserve_gib*1024**3:reason='HOST_DISK_RESERVE';break
    if reason:
        os.killpg(child.pid,signal.SIGTERM)
        try:code=child.wait(timeout=5)
        except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);code=child.wait()

result=dict(execution_status='COMPLETED' if code==0 else 'FAILED',exit_code=code,reason=reason,wall_s=time.monotonic()-started)
(a.receipt/'result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
if code:print((a.receipt/'stderr.log').read_text()[-6000:])
else:print((a.receipt/'stdout.log').read_text()[-6000:])
raise SystemExit(0 if code==0 else 1)

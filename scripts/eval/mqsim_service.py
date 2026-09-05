"""Owned-process client for the optional native MQSim CPU service.

Every command/response is retained. A successful receipt requires native finish,
process exit, request/byte conservation, and all three media observations per ID.
Only this directly spawned child is terminated on protocol failure or timeout.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import select
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]


class MqsimService:
    source='MQSIM_SIMULATED'

    def __init__(self, binary, profile, directory, *, timeout=30, parallel_units=None):
        self.binary=Path(binary).resolve(strict=True)
        self.profile=Path(profile).resolve(strict=True)
        self.directory=Path(directory).resolve()
        if not self.binary.is_relative_to(ROOT) or not self.directory.is_relative_to(ROOT):
            raise ValueError('service binary and artifacts must remain in experiment checkout')
        if not math.isfinite(timeout) or timeout<=0:
            raise ValueError('service timeout must be positive')
        self.timeout=timeout
        self.now=0
        self.buffer=b''
        self.requests={}
        self.completions={}
        self.observations=[]
        self.finished=False
        self.process=None
        self.directory.mkdir(parents=True,exist_ok=False)
        self.transcript=(self.directory/'service-transcript.jsonl').open('xb')
        self.stderr=(self.directory/'service-stderr.log').open('xb')
        self.argv=[str(self.binary),'--profile',str(self.profile)]
        if parallel_units is not None:
            self.argv+=['--parallel-units',str(parallel_units)]
        try:
            self.process=subprocess.Popen(self.argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                          stderr=self.stderr,bufsize=0)
            self.header=self.read()
            if (self.header.get('schema_version')!=1 or self.header.get('service_source')!=self.source
                    or self.header.get('provenance')!='PROJECTED'
                    or self.header.get('scope')!='READ_ONLY_MEDIA_SERVICE_NOT_HARDWARE'
                    or type(self.header.get('queue_depth')) is not int or self.header['queue_depth']<1
                    or self.now!=0 or self.header['pending']!=0):
                raise ValueError('native service header mismatch')
        except BaseException:
            self.close()
            raise

    def log(self, direction, payload):
        self.transcript.write(json.dumps(dict(direction=direction,payload=payload),allow_nan=False).encode()+b'\n')
        self.transcript.flush()

    def read(self):
        deadline=time.monotonic()+self.timeout
        while b'\n' not in self.buffer:
            remaining=deadline-time.monotonic()
            if remaining<=0 or not select.select([self.process.stdout],[],[],remaining)[0]:
                raise TimeoutError('native MQSim response timeout')
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:
                raise ValueError('native MQSim exited without required response; see service-stderr.log')
            self.buffer+=chunk
            if len(self.buffer)>64*1024*1024:
                raise ValueError('native service response exceeds bounded protocol size')
        line,self.buffer=self.buffer.split(b'\n',1)
        response=json.loads(line)
        self.log('response',response)
        current=response['now_ns']
        if type(current) is not int or current<self.now:
            raise ValueError('native service clock moved backward')
        self.now=current
        self.observations.extend(response['events'])
        return response

    def command(self, command):
        if self.finished:
            raise ValueError('native service already finished')
        self.log('command',command)
        payload=json.dumps(command,allow_nan=False).encode()+b'\n'
        view=memoryview(payload)
        while view:
            count=os.write(self.process.stdin.fileno(),view)
            if count<=0:
                raise ValueError('native service input pipe failed')
            view=view[count:]
        return self.read()

    def submit(self, request):
        rid=request['request_id']
        if rid in self.requests:
            raise ValueError('duplicate service request ID')
        response=self.command(dict(command='submit',requests=[request]))
        if response.get('accepted')!=1:
            raise ValueError('native service did not accept one request')
        self.requests[rid]=dict(request)

    def until(self, horizon):
        response=self.command(dict(command='until',deadline_ns=horizon))
        completion=response['completion']
        if self.now>horizon or (completion is None and self.now!=horizon):
            raise ValueError('native service violated external clock horizon')
        if completion is not None:
            rid=completion['request_id']
            if rid not in self.requests or rid in self.completions:
                raise ValueError('native service returned duplicate/unknown completion')
            self.completions[rid]=completion
        return completion

    def finish(self):
        receipt=self.command(dict(command='finish'))
        self.process.stdin.close()
        code=self.process.wait(timeout=self.timeout)
        if (code!=0 or receipt.get('status')!='FINISHED' or receipt['pending']!=0
                or set(self.requests)!=set(self.completions)
                or receipt['issued']!=len(self.requests) or receipt['completed']!=len(self.requests)
                or receipt['issued_bytes']!=sum(r['bytes'] for r in self.requests.values())
                or receipt['completed_bytes']!=receipt['issued_bytes']):
            raise ValueError('native service finish conservation failed')
        events={rid:{} for rid in self.requests}
        for event in self.observations:
            rid,kind=event['request_id'],event['kind']
            if (rid not in events or kind not in (0,1,2) or kind in events[rid]
                    or event['bytes']!=self.requests[rid]['bytes']
                    or event['arrival_ns']!=self.requests[rid]['issue_ns']
                    or not 0<=event['device_outstanding']<=self.header['queue_depth']):
                raise ValueError('native observation identity/QD mismatch')
            events[rid][kind]=event
        for rid,lifecycle in events.items():
            if set(lifecycle)!={0,1,2}:
                raise ValueError('native observation conservation failed')
            times=[lifecycle[k]['time_ns'] for k in (0,1,2)]
            if (times[0]!=self.requests[rid]['issue_ns'] or times!=sorted(times)
                    or self.completions[rid]['reported_complete']<times[-1]
                    or lifecycle[2]['reported_complete']!=self.completions[rid]['reported_complete']):
                raise ValueError('native request lifecycle mismatch')
        self.finished=True
        return receipt

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                if self.process.stdin is not None and not self.process.stdin.closed:
                    self.process.stdin.close()
                try:
                    self.process.wait(timeout=min(self.timeout,2))
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
            for stream in (self.process.stdin,self.process.stdout):
                if stream is not None and not stream.closed:
                    stream.close()
        for stream in (self.transcript,self.stderr):
            if not stream.closed:
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()

    def __enter__(self):
        return self

    def __exit__(self,*unused):
        self.close()

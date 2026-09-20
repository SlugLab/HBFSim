"""Owned experimental thermal process plus explicit research guard semantics."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import time


class ThermalService:
    def __init__(self, binary, model_dir, directory, *, window_ns=20000000,
                 timeout=180, limits=None, action_delay_ns=20000000, recovery_dwell_ns=100000000,
                 artifact_root=None, baseline_budgets=None, light_fraction=0.5):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=False)
        self.binary=Path(binary).resolve(strict=True)
        if artifact_root is not None and not self.binary.is_relative_to(Path(artifact_root).resolve()):
            raise ValueError('thermal binary outside explicit experimental artifact root')
        self.timeout=timeout;self.buffer=b'';self.now=0;self.window=window_ns
        self.action_delay=action_delay_ns;self.dwell=recovery_dwell_ns
        self.limits=limits or {'hbf':(353.15,363.15,378.15),'hbm':(353.15,363.15,378.15),'gpu':(363.15,373.15,383.15)}
        self.states={};self.pending={};self.last_change={};self.hysteresis_k=2.0
        self.baseline_budgets=dict(baseline_budgets or {});self.light_fraction=light_fraction
        self.transcript=(self.directory/'thermal-transcript.jsonl').open('w')
        self.stderr=(self.directory/'thermal-stderr.log').open('w')
        self.argv=[str(self.binary)]
        names={'model':'model.txt','grid':'rc_grid.json','sensors':'rc_sensors.json'}
        self.lock={}
        for key,name in names.items():
            p=Path(model_dir)/name
            digest=hashlib.sha256(p.read_bytes()).hexdigest()
            self.lock[name]=digest
            self.argv += ['--'+key,str(p),'--'+key+'-sha256',digest]
        self.argv += ['--step-ns',str(window_ns),'--min-k','300','--max-k','400']
        self.process=subprocess.Popen(self.argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.stderr,bufsize=0)
        try:
            self.header=self.read()
            if self.header.get('type')!='READY':raise ValueError('thermal startup did not return READY')
        except BaseException:
            self.close();raise

    def read(self):
        deadline=time.monotonic()+self.timeout
        while b'\n' not in self.buffer:
            if not select.select([self.process.stdout],[],[],max(0,deadline-time.monotonic()))[0]:
                raise TimeoutError('thermal service response timeout')
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:raise RuntimeError('thermal service closed without response')
            self.buffer+=chunk
            if len(self.buffer)>16*1024*1024:raise ValueError('thermal response exceeds bound')
        line,self.buffer=self.buffer.split(b'\n',1)
        result=json.loads(line)
        self.transcript.write(json.dumps({'response':result})+'\n');self.transcript.flush()
        if result.get('type')=='ERROR':raise RuntimeError('thermal failure preserved in transcript: '+str(result))
        return result

    def command(self, text):
        self.transcript.write(json.dumps({'command':text})+'\n');self.transcript.flush()
        self.process.stdin.write((text+'\n').encode());self.process.stdin.flush()
        return self.read()

    def _guard(self, entity_temperatures, time_ns):
        temperatures={}
        for entity, values in entity_temperatures.items():
            owner=entity.split('.')[0]
            if owner=='gpu' or owner.startswith(('hbf','hbm')):
                temperatures[owner]=max(temperatures.get(owner,float('-inf')),values['hotspot_k'])
        names=('normal','light','severe','shutdown')
        for stack,temp in temperatures.items():
            prefix='gpu' if stack=='gpu' else stack[:3]
            thresholds=self.limits[prefix]
            current=self.states.setdefault(stack,0)
            desired=sum(temp>=limit for limit in thresholds)
            if desired<current and temp>=thresholds[current-1]-self.hysteresis_k:
                desired=current
            if desired==current:
                self.pending.pop(stack,None)
                continue
            previous=self.pending.get(stack)
            if previous is None or previous[0]!=desired:
                self.pending[stack]=(desired,time_ns+self.action_delay)
            desired,effective=self.pending[stack]
            recovery_ready=desired>current or time_ns-self.last_change.get(stack,0)>=self.dwell
            if time_ns>=effective and recovery_ready:
                self.states[stack]=desired;self.last_change[stack]=time_ns;self.pending.pop(stack)
        return temperatures,{stack:names[state] for stack,state in self.states.items()}

    def advance(self,start_ns,end_ns,component_energy_j):
        if start_ns!=self.now or end_ns-start_ns!=self.window:
            raise ValueError('thermal client requires contiguous fixed windows')
        for component,energy in sorted(component_energy_j.items()):
            if energy:
                self.command(f'ENERGY {start_ns} {end_ns} {component} {energy:.17g}')
        result=self.command(f'ADVANCE {end_ns}')
        self.now=end_ns
        temperatures,states=self._guard(result['entity_temperatures_k'],end_ns)
        # The shared compute-die research guard constrains future package traffic.
        if states.get('gpu') in ('light','severe','shutdown'):
            rank={'normal':0,'light':1,'severe':2,'shutdown':3}
            for stack in states:
                if rank[states[stack]]<rank[states['gpu']]:states[stack]=states['gpu']
        return dict(result,start_ns=start_ns,end_ns=end_ns,temperatures=temperatures,stack_states={s:v for s,v in states.items() if s != "gpu"},
                    hysteresis_budget_bytes={stack:int(budget*(self.light_fraction if states.get(stack)=="light" else 1.0)) for stack,budget in self.baseline_budgets.items()},
                    guard_evidence='RESEARCH_LIMIT_NOT_PRODUCT_CERTIFICATION',guard_action_delay_ns=self.action_delay)

    def close(self):
        if hasattr(self,'process') and self.process.poll() is None:
            try:
                self.command('QUIT');self.process.wait(timeout=5)
            except (OSError,ValueError,RuntimeError,TimeoutError,subprocess.TimeoutExpired):
                self.process.terminate()
                try:self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        if hasattr(self,'process'):
            for stream in (self.process.stdin,self.process.stdout):
                if stream and not stream.closed:stream.close()
        for name in ('transcript','stderr'):
            stream=getattr(self,name,None)
            if stream and not stream.closed:stream.close()

    def __enter__(self):return self
    def __exit__(self,*unused):self.close()

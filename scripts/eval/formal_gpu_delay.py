#!/usr/bin/env python3
"""Original D0/K64 formal adapter. GPU launch is only a scheduler-owned producer.

prepare validates a fresh standalone run_gpu_delay pilot and creates a frozen
registry with condition-scoped G1/G2 evidence. It never grants nonzero-delay G2.
The scheduler owns the GPU lock; this producer creates no second resource guard.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time

from run_gpu_delay import ROOT, analyze, make_plan, regular_bytes
from gpu_delay_raw import compress_raw, read_raw
from run_manifest import (atomic_json, artifact_inventory, canonical_hash, git_snapshot,
                          identity_alive, owned_processes, process_identity, same_process,
                          sha256, uncertain_session, verify_hashes)
from run_matrix import stop_child, task_bindings
from validate_results import COLUMNS, UNITS, read_rows, validate_rows

METRICS = ['critical_delta_us', 'event_delta_us', 'checksum_ok']
SOURCE = Path(__file__).resolve()


def document(path):
    return json.loads(regular_bytes(path))


def require_d0(plan):
    row=plan['condition']
    if (row['cell_id'] not in {f'gpu_delay-{i:05d}' for i in range(1,31)}
            or plan['hops']!=64 or plan['delay_ns']!=0
            or row['delay_us']!='0' or row['warps'] not in {'1','2','4','8','16'}
            or row['occupancy'] not in {'low','high'}
            or row['repeats']!='10' or row['minimum_configuration']!='yes'):
        raise ValueError('only original minimum D0/K64 cells 00001 through 00030 are supported')
    expected=make_plan(plan['matrix'],row['cell_id'],plan['replicate'],64)
    if expected!=plan: raise ValueError('plan differs from exact original matrix row')


def condition_geometry(plan, sm_count):
    require_d0(plan)
    if type(sm_count) is not int or sm_count < 1:
        raise ValueError('positive actual SM count required')
    warps=int(plan['condition']['warps']);occupancy=plan['condition']['occupancy']
    return dict(warps=warps,occupancy=occupancy,
                blocks=sm_count*(1 if occupancy=='low' else 32//warps))


def verify_payloads(root, hashes):
    for name,digest in hashes.items():
        path=root/name
        if (not path.resolve().is_relative_to(root.resolve())
                or hashlib.sha256(regular_bytes(path)).hexdigest()!=digest):
            raise ValueError('immutable payload hash mismatch: '+name)


def validate_cases(root, plan, trace_mode, raw_compression=None):
    require_d0(plan)
    if raw_compression not in (None,'none','gzip'):raise ValueError('unknown raw compression')
    cases={}
    for name,treatment in [('native','native'),('matched_zero','fast_logical'),
                           ('target',plan['condition']['profile'])]:
        if raw_compression is not None and (root/name/'raw.json.gz').exists()!=(raw_compression=='gzip'):
            raise ValueError('raw representation differs from frozen compression mode')
        case=json.loads(read_raw(root/name))
        for key,value in dict(treatment=treatment,hops=64,
                              **condition_geometry(plan,case.get('sm_count')),
                              requested_delay_ns=0,trace_mode=trace_mode,
                              evidence='GPU_ACQUISITION').items():
            if type(case.get(key)) is not type(value) or case[key]!=value:
                raise ValueError('raw condition/evidence mismatch: '+name+'/'+key)
        if treatment!='native':
            decisions=[json.loads(line) for line in regular_bytes(root/name/'coverage.jsonl').splitlines() if line.strip()]
            if not decisions or any(d.get('allowed') is not True or d.get('modeled') is not True for d in decisions):
                raise ValueError('launch coverage rejected')
        cases[name]=case
    report=analyze(cases)
    if report['g2_cell_pass'] is not None or report['g2_gate_closed'] is not False:
        raise ValueError('D0 must report noise with nonzero fidelity unevaluated')
    return report


def result_rows(plan, report, run_id, git, gpu_uuid):
    row={key:'' for key in COLUMNS}
    row.update({key:value for key,value in plan['condition'].items() if value})
    row.update(schema_version='1',provenance='MEASURED',run_id=run_id,
               git_sha=git['git_sha'],branch=git['branch'],hardware=gpu_uuid,
               model='dependent_pointer_chase',workload='deterministic_16MiB_K64',
               mode='D0_absolute_noise',backend='physical_gpu',figure='fig-e1-hardware-fidelity',panel='gpu',
               series=plan['condition']['profile'],replicate=str(plan['replicate']),
               source_file=str(SOURCE),source_function='result_rows',
               source_line_start=str(result_rows.__code__.co_firstlineno),
               source_line_end=str(result_rows.__code__.co_firstlineno),
               g2_scope='D0_measurement_only',nonzero_delay_fidelity='NOT_EVALUATED')
    values=[statistics.mean(report['per_access_delta_ns'])/1000,report['event_delta_ns']/1000,1]
    rows=[dict(row,metric=metric,value=str(value),unit=UNITS[metric]) for metric,value in zip(METRICS,values)]
    validate_rows(rows,True)
    return rows


def scheduler_context(attempt):
    """Validate the live existing scheduler ownership record, never an env flag."""
    attempt=attempt.resolve()
    manifest=document(attempt/'manifest.json'); status=document(attempt/'status.json')
    owner=status.get('owner'); child=status.get('child')
    me=process_identity(os.getpid())
    if (status.get('state')!='RUNNING' or status.get('phase')!='producer'
            or not same_process(child,me) or not identity_alive(owner)
            or owner['pid']!=os.getppid() or me['session']!=me['pid']
            or manifest.get('resource_class')!='GPU_EXCLUSIVE'
            or manifest.get('task',{}).get('adapter')!='original-d0-v1'
            or manifest.get('root')!=str(ROOT)
            or Path.cwd().resolve()!=attempt):
        raise ValueError('producer requires exact live scheduler ownership and GPU scope')
    task=manifest['task']
    if os.environ.get('CUDA_VISIBLE_DEVICES')!=task['gpu_uuid']:
        raise ValueError('selected physical UUID differs from scheduler')
    verify_hashes(task['artifacts'])
    if task.get('conditions',{}).get(manifest['condition']['cell_id'])!=canonical_hash(manifest['condition']):
        raise ValueError('scheduler condition binding mismatch')
    return manifest,owner


def run_arm(argv, env, directory, owner):
    """Bounded child in the producer session; outer scheduler retains its lock."""
    if not identity_alive(owner): raise ValueError('scheduler owner exited')
    reader,writer=os.pipe(); process=None; identity=None
    try:
        with (directory/'stdout.log').open('xb') as out,(directory/'stderr.log').open('xb') as err:
            launcher=[sys.executable,'-B',str(ROOT/'scripts/eval/run_matrix.py'),
                      '--_owned-launch',str(reader),json.dumps(argv)]
            process=subprocess.Popen(launcher,env=env,cwd=directory,stdout=out,stderr=err,
                                     pass_fds=(reader,),start_new_session=False)
            os.close(reader);reader=None
            identity=process_identity(process.pid,include_zombies=True)
            if not identity: raise ValueError('child identity unavailable before acknowledgement')
            atomic_json(directory/'ownership.json',dict(owner=owner,child=identity,argv=argv))
            if not identity_alive(owner): raise ValueError('scheduler owner exited before acknowledgement')
            os.write(writer,b'1');os.close(writer);writer=None
            deadline=time.monotonic()+30
            while True:
                owned_processes(identity)
                if not identity_alive(owner): raise ValueError('scheduler owner exited during acquisition')
                if time.monotonic()>deadline: raise ValueError('benchmark arm exceeded 30 seconds')
                if os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None:
                    survivors=owned_processes(identity)
                    code=process.wait()
                    if code or survivors or uncertain_session(identity):
                        raise ValueError('arm failed or left owned descendants')
                    atomic_json(directory/'exit.json',dict(exit_code=code,child=identity,
                                                           owned_remaining=[],observed=True))
                    return
                time.sleep(.1)
    finally:
        if reader is not None: os.close(reader)
        if writer is not None: os.close(writer)
        if process is not None: stop_child(process,identity)


def produce(attempt, config_path):
    manifest,owner=scheduler_context(attempt)
    config=document(config_path); verify_hashes(config['artifacts'])
    if config.get('raw_compression') not in ('none','gzip'):raise ValueError('unknown configured compression')
    row=manifest['condition']
    plan=make_plan(config['matrix'],row['cell_id'],manifest['replicate'],64);require_d0(plan)
    if row!=plan['condition'] or config['cell_id']!=row['cell_id']:
        raise ValueError('configured original condition differs')
    paths={key:Path(value) for key,value in config['paths'].items()}
    if config['gpu_uuid']!=manifest['task']['gpu_uuid']: raise ValueError('config UUID mismatch')
    root=attempt/'raw.triplet';root.mkdir()
    previous={}
    def interrupted(number,frame): raise InterruptedError('producer signal '+str(number))
    try:
        for number in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            previous[number]=signal.signal(number,interrupted)
        for name,treatment in [('native','native'),('matched_zero','fast_logical'),('target',row['profile'])]:
            verify_hashes(config['artifacts']);directory=root/name;directory.mkdir()
            argv=[str(paths['binary']),'--treatment',treatment,'--delay-ns','0','--hops','64',
                  '--warps',row['warps'],'--occupancy',row['occupancy'],'--profile',str(paths['profile']),
                  '--plugin',str(paths['plugin']),'--ptx',str(paths['ptx']),
                  '--output',str(directory/'raw.json'),'--report-dir',str(directory/'reports'),
                  '--trace-mode',config['trace_mode']]
            env={k:v for k,v in os.environ.items() if k not in ('LD_PRELOAD','LD_AUDIT')
                 and not k.startswith(('HBFSIM_','BPFTIME_','PTX_PASS_'))}
            env.update(CUDA_VISIBLE_DEVICES=config['gpu_uuid'],HBFSIM_DAEMON_PATH=str(paths['daemon']),
                       HBFSIM_PASS_MANIFEST_PATH=str(directory/'pass.jsonl'),
                       HBFSIM_COVERAGE_PATH=str(directory/'coverage.jsonl'))
            if treatment!='native':env['LD_PRELOAD']=str(paths['gate'])
            atomic_json(directory/'environment.json',dict(sha256=canonical_hash(env),
                launch_settings={k:v for k,v in env.items() if k.startswith('HBFSIM_')
                                 or k in ('CUDA_VISIBLE_DEVICES','CUDA_DEVICE_ORDER','LD_PRELOAD')}))
            run_arm(argv,env,directory,owner)
            if config['raw_compression']=='gzip':compress_raw(directory)
        verify_hashes(config['artifacts'])
        report=validate_cases(root,plan,config['trace_mode'],config['raw_compression'])
        files,rejected=artifact_inventory(root)
        if rejected:raise ValueError('nonregular acquisition artifacts')
        acquisition=dict(schema_version=1,evidence='GPU_ACQUISITION',plan=plan,
                         gpu_uuid=config['gpu_uuid'],trace_mode=config['trace_mode'],
                         raw_compression=config['raw_compression'],
                         config_sha256=sha256(config_path),run_id=manifest['run_id'],
                         artifacts={name:sha256(path) for name,path in files.items()},analysis=report,
                         g2_gate_closed=False,scope='original D0 absolute noise only')
        atomic_json(attempt/'raw.acquisition.json',acquisition)
        atomic_json(attempt/'raw.results.json',result_rows(plan,report,manifest['run_id'],manifest['git'],config['gpu_uuid']))
    finally:
        for number,handler in previous.items():signal.signal(number,handler)


def validate_attempt(attempt, config_path):
    manifest=document(attempt/'manifest.json'); config=document(config_path)
    verify_hashes(config['artifacts']); acquisition=document(attempt/'raw.acquisition.json')
    plan=make_plan(config['matrix'],manifest['condition']['cell_id'],manifest['replicate'],64)
    require_d0(plan)
    if (acquisition.get('evidence')!='GPU_ACQUISITION' or acquisition['plan']!=plan
            or acquisition['config_sha256']!=sha256(config_path)
            or acquisition['run_id']!=manifest['run_id']
            or acquisition['gpu_uuid']!=config['gpu_uuid']
            or acquisition['trace_mode']!=config['trace_mode']
            or acquisition.get('raw_compression','none')!=config.get('raw_compression','none')):
        raise ValueError('acquisition provenance mismatch')
    root=attempt/'raw.triplet';verify_payloads(root,acquisition['artifacts'])
    files,rejected=artifact_inventory(root)
    if rejected or set(files)!=set(acquisition['artifacts']):raise ValueError('incomplete raw inventory')
    for name in ('native','matched_zero','target'):
        ownership=document(root/name/'ownership.json');exit_record=document(root/name/'exit.json')
        if (exit_record.get('exit_code')!=0 or exit_record.get('observed') is not True
                or exit_record.get('owned_remaining')!=[]
                or not same_process(exit_record.get('child'),ownership.get('child'))):
            raise ValueError('missing observed successful arm exit/ownership')
    report=validate_cases(root,plan,config['trace_mode'],config.get('raw_compression','none'))
    if report!=acquisition['analysis']:raise ValueError('raw analysis differs from independent recomputation')
    expected=result_rows(plan,report,manifest['run_id'],manifest['git'],config['gpu_uuid'])
    if read_rows(attempt/'raw.results.json')!=expected:raise ValueError('metric rows differ from raw recomputation')
    return dict(status='PASS',scope='D0_measurement_only',g2_gate_closed=False)


def validate_pilot(pilot):
    """Validate original guarded acquisition; no diagnostic rebinding or promotion."""
    pilot=pilot.resolve();manifest=document(pilot/'manifest.json');status=document(pilot/'status.json')
    if manifest.get('evidence')!='GPU_ACQUISITION' or status.get('state')!='DONE' or manifest.get('rejected_artifacts'):
        raise ValueError('pilot is not completed uncontaminated physical acquisition')
    plan=manifest['plan'];require_d0(plan)
    # artifact_inventory excludes mutable control files; bind their final bytes
    # separately below while validating every immutable payload.
    payloads=manifest['artifact_hashes']
    verify_payloads(pilot,payloads)
    files,rejected=artifact_inventory(pilot)
    if rejected or set(files)-{'manifest.json','status.json'}!=set(payloads):
        raise ValueError('pilot immutable artifact inventory differs')
    for item in manifest['inputs'].values():
        if sha256(item['path'])!=item['sha256']:raise ValueError('pilot input differs from current source/build')
    current=git_snapshot(ROOT)
    for key in ('git_sha','dirty_patch_sha256'):
        if manifest['git'][key]!=current[key]:raise ValueError('pilot source snapshot changed: '+key)
    report=validate_cases(pilot,plan,manifest['trace_mode'],manifest.get('raw_compression','none'))
    if document(pilot/'raw.analysis.json')!=dict(report,evidence='GPU_ACQUISITION'):
        raise ValueError('pilot analysis mismatch')
    snapshots=[json.loads(line) for line in regular_bytes(pilot/'raw.gpu.jsonl').splitlines() if line.strip()]
    if not snapshots or any(s.get('available') is not True or s.get('gpu_uuid')!=manifest['gpu_uuid'] for s in snapshots):
        raise ValueError('pilot GPU observations missing/mismatched')
    if snapshots[0].get('phase')!='preflight' or snapshots[-1].get('phase')!='end-acquisition':
        raise ValueError('pilot GPU guard lifecycle incomplete')
    return dict(schema_version=1,status='PASS',scope='D0_measurement_only',g2_gate_closed=False,
                condition_sha256=plan['condition_sha256'],gpu_uuid=manifest['gpu_uuid'],
                pilot_manifest_sha256=sha256(pilot/'manifest.json'),pilot_status_sha256=sha256(pilot/'status.json'),
                nonzero_delay_fidelity='NOT_EVALUATED')


def prepare(pilot, out):
    """Register one exact condition only after actually running its pilot validator."""
    pilot=pilot.resolve();out=out.resolve();out.mkdir(parents=True,exist_ok=False)
    validation=out/'pilot-validation.json'
    argv=[sys.executable,'-B',str(SOURCE),'validate-pilot','--pilot',str(pilot),'--output',str(validation)]
    with (out/'validator.stdout.log').open('xb') as stdout,(out/'validator.stderr.log').open('xb') as stderr:
        subprocess.run(argv,check=True,stdout=stdout,stderr=stderr)
    manifest=document(pilot/'manifest.json');plan=manifest['plan'];require_d0(plan)
    artifacts=[dict(path=item['path'],sha256=item['sha256'],role='config' if key=='profile' else 'input' if key=='matrix' else 'build')
               for key,item in manifest['inputs'].items()]
    dependencies=('formal_gpu_delay.py','run_gpu_delay.py','run_matrix.py','run_manifest.py',
                  'resource_guard.py','export_results.py','validate_results.py','gpu_delay_raw.py')
    for path in [Path(sys.executable).resolve(),*[ROOT/'scripts/eval'/name for name in dependencies]]:
        if str(path) not in {a['path'] for a in artifacts}:
            artifacts.append(dict(path=str(path),sha256=sha256(path),role='build'))
    config=dict(schema_version=1,cell_id=plan['condition']['cell_id'],matrix=plan['matrix'],
                gpu_uuid=manifest['gpu_uuid'],trace_mode=manifest['trace_mode'],artifacts=artifacts,
                raw_compression=manifest.get('raw_compression','none'),
                paths={key:item['path'] for key,item in manifest['inputs'].items()})
    config_path=out/'config.json';atomic_json(config_path,config)
    gates=['G1-GPU','G2-known-delay'];receipts=[out/(gate+'.json') for gate in gates]
    task=dict(id='original-d0-'+config['cell_id'],adapter='original-d0-v1',implemented=True,foreground=True,
              conditions={config['cell_id']:plan['condition_sha256']},resource_class='GPU_EXCLUSIVE',
              gpu_uuid=config['gpu_uuid'],argv=[sys.executable,'-B',str(SOURCE),'produce','--attempt','{attempt_dir}','--config',str(config_path)],
              validator_argv=[sys.executable,'-B',str(SOURCE),'validate','--attempt','{attempt_dir}','--config',str(config_path)],
              artifacts=[*artifacts,dict(path=str(config_path),sha256=sha256(config_path),role='config')],
              gate_receipts=[str(p) for p in receipts],timeout_seconds=150,result_file='raw.results.json',
              raw_artifacts=['raw.acquisition.json'],expected_metrics=METRICS,
              provenance=dict(provenance='MEASURED',measurement_scope='physical_hardware'))
    # Registry interpreters require the script immediately after the executable.
    task['argv'].remove('-B');task['validator_argv'].remove('-B')
    bindings=task_bindings(ROOT,Path(plan['matrix']),task,plan['condition'])
    evidence=[dict(path=str(path),sha256=sha256(path)) for path in [validation,pilot/'manifest.json',pilot/'status.json',
             *[pilot/name for name in manifest['artifact_hashes'] if name not in {'manifest.json','status.json'}]]]
    for gate,path in zip(gates,receipts):
        atomic_json(path,dict(schema_version=1,gate=gate,status='PASS',bindings=bindings,
                    validator=dict(argv=argv,exit_code=0),artifacts=evidence,scope='D0_measurement_only',
                    g2_gate_closed=False,nonzero_delay_fidelity='NOT_EVALUATED'))
    atomic_json(out/'registry.json',dict(schema_version=1,tasks=[task]))


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    for name in ('produce','validate'):
        p=sub.add_parser(name);p.add_argument('--attempt',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    p=sub.add_parser('validate-pilot');p.add_argument('--pilot',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('prepare');p.add_argument('--pilot',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='produce':produce(args.attempt.resolve(),args.config.resolve())
    elif args.command=='validate':print(json.dumps(validate_attempt(args.attempt.resolve(),args.config.resolve()),sort_keys=True))
    elif args.command=='validate-pilot':
        if args.output.exists():raise ValueError('validation output must be fresh')
        atomic_json(args.output,validate_pilot(args.pilot))
    else:prepare(args.pilot,args.out)


if __name__=='__main__':main()

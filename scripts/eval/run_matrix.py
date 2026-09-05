#!/usr/bin/env python3
"""Bounded, resumable evaluation orchestration; no handlers are implicit.

Default/--plan/--dry-run is read-only planning from run-matrix.csv. Execution
requires --max-runs N and a local --registry JSON. --resume permits a NEW attempt
for failed/interrupted/blocked runs; DONE always skips. --status is read-only.
--only accepts comma-separated cell IDs, groups, or glob patterns. --eq,
--resource-class and --replicate further select matrix rows/replicates.

Registry schema v1: {"schema_version":1,"tasks":[TASK,...]}. TASK requires:
  id: unique local name; implemented: true; foreground: true (no daemonization);
  conditions: {cell_id: canonical_hash(exact CSV row)}; resource_class: exact enum;
  argv / validator_argv: nonempty argv arrays, absolute executable at argv[0];
  artifacts: [{path:absolute,sha256:64-hex,role:build|config|input},...];
  gate_receipts: [absolute receipt paths, ...]; timeout_seconds: positive number;
  result_file: "raw.results.json" (or CSV); raw_artifacts:["raw.data.json",...];
  expected_metrics:[metric,...]; provenance:{provenance:PROJECTED|MEASURED|
    VALIDATED_MODEL, assumptions:[...] / measurement_scope:physical_hardware /
    calibration_id,heldout_validation_id,validity_domain as applicable}.
GPU tasks additionally require gpu_uuid (physical GPU-UUID). GPU_SHARED_SAFE also
requires performance_collection:false; only correctness metrics may be exported.
Storage tasks require
storage_manifest, also registered as a hashed config artifact; see resource_guard.
Every executable and source script must be a hashed build artifact. Shells and
inline interpreter code are forbidden; argv is never evaluated by a shell.
Allowed substitutions in argv are {attempt_dir}, {run_id}, {replicate},
{condition_json}; each substitution remains ONE argument. Other braces are invalid.
The child also receives HBFSIM_ATTEMPT_DIR/RUN_ID/REPLICATE/CONDITION_JSON/GIT_SHA/
BRANCH and CUDA_VISIBLE_DEVICES pinned to gpu_uuid (empty for CPU/storage-only).

Gate receipt schema v1:
 {schema_version:1,gate:<exact semicolon-separated blocking_gate token>,
  status:"PASS",bindings:<task_bindings(...)>,
  validator:{argv:[...],exit_code:0},artifacts:[{path:absolute,sha256:...},...]}
All matrix-required gates must have receipts. Bindings contain git_sha, source
patch/tool hashes, build/config hashes, matrix hash, condition hash and task hash.
A receipt records a previously run scientific gate; the registration/receipt is a
reviewed trust boundary, not proof that arbitrary claimed scientific values are
true. The runner verifies immutable evidence and matching bindings, then executes
the task's semantic validator AND the unchanged strict schema/raw validator. A
missing implementation or receipt cannot produce DONE. No current real matrix
handler is registered by this module. Users must freeze/review real registrations.

Layout: results/runs/<cell>-rNNN/status.json points to attempts/NNNN. Each attempt
retains manifest.json, status.json, environment.json, stdout.log, stderr.log,
validator logs, source.patch, raw artifacts and frozen gate receipts. A new retry
never overwrites an old attempt. DONE publication is atomic and last. SIGINT,
SIGTERM and SIGHUP stop verified owned members using pidfds and retain INTERRUPTED.
A pipe handshake prevents payload execution until exact child identity is durable.
All output roots share the repository results/locks directory.
After a crash/reboot, --resume checks boot ID/PID/starttime and surviving session
members; a live run is never retaken. Locks coordinate only this project, not
other users; resource sampling cannot guarantee absence of between-sample jobs.
"""
from __future__ import annotations
import argparse
import csv
import fnmatch
import hashlib
import json
import math
import os
import pathlib
import re
import signal
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from run_manifest import (STATES,atomic_json,canonical_hash,confined_file,environment_snapshot,
    git_snapshot,identity_alive,now,owned_processes,process_identity,read_json,set_status,sha256,verify_hashes,signal_identity,uncertain_session,artifact_inventory)
from resource_guard import FileLock,ResourceBusy,ResourceGuard,RESOURCE_CLASSES,SHARED_SAFE_METRICS
from export_results import check_rows, validate_attempt
from validate_results import load_validated,read_rows,validate_rows,UNITS

ROOT=pathlib.Path(__file__).resolve().parents[2]


def load_matrix(path):
    with pathlib.Path(path).open(newline='') as stream:
        reader=csv.DictReader(stream)
        if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)):raise ValueError('invalid matrix header')
        rows=list(reader)
    seen=set()
    for row in rows:
        if None in row or any(v is None or v!=v.strip() for v in row.values()):raise ValueError('malformed matrix row')
        for field in ('cell_id','eq','group','repeats','resource_class','blocking_gate'):
            if not row.get(field):raise ValueError('missing matrix field: '+field)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',row['cell_id']):raise ValueError('unsafe condition ID')
        if row['cell_id'] in seen:raise ValueError('duplicate matrix condition')
        seen.add(row['cell_id'])
        if row['resource_class'] not in RESOURCE_CLASSES:raise ValueError('invalid matrix resource class')
        if not row['repeats'].isdigit() or int(row['repeats'])<1:raise ValueError('invalid replicate count')
        required={'gpu_delay':'GPU_EXCLUSIVE','async_overlap':'GPU_EXCLUSIVE','async_correctness':'GPU_EXCLUSIVE',
                  'three_arm':'GPU_STORAGE_EXCLUSIVE','live_confirmation':'GPU_EXCLUSIVE','mode_cost':'GPU_EXCLUSIVE',
                  'routing_capture':'GPU_SHARED_SAFE','feasibility':'CPU_ONLY','workload_boundary':'CPU_ONLY','robustness':'CPU_ONLY'}.get(row['group'])
        if row['group']=='flash_fidelity':required='STORAGE_EXCLUSIVE' if row.get('backend')=='physical' else 'CPU_ONLY'
        if required and row['resource_class']!=required:raise ValueError('resource class violates condition safety policy')
    return rows


def select_runs(rows,only=None,eq=None,resource_class=None,replicate=None):
    patterns=[p for s in (only or []) for p in s.split(',')]
    for row in rows:
        if patterns and not any(fnmatch.fnmatchcase(row['cell_id'],p) or fnmatch.fnmatchcase(row['group'],p) for p in patterns):continue
        if eq and row['eq'] not in eq:continue
        if resource_class and row['resource_class'] not in resource_class:continue
        for rep in range(1,int(row['repeats'])+1):
            if replicate is None or rep==replicate:
                yield row,rep,f"{row['cell_id']}-r{rep:03d}"


def task_bindings(root,matrix,task,row):
    git=git_snapshot(root)
    role_hash=lambda role:canonical_hash(sorted((a['path'],a['sha256']) for a in task.get('artifacts',[]) if a['role']==role))
    tools={p.name:sha256(p) for p in pathlib.Path(__file__).parent.glob('*.py') if p.name in {'run_matrix.py','run_manifest.py','resource_guard.py','export_results.py','validate_results.py'}}
    return dict(git_sha=git['git_sha'],source_sha256=canonical_hash(dict(patch=git['dirty_patch_sha256'],tools=tools)),
                build_sha256=role_hash('build'),config_sha256=role_hash('config'),input_sha256=role_hash('input'),
                matrix_sha256=sha256(matrix),condition_sha256=canonical_hash(row),task_sha256=canonical_hash(task))


def validate_argv(argv,artifacts):
    if not isinstance(argv,list) or not argv or any(not isinstance(arg,str) or not arg or '\0' in arg for arg in argv):raise ValueError('command must be a nonempty argv array')
    executable=pathlib.Path(argv[0])
    builds={str(pathlib.Path(a['path']).resolve()) for a in artifacts if a['role']=='build'}
    if not executable.is_absolute() or str(executable.resolve()) not in builds or not os.access(executable,os.X_OK):raise ValueError('executable is not a frozen executable build artifact')
    if executable.name in {'sh','bash','dash','zsh','fish','csh','ksh','env'}:raise ValueError('shell/eval command registry is forbidden')
    if executable.name.startswith(('python','perl','ruby','node')):
        if len(argv)<2 or argv[1].startswith('-') or str(pathlib.Path(argv[1]).resolve()) not in builds:
            raise ValueError('interpreter requires a frozen source script; inline code is forbidden')
    return argv


def validate_task(root,matrix,task,row,attempt):
    if task.get('implemented') is not True or task.get('foreground') is not True:raise ValueError('NOT IMPLEMENTED or non-foreground handler')
    if task.get('resource_class')!=row['resource_class']:raise ValueError('task/matrix resource mismatch')
    if task.get('conditions',{}).get(row['cell_id'])!=canonical_hash(row):raise ValueError('condition binding mismatch')
    verify_hashes(task.get('artifacts'))
    if any(a.get('role') not in {'build','config','input'} for a in task['artifacts']):raise ValueError('invalid artifact role')
    if not any(a['role']=='build' for a in task['artifacts']) or not any(a['role']=='config' for a in task['artifacts']):raise ValueError('frozen build and config required')
    validate_argv(task.get('argv'),task['artifacts']);validate_argv(task.get('validator_argv'),task['artifacts'])
    timeout=task.get('timeout_seconds',0)
    if not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:raise ValueError('finite positive task timeout required')
    metrics=task.get('expected_metrics')
    if not isinstance(metrics,list) or not metrics or len(set(metrics))!=len(metrics) or set(metrics)-UNITS.keys():raise ValueError('exact expected metrics required')
    if row['resource_class']=='GPU_SHARED_SAFE' and set(metrics)-SHARED_SAFE_METRICS:raise ValueError('GPU_SHARED_SAFE cannot collect/export performance metrics')
    if row['resource_class']=='GPU_SHARED_SAFE' and task.get('performance_collection') is not False:raise ValueError('GPU_SHARED_SAFE requires explicit no-timing contract')
    provenance=task.get('provenance',{})
    if provenance.get('provenance') not in {'PROJECTED','MEASURED','VALIDATED_MODEL'}:raise ValueError('explicit non-MOCK provenance required')
    if provenance['provenance']=='PROJECTED' and not provenance.get('assumptions'):raise ValueError('PROJECTED requires frozen assumptions')
    if provenance['provenance']=='MEASURED' and provenance.get('measurement_scope')!='physical_hardware':raise ValueError('MEASURED requires physical_hardware scope')
    if provenance['provenance']=='VALIDATED_MODEL' and any(not provenance.get(k) for k in ('calibration_id','heldout_validation_id','validity_domain')):raise ValueError('VALIDATED_MODEL requires calibration/heldout/domain')
    if not task.get('raw_artifacts'):raise ValueError('raw artifacts required')
    for name in [task.get('result_file',''),*task['raw_artifacts']]:
        confined_file(attempt,name)
        if not pathlib.Path(name).name.startswith('raw.'):raise ValueError('raw artifact names must begin raw.')
    if row['resource_class'] in {'STORAGE_EXCLUSIVE','GPU_STORAGE_EXCLUSIVE'} and task.get('storage_manifest'):
        if task['storage_manifest'] not in {a['path'] for a in task['artifacts'] if a['role']=='config'}:raise ValueError('storage authorization must be a frozen config artifact')
    bindings=task_bindings(root,matrix,task,row)
    required=set(row['blocking_gate'].split(';'));receipts={}
    for path in task.get('gate_receipts',[]):
        receipt=read_json(path)
        if receipt.get('schema_version')!=1 or receipt.get('status')!='PASS':raise ValueError('gold gate did not pass')
        if receipt.get('bindings')!=bindings:raise ValueError('gold gate git/source/build/config/condition binding mismatch')
        validator=receipt.get('validator',{})
        if not validator.get('argv') or validator.get('exit_code')!=0:raise ValueError('gold receipt lacks successful validator evidence')
        verify_hashes(receipt.get('artifacts'))
        gate=receipt.get('gate')
        if not gate or gate in receipts:raise ValueError('duplicate/unnamed gold receipt')
        receipt_name='gates/'+hashlib.sha256(gate.encode()).hexdigest()+'.json'
        atomic_json(attempt/receipt_name,receipt)
        evidence=[]
        for index,item in enumerate(receipt['artifacts']):
            name='gates/evidence/'+item['sha256']+'-'+str(index)
            destination=attempt/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(item['path'],destination)
            if sha256(destination)!=item['sha256']:raise ValueError('gold evidence changed during freeze')
            evidence.append(dict(path=name,sha256=item['sha256'],source_path=item['path']))
        receipts[gate]=dict(path=receipt_name,sha256=sha256(attempt/receipt_name),evidence=evidence)
    if required-receipts.keys():raise ValueError('missing gold gate receipts: '+','.join(sorted(required-receipts.keys())))
    return bindings,receipts


def expand_argv(argv,attempt,run_id,replicate,row):
    values=dict(attempt_dir=str(attempt),run_id=run_id,replicate=str(replicate),condition_json=json.dumps(row,sort_keys=True))
    try:return [arg.format_map(values) for arg in argv]
    except (KeyError,ValueError) as error:raise ValueError('unknown argv substitution') from error


@contextmanager
def signal_state():
    state={'signal':None};old={}
    def handler(number,frame):state['signal']=number
    try:
        for number in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP):
            old[number]=signal.signal(number,handler)
        yield state
    finally:
        for number,handler in old.items():signal.signal(number,handler)


class InterruptedRun(RuntimeError):pass
class FailedRun(RuntimeError):pass


def stop_child(process,identity):
    """Stop every verified owned member, including separate process groups."""
    for number in (signal.SIGTERM,signal.SIGKILL):
        deadline=time.monotonic()+1
        while True:
            members=owned_processes(identity)
            if not members:break
            for member in members.values():
                try:signal_identity(member,number)
                except OSError as error:raise FailedRun('safe owned-process cleanup failed: '+str(error)) from error
            if time.monotonic()>=deadline:break
            time.sleep(.03)
    if owned_processes(identity) or uncertain_session(identity):
        raise FailedRun('owned session remains live/unverifiable; resume is blocked')
    try:process.wait(timeout=2)
    except subprocess.TimeoutExpired:raise FailedRun('owned subprocess did not terminate; resume requires identity check')


def owned_launcher(fd,argv):
    """No workload executes unless the parent has durably committed identity."""
    try:
        if os.read(fd,1)!=b'1':return 125
    finally:os.close(fd)
    os.execvpe(argv[0],argv,os.environ)


def run_child(argv,env,attempt,phase,guard,status_callback,signals,timeout,poll_seconds):
    if signals['signal']:raise InterruptedRun('signal received before launch')
    guard.check('before-'+phase)
    process=None;identity=None;reader=None;writer=None
    prefix='' if phase=='producer' else 'validator.'
    with (attempt/(prefix+'stdout.log')).open('w') as stdout,(attempt/(prefix+'stderr.log')).open('w') as stderr:
        try:
            reader,writer=os.pipe()
            launch=[sys.executable,str(pathlib.Path(__file__).resolve()),'--_owned-launch',str(reader),json.dumps(argv)]
            process=subprocess.Popen(launch,cwd=attempt,env=env,stdout=stdout,stderr=stderr,start_new_session=True,pass_fds=(reader,))
            os.close(reader);reader=None
            # Keep the unreaped leader as an exact identity anchor, even if it
            # exits quickly; never substitute start_time=0.
            identity=process_identity(process.pid,include_zombies=True)
            if not identity:raise FailedRun('new child identity unavailable; workload not acknowledged')
            guard.child=identity;owned_processes(identity);status_callback(identity,phase)
            guard.check('start-'+phase)
            if signals['signal']:raise InterruptedRun('signal before workload acknowledgement')
            os.write(writer,b'1');os.close(writer);writer=None;guard.started=True
            deadline=time.monotonic()+timeout
            while True:
                owned_processes(identity);status_callback(identity,phase)
                # WNOWAIT preserves the leader's identity until descendants are
                # captured; Popen.poll() would reap it too early.
                exited=os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
                if exited is not None:
                    owned_processes(identity)
                    code=process.wait()
                    break
                if signals['signal']:raise InterruptedRun('received '+signal.Signals(signals['signal']).name)
                if time.monotonic()>deadline:raise FailedRun(phase+' timeout')
                time.sleep(poll_seconds);guard.check('periodic-'+phase)
            if signals['signal']:raise InterruptedRun('signal at subprocess completion')
            if owned_processes(identity):raise FailedRun('registered foreground command left running descendants')
            guard.check('end-'+phase)
            return code
        finally:
            if reader is not None:os.close(reader)
            if writer is not None:os.close(writer)  # EOF prevents unacknowledged exec.
            try:
                if process:stop_child(process,identity)
            finally:
                stdout.flush();os.fsync(stdout.fileno());stderr.flush();os.fsync(stderr.fileno())
                if identity and (owned_processes(identity) or uncertain_session(identity)):
                    guard.child=identity;status_callback(identity,phase)
                else:guard.child=None


def seal_artifacts(attempt,manifest,strict=True):
    """Hash only confined regular artifacts; retain rejected paths on failure."""
    if 'artifact_hashes' not in manifest:
        hashes={};errors={}
        try:
            files,errors=artifact_inventory(attempt)
            for name,path in files.items():
                try:hashes[name]=sha256(path)
                except OSError as error:errors[name]=str(error)
        except OSError as error:errors['inventory']=str(error)
        manifest['raw_hashes']={k:v for k,v in hashes.items() if pathlib.Path(k).name.startswith('raw.')}
        manifest['artifact_hashes']=hashes
        manifest['artifact_errors']=errors
        manifest['environment_sha256']=hashes.get('environment.json')
    if strict and manifest.get('artifact_errors'):
        raise ValueError('unsafe/unconfined artifacts: '+', '.join(sorted(manifest['artifact_errors'])))


def execute_one(root,matrix,results,registry,task,row,replicate,run_id,resume,signals,gpu_probe,storage_probe,poll_seconds):
    run_dir=results/'runs'/run_id
    try:
        with FileLock(root/'results'/'locks'/('run-'+run_id+'.lock')):
            status_file=run_dir/'status.json'
            old=read_json(status_file) if status_file.exists() else None
            if old:
                if old['state']=='DONE':return dict(run_id=run_id,state='DONE',skipped=True)
                if owned_processes(old.get('child')) or uncertain_session(old.get('child')):
                    return dict(run_id=run_id,state=old['state'],skipped=True,reason='live/unverifiable recorded child session; not retaken')
                if old['state']=='RUNNING':
                    if identity_alive(old.get('owner')) or owned_processes(old.get('child')):
                        return dict(run_id=run_id,state='RUNNING',skipped=True,reason='live recorded owner/child; not retaken')
                    abandoned=confined_file(run_dir,old['attempt'])
                    if (abandoned/'status.json').is_file() and read_json(abandoned/'status.json').get('state')=='DONE':
                        validate_attempt(abandoned)
                        recovered=read_json(abandoned/'status.json')
                        atomic_json(run_dir/'status.json',recovered)
                        return dict(run_id=run_id,state='DONE',skipped=True,reason='recovered durable completed attempt')
                    old=set_status(run_dir,abandoned,'INTERRUPTED',reason='abandoned owner/child identity; crash/reboot recovery',owner=old.get('owner'),child=old.get('child'))
                if not resume:return dict(run_id=run_id,state=old['state'],skipped=True,reason='use --resume for a new attempt')
            attempts=run_dir/'attempts';attempts.mkdir(parents=True,exist_ok=True)
            number=max([int(p.name) for p in attempts.iterdir() if p.name.isdigit()]+[0])+1
            attempt=attempts/f'{number:04d}';attempt.mkdir()
            owner=process_identity(os.getpid())
            set_status(run_dir,attempt,'PLANNED',owner=owner,child=None)
            atomic_json(attempt/'environment.json',environment_snapshot())
            for name in ('stdout.log','stderr.log'):(attempt/name).touch()
            git=git_snapshot(root)
            patch=subprocess.check_output(['git','diff','HEAD','--binary'],cwd=root)
            (attempt/'source.patch').write_bytes(patch)
            manifest=dict(schema_version=1,root=str(root),run_id=run_id,replicate=replicate,condition=row,resource_class=row['resource_class'],
                          git=git,matrix_sha256=sha256(matrix),registry_sha256=sha256(registry) if registry and pathlib.Path(registry).is_file() else None,
                          task=task,complete=False,started_at=now())
            atomic_json(attempt/'manifest.json',manifest)
            last_child=None
            def running(child,phase):
                nonlocal last_child
                last_child=child
                set_status(run_dir,attempt,'RUNNING',owner=owner,child=child,phase=phase)
            try:
                if task is None:raise ValueError('NOT IMPLEMENTED: no exact registered condition handler')
                bindings,receipts=validate_task(root,matrix,task,row,attempt)
                argv=expand_argv(task['argv'],attempt,run_id,replicate,row)
                validator_argv=expand_argv(task['validator_argv'],attempt,run_id,replicate,row)
                manifest.update(bindings=bindings,task_sha256=canonical_hash(task),argv=argv,validator_argv=validator_argv,gate_receipts=receipts)
                env=os.environ.copy();env.update(HBFSIM_ATTEMPT_DIR=str(attempt),HBFSIM_RUN_ID=run_id,HBFSIM_REPLICATE=str(replicate),
                    HBFSIM_CONDITION_JSON=json.dumps(row,sort_keys=True),HBFSIM_GIT_SHA=git['git_sha'],HBFSIM_BRANCH=git['branch'],
                    CUDA_VISIBLE_DEVICES=task.get('gpu_uuid',''),CUDA_DEVICE_ORDER='PCI_BUS_ID')
                if task.get('storage_manifest'):env['HBFSIM_STORAGE_MANIFEST']=task['storage_manifest']
                environment=environment_snapshot()
                environment['child_environment']={k:v for k,v in env.items() if k.startswith('HBFSIM_') or k in {'CUDA_VISIBLE_DEVICES','CUDA_DEVICE_ORDER'}}
                atomic_json(attempt/'environment.json',environment)
                atomic_json(attempt/'manifest.json',manifest)
                with ResourceGuard(results,attempt,task,gpu_probe,storage_probe,project_root=root) as guard:
                    code=run_child(argv,env,attempt,'producer',guard,running,signals,task['timeout_seconds'],poll_seconds)
                    manifest['producer_exit_code']=code
                    if code:raise FailedRun('producer exited '+str(code))
                    for name in [task['result_file'],*task['raw_artifacts']]:
                        if not confined_file(attempt,name).is_file():raise ValueError('missing required output: '+name)
                    rows=validate_rows(read_rows(attempt/task['result_file']),True);check_rows(rows,manifest)
                    code=run_child(validator_argv,env,attempt,'validator',guard,running,signals,task['timeout_seconds'],poll_seconds)
                    manifest['validation']=dict(semantic_exit_code=code,strict_no_mock=False)
                    if code:raise ValueError('actual semantic validator exited '+str(code))
                    verify_hashes(task['artifacts'])
                    if task_bindings(root,matrix,task,row)!=bindings:raise ValueError('frozen source/config changed during run')
                    guard.check('final')
                    if signals['signal']:raise InterruptedRun('signal before commit')
                    # All writers have exited and all resource observations are saved.
                    seal_artifacts(attempt,manifest)
                    hashes=manifest['artifact_hashes']
                    record=dict(task['provenance'],git_sha=git['git_sha'],branch=git['branch'],raw_artifact=task['raw_artifacts'][0],raw_sha256=hashes[task['raw_artifacts'][0]])
                    manifest['runs']={run_id:record}
                    atomic_json(attempt/'manifest.json',manifest)
                    final_rows=load_validated(attempt/task['result_file'],True,attempt/'manifest.json')
                    check_rows(final_rows,manifest)
                    manifest['validation']['strict_no_mock']=True;manifest['complete']=True;manifest['finished_at']=now()
                    atomic_json(attempt/'manifest.json',manifest)
                    validate_attempt(attempt,require_done=False)
                    for p in attempt.rglob('*'):
                        if p.is_file():
                            with p.open('rb') as stream:os.fsync(stream.fileno())
                    set_status(run_dir,attempt,'DONE',owner=owner,child=None,manifest_sha256=sha256(attempt/'manifest.json'))
                    return dict(run_id=run_id,state='DONE',attempt=str(attempt))
            except ResourceBusy as error:state=error.state;reason=str(error)
            except InterruptedRun as error:state='INTERRUPTED';reason=str(error)
            except FailedRun as error:state='FAILED';reason=str(error)
            except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:state='INVALID_GOLD_GATE';reason=str(error)
            remaining=last_child if owned_processes(last_child) or uncertain_session(last_child) else None
            if remaining is None:seal_artifacts(attempt,manifest,strict=False)
            manifest['remaining_child']=remaining
            manifest.update(finished_at=now(),failure=dict(state=state,reason=reason),complete=False)
            atomic_json(attempt/'manifest.json',manifest)
            set_status(run_dir,attempt,state,owner=owner,child=remaining,reason=reason)
            return dict(run_id=run_id,state=state,reason=reason,attempt=str(attempt))
    except ResourceBusy:
        return dict(run_id=run_id,state='RUNNING',skipped=True,reason='same-run lock occupied')


def run_campaign(*,root=ROOT,matrix=None,results=None,registry=None,max_runs=None,replicate=None,resume=False,
                 only=None,eq=None,resource_class=None,plan=False,dry_run=False,status=False,gpu_probe=None,storage_probe=None,poll_seconds=.25):
    root=pathlib.Path(root).resolve();matrix=pathlib.Path(matrix or root/'docs/49-eval-audit/run-matrix.csv').resolve()
    results=pathlib.Path(results or root/'results').resolve()
    if max_runs is not None and (not isinstance(max_runs,int) or max_runs<1):raise ValueError('--max-runs must be positive')
    if replicate is not None and replicate<1:raise ValueError('--replicate must be positive')
    if not 0<poll_seconds<=5:raise ValueError('poll interval must be in (0,5] seconds')
    selected=list(select_runs(load_matrix(matrix),only,eq,resource_class,replicate))
    summary=dict(mode='status' if status else 'plan',conditions=len({r['cell_id'] for r,_,_ in selected}),planned_runs=len(selected),matrix_sha256=sha256(matrix),resource_classes={})
    for row,rep,run_id in selected:
        klass=row['resource_class'];summary['resource_classes'][klass]=summary['resource_classes'].get(klass,0)+1
    if status:
        summary['states']={}
        for row,rep,run_id in selected:
            p=results/'runs'/run_id/'status.json';state=read_json(p)['state'] if p.is_file() else 'PLANNED'
            summary['states'][state]=summary['states'].get(state,0)+1
        return summary
    if plan or dry_run or max_runs is None:return summary
    if not results.is_relative_to(root):raise ValueError('results must remain inside the authorized repository')
    registry=pathlib.Path(registry).resolve() if registry else None
    tasks=[]
    if registry:
        data=read_json(registry)
        if data.get('schema_version')!=1 or not isinstance(data.get('tasks'),list):raise ValueError('invalid task registry schema')
        tasks=data['tasks']
        ids=[task.get('id') for task in tasks]
        if any(not item for item in ids) or len(ids)!=len(set(ids)):raise ValueError('task IDs must be unique and nonempty')
    by_condition={}
    for task in tasks:
        for cell in task.get('conditions',{}):
            if cell in by_condition:raise ValueError('ambiguous task registration: '+cell)
            by_condition[cell]=task
    outcomes=[];attempted=0
    with signal_state() as signals:
        for row,rep,run_id in selected:
            if attempted>=max_runs or signals['signal']:break
            outcome=execute_one(root,matrix,results,registry,by_condition.get(row['cell_id']),row,rep,run_id,resume,signals,gpu_probe,storage_probe,poll_seconds)
            outcomes.append(outcome)
            if not outcome.get('skipped'):attempted+=1
    summary.update(mode='execute',attempted_runs=attempted,outcomes=outcomes)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--matrix',type=pathlib.Path,default=ROOT/'docs/49-eval-audit/run-matrix.csv')
    parser.add_argument('--results',type=pathlib.Path,default=ROOT/'results')
    parser.add_argument('--registry',type=pathlib.Path)
    for flag in ('dry-run','plan','resume','status'):parser.add_argument('--'+flag,action='store_true')
    parser.add_argument('--only',action='append');parser.add_argument('--eq',action='append',choices=['EQ1','EQ2','EQ3','EQ4','APPENDIX'])
    parser.add_argument('--resource-class',action='append',choices=sorted(RESOURCE_CLASSES))
    parser.add_argument('--max-runs',type=int);parser.add_argument('--replicate',type=int)
    args=parser.parse_args()
    try:
        summary=run_campaign(**vars(args));print(json.dumps(summary,sort_keys=True))
        return 2 if any(r['state'] not in {'DONE'} for r in summary.get('outcomes',[]) if not r.get('skipped')) else 0
    except (OSError,ValueError,KeyError,TypeError) as error:
        parser.exit(2,'INVALID: '+str(error)+'\n')

if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--_owned-launch':
        raise SystemExit(owned_launcher(int(sys.argv[2]),json.loads(sys.argv[3])))
    raise SystemExit(main())

"""Scheduler integration tests: synthetic PROJECTED data in repository tempdirs only."""
import csv
import ctypes
import hashlib
import importlib.util
import json
import os
import pathlib
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent

def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()

def kill_test_child(identity,manifests):
    """Only cleanup the precise subprocess created by a test; no PID-only kill."""
    libc=ctypes.CDLL(None,use_errno=True)
    fd=libc.pidfd_open(identity['pid'],0)
    if fd<0:return
    try:
        if manifests.identity_alive(identity):libc.pidfd_send_signal(fd,signal.SIGKILL,None,0)
    finally:os.close(fd)

class MatrixRunnerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('run_matrix'), 'bounded runner is not implemented')
        import run_matrix
        import run_manifest
        import resource_guard
        import export_results
        self.runner, self.manifests, self.guards, self.exporter = run_matrix, run_manifest, resource_guard, export_results
        self.tmp = tempfile.TemporaryDirectory(prefix='.test-matrix-', dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.base = pathlib.Path(self.tmp.name)
        self.cli_tools=self.base/'scripts'/'eval';self.cli_tools.mkdir(parents=True)
        for name in ('run_matrix.py','run_manifest.py','resource_guard.py','export_results.py','validate_results.py'):
            shutil.copyfile(HERE/name,self.cli_tools/name)
        self.results = self.base / 'results'
        self.matrix = self.base / 'matrix.csv'
        self.row = dict(cell_id='synthetic-00001', eq='EQ3', group='feasibility', repeats='2', resource_class='CPU_ONLY', blocking_gate='G-test', rho='0.25')
        self.write_matrix()
        self.producer = self.base / 'producer.py'
        self.producer.write_text('''import json, os, pathlib, sys, time
p = pathlib.Path(os.environ['HBFSIM_ATTEMPT_DIR'])
(p/'started').write_text(str(os.getpid()))
time.sleep(float(sys.argv[1]))
if sys.argv[2] == 'fail':
    (p/'raw.partial.json').write_text('{"synthetic": true}')
    raise SystemExit(7)
row=json.loads(os.environ['HBFSIM_CONDITION_JSON'])
r={k:'' for k in ''' + repr(__import__('validate_results').COLUMNS) + '''}
r.update(provenance='PROJECTED',eq=row['eq'],figure='synthetic-test-only',panel='test',run_id=os.environ['HBFSIM_RUN_ID'],git_sha=os.environ['HBFSIM_GIT_SHA'],hardware='CPU-test',model='synthetic-test',workload='synthetic-test',mode='test',backend='test',operation='test',profile='test',rho=row['rho'],metric='checksum_ok',value='1',unit='bool',replicate=os.environ['HBFSIM_REPLICATE'],schema_version='1',branch=os.environ['HBFSIM_BRANCH'],series='test',source_file=__file__,source_function='synthetic_fixture',source_line_start='1',source_line_end='1')
(p/'raw.results.json').write_text(json.dumps([r]))
(p/'raw.data.json').write_text(json.dumps({'label':'SYNTHETIC TEST ONLY','checksum':1}))
print('producer complete',flush=True)
print('producer diagnostic',file=sys.stderr,flush=True)
''')
        self.validator = self.base / 'validator.py'
        self.validator.write_text("import json,pathlib,sys\np=pathlib.Path(sys.argv[1])\nassert json.loads((p/'raw.data.json').read_text())['checksum']==1\nraise SystemExit(int(sys.argv[2]))\n")
        self.config = self.base / 'config.json'
        self.config.write_text('{"synthetic_fixture_only":true}')
        self.spec = self.base / 'tasks.json'
        self.receipt = self.base / 'gate.json'
        self.evidence = self.base / 'gate-evidence.json'
        self.evidence.write_text('{"synthetic_test_gate":true,"passed":true}')
        self.task = dict(id='synthetic-test-only', implemented=True, foreground=True,
            conditions={self.row['cell_id']: self.manifests.canonical_hash(self.row)}, resource_class='CPU_ONLY',
            argv=[str(pathlib.Path(sys.executable).resolve()),str(self.producer),'0','okay'],
            validator_argv=[str(pathlib.Path(sys.executable).resolve()),str(self.validator),'{attempt_dir}','0'],
            artifacts=[dict(path=str(p),sha256=digest(p),role=role) for p,role in [(pathlib.Path(sys.executable).resolve(),'build'),(self.producer,'build'),(self.validator,'build'),(self.config,'config')]],
            gate_receipts=[str(self.receipt)], result_file='raw.results.json', raw_artifacts=['raw.data.json'],
            expected_metrics=['checksum_ok'], provenance=dict(provenance='PROJECTED',assumptions=['Synthetic integration-test fixture only; no scientific result.']),timeout_seconds=4)
        self.freeze()

    def write_matrix(self):
        with self.matrix.open('w', newline='') as stream:
            w = csv.DictWriter(stream,fieldnames=list(self.row));w.writeheader();w.writerow(self.row)

    def freeze(self):
        self.task['conditions']={self.row['cell_id']:self.manifests.canonical_hash(self.row)}
        self.spec.write_text(json.dumps(dict(schema_version=1,tasks=[self.task])))
        bindings=self.runner.task_bindings(self.base,self.matrix,self.task,self.row)
        self.receipt.write_text(json.dumps(dict(schema_version=1,gate='G-test',status='PASS',bindings=bindings,
            validator=dict(argv=[str(self.validator)],exit_code=0),artifacts=[dict(path=str(self.evidence),sha256=digest(self.evidence))])))

    def execute(self, **kwargs):
        return self.runner.run_campaign(root=self.base,matrix=self.matrix,results=self.results,registry=self.spec,max_runs=1,replicate=1,**kwargs)

    def run_dir(self):
        return self.results/'runs'/'synthetic-00001-r001'

    def status(self):
        return json.loads((self.run_dir()/'status.json').read_text())

    def attempt(self):
        return self.run_dir()/self.status()['attempt']

    def cli(self, *args):
        return [sys.executable,str(self.cli_tools/'run_matrix.py'),'--matrix',str(self.matrix),'--results',str(self.results),'--registry',str(self.spec),*args]

    def test_plan_is_default_and_matrix_replicates_filter(self):
        p=subprocess.run(self.cli(),text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(p.stdout)['planned_runs'],2)
        self.assertFalse(self.results.exists())
        p=subprocess.run(self.cli('--plan','--replicate','2','--eq','EQ3','--only','synthetic-00001'),text=True,capture_output=True)
        self.assertEqual(json.loads(p.stdout)['planned_runs'],1)
        self.assertFalse(self.results.exists())

    def test_actual_subprocess_done_validates_and_skips_resume(self):
        self.execute()
        self.assertEqual(self.status()['state'],'DONE')
        attempt=self.attempt()
        self.assertIn('producer complete',(attempt/'stdout.log').read_text())
        self.assertIn('producer diagnostic',(attempt/'stderr.log').read_text())
        manifest=json.loads((attempt/'manifest.json').read_text())
        self.assertEqual(manifest['validation']['semantic_exit_code'],0)
        self.assertTrue(manifest['raw_hashes']['raw.data.json'])
        self.execute(resume=True)
        self.assertEqual(len(list((self.run_dir()/'attempts').iterdir())),1)
        rows=self.exporter.collect_done(self.results)
        self.assertEqual(len(rows[0]),1)
        self.assertEqual(rows[0][0]['provenance'],'PROJECTED')

    def test_missing_gold_never_spawns_and_cannot_export(self):
        self.receipt.unlink()
        self.execute()
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')
        self.assertFalse((self.attempt()/'started').exists())
        self.assertEqual(self.exporter.collect_done(self.results)[0],[])

    def test_unregistered_condition_fails_closed(self):
        self.task['conditions']={};self.freeze()
        data=json.loads(self.spec.read_text());data['tasks']=[];self.spec.write_text(json.dumps(data))
        self.execute()
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')

    def test_failed_attempt_is_preserved_on_explicit_resume(self):
        self.task['argv'][-1]='fail';self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'FAILED')
        first=self.attempt()
        self.assertTrue((first/'raw.partial.json').exists())
        failure_manifest=json.loads((first/'manifest.json').read_text())
        self.assertEqual(failure_manifest.get('raw_hashes',{}).get('raw.partial.json'),digest(first/'raw.partial.json'))
        self.task['argv'][-1]='okay';self.freeze();self.execute(resume=True)
        self.assertEqual(self.status()['state'],'DONE')
        self.assertEqual(json.loads((first/'status.json').read_text())['state'],'FAILED')
        self.assertEqual(len(list((self.run_dir()/'attempts').iterdir())),2)

    def test_failed_actual_validator_prevents_done(self):
        self.task['validator_argv'][-1]='9';self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')
        self.assertTrue((self.attempt()/'raw.data.json').exists())
        self.assertEqual(self.exporter.collect_done(self.results)[0],[])

    def test_bound_artifact_change_and_missing_metrics_fail(self):
        self.config.write_text('{"changed":true}')
        self.execute();self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')
        self.task['artifacts'][-1]['sha256']=digest(self.config)
        self.task['expected_metrics'].append('service_gbs');self.freeze();self.execute(resume=True)
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')

    def test_sigterm_retains_attempt_then_resumes(self):
        self.task['argv'][-2]='3';self.freeze()
        child=subprocess.Popen(self.cli('--max-runs','1','--replicate','1'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(lambda:child.poll() is None and child.kill())
        deadline=time.monotonic()+6
        while time.monotonic()<deadline and not list(self.results.glob('runs/*/attempts/*/started')):
            time.sleep(.03)
        self.assertTrue(list(self.results.glob('runs/*/attempts/*/started')))
        child.send_signal(signal.SIGTERM);child.communicate(timeout=6)
        self.assertEqual(self.status()['state'],'INTERRUPTED')
        first=self.attempt()
        self.task['argv'][-2]='0';self.freeze();self.execute(resume=True)
        self.assertEqual(self.status()['state'],'DONE')
        self.assertEqual(json.loads((first/'status.json').read_text())['state'],'INTERRUPTED')

    def test_live_process_identity_and_abandoned_attempt(self):
        self.execute()
        status=self.status();status.update(state='RUNNING',owner=self.manifests.process_identity(os.getpid()),child=None)
        (self.run_dir()/'status.json').write_text(json.dumps(status));self.execute(resume=True)
        self.assertEqual(self.status()['state'],'RUNNING')
        status['owner']['boot_id']='previous-boot';(self.run_dir()/'status.json').write_text(json.dumps(status));(self.attempt()/'status.json').write_text(json.dumps(status))
        self.execute(resume=True)
        self.assertEqual(self.status()['state'],'DONE')
        self.assertEqual(len(list((self.run_dir()/'attempts').iterdir())),2)

    def test_gpu_foreign_and_unavailable_block_before_launch(self):
        self.row.update(group='gpu_delay',resource_class='GPU_EXCLUSIVE');self.write_matrix()
        self.task.update(resource_class='GPU_EXCLUSIVE',gpu_uuid='GPU-test');self.freeze()
        for snapshot in [dict(available=False,error='driver unavailable',processes=[]),dict(available=True,gpu_uuid='GPU-test',processes=[dict(pid=999999,name='foreign')])]:
            self.execute(resume=True,gpu_probe=lambda gpu:snapshot)
            self.assertEqual(self.status()['state'],'BLOCKED_GPU_BUSY')
            self.assertFalse((self.attempt()/'started').exists())

    def test_gpu_foreign_midrun_contaminates_and_preserves_raw(self):
        self.row.update(group='gpu_delay',resource_class='GPU_EXCLUSIVE');self.write_matrix()
        self.task.update(resource_class='GPU_EXCLUSIVE',gpu_uuid='GPU-test');self.task['argv'][-2]='.3';self.freeze()
        calls=[]
        def probe(gpu):
            calls.append(1)
            return dict(available=True,gpu_uuid='GPU-test',processes=[] if len(calls)<4 else [dict(pid=999999,name='foreign')])
        self.execute(gpu_probe=probe,poll_seconds=.03)
        self.assertEqual(self.status()['state'],'CONTAMINATED_EXTERNAL_GPU')
        self.assertTrue((self.attempt()/'raw.gpu.jsonl').exists())
        self.assertEqual(self.exporter.collect_done(self.results)[0],[])

    def test_storage_unknown_or_root_backing_blocks(self):
        self.row.update(group='flash_fidelity',resource_class='STORAGE_EXCLUSIVE',backend='physical');self.write_matrix()
        self.task['resource_class']='STORAGE_EXCLUSIVE';self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'BLOCKED_STORAGE_BUSY')
        self.assertFalse((self.attempt()/'started').exists())

    def test_shared_safe_performance_cannot_reach_done(self):
        self.row.update(group='routing_capture',resource_class='GPU_SHARED_SAFE');self.write_matrix()
        self.task.update(resource_class='GPU_SHARED_SAFE',gpu_uuid='GPU-test',expected_metrics=['service_gbs']);self.freeze()
        self.execute(gpu_probe=lambda gpu:dict(available=True,gpu_uuid='GPU-test',processes=[]))
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')
        self.assertFalse((self.attempt()/'started').exists())

    def test_cross_process_resource_lock_exclusion(self):
        lock=self.base/'exclusive.lock'
        code="import fcntl,pathlib,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); time.sleep(3)"
        child=subprocess.Popen([sys.executable,'-c',code,str(lock)],stdout=subprocess.PIPE,text=True)
        self.addCleanup(lambda:child.poll() is None and child.kill())
        self.assertEqual(child.stdout.readline().strip(),'locked')
        with self.assertRaises(self.guards.ResourceBusy):
            with self.guards.FileLock(lock):
                self.fail('acquired a live foreign lock')
        child.terminate();child.wait(timeout=4);child.stdout.close()

    def test_export_rechecks_provenance_and_raw_hashes(self):
        self.execute();(self.attempt()/'raw.data.json').write_text('{"tampered":true}')
        with self.assertRaisesRegex(ValueError,'hash'):
            self.exporter.collect_done(self.results)

    def test_actual_timeout_is_failed_and_owned_child_stops(self):
        self.task['argv'][-2]='3';self.task['timeout_seconds']=.15;self.freeze();self.execute(poll_seconds=.02)
        self.assertEqual(self.status()['state'],'FAILED')
        pid=int((self.attempt()/'started').read_text())
        self.assertIsNone(self.manifests.process_identity(pid))

    def test_result_mutated_by_semantic_validator_never_done(self):
        self.validator.write_text("import pathlib,json,sys\np=pathlib.Path(sys.argv[1])/'raw.results.json'\nr=json.loads(p.read_text());r[0]['rho']='0.5';p.write_text(json.dumps(r))\n")
        self.task['artifacts'][2]['sha256']=digest(self.validator);self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')

    def test_export_requires_full_artifact_chain(self):
        self.execute();attempt=self.attempt()
        manifest=json.loads((attempt/'manifest.json').read_text());manifest['artifact_hashes']={}
        (attempt/'manifest.json').write_text(json.dumps(manifest))
        for status_path in (attempt/'status.json',self.run_dir()/'status.json'):
            status=json.loads(status_path.read_text());status['manifest_sha256']=digest(attempt/'manifest.json');status_path.write_text(json.dumps(status))
        with self.assertRaisesRegex(ValueError,'artifact'):
            self.exporter.collect_done(self.results)

    def test_same_run_live_lock_is_not_reentered(self):
        self.task['argv'][-2]='.6';self.freeze()
        child=subprocess.Popen(self.cli('--max-runs','1','--replicate','1'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(lambda:child.poll() is None and child.kill())
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and not list(self.results.glob('runs/*/attempts/*/started')):time.sleep(.02)
        self.assertTrue(list(self.results.glob('runs/*/attempts/*/started')))
        outcome=self.execute(resume=True)
        self.assertTrue(outcome['outcomes'][0]['skipped'])
        self.assertEqual(len(list((self.run_dir()/'attempts').iterdir())),1)
        stdout,stderr=child.communicate(timeout=6)
        self.assertEqual(child.returncode,0,stderr)

    def test_gpu_owned_child_and_descendant_are_excluded(self):
        self.row.update(group='gpu_delay',resource_class='GPU_EXCLUSIVE');self.write_matrix()
        self.task.update(resource_class='GPU_EXCLUSIVE',gpu_uuid='GPU-test')
        self.producer.write_text("import subprocess,sys\nhelper=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.1)'])\n"+self.producer.read_text()+"\nhelper.wait()\n")
        self.task['artifacts'][1]['sha256']=digest(self.producer);self.task['argv'][-2]='.2';self.freeze()
        observed=[]
        def probe(gpu):
            status_files=list(self.results.glob('runs/*/status.json'))
            identity=json.loads(status_files[0].read_text()).get('child') if status_files else None
            owned=self.manifests.owned_processes(identity)
            observed.append(len(owned))
            return dict(available=True,gpu_uuid='GPU-test',processes=[dict(pid=pid,identity=value) for pid,value in owned.items()])
        self.execute(gpu_probe=probe,poll_seconds=.02)
        self.assertEqual(self.status()['state'],'DONE',self.status())
        self.assertGreaterEqual(max(observed),2)
        environment=json.loads((self.attempt()/'environment.json').read_text())
        self.assertEqual(environment['child_environment']['CUDA_VISIBLE_DEVICES'],'GPU-test')

    def test_sigkill_owner_keeps_live_child_from_being_retaken(self):
        self.task['argv'][-2]='.8';self.freeze()
        child=subprocess.Popen(self.cli('--max-runs','1','--replicate','1'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(lambda:child.poll() is None and child.kill())
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and not list(self.results.glob('runs/*/attempts/*/started')):time.sleep(.02)
        self.assertTrue(list(self.results.glob('runs/*/attempts/*/started')))
        identity=self.status()['child']
        child.kill();child.communicate(timeout=3)
        result=self.execute(resume=True)
        self.assertTrue(result['outcomes'][0]['skipped'])
        self.assertEqual(self.status()['state'],'RUNNING')
        deadline=time.monotonic()+3
        while self.manifests.owned_processes(identity) and time.monotonic()<deadline:time.sleep(.03)
        self.task['argv'][-2]='0';self.freeze();self.execute(resume=True)
        self.assertEqual(self.status()['state'],'DONE')
        self.assertEqual(len(list((self.run_dir()/'attempts').iterdir())),2)

    def test_storage_frozen_identity_mismatch_and_foreign_users_block(self):
        self.row.update(group='flash_fidelity',resource_class='STORAGE_EXCLUSIVE',backend='physical');self.write_matrix()
        self.task['resource_class']='STORAGE_EXCLUSIVE'
        storage=self.base/'storage.json'
        identity=dict(file='/synthetic-dedicated/file',disk_sysfs_path='/synthetic-dedicated/device')
        storage.write_text(json.dumps(dict(schema_version=1,authorized=True,dedicated=True,exclusive=True,access='read_only',authorization_ref='synthetic-test-only',file=identity['file'],identity=identity)))
        self.task['storage_manifest']=str(storage)
        self.task['artifacts'].append(dict(path=str(storage),sha256=digest(storage),role='config'));self.freeze()
        for snapshot in [dict(identity=dict(identity,file='changed'),foreign_pids=[]),dict(identity=identity,foreign_pids=[999999])]:
            self.execute(resume=True,storage_probe=lambda file:snapshot)
            self.assertEqual(self.status()['state'],'BLOCKED_STORAGE_BUSY')
            self.assertFalse((self.attempt()/'started').exists())
            self.assertTrue((self.attempt()/'raw.storage.jsonl').exists())

    def test_root_storage_is_rejected_without_payload_read(self):
        self.config.chmod(0o400)
        with self.assertRaisesRegex(ValueError,'root'):
            self.guards.storage_snapshot(str(self.config))

    def test_export_bundle_runs_unchanged_strict_validator(self):
        self.execute();out=self.base/'export'
        result=self.exporter.export(self.results,out)
        self.assertEqual(result['runs'],1)
        validator=subprocess.run([sys.executable,str(HERE/'validate_results.py'),'--input',str(out/'eval.csv'),'--strict-no-mock'],capture_output=True,text=True)
        self.assertEqual(validator.returncode,0,validator.stderr)

    def test_done_attempt_recovers_interrupted_root_pointer_commit(self):
        self.execute();status=self.status();status.update(state='RUNNING',owner=dict(status['owner'],boot_id='previous-boot'))
        (self.run_dir()/'status.json').write_text(json.dumps(status))
        self.execute(resume=True)
        self.assertEqual(self.status()['state'],'DONE')
        self.assertEqual(len(list((self.run_dir()/'attempts').iterdir())),1)

    def test_gate_evidence_is_preserved_and_checked_on_export(self):
        self.execute();manifest=json.loads((self.attempt()/'manifest.json').read_text())
        receipt=next(iter(manifest['gate_receipts'].values()))
        self.assertTrue(receipt.get('evidence'),'gold evidence needs a frozen local copy')
        evidence=self.attempt()/receipt['evidence'][0]['path']
        self.assertEqual(digest(evidence),digest(self.evidence))
        evidence.write_text('tampered')
        with self.assertRaisesRegex(ValueError,'hash'):self.exporter.collect_done(self.results)

    def test_missing_projected_assumptions_blocks_before_spawn(self):
        self.task['provenance']={'provenance':'PROJECTED'};self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')
        self.assertFalse((self.attempt()/'started').exists())

    def test_gpu_shared_safe_requires_explicit_no_timing_contract(self):
        self.row.update(group='routing_capture',resource_class='GPU_SHARED_SAFE');self.write_matrix()
        self.task.update(resource_class='GPU_SHARED_SAFE',gpu_uuid='GPU-test');self.freeze()
        self.execute(gpu_probe=lambda gpu:dict(available=True,gpu_uuid='GPU-test',processes=[]))
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')
        self.assertFalse((self.attempt()/'started').exists())

    def test_storage_backing_resolves_root_lvm_partitions(self):
        self.assertTrue(hasattr(self.guards,'backing_disks'),'root backing ancestry must be verified')
        disk=self.base/'sysfs-disk';partition=disk/'partition1';partition.mkdir(parents=True)
        (partition/'partition').write_text('1')
        (disk/'slaves').mkdir()
        dm=self.base/'sysfs-dm';(dm/'slaves').mkdir(parents=True)
        (dm/'slaves'/'part').symlink_to(partition)
        self.assertEqual(self.guards.backing_disks(dm),{disk.resolve()})

    def test_abandoned_without_resume_reports_actual_interrupted_state(self):
        self.execute();status=self.status();status.update(state='RUNNING',owner=dict(status['owner'],boot_id='previous-boot'))
        (self.run_dir()/'status.json').write_text(json.dumps(status));(self.attempt()/'status.json').write_text(json.dumps(status))
        result=self.execute()
        self.assertEqual(result['outcomes'][0]['state'],'INTERRUPTED')
        self.assertEqual(len(list((self.run_dir()/'attempts').iterdir())),1)

    def test_storage_exclusivity_includes_sibling_partitions(self):
        self.assertTrue(hasattr(self.guards,'disk_device_numbers'),'exclusivity must cover every partition of the disk')
        disk=self.base/'fake-sysfs-disk';disk.mkdir();(disk/'dev').write_text('8:0')
        for name,device in [('part1','8:1'),('part2','8:2')]:
            part=disk/name;part.mkdir();(part/'partition').write_text('1');(part/'dev').write_text(device)
        self.assertEqual(self.guards.disk_device_numbers(disk),{os.makedev(8,0),os.makedev(8,1),os.makedev(8,2)})

    def test_timeout_stops_owned_worker_in_separate_process_group(self):
        original=self.producer.read_text()
        self.producer.write_text("import subprocess,sys,os,pathlib\nworker=subprocess.Popen([sys.executable,'-c','import time;time.sleep(4)'],preexec_fn=os.setpgrp)\npathlib.Path(os.environ['HBFSIM_ATTEMPT_DIR'],'worker.pid').write_text(str(worker.pid))\n"+original)
        self.task['artifacts'][1]['sha256']=digest(self.producer);self.task['argv'][-2]='3';self.task['timeout_seconds']=.2;self.freeze()
        try:
            self.execute(poll_seconds=.02)
            worker=int((self.attempt()/'worker.pid').read_text())
            self.assertEqual(self.status()['state'],'FAILED')
            self.assertIsNone(self.manifests.process_identity(worker),'owned worker survived timeout cleanup in another process group')
        finally:
            paths=list(self.results.glob('runs/*/attempts/*/worker.pid'))
            if paths:
                identity=self.manifests.process_identity(int(paths[0].read_text()))
                if identity:
                    kill_test_child(identity,self.manifests)

    def test_reused_session_leader_pid_is_never_owned_or_signaled(self):
        boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        old=dict(pid=777771,boot_id=boot,start_time=100,ppid=1,pgrp=777771,session=777771)
        reused=dict(old,start_time=900)
        process=mock.Mock();process.wait.return_value=0
        with mock.patch.object(pathlib.Path,'iterdir',return_value=[pathlib.Path('/proc/777771')]),mock.patch.object(self.manifests,'process_identity',return_value=reused),mock.patch.object(os,'killpg') as killpg,mock.patch.object(self.runner,'signal_identity',create=True) as pidfd:
            self.assertFalse(self.manifests.identity_alive(old))
            self.assertEqual(self.manifests.owned_processes(old),{},'recycled session leader is foreign')
            self.runner.stop_child(process,old)
            killpg.assert_not_called();pidfd.assert_not_called()

    def test_verified_descendant_survives_leader_exit_identity_check(self):
        worker_file=self.base/'detached-worker.pid'
        script="import subprocess,sys,os,pathlib,time; child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(4)'],preexec_fn=os.setpgrp); pathlib.Path(sys.argv[1]).write_text(str(child.pid));time.sleep(.3)"
        leader=subprocess.Popen([sys.executable,'-c',script,str(worker_file)],start_new_session=True)
        identity=self.manifests.process_identity(leader.pid)
        try:
            deadline=time.monotonic()+3
            while not worker_file.exists() and time.monotonic()<deadline:time.sleep(.01)
            worker=int(worker_file.read_text())
            self.assertIn(worker,self.manifests.owned_processes(identity))
            leader.wait(timeout=3)
            self.assertIn(worker,self.manifests.owned_processes(identity))
            self.runner.stop_child(leader,identity)
            self.assertIsNone(self.manifests.process_identity(worker))
        finally:
            if leader.poll() is None:leader.kill();leader.wait(timeout=3)
            if worker_file.exists():
                current=self.manifests.process_identity(int(worker_file.read_text()))
                if current:
                    kill_test_child(current,self.manifests)

    def test_cleanup_failure_retains_live_identity_and_blocks_resume(self):
        self.task['argv'][-2]='4';self.task['timeout_seconds']=.1;self.freeze()
        retained=[];real_stop=self.runner.stop_child
        def track_stop(process,identity):
            retained.append(process)
            return real_stop(process,identity)
        try:
            with mock.patch.object(self.runner,'stop_child',side_effect=track_stop),mock.patch.object(self.runner,'signal_identity',side_effect=PermissionError('synthetic pidfd refusal')):
                self.execute(poll_seconds=.02)
            self.assertEqual(self.status()['state'],'FAILED')
            self.assertIsNotNone(self.status()['child'],'live owned child identity was erased')
            result=self.execute(resume=True)
            self.assertTrue(result['outcomes'][0]['skipped'])
            self.assertEqual(len(list((self.run_dir()/'attempts').iterdir())),1)
        finally:
            paths=list(self.results.glob('runs/*/attempts/*/started'))
            if paths:
                identity=self.manifests.process_identity(int(paths[0].read_text()))
                if identity:
                    kill_test_child(identity,self.manifests)
            for process in retained:process.wait(timeout=3)

    def test_omitted_frozen_scientific_dimensions_cannot_be_done(self):
        self.row.update(service_multiplier='1.2',time_scale='1');self.write_matrix();self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE')

    def test_frozen_scientific_dimensions_survive_export(self):
        self.row.update(service_multiplier='1.2',time_scale='1');self.write_matrix()
        self.producer.write_text(self.producer.read_text().replace("(p/'raw.results.json').write_text", "r.update(service_multiplier=row['service_multiplier'],time_scale=row['time_scale'])\n(p/'raw.results.json').write_text"))
        self.task['artifacts'][1]['sha256']=digest(self.producer);self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'DONE',self.status())
        out=self.base/'dimension-export';self.exporter.export(self.results,out)
        with (out/'eval.csv').open(newline='') as stream:rows=list(csv.DictReader(stream))
        self.assertEqual(rows[0]['service_multiplier'],'1.2');self.assertEqual(rows[0]['time_scale'],'1')

    def test_resource_lock_is_shared_across_different_output_roots(self):
        first=self.base/'attempt-one';second=self.base/'attempt-two';first.mkdir();second.mkdir()
        task=dict(resource_class='GPU_EXCLUSIVE',gpu_uuid='GPU-test')
        probe=lambda gpu:dict(available=True,gpu_uuid='GPU-test',processes=[])
        # The fixture project is fixed; only the selected output directory differs.
        with self.guards.ResourceGuard(self.base/'results-one',first,task,gpu_probe=probe,project_root=self.base):
            with self.assertRaises(self.guards.ResourceBusy):
                with self.guards.ResourceGuard(self.base/'results-two',second,task,gpu_probe=probe,project_root=self.base):pass

    def test_parent_crash_before_identity_commit_never_starts_payload(self):
        self.task['argv'][-2]='3';self.freeze()
        wrapper=self.base/'crash-before-record.py'
        wrapper.write_text("import os,pathlib,sys\nsys.path.insert(0,sys.argv[1])\nimport run_matrix as r\noriginal=r.process_identity\ndef crash(pid,*args,**kwargs):\n    if pid!=os.getpid():os._exit(88)\n    return original(pid,*args,**kwargs)\nr.process_identity=crash\nr.run_campaign(root=pathlib.Path(sys.argv[2]),matrix=pathlib.Path(sys.argv[3]),results=pathlib.Path(sys.argv[4]),registry=pathlib.Path(sys.argv[5]),max_runs=1,replicate=1)\n")
        try:
            result=subprocess.run([sys.executable,str(wrapper),str(HERE),str(self.base),str(self.matrix),str(self.results),str(self.spec)],capture_output=True,text=True,timeout=5)
            self.assertEqual(result.returncode,88,result.stderr)
            time.sleep(.3)
            self.assertFalse(list(self.results.glob('runs/*/attempts/*/started')),'payload ran before a durable identity commit')
        finally:
            paths=list(self.results.glob('runs/*/attempts/*/started'))
            for path in paths:
                identity=self.manifests.process_identity(int(path.read_text()))
                if identity:kill_test_child(identity,self.manifests)

    def test_incidental_symlink_never_commits_done_and_can_resume(self):
        original=self.producer.read_text()
        self.producer.write_text(original+"\n(p/'artifact-link').symlink_to(pathlib.Path(__file__))\n")
        self.task['artifacts'][1]['sha256']=digest(self.producer);self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE',self.status())
        first=self.attempt()
        self.assertTrue((first/'artifact-link').is_symlink())
        self.assertEqual(self.exporter.collect_done(self.results)[0],[])
        self.producer.write_text(original);self.task['artifacts'][1]['sha256']=digest(self.producer);self.freeze();self.execute(resume=True)
        self.assertEqual(self.status()['state'],'DONE',self.status())
        self.assertEqual(json.loads((first/'status.json').read_text())['state'],'INVALID_GOLD_GATE')
        self.assertEqual(len(self.exporter.collect_done(self.results)[0]),1)

    def test_validator_mutated_gate_evidence_never_commits_done(self):
        self.validator.write_text("import pathlib,sys\np=pathlib.Path(sys.argv[1])\nfor evidence in (p/'gates'/'evidence').iterdir():evidence.write_text('synthetic mutation')\n")
        self.task['artifacts'][2]['sha256']=digest(self.validator);self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE',self.status())
        self.assertEqual(self.exporter.collect_done(self.results)[0],[])

    def test_validator_mutated_gate_receipt_never_commits_done(self):
        self.validator.write_text("import pathlib,sys,json\np=pathlib.Path(sys.argv[1])\nfor receipt in (p/'gates').glob('*.json'):\n    value=json.loads(receipt.read_text());value['status']='FAIL';receipt.write_text(json.dumps(value))\n")
        self.task['artifacts'][2]['sha256']=digest(self.validator);self.freeze();self.execute()
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE',self.status())
        self.assertEqual(self.exporter.collect_done(self.results)[0],[])

    def test_precommit_validation_uses_export_artifact_checks(self):
        self.execute();(self.attempt()/'stdout.log').write_text('changed after seal')
        with self.assertRaisesRegex(ValueError,'hash'):
            self.exporter.validate_attempt(self.attempt(),require_done=False)

    def test_export_rejects_fifo_artifacts_without_blocking(self):
        self.execute();attempt=self.attempt()
        manifest=json.loads((attempt/'manifest.json').read_text())
        receipt=next(iter(manifest['gate_receipts'].values()))
        paths=[attempt/receipt['evidence'][0]['path'],attempt/receipt['path'],attempt/'manifest.json',attempt/'status.json',self.run_dir()/'status.json',attempt/'raw.results.json',attempt/'raw.data.json',attempt/'environment.json']
        for index,path in enumerate(paths):
            with self.subTest(artifact=str(path.relative_to(self.base))):
                original=path.read_bytes();path.unlink();os.mkfifo(path)
                try:
                    try:
                        result=subprocess.run([sys.executable,str(HERE/'export_results.py'),'--results',str(self.results),'--out',str(self.base/f'fifo-export-{index}')],capture_output=True,text=True,timeout=2)
                    except subprocess.TimeoutExpired:
                        self.fail('export blocked while opening a FIFO artifact: '+str(path.relative_to(self.base)))
                    self.assertEqual(result.returncode,2,result.stderr)
                    self.assertIn('artifact',result.stderr)
                    self.assertFalse((self.base/f'fifo-export-{index}').exists())
                finally:
                    path.unlink();path.write_bytes(original)

    def test_producer_fifo_artifact_never_commits_done(self):
        self.producer.write_text(self.producer.read_text()+"\nos.mkfifo(p/'incidental-fifo')\n")
        self.task['artifacts'][1]['sha256']=digest(self.producer);self.freeze()
        result=subprocess.run(self.cli('--max-runs','1','--replicate','1'),capture_output=True,text=True,timeout=4)
        self.assertEqual(result.returncode,2,result.stderr)
        self.assertEqual(self.status()['state'],'INVALID_GOLD_GATE',self.status())
        self.assertEqual(self.exporter.collect_done(self.results)[0],[])

    def test_matrix_resource_classification_is_complete(self):
        with (ROOT/'docs/49-eval-audit/run-matrix.csv').open() as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual(len(rows),2848);self.assertEqual(sum(int(r['repeats']) for r in rows),20485)
        for row in rows:
            self.assertIn(row.get('resource_class'),self.guards.RESOURCE_CLASSES)
            if row['group']=='flash_fidelity':self.assertEqual(row['resource_class'],'STORAGE_EXCLUSIVE' if row['backend']=='physical' else 'CPU_ONLY')
            if row['group']=='three_arm':self.assertEqual(row['resource_class'],'GPU_STORAGE_EXCLUSIVE')
            if row['group'] in {'gpu_delay','async_overlap','async_correctness','live_confirmation','mode_cost'}:self.assertEqual(row['resource_class'],'GPU_EXCLUSIVE')

if __name__=='__main__':
    unittest.main()

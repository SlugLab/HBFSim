"""TEST_ONLY CPU acquisition fixtures. Never read or write an SSD payload."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import subprocess
import hashlib
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/eval/collect_storage.py'


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'storage collector implementation is missing')
        spec = importlib.util.spec_from_file_location('collect_storage', SCRIPT)
        self.api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.api)
        self.temp = tempfile.TemporaryDirectory(prefix='.collector-test-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def plan(self, family='fixed_arrival_trace', qd=2):
        return dict(kind=family, qd=qd, seed=17, alignment_bytes=4096,
                    region_offset=0, region_length=65536, test_only=True,
                    requests=[dict(request_id=i, offset=i * 4096, bytes=4096,
                                   operation='read', issue_ns=i * 1000000)
                              if family == 'fixed_arrival_trace' else
                              dict(request_id=i, offset=i * 4096, bytes=4096, operation='read')
                              for i in range(6)])

    def settings(self, **extra):
        return dict(mode='pilot', warmup_seconds=0, steady_seconds=.15,
                    min_completions=1, timeout_seconds=.6, poll_seconds=.001, **extra)

    def engine(self, plan=None, reader=None, guard=None, stop=None, settings=None):
        plan = plan or self.plan()
        guard = guard or self.api.TestGuard()
        events = []
        reader = reader or (lambda offset, size: size)
        summary = self.api.acquire(plan, reader, guard, settings or self.settings(),
                                   events.append, stop=stop or threading.Event())
        return summary, events

    def test_fixed_arrivals_preserve_requested_actual_and_lag(self):
        def read(offset, size):
            time.sleep(.004)
            return size
        summary, events = self.engine(reader=read)
        complete = [e for e in events if e['event'] == 'completion']
        self.assertEqual(len(complete), 6)
        for record in complete:
            self.assertEqual(record['requested_issue_ns'], record['request_id'] * 1000000)
            self.assertGreaterEqual(record['actual_submit_ns'], record['requested_issue_ns'])
            self.assertEqual(record['admission_lag_ns'], record['actual_submit_ns'] - record['requested_issue_ns'])
            self.assertGreaterEqual(record['completion_ns'], record['actual_submit_ns'])
        self.assertEqual(summary['issued'], summary['completed'])
        self.assertEqual(summary['completed_bytes'], 6 * 4096)
        self.assertLessEqual(summary['max_outstanding'], 2)
        self.assertEqual(summary['provenance'], 'TEST_ONLY')

    def test_closed_loop_refills_only_after_observed_completion(self):
        summary, events = self.engine(self.plan('closed_loop_qd'), reader=lambda offset, size: (time.sleep(.003) or size))
        outstanding = 0
        for event in events:
            if event['event'] == 'issue':
                outstanding += 1
                self.assertLessEqual(outstanding, 2)
                self.assertIsNone(event['requested_issue_ns'])
            elif event['event'] == 'completion':
                outstanding -= 1
                self.assertIsNone(event['admission_lag_ns'])
        self.assertEqual(outstanding, 0)
        self.assertEqual(summary['issued'], 6)

    def test_read_only_memfd_payload_fixture_is_never_physical_evidence(self):
        fd = os.memfd_create('TEST_ONLY_storage_fixture')
        self.addCleanup(os.close, fd)
        os.ftruncate(fd, 65536)
        def read(offset, size):
            return len(os.pread(fd, size, offset))
        summary, _ = self.engine(reader=read)
        self.assertEqual(summary['provenance'], 'TEST_ONLY')
        self.assertFalse(summary['formal_acquisition_bounds_met'])

    def test_worker_start_failure_precedes_payload_admission(self):
        calls, events = [], []
        original_start = threading.Thread.start
        starts = 0
        def start(thread):
            nonlocal starts
            starts += 1
            if starts == 2:
                raise RuntimeError('TEST_ONLY worker start failure')
            return original_start(thread)
        def reader(offset, size):
            calls.append(offset)
            time.sleep(.01)
            return size
        with patch.object(threading.Thread, 'start', start):
            with self.assertRaisesRegex(RuntimeError, 'worker start failure'):
                self.api.acquire(self.plan('closed_loop_qd'), reader, self.api.TestGuard(),
                                 self.settings(), events.append)
        self.assertEqual(calls, [])
        self.assertFalse(any(e['event']=='issue' for e in events))
        abort = next(e for e in events if e['event']=='abort')
        self.assertEqual(abort['issued'], 0)
        self.assertEqual(abort['outstanding_request_ids'], [])

    def test_range_and_alignment_violation_precedes_every_read(self):
        for offset, size in ((-4096, 4096), (1, 4096), (65536, 4096), (0, 1)):
            plan = self.plan()
            plan['requests'][-1].update(offset=offset, bytes=size)
            calls = []
            with self.assertRaises(ValueError):
                self.engine(plan, reader=lambda offset, size: calls.append(offset))
            self.assertEqual(calls, [])

    def test_ambiguous_request_id_types_reject_before_reads(self):
        for value in (True,1.5,None,''):
            plan=self.plan()
            plan['requests'][-1]['request_id']=value
            calls=[]
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.engine(plan,reader=lambda offset,size:calls.append(offset))
            self.assertEqual(calls,[])

    def test_formal_minimum_bounds_and_short_reads_fail(self):
        settings = self.settings()
        settings['mode'] = 'formal'
        with self.assertRaises(ValueError):
            self.engine(settings=settings)
        with self.assertRaisesRegex(ValueError, 'short read'):
            self.engine(reader=lambda offset, size: size - 1)

    def test_periodic_guard_and_interruption_stop_new_issues_preserve_events(self):
        stop = threading.Event()
        events = []
        def read(offset, size):
            stop.set()
            return size
        with self.assertRaises(self.api.CollectionInterrupted):
            self.api.acquire(self.plan(), read, self.api.TestGuard(), self.settings(),
                             events.append, stop=stop)
        self.assertTrue(events)
        self.assertLessEqual(sum(e['event'] == 'issue' for e in events), 2)
        class Guard:
            def check(self, phase):
                if phase == 'periodic':
                    raise self_error('foreign storage user')
        self_error = self.api.ResourceBusy
        with self.assertRaises(self.api.ResourceBusy):
            self.engine(reader=lambda offset, size: (time.sleep(.01) or size), guard=Guard())

    def test_statistics_use_completed_bytes_over_entire_steady_interval(self):
        summary, events = self.engine()
        completed = [e for e in events if e['event'] == 'completion' and e['in_steady']]
        self.assertEqual(summary['steady_completed_bytes'], sum(e['bytes'] for e in completed))
        self.assertEqual(summary['throughput_bytes_per_second'], summary['steady_completed_bytes'] / .15)
        self.assertLessEqual(summary['latency_p50_ns'], summary['latency_p99_ns'])

    def test_direct_reader_requires_preflight_and_opens_only_read_direct(self):
        manifest = dict(file='/UNCREATED_TEST_ONLY_FILE', identity=dict(file_inode=1, file_size=65536, major_minor='1:1'))
        with patch.object(self.api.os, 'open') as opened:
            with self.assertRaises(ValueError):
                self.api.DirectReader(manifest, 4096).__enter__()
        opened.assert_not_called()

    def test_attempt_blocked_before_payload_retains_diagnostics(self):
        out = self.base / 'attempt'
        with patch.object(self.api, 'load_plan', side_effect=self.api.ResourceBusy('blocked file', 'BLOCKED_STORAGE_BUSY')):
            result = self.api.collect(self.base / 'absent-split', 'cell', self.base / 'device.json', out)
        self.assertEqual(result['state'], 'BLOCKED_STORAGE_BUSY')
        for name in ('manifest.json', 'status.json', 'stdout.log', 'stderr.log', 'raw.requests.jsonl'):
            self.assertTrue((out / name).is_file())
        with self.assertRaises(FileExistsError):
            self.api.collect(self.base / 'absent-split', 'cell', self.base / 'device.json', out)

    def assert_output_rejected_before_metadata_reads(self, root, out, **injection):
        reads = []
        def record_read(path):
            reads.append(str(path))
            return b'{}'
        # A simulated checkout keeps even the RED regression's writes inside
        # this CPU fixture, never inside the actual formal results directory.
        with patch.object(self.api, 'ROOT', root), \
                patch.object(self.api, 'regular_bytes', side_effect=record_read), \
                patch.object(self.api, 'hash_regular', return_value='test-hash') as build_read, \
                patch.object(self.api, 'git_snapshot', return_value={}) as git_read, \
                patch.object(self.api, 'environment_snapshot', return_value={}) as environment_read, \
                patch.object(self.api, 'load_plan', side_effect=self.api.ResourceBusy('TEST_ONLY unavailable', 'BLOCKED_STORAGE_BUSY')) as inputs:
            with self.assertRaises(ValueError):
                self.api.collect('unused', 'cell', 'unused', out, **injection)
            self.assertEqual(reads, [])
            build_read.assert_not_called()
            git_read.assert_not_called()
            environment_read.assert_not_called()
            inputs.assert_not_called()
        self.assertFalse(out.exists())

    def test_each_test_injection_is_rejected_under_formal_runs_before_creation(self):
        root = self.base / 'checkout'
        runs = root / 'results/runs'
        runs.mkdir(parents=True)
        for index, injection in enumerate(({'reader_factory': object()}, {'guard_factory': object()}, {'clock': object()})):
            with self.subTest(injection=index):
                self.assert_output_rejected_before_metadata_reads(root, runs / str(index), **injection)

    def test_test_only_formal_destination_symlink_alias_is_rejected(self):
        root = self.base / 'checkout'
        runs = root / 'results/runs'
        runs.mkdir(parents=True)
        alias = root / 'run-alias'
        alias.symlink_to(runs, target_is_directory=True)
        self.assert_output_rejected_before_metadata_reads(root, alias / 'attempt', reader_factory=object())

    def test_all_output_modes_reject_destinations_outside_checkout(self):
        root = self.base / 'checkout'
        root.mkdir()
        for index, injection in enumerate(({}, {'reader_factory': object()})):
            with self.subTest(injection=index):
                self.assert_output_rejected_before_metadata_reads(root, self.base / ('outside-' + str(index)), **injection)

    def test_outside_symlink_and_parent_traversal_destinations_are_rejected(self):
        root = self.base / 'checkout'
        root.mkdir()
        outside = self.base / 'outside'
        outside.mkdir()
        (root / 'escape').symlink_to(outside, target_is_directory=True)
        for out in (root / 'escape/attempt', root / '../traversal-attempt'):
            with self.subTest(out=str(out)):
                self.assert_output_rejected_before_metadata_reads(root, out)

    def test_injected_collection_is_explicit_test_only_and_failed_never_done(self):
        out = self.base / 'attempt'
        plan = self.plan()
        plan.update(device={}, condition={}, profile_sha256='fixture', device_manifest_sha256='fixture')
        with patch.object(self.api, 'load_plan', return_value=plan):
            result = self.api.collect('unused', 'cell', 'unused', out, settings=self.settings(),
                                      reader_factory=lambda plan: self.api.TestReader(lambda offset, size: size),
                                      guard_factory=lambda plan, out: self.api.TestGuard())
        self.assertEqual(result['state'], 'TEST_ONLY_DONE', result)
        self.assertEqual(json.loads((out / 'raw.summary.json').read_text())['provenance'], 'TEST_ONLY')
        with patch.object(self.api, 'load_plan', return_value=plan):
            result = self.api.collect('unused', 'cell', 'unused', self.base / 'failed', settings=self.settings(),
                                      reader_factory=lambda plan: self.api.TestReader(lambda offset, size: 1),
                                      guard_factory=lambda plan, out: self.api.TestGuard())
        self.assertEqual(result['state'], 'FAILED')

    def test_worker_environment_alone_cannot_authorize_io(self):
        with patch.dict(os.environ, HBFSIM_ATTEMPT_DIR=str(self.base), HBFSIM_RUN_ID='fake'):
            with self.assertRaises(self.api.ResourceBusy):
                self.api.verify_worker(self.base, self.plan(), ROOT)

    def worker_fixture(self, mutation=None, hold_lock=True, wrong_lock=False):
        from resource_guard import FileLock
        from run_manifest import process_identity, atomic_json, canonical_hash
        project = self.base / 'project'
        attempt = project / 'attempt'
        attempt.mkdir(parents=True)
        device = attempt / 'device.json'
        device.write_text('{}')
        disk = '/TEST_ONLY/disk'
        plan = self.plan()
        plan.update(cell_id='test-cell', condition={'cell_id': 'test-cell'},
                    replicate=1, matrix_sha256='TEST_ONLY_MATRIX_HASH',
                    device_manifest=str(device), device_manifest_sha256=self.api.digest(device.read_bytes()),
                    device={'identity': {'disk_sysfs_path': disk}})
        task = dict(resource_class='STORAGE_EXCLUSIVE', storage_manifest=str(device),
                    conditions={'test-cell': canonical_hash(plan['condition'])},
                    artifacts=[dict(path=str(device), role='config', sha256=plan['device_manifest_sha256']),
                               dict(path=str(SCRIPT), role='build', sha256=self.api.digest(SCRIPT.read_bytes()))])
        manifest = dict(root=str(project), run_id='test-run', condition=plan['condition'],
                        replicate=1, matrix_sha256='TEST_ONLY_MATRIX_HASH',
                        resource_class='STORAGE_EXCLUSIVE', task=task, task_sha256=canonical_hash(task))
        atomic_json(attempt / 'manifest.json', manifest)
        atomic_json(attempt / 'plan.json', plan)
        worker = attempt / 'worker.py'
        worker.write_text("import json,os,pathlib,sys,time\nsys.path.insert(0,sys.argv[1])\nimport collect_storage as c\na=pathlib.Path(sys.argv[2])\nwhile not (a/'go').exists():time.sleep(.001)\ntry:c.verify_worker(a,json.loads((a/'plan.json').read_text()),a.parent)\nexcept c.ResourceBusy:sys.exit(3)\n")
        lock_path = project / 'results/locks' / ('storage-' + hashlib.sha256(disk.encode()).hexdigest()[:24] + '.lock')
        if wrong_lock:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.touch()
        lock = FileLock(lock_path.with_name('wrong.lock') if wrong_lock else lock_path, state='BLOCKED_STORAGE_BUSY')
        lock.__enter__()
        if not hold_lock:
            lock.__exit__()
        env = dict(os.environ, HBFSIM_ATTEMPT_DIR=str(attempt), HBFSIM_RUN_ID='test-run', HBFSIM_STORAGE_MANIFEST=str(device), HBFSIM_REPLICATE='1')
        process = subprocess.Popen([sys.executable, str(worker), str(SCRIPT.parent), str(attempt)], env=env)
        self.addCleanup(lambda: process.wait(timeout=3))
        try:
            status = dict(owner=process_identity(os.getpid()), child=process_identity(process.pid),
                          state='RUNNING', phase='producer')
            if mutation:
                mutation(status, manifest, device)
            atomic_json(attempt / 'status.json', status)
            atomic_json(attempt / 'manifest.json', manifest)
            (attempt / 'go').touch()
            return process.wait(timeout=3)
        finally:
            (attempt / 'go').touch()
            lock.__exit__()

    def test_actual_parent_lock_proof_and_released_lock_rejection(self):
        self.assertEqual(self.worker_fixture(), 0)

    def test_released_parent_lock_is_not_a_guard_capability(self):
        self.assertEqual(self.worker_fixture(hold_lock=False), 3)

    def test_parent_holding_a_different_lock_is_not_a_capability(self):
        self.assertEqual(self.worker_fixture(wrong_lock=True), 3)

    def test_recycled_parent_pid_cannot_authorize_worker(self):
        self.assertEqual(self.worker_fixture(lambda status, *_: status['owner'].update(start_time=-1)), 3)

    def test_dead_parent_identity_cannot_authorize_worker(self):
        self.assertEqual(self.worker_fixture(lambda status, *_: status.update(owner=None)), 3)

    def test_swapped_storage_manifest_cannot_authorize_worker(self):
        self.assertEqual(self.worker_fixture(lambda status, manifest, device: device.write_text('{"swapped":true}')), 3)

    def test_wrong_task_condition_cannot_authorize_worker(self):
        self.assertEqual(self.worker_fixture(lambda status, manifest, _: manifest.update(condition={})), 3)

    def test_wrong_matrix_or_replicate_cannot_authorize_worker(self):
        self.assertEqual(self.worker_fixture(lambda status, manifest, _: manifest.update(replicate=2)), 3)

    def test_abort_diagnostics_are_durable_before_reads_drain(self):
        release, started, stop = threading.Event(), threading.Event(), threading.Event()
        out = self.base / 'interrupted'
        plan = self.plan()
        plan.update(device={'identity': {'TEST_ONLY': True}}, condition={})
        def read(offset, size):
            started.set()
            release.wait(2)
            return size
        results = []
        with patch.object(self.api, 'load_plan', return_value=plan):
            thread = threading.Thread(target=lambda: results.append(self.api.collect(
                'unused', 'cell', 'unused', out, settings=self.settings(), stop=stop,
                reader_factory=lambda p: self.api.TestReader(read),
                guard_factory=lambda p, o: self.api.TestGuard())))
            thread.start()
            try:
                self.assertTrue(started.wait(2))
                stop.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    status = json.loads((out / 'status.json').read_text())
                    if status['state'] == 'INTERRUPTED':
                        break
                    time.sleep(.005)
                self.assertEqual(status['state'], 'INTERRUPTED')
                self.assertTrue(status['outstanding_request_ids'])
                self.assertFalse(status['kernel_reads_canceled'])
                self.assertTrue(thread.is_alive())
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]['state'], 'INTERRUPTED')

    def test_internal_injected_reader_cannot_emit_measured_provenance(self):
        plan = self.plan()
        plan['test_only'] = False
        summary, _ = self.engine(plan)
        self.assertEqual(summary['provenance'], 'TEST_ONLY')

    def test_all_failed_outstanding_reads_leave_terminal_diagnostics(self):
        events = []
        def read(offset, size):
            time.sleep(.003)
            raise OSError('TEST_ONLY read error')
        with self.assertRaises(OSError):
            self.api.acquire(self.plan('closed_loop_qd', qd=4), read, self.api.TestGuard(), self.settings(), events.append)
        issued = {e['request_id'] for e in events if e['event'] == 'issue'}
        terminal = {e['request_id'] for e in events if e['event'] == 'read_error'}
        self.assertEqual(issued, terminal)

    def test_output_hashing_does_not_treat_long_raw_logs_as_small_input_metadata(self):
        out = self.base / 'large-log'
        plan = self.plan()
        plan.update(device={}, condition={})
        original = self.api.regular_bytes
        def metadata_only(path):
            if Path(path).name == 'raw.requests.jsonl':
                raise ValueError('raw log exceeds small input metadata limit')
            return original(path)
        with patch.object(self.api, 'load_plan', return_value=plan), patch.object(self.api, 'regular_bytes', side_effect=metadata_only):
            result = self.api.collect('unused', 'cell', 'unused', out, settings=self.settings(),
                                      reader_factory=lambda p: self.api.TestReader(lambda offset, size: size),
                                      guard_factory=lambda p, o: self.api.TestGuard())
        self.assertEqual(result['state'], 'TEST_ONLY_DONE', result)

    def test_direct_reader_uses_aligned_read_only_direct_flags_on_memfd_fixture(self):
        fd = os.memfd_create('TEST_ONLY_direct_reader')
        self.addCleanup(os.close, fd)
        os.ftruncate(fd, 65536)
        os.fchmod(fd, 0o444)
        info = os.fstat(fd)
        device = dict(file='/UNCREATED_TEST_ONLY_FILE', identity=dict(file_inode=info.st_ino,
                      file_size=info.st_size, major_minor=f'{os.major(info.st_dev)}:{os.minor(info.st_dev)}'))
        events = []
        class Guard(self.api.TestGuard):
            def check(self, phase):
                events.append(phase)
        def open_memfd(path, flags):
            self.assertEqual(events, ['before-open'])
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            self.assertTrue(flags & os.O_DIRECT)
            self.assertFalse(flags & (os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            return os.dup(fd)
        actual_preadv = os.preadv
        def aligned_read(opened, buffers, offset):
            address = self.api.ctypes.addressof(self.api.ctypes.c_char.from_buffer(buffers[0]))
            self.assertEqual(address % 4096, 0)
            return actual_preadv(opened, buffers, offset)
        with patch.object(self.api.os, 'open', side_effect=open_memfd), patch.object(self.api.os, 'stat', side_effect=lambda *a, **k: os.fstat(fd)), patch.object(self.api.os, 'preadv', side_effect=aligned_read):
            with self.assertRaisesRegex(ValueError, 'identity'):
                with self.api.DirectReader(device, 4096, Guard(), qd=2, max_bytes=8192) as reader:
                    result = reader.read(4096, 8192)
                    self.assertEqual(result['count'], 8192)
                    self.assertGreaterEqual(result['completion_ns'], result['submit_ns'])
                    # Mutation after the final payload must still fail at close.
                    os.ftruncate(fd, 32768)

    def storage_fixture(self):
        from test_storage_split import StorageSplitTests
        fixture = StorageSplitTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.write_json('device.json', dict(schema_version=1, authorized=True, dedicated=True,
            exclusive=True, access='read_only', authorization_ref='TEST_ONLY_CPU_FIXTURE',
            file='/UNCREATED_TEST_ONLY_FILE', identity=dict(file_inode=1, file_size=65536,
            major_minor='1:1', disk_sysfs_path='/TEST_ONLY/disk'),
            read_region=dict(offset=0, length=65536, alignment_bytes=4096)))
        fixture.prepare()
        return fixture

    def test_real_frozen_split_plan_and_profile_gate_without_hardware(self):
        fixture = self.storage_fixture()
        cells = fixture.api.load_split(fixture.out)['cells']
        calibration = next(c['cell_id'] for c in cells if c['role'] == 'calibration' and c['backend'] == 'physical')
        plan = self.api.load_plan(fixture.out, calibration, fixture.device)
        self.assertEqual(plan['seed'], 17)
        self.assertEqual(plan['qd'], 1)
        heldout = next(c['cell_id'] for c in cells if c['role'] == 'heldout' and c['backend'] == 'physical')
        with self.assertRaises(ValueError):
            self.api.load_plan(fixture.out, heldout, fixture.device)
        profile, receipt = fixture.calibrated()
        target = fixture.base / 'frozen-profile'
        frozen = fixture.api.freeze_profile(fixture.out, profile, receipt, target)
        plan = self.api.load_plan(fixture.out, heldout, fixture.device, target)
        self.assertEqual(plan['validation_id'], frozen['validation_id'])
        result = self.api.collect(fixture.out, heldout, fixture.device, self.base / 'complete', target,
            self.settings(), reader_factory=lambda p: self.api.TestReader(lambda offset, size: size),
            guard_factory=lambda p, o: self.api.TestGuard())
        self.assertEqual(result['state'], 'TEST_ONLY_DONE')

    def test_standalone_guard_blocks_before_reader_factory(self):
        fixture = self.storage_fixture()
        cell = next(c['cell_id'] for c in fixture.api.load_split(fixture.out)['cells']
                    if c['role'] == 'calibration' and c['backend'] == 'physical')
        calls = []
        result = self.api.collect(fixture.out, cell, fixture.device, self.base / 'blocked',
            settings=self.settings(), reader_factory=lambda p: calls.append('payload'))
        self.assertEqual(result['state'], 'BLOCKED_STORAGE_BUSY')
        self.assertEqual(calls, [])

    def test_guarded_worker_still_checks_foreign_storage_users(self):
        fixture = self.storage_fixture()
        cell = next(c['cell_id'] for c in fixture.api.load_split(fixture.out)['cells']
                    if c['role'] == 'calibration' and c['backend'] == 'physical')
        calls = []
        def guard_factory(plan, out):
            guard = self.api.CollectorGuard(plan, out, worker_attempt=self.base)
            guard.storage_probe = lambda path: dict(identity=plan['device']['identity'], foreign_pids=[999999])
            return guard
        with patch.object(self.api, 'verify_worker', return_value={}):
            result = self.api.collect(fixture.out, cell, fixture.device, self.base / 'foreign',
                settings=self.settings(), reader_factory=lambda p: calls.append('payload'), guard_factory=guard_factory)
        self.assertEqual(result['state'], 'BLOCKED_STORAGE_BUSY')
        self.assertEqual(calls, [])

    def test_timeout_is_recorded_before_uncancelable_read_drains(self):
        started, release = threading.Event(), threading.Event()
        events = []
        def reader(offset, size):
            started.set()
            release.wait(2)
            return size
        def run():
            try:
                self.api.acquire(self.plan(), reader, self.api.TestGuard(),
                    dict(self.settings(), steady_seconds=.04, timeout_seconds=.06), events.append)
            except TimeoutError:
                pass
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            deadline = time.monotonic() + 1
            while not any(e['event'] == 'abort' for e in events) and time.monotonic() < deadline:
                time.sleep(.003)
            abort = next(e for e in events if e['event'] == 'abort')
            self.assertEqual(abort['state'], 'FAILED')
            self.assertFalse(abort['kernel_reads_canceled'])
            self.assertTrue(thread.is_alive())
        finally:
            release.set()
            thread.join(3)


if __name__ == '__main__':
    unittest.main()

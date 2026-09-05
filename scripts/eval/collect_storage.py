#!/usr/bin/env python3
"""Read-only EQ1-B dedicated-file acquisition, one frozen physical cell per call.

Default is planning: --execute is required to open a payload. All execution
requires --split, --cell-id, --device-manifest and a NEW --out directory;
heldout cells additionally require --profile-freeze. Formal defaults require
10 s warmup, 30 s steady interval, and 10000 steady completions. --mode pilot
allows explicit smaller bounds and never closes formal acquisition gates.

The authorization manifest uses ResourceGuard's dedicated/read_only schema and
adds read_region={offset,length,alignment_bytes}. Request offsets are absolute
file offsets, constrained to that region. Alignment must be a power of two,
at least 512 bytes. No write, trim, cache-drop, buffered-read fallback, device
configuration, synthetic arrival generation, or automatic trace looping exists.
Closed-loop policy requests are issued once in order, refilling after observed
completion. Fixed requested issue_ns are retained alongside actual submissions
and admission lag. Host outstanding QD is distinct from controller queue depth.

Standalone execution acquires ResourceGuard normally. --guarded-worker verifies
the live scheduler parent/child identity, durable RUNNING producer state, exact
task/condition/device bindings and parent's canonical FLOCK WRITE ownership,
then runs the same physical storage checks without reacquiring that lock. No
environment variable alone bypasses a guard. Proof is rechecked throughout.

Python worker threads bound outstanding reads to QD. A timeout/signal stops new
issues and durably records outstanding IDs before draining existing reads.
An in-kernel stuck read cannot be canceled by this transport; it keeps the
attempt non-DONE and requires the scheduler's outer supervision. No process is
signaled by this collector. Read syscall duration, host observation delay and
overall control timeout are separate fields. Raw metadata is retained on every
failure; only complete successful acquisition publishes DONE last.

Library injection is for CPU tests only and forces TEST_ONLY provenance and
TEST_ONLY_DONE status; it cannot create physical evidence or close formal gates.
Every output destination must resolve inside this checkout. Test-only attempts
are forbidden under the canonical results/runs tree, including symlink aliases;
these checks precede output creation and acquisition metadata reads.
No actual hardware readiness has been established by implementing this tool.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import ctypes
import hashlib
import json
import math
import mmap
import os
from pathlib import Path
import queue
import signal
import stat
import sys
import threading
import time
import traceback
import uuid

from freeze_storage_split import (decode, digest, encoded, load_profile_freeze,
                                 read_object, regular_bytes, split_snapshot)
from resource_guard import ResourceBusy, ResourceGuard
from run_manifest import (atomic_json, canonical_hash, environment_snapshot,
                          git_snapshot, now, process_identity, same_process)

ROOT = Path(__file__).resolve().parents[2]


class CollectionInterrupted(RuntimeError):
    pass


class RealClock:
    monotonic_ns = staticmethod(time.monotonic_ns)
    sleep = staticmethod(time.sleep)


class TestGuard:
    """Explicit CPU-test dependency; production factories never construct this."""
    test_only = True
    def check(self, phase):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        pass


class TestReader:
    test_only = True
    def __init__(self, function):
        self.read = function
    def __enter__(self):
        return self
    def __exit__(self, *unused):
        pass


def settings_checked(settings):
    result = dict(mode='formal', warmup_seconds=10., steady_seconds=30.,
                  min_completions=10000, timeout_seconds=90., poll_seconds=.01,
                  guard_seconds=1.)
    result.update(settings or {})
    if result['mode'] not in ('formal', 'pilot'):
        raise ValueError('mode must be formal or pilot')
    for key in ('warmup_seconds', 'steady_seconds', 'timeout_seconds', 'poll_seconds', 'guard_seconds'):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid duration: ' + key)
    if not result['steady_seconds'] or not 0 < result['poll_seconds'] <= 1:
        raise ValueError('steady duration and bounded poll interval must be positive')
    if not 0 < result['guard_seconds'] <= 1:
        raise ValueError('guard interval must be positive and at most one second')
    if result['timeout_seconds'] <= result['warmup_seconds'] + result['steady_seconds']:
        raise ValueError('timeout must exceed warmup plus steady interval')
    if type(result['min_completions']) is not int or result['min_completions'] < 1:
        raise ValueError('positive minimum completion count required')
    if result['mode'] == 'formal' and (result['warmup_seconds'] < 10 or result['steady_seconds'] < 30 or result['min_completions'] < 10000):
        raise ValueError('formal acquisition requires warmup>=10 s, steady>=30 s, completions>=10000')
    return result


def validate_requests(plan):
    if plan['kind'] not in ('fixed_arrival_trace', 'closed_loop_qd') or type(plan['qd']) is not int or not 1 <= plan['qd'] <= 128:
        raise ValueError('invalid arrival family or bounded QD')
    alignment = plan['alignment_bytes']
    if type(alignment) is not int or alignment < 512 or alignment & (alignment - 1):
        raise ValueError('alignment must be a power of two >=512')
    start, length = plan['region_offset'], plan['region_length']
    if type(start) is not int or start < 0 or type(length) is not int or length <= 0:
        raise ValueError('invalid authorized file span')
    if not plan['requests']:
        raise ValueError('frozen request list is empty')
    previous = -1
    ids = set()
    for request in plan['requests']:
        offset, size = request['offset'], request['bytes']
        if type(offset) is not int or type(size) is not int or offset < start or size <= 0 or offset + size > start + length:
            raise ValueError('request escapes authorized file span')
        if offset % alignment or size % alignment or request['operation'] != 'read':
            raise ValueError('request alignment/read operation mismatch')
        if request['request_id'] in ids:
            raise ValueError('duplicate request ID')
        ids.add(request['request_id'])
        if plan['kind'] == 'fixed_arrival_trace':
            issue = request.get('issue_ns')
            if type(issue) is not int or issue < 0 or issue < previous:
                raise ValueError('fixed issue_ns must be ordered nonnegative integers')
            previous = issue
        elif 'issue_ns' in request:
            raise ValueError('closed-loop policy cannot contain requested timestamps')


def load_plan(split, cell_id, device_manifest, profile_freeze=None):
    frozen, snapshot = split_snapshot(split)
    cells = [cell for cell in frozen['cells'] if cell['cell_id'] == cell_id]
    if len(cells) != 1 or cells[0]['backend'] != 'physical':
        raise ValueError('collector requires one exact physical flash_fidelity cell')
    cell = cells[0]
    pair = next(pair for pair in frozen['pairs'] if pair['pair_id'] == cell['pair_id'])
    device_raw = regular_bytes(device_manifest)
    if digest(device_raw) != frozen['device_manifest_sha256']:
        raise ValueError('device authorization does not match frozen split')
    device = decode(device_raw)
    for key, expected in dict(schema_version=1, authorized=True, dedicated=True, exclusive=True, access='read_only').items():
        if device.get(key) != expected:
            raise ResourceBusy('frozen storage authorization unavailable: ' + key, 'BLOCKED_STORAGE_BUSY')
    if not device.get('authorization_ref'):
        raise ResourceBusy('missing storage authorization reference', 'BLOCKED_STORAGE_BUSY')
    region = device.get('read_region')
    if not isinstance(region, dict) or not {'offset', 'length', 'alignment_bytes'} <= region.keys():
        raise ValueError('authorization must freeze read_region offset/length/alignment_bytes')
    profile_sha = frozen['profile_sha256']
    validation_id = None
    profile_path = str(Path(split).absolute() / 'calibration/initial-profile')
    if cell['role'] == 'heldout':
        if not profile_freeze:
            raise ValueError('heldout acquisition requires frozen calibrated profile')
        profile = load_profile_freeze(profile_freeze)
        if profile['split_id'] != frozen['split_id'] or profile['split_manifest_sha256'] != snapshot['manifest_sha256']:
            raise ValueError('calibrated profile belongs to a different split')
        profile_sha, validation_id = profile['profile_sha256'], profile['validation_id']
        profile_path = str(Path(profile_freeze).absolute() / 'profile')
    raw = snapshot['artifacts'][pair['input_path']]
    records = [decode(line) for line in raw.splitlines() if line.strip()] if pair['kind'] == 'fixed_arrival_trace' else decode(raw)['requests']
    requests = []
    for index, record in enumerate(records):
        request = dict(record)
        request['offset'] = record.get('offset', record.get('logical_address'))
        request.setdefault('request_id', index)
        requests.append(request)
    plan = dict(kind=pair['kind'], qd=int(cell['condition']['qd']), seed=cell['seed'],
                condition=cell['condition'], role=cell['role'], cell_id=cell_id,
                requests=requests, alignment_bytes=region['alignment_bytes'],
                region_offset=region['offset'], region_length=region['length'],
                device=device, device_manifest=str(Path(device_manifest).absolute()),
                device_manifest_sha256=digest(device_raw), profile_sha256=profile_sha,
                profile_path=profile_path, validation_id=validation_id,
                input_sha256=digest(raw), split_id=frozen['split_id'],
                split_manifest_sha256=snapshot['manifest_sha256'], matrix_sha256=frozen['matrix_sha256'],
                _input_bytes=raw, test_only=False)
    validate_requests(plan)
    if plan['region_offset'] + plan['region_length'] > device['identity']['file_size']:
        raise ValueError('authorized read region exceeds frozen file size')
    return plan


def verify_worker(attempt, plan, project_root=ROOT):
    """Prove parent ownership instead of treating an environment string as a lease."""
    try:
        attempt = Path(attempt).resolve()
        status = read_object(attempt / 'status.json')
        manifest = read_object(attempt / 'manifest.json')
        parent = process_identity(os.getppid())
        child = process_identity(os.getpid())
        if not same_process(parent, status.get('owner')) or not same_process(child, status.get('child')):
            raise ValueError('scheduler parent/child identity is absent, dead, or recycled')
        if status.get('state') != 'RUNNING' or status.get('phase') != 'producer':
            raise ValueError('worker is not the durable RUNNING producer')
        if str(attempt) != os.environ.get('HBFSIM_ATTEMPT_DIR') or manifest['run_id'] != os.environ.get('HBFSIM_RUN_ID'):
            raise ValueError('worker run/attempt environment mismatch')
        if Path(manifest['root']).resolve() != Path(project_root).resolve():
            raise ValueError('worker project root mismatch')
        if manifest['condition'] != plan['condition'] or manifest['resource_class'] != 'STORAGE_EXCLUSIVE':
            raise ValueError('worker condition/resource class mismatch')
        if (manifest['matrix_sha256'] != plan['matrix_sha256'] or manifest['replicate'] != plan['replicate'] or
                os.environ.get('HBFSIM_REPLICATE') != str(plan['replicate'])):
            raise ValueError('worker matrix/replicate binding mismatch')
        task = manifest['task']
        if canonical_hash(task) != manifest['task_sha256'] or task['resource_class'] != 'STORAGE_EXCLUSIVE':
            raise ValueError('worker task hash/class mismatch')
        if task['conditions'].get(plan['cell_id']) != canonical_hash(plan['condition']):
            raise ValueError('worker task condition binding mismatch')
        if task['storage_manifest'] != plan['device_manifest'] or os.environ.get('HBFSIM_STORAGE_MANIFEST') != plan['device_manifest']:
            raise ValueError('worker storage manifest path mismatch')
        if digest(regular_bytes(plan['device_manifest'])) != plan['device_manifest_sha256']:
            raise ValueError('worker storage manifest changed')
        expected = {plan['device_manifest']: ('config', plan['device_manifest_sha256']),
                    str(Path(__file__).resolve()): ('build', digest(regular_bytes(__file__)))}
        for path, (role, sha) in expected.items():
            if not any(a.get('path') == path and a.get('role') == role and a.get('sha256') == sha for a in task['artifacts']):
                raise ValueError('worker build/config hash binding missing')
        key = hashlib.sha256(plan['device']['identity']['disk_sysfs_path'].encode()).hexdigest()[:24]
        lock = Path(project_root) / 'results/locks' / ('storage-' + key + '.lock')
        if lock.is_symlink():
            raise ValueError('canonical storage lock cannot be a symbolic link')
        info = lock.stat()
        matches = []
        for line in Path('/proc/locks').read_text().splitlines():
            fields = line.split()
            if len(fields) != 8 or fields[1:4] != ['FLOCK', 'ADVISORY', 'WRITE']:
                continue
            major, minor, inode = fields[5].split(':')
            if int(major, 16) == os.major(info.st_dev) and int(minor, 16) == os.minor(info.st_dev) and int(inode) == info.st_ino:
                matches.append(int(fields[4]))
        if matches != [parent['pid']] or not same_process(parent, process_identity(parent['pid'])):
            raise ValueError('live parent canonical FLOCK WRITE ownership is unproven')
        return dict(parent=parent, child=child, lock=str(lock), lock_inode=info.st_ino)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ResourceBusy('guarded-worker proof failed: ' + str(error), 'BLOCKED_STORAGE_BUSY') from error


class CollectorGuard(ResourceGuard):
    def __init__(self, plan, out, worker_attempt=None):
        super().__init__(ROOT / 'results', out, dict(resource_class='STORAGE_EXCLUSIVE',
                         storage_manifest=plan['device_manifest']), project_root=ROOT)
        self.plan, self.worker_attempt = plan, worker_attempt
        self.storage = plan['device']

    def proof(self, phase):
        if digest(regular_bytes(self.plan['device_manifest'])) != self.plan['device_manifest_sha256']:
            raise ResourceBusy('storage manifest changed', 'BLOCKED_STORAGE_BUSY')
        if self.worker_attempt:
            proof = verify_worker(self.worker_attempt, self.plan)
            with (self.attempt / 'raw.worker-guard.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(proof, phase=phase, timestamp=now())) + '\n')
                stream.flush()
                os.fsync(stream.fileno())

    def __enter__(self):
        self.proof('preflight')
        if self.worker_attempt:
            self._storage('preflight')
            return self
        return super().__enter__()

    def check(self, phase='periodic'):
        self.proof(phase)
        super().check(phase)


class DirectReader:
    """Aligned O_DIRECT preadv only, guarded before payload open and identity checked."""
    def __init__(self, device, alignment, guard=None, qd=1, max_bytes=4096):
        self.device, self.alignment, self.guard = device, alignment, guard
        self.qd, self.max_bytes = qd, max_bytes
        self.fd = None
        self.buffers = []
        self.available = queue.Queue()

    def fingerprint(self):
        opened = os.fstat(self.fd)
        path = os.stat(self.device['file'], follow_symlinks=False)
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mode', 'st_mtime_ns', 'st_ctime_ns')
        identity = tuple(getattr(opened, key) for key in keys)
        if identity != tuple(getattr(path, key) for key in keys) or not stat.S_ISREG(opened.st_mode) or opened.st_mode & 0o222:
            raise ValueError('dedicated file identity/mode changed')
        expected = self.device['identity']
        if opened.st_ino != expected['file_inode'] or opened.st_size != expected['file_size'] or f'{os.major(opened.st_dev)}:{os.minor(opened.st_dev)}' != expected['major_minor']:
            raise ValueError('opened payload differs from authorized identity')
        return identity

    def __enter__(self):
        if self.guard is None:
            raise ValueError('payload open requires an active resource guard')
        self.guard.check('before-open')
        if not hasattr(os, 'O_DIRECT') or not hasattr(os, 'preadv'):
            raise ValueError('O_DIRECT preadv unavailable; no buffered fallback')
        try:
            self.fd = os.open(self.device['file'], os.O_RDONLY | os.O_DIRECT | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            self.identity = self.fingerprint()
            for _ in range(self.qd):
                memory = mmap.mmap(-1, self.max_bytes + self.alignment)
                address = ctypes.addressof(ctypes.c_char.from_buffer(memory))
                shift = (-address) % self.alignment
                self.buffers.append(memory)
                self.available.put((memory, shift))
            return self
        except BaseException:
            self.__exit__()
            raise

    def read(self, offset, size):
        if self.fingerprint() != self.identity:
            raise ValueError('payload file changed before read')
        memory, shift = self.available.get()
        view = memoryview(memory)[shift:shift + size]
        try:
            submit = time.monotonic_ns()
            count = os.preadv(self.fd, [view], offset)
            complete = time.monotonic_ns()
            if self.fingerprint() != self.identity:
                raise ValueError('payload file changed during read')
            return dict(count=count, submit_ns=submit, completion_ns=complete)
        finally:
            view.release()
            self.available.put((memory, shift))

    def __exit__(self, *unused):
        try:
            if self.fd is not None and hasattr(self, 'identity') and (not unused or unused[0] is None):
                if self.fingerprint() != self.identity:
                    raise ValueError('payload file identity changed before final close')
        finally:
            for memory in self.buffers:
                memory.close()
            self.buffers.clear()
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None


def acquire(plan, reader, guard, settings, emit, stop=None, clock=None):
    """Finite bounded-QD acquisition; injected dependencies never imply hardware proof."""
    settings = settings_checked(settings)
    validate_requests(plan)
    injected_clock = clock is not None
    clock = clock or RealClock()
    stop = stop or threading.Event()
    warmup = int(settings['warmup_seconds'] * 1e9)
    end = warmup + int(settings['steady_seconds'] * 1e9)
    timeout = int(settings['timeout_seconds'] * 1e9)
    if plan['kind'] == 'fixed_arrival_trace' and plan['requests'][-1]['issue_ns'] >= end:
        raise ValueError('frozen fixed arrival lies outside acquisition interval')
    guard.check('start')
    epoch = clock.monotonic_ns()
    active, completions = {}, []
    issued = maximum = 0
    area = previous_time = 0
    pool = ThreadPoolExecutor(max_workers=plan['qd'], thread_name_prefix='owned-storage-read')
    next_guard = epoch

    def periodic():
        nonlocal next_guard
        current = clock.monotonic_ns()
        if current >= next_guard:
            guard.check('periodic')
            next_guard = current + int(settings['guard_seconds'] * 1e9)

    def pause():
        delay = settings['poll_seconds']
        elapsed = clock.monotonic_ns() - epoch
        if plan['kind'] == 'fixed_arrival_trace' and issued < len(plan['requests']) and len(active) < plan['qd']:
            delay = min(delay, max(0, plan['requests'][issued]['issue_ns'] - elapsed) / 1e9)
        if active and not injected_clock:
            wait(active, timeout=delay, return_when=FIRST_COMPLETED)
        else:
            clock.sleep(delay)

    def update_area():
        nonlocal area, previous_time
        current = clock.monotonic_ns() - epoch
        area += max(0, min(current, end) - max(previous_time, warmup)) * len(active)
        previous_time = current

    def perform(request):
        start = clock.monotonic_ns()
        result = reader(request['offset'], request['bytes'])
        finish = clock.monotonic_ns()
        if isinstance(result, dict):
            return result
        return dict(count=result, submit_ns=start, completion_ns=finish)

    def harvest():
        first_error = None
        for future in list(active):
            if not future.done():
                continue
            update_area()
            request = active.pop(future)
            try:
                result = future.result()
                actual, complete = result['submit_ns'] - epoch, result['completion_ns'] - epoch
                requested = request.get('issue_ns') if plan['kind'] == 'fixed_arrival_trace' else None
                record = dict(event='completion', request_id=request['request_id'], offset=request['offset'],
                              bytes=result['count'], requested_bytes=request['bytes'], operation='read', seed=plan['seed'],
                              requested_issue_ns=requested, actual_submit_ns=actual, completion_ns=complete,
                              observed_completion_ns=clock.monotonic_ns() - epoch,
                              admission_lag_ns=actual - requested if requested is not None else None,
                              latency_ns=complete - actual, in_steady=warmup <= complete < end,
                              outstanding_after=len(active))
                emit(record)
                if result['count'] != request['bytes']:
                    raise ValueError('short read: ' + str(request['request_id']))
                if actual < 0 or complete < actual or (requested is not None and actual < requested):
                    raise ValueError('invalid observed request clock order')
                completions.append(record)
            except BaseException as error:
                emit(dict(event='read_error', request_id=request['request_id'], error=str(error)))
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    try:
        # submit() may enqueue a callable and then raise during thread startup.
        # Establish all workers with payload-free tasks first, so this partial
        # submission failure can never leave an unaccounted read in the queue.
        release_workers = threading.Event()
        warm_futures = []
        def ready_worker(ready):
            ready.set()
            release_workers.wait()
        try:
            for _ in range(plan['qd']):
                ready = threading.Event()
                warm_futures.append(pool.submit(ready_worker, ready))
                if not ready.wait(settings['timeout_seconds']):
                    raise TimeoutError('storage worker startup timed out before payload admission')
        finally:
            release_workers.set()
        for future in warm_futures:
            future.result()
        epoch = clock.monotonic_ns()
        next_guard = epoch
        while True:
            if stop.is_set():
                raise CollectionInterrupted('collection interrupted; existing kernel reads will drain')
            elapsed = clock.monotonic_ns() - epoch
            if elapsed >= timeout:
                raise TimeoutError('control timeout; existing kernel reads are not canceled')
            periodic()
            harvest()
            elapsed = clock.monotonic_ns() - epoch
            if elapsed >= end:
                break
            while issued < len(plan['requests']) and len(active) < plan['qd']:
                request = plan['requests'][issued]
                if plan['kind'] == 'fixed_arrival_trace' and request['issue_ns'] > clock.monotonic_ns() - epoch:
                    break
                if stop.is_set() or clock.monotonic_ns() - epoch >= end:
                    break
                update_area()
                emit(dict(event='issue', request_id=request['request_id'], offset=request['offset'],
                          bytes=request['bytes'], operation='read', seed=plan['seed'],
                          requested_issue_ns=request.get('issue_ns'),
                          scheduled_ns=clock.monotonic_ns() - epoch, outstanding_after=len(active) + 1))
                active[pool.submit(perform, request)] = request
                issued += 1
                maximum = max(maximum, len(active))
            pause()
        while active:
            if stop.is_set():
                raise CollectionInterrupted('interrupted while draining')
            if clock.monotonic_ns() - epoch >= timeout:
                raise TimeoutError('control timeout during drain; kernel reads are not canceled')
            periodic()
            harvest()
            if active:
                pause()
        guard.check('end')
        if issued != len(plan['requests']):
            raise ValueError('acquisition interval ended with unissued frozen requests')
        steady = [record for record in completions if record['in_steady']]
        if len(steady) < settings['min_completions']:
            raise ValueError('insufficient steady completions')
        latencies = sorted(record['latency_ns'] for record in steady)
        test_only = (plan.get('test_only') is not False or injected_clock or
                     not isinstance(getattr(reader, '__self__', None), DirectReader) or
                     not isinstance(guard, CollectorGuard))
        return dict(provenance='TEST_ONLY' if test_only else 'MEASURED',
                    measurement_scope='test_fixture' if test_only else 'physical_storage_read_syscall',
                    mode=settings['mode'], issued=issued, completed=len(completions),
                    completed_bytes=sum(record['bytes'] for record in completions),
                    steady_completed=len(steady), steady_completed_bytes=sum(record['bytes'] for record in steady),
                    warmup_seconds=settings['warmup_seconds'], steady_seconds=settings['steady_seconds'],
                    throughput_bytes_per_second=sum(record['bytes'] for record in steady) / settings['steady_seconds'],
                    iops=len(steady) / settings['steady_seconds'], latency_p50_ns=latencies[math.ceil(.5 * len(latencies)) - 1],
                    latency_p99_ns=latencies[math.ceil(.99 * len(latencies)) - 1], percentile_method='nearest_rank_completed_in_steady',
                    max_outstanding=maximum, mean_host_outstanding_qd=area / (end - warmup),
                    qd_scope='host_submitted_until_completion_observed', outstanding=0,
                    formal_acquisition_bounds_met=settings['mode'] == 'formal' and not test_only,
                    scientific_validation_passed=False)
    except BaseException as error:
        # The caller fsyncs this and publishes non-DONE status BEFORE shutdown waits.
        emit(dict(event='abort', error=str(error), error_type=type(error).__name__,
                  state='INTERRUPTED' if isinstance(error, CollectionInterrupted) else
                  error.state if isinstance(error, ResourceBusy) else 'FAILED',
                  outstanding_request_ids=[r['request_id'] for r in active.values()],
                  issued=issued, completed=len(completions),
                  file_identity=plan.get('device', {}).get('identity'), kernel_reads_canceled=False))
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=False)
        # Preserve completions observed during drain even on a failed attempt.
        if active:
            try:
                harvest()
            except BaseException:
                pass


def hash_regular(path):
    """Stream owned raw artifacts/build files without the small-input size limit."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('artifact/build must be a regular file: ' + str(path))
        sha = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
        after = os.fstat(stream.fileno())
        if any(getattr(before, key) != getattr(after, key) for key in ('st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
            raise ValueError('artifact/build changed while hashing')
        return sha.hexdigest()


def collect(split, cell_id, device_manifest, out, profile_freeze=None, settings=None,
            worker_attempt=None, reader_factory=None, guard_factory=None, clock=None, stop=None, replicate=1):
    test_only = any(x is not None for x in (reader_factory, guard_factory, clock))
    root = ROOT.resolve()
    out = Path(out).resolve()
    if not out.is_relative_to(root):
        raise ValueError('collector output must remain inside the authorized checkout')
    if test_only and out.is_relative_to((root / 'results/runs').resolve()):
        raise ValueError('TEST_ONLY output is forbidden under formal results/runs')
    out.mkdir(exist_ok=False)
    for name in ('stdout.log', 'stderr.log', 'raw.requests.jsonl'):
        (out / name).touch()
    manifest = dict(schema_version=1, started_at=now(), argv=list(sys.argv),
                    run_id=os.environ.get('HBFSIM_RUN_ID') if worker_attempt else cell_id + '-' + str(uuid.uuid4()),
                    replicate=replicate, build=dict(executable=str(Path(sys.executable).resolve()),
                        sha256=hash_regular(Path(sys.executable).resolve()), python=sys.version),
                    cell_id=cell_id, split=str(split), device_manifest=str(device_manifest),
                    profile_freeze=str(profile_freeze) if profile_freeze else None,
                    settings=settings, complete=False, test_only=test_only,
                    environment=environment_snapshot(), git=git_snapshot(ROOT),
                    script_hashes={str(ROOT / 'scripts/eval' / name): digest(regular_bytes(ROOT / 'scripts/eval' / name))
                                   for name in ('collect_storage.py', 'freeze_storage_split.py', 'resource_guard.py', 'run_manifest.py')})
    atomic_json(out / 'manifest.json', manifest)
    atomic_json(out / 'status.json', dict(state='PLANNED', timestamp=now()))

    def event(record):
        record = dict(record, provenance='TEST_ONLY' if manifest['test_only'] else 'ACQUISITION_DIAGNOSTIC')
        with (out / 'raw.requests.jsonl').open('a') as stream:
            stream.write(json.dumps(record, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        if record['event'] == 'abort':
            atomic_json(out / 'status.json', dict(record, timestamp=now()))

    try:
        config = settings_checked(settings)
        plan = load_plan(split, cell_id, device_manifest, profile_freeze)
        if type(replicate) is not int or not 1 <= replicate <= int(plan['condition'].get('repeats', 1)):
            raise ValueError('replicate is outside the frozen matrix bounds')
        plan['replicate'] = replicate
        plan['test_only'] = manifest['test_only']
        manifest['settings'] = config
        manifest['plan'] = {key: value for key, value in plan.items() if key != 'requests' and not key.startswith('_')}
        manifest['request_count'] = len(plan['requests'])
        (out / 'frozen.input').write_bytes(plan.get('_input_bytes', encoded(dict(TEST_ONLY=True, requests=plan['requests']))))
        atomic_json(out / 'manifest.json', manifest)
        guard = guard_factory(plan, out) if guard_factory else CollectorGuard(plan, out, worker_attempt)
        with guard:
            reader = reader_factory(plan) if reader_factory else DirectReader(plan['device'], plan['alignment_bytes'],
                                 guard=guard, qd=plan['qd'], max_bytes=max(r['bytes'] for r in plan['requests']))
            with reader:
                atomic_json(out / 'status.json', dict(state='RUNNING', timestamp=now()))
                summary = acquire(plan, reader.read, guard, config, event, stop=stop, clock=clock)
            # Recheck all frozen input/profile/device identities after payload closes.
            current = load_plan(split, cell_id, device_manifest, profile_freeze)
            if any(current.get(key) != plan.get(key) for key in ('input_sha256', 'profile_sha256', 'device_manifest_sha256', 'split_manifest_sha256')):
                raise ValueError('frozen acquisition inputs changed')
            guard.check('final')
            if any(hash_regular(path) != sha for path, sha in manifest['script_hashes'].items()) or hash_regular(manifest['build']['executable']) != manifest['build']['sha256']:
                raise ValueError('collector build/source changed during acquisition')
            if plan.get('input_sha256') and hash_regular(out / 'frozen.input') != plan['input_sha256']:
                raise ValueError('copied frozen request input changed during acquisition')
            atomic_json(out / 'raw.summary.json', summary)
        if stop is not None and stop.is_set():
            raise CollectionInterrupted('interrupted before final commit')
        manifest['complete'] = True
        state, reason = ('TEST_ONLY_DONE' if manifest['test_only'] else 'DONE'), None
    except BaseException as error:
        state = 'INTERRUPTED' if isinstance(error, (CollectionInterrupted, KeyboardInterrupt)) else error.state if isinstance(error, ResourceBusy) else 'FAILED'
        reason = str(error)
        (out / 'stderr.log').write_text(traceback.format_exc())
    manifest.update(finished_at=now(), state=state, reason=reason)
    try:
        manifest['artifact_hashes'] = {p.name: hash_regular(p) for p in out.iterdir()
                                     if p.name not in ('manifest.json', 'status.json')}
    except (OSError, ValueError) as error:
        state, reason = 'FAILED', 'artifact sealing failed: ' + str(error)
        manifest.update(complete=False, state=state, reason=reason)
    atomic_json(out / 'manifest.json', manifest)
    result = dict(state=state, reason=reason, timestamp=now(), manifest_sha256=digest(regular_bytes(out / 'manifest.json')))
    atomic_json(out / 'status.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for field in ('split', 'cell-id', 'device-manifest'):
        parser.add_argument('--' + field, required=True)
    parser.add_argument('--profile-freeze')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--guarded-worker', action='store_true')
    parser.add_argument('--mode', choices=('formal', 'pilot'), default='formal')
    for name, default in [('warmup-seconds', 10), ('steady-seconds', 30), ('timeout-seconds', 90), ('poll-seconds', .01)]:
        parser.add_argument('--' + name, type=float, default=default)
    parser.add_argument('--min-completions', type=int, default=10000)
    parser.add_argument('--replicate', type=int, default=1)
    args = parser.parse_args(argv)
    config = {key: getattr(args, key) for key in ('mode', 'warmup_seconds', 'steady_seconds', 'timeout_seconds', 'poll_seconds', 'min_completions')}
    try:
        if not args.execute:
            plan = load_plan(args.split, args.cell_id, args.device_manifest, args.profile_freeze)
            print(json.dumps(dict(state='PLANNED', cell_id=args.cell_id, request_count=len(plan['requests']), settings=settings_checked(config))))
            return 0
        if args.out is None:
            raise ValueError('--execute requires a new --out directory')
        worker = os.environ.get('HBFSIM_ATTEMPT_DIR') if args.guarded_worker else None
        if args.guarded_worker and not worker:
            raise ValueError('guarded worker requires a verifiable scheduler attempt')
        stop = threading.Event()
        previous = {}
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[number] = signal.signal(number, lambda *_: stop.set())
        try:
            result = collect(args.split, args.cell_id, args.device_manifest, args.out, args.profile_freeze,
                             config, worker_attempt=worker, stop=stop, replicate=args.replicate)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
        print(json.dumps(result))
        return 0 if result['state'] == 'DONE' else 1
    except (OSError, ValueError, ResourceBusy) as error:
        parser.exit(1, 'storage collection blocked: ' + str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())

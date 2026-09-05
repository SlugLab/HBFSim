#!/usr/bin/env python3
"""Freeze inputs and run the optional CPU MQSim concurrent replay.

This produces validated raw PROJECTED media output, never scheduler DONE or a
hardware validation receipt. Fixed arrivals and closed-loop policies are separate
experiments. Nominal consume deadlines do not constitute a causal decode model.
The existing C++ profile loader is authoritative; Python does not parse profiles.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from run_manifest import atomic_json, environment_snapshot, git_snapshot, now, sha256

ROOT = Path(__file__).resolve().parents[2]
MODES = ('fixed_arrival_trace', 'closed_loop_qd')
KINDS = ('synthetic_control', 'external_trace_unverified')


def regular_file(path):
    path = Path(path).absolute()
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('input must be a regular file: '+str(path))
    return path


def identity(path):
    value = path.stat()
    return dict(device=value.st_dev, inode=value.st_ino, bytes=value.st_size,
                mtime_ns=value.st_mtime_ns, ctime_ns=value.st_ctime_ns)


def freeze_file(source, target):
    """Copy/hash small inputs once; reject modification during the copy."""
    source = regular_file(source)
    before = identity(source)
    digest = hashlib.sha256()
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
            digest.update(chunk)
            outgoing.write(chunk)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    if identity(source) != before:
        raise ValueError('input changed during freeze: '+str(source))
    return dict(path=target.name, sha256=digest.hexdigest(), original_path=str(source),
                original_identity=before)


def validate_raw(raw, inputs, arrival_mode):
    """Reconcile serialized output against frozen inputs, without new timing model."""
    if (raw.get('schema_version') != 1 or raw.get('service_source') != 'MQSIM_SIMULATED'
            or raw.get('provenance') != 'PROJECTED' or raw.get('time_unit') != 'ns'
            or raw.get('scope') != 'MEDIA_REPLAY_NOT_CAUSAL_DECODE_OR_HARDWARE'
            or raw.get('arrival_process') != arrival_mode):
        raise ValueError('raw evidence boundary mismatch')
    expected = {r['request_id']: r for r in inputs}
    rows = raw['requests']
    if not inputs or len(expected) != len(inputs) or len(rows) != len(inputs):
        raise ValueError('request conservation failed')
    seen = set()
    actual_bytes = 0
    fields = ('logical_address', 'bytes', 'operation', 'layer', 'step', 'sequence',
              'resource', 'channel', 'consume_deadline')
    for row in rows:
        request_id = row['request_id']
        if request_id in seen or request_id not in expected:
            raise ValueError('request identity duplicated/unknown')
        seen.add(request_id)
        source = expected[request_id]
        if any(row[k] != source[k] for k in fields):
            raise ValueError('request identity differs from frozen arrival trace')
        if arrival_mode == 'fixed_arrival_trace' and row['issue_ns'] != source['issue_ns']:
            raise ValueError('fixed arrival identity changed')
        if arrival_mode == 'closed_loop_qd' and (source['issue_ns'] != 0 or row['initial_issue_ns'] != 0):
            raise ValueError('closed-loop input contains a fixed schedule')
        times = [row[k] for k in ('issue_ns', 'queue_enter', 'service_start',
                                 'service_complete', 'reported_complete', 'consume')]
        if any(type(t) is not int or t < 0 for t in times) or times != sorted(times):
            raise ValueError('nonmonotonic request timestamps')
        if (row['queue_enter'] != row['issue_ns']
                or row['consume'] != max(row['consume_deadline'], row['reported_complete'])
                or row['queue_delay'] != row['service_start']-row['queue_enter']
                or row['service_delay'] != row['service_complete']-row['service_start']
                or row['interface_bound_delay'] != row['reported_complete']-row['service_complete']
                or row['residual_delay'] != row['consume']-row['consume_deadline']
                or not 1 <= row['qd'] <= raw['topology']['queue_depth']):
            raise ValueError('request lifecycle accounting mismatch')
        actual_bytes += row['bytes']
    summary = raw['summary']
    if (summary['issued'] != len(inputs) or summary['completed'] != len(inputs)
            or summary['issued_bytes'] != sum(r['bytes'] for r in inputs)
            or summary['completed_bytes'] != actual_bytes
            or not 1 <= summary['peak_device_qd'] <= raw['topology']['queue_depth']):
        raise ValueError('request/byte/QD conservation failed')
    distribution = raw['qd_distribution']
    depth = raw['topology']['queue_depth']
    if arrival_mode == 'closed_loop_qd':
        by_id = {r['request_id']: r for r in rows}
        initial = min(depth, len(inputs))
        replenishments = sorted(r['reported_complete'] for r in rows)[:len(inputs)-initial]
        expected_issues = [0]*initial+replenishments
        if [by_id[r['request_id']]['issue_ns'] for r in inputs] != expected_issues:
            raise ValueError('closed-loop actual arrivals violate replenishment policy')
    durations = distribution['duration_ns']
    if (not isinstance(durations, dict) or any(
            not isinstance(k, str) or not k.isdecimal() or str(int(k)) != k
            or not 0 <= int(k) <= depth or type(v) is not int or v < 0
            for k, v in durations.items())):
        raise ValueError('queue occupancy bucket outside valid domain')
    if (distribution['start_ns'] != min(r['issue_ns'] for r in rows)
            or distribution['end_ns'] != max(r['service_complete'] for r in rows)):
        raise ValueError('queue occupancy horizon differs from lifecycle')
    transitions = defaultdict(int)
    for row in rows:
        transitions[row['service_start']] += 1
        transitions[row['service_complete']] -= 1
    reconstructed = defaultdict(int)
    previous, outstanding = distribution['start_ns'], 0
    for timestamp, delta in sorted(transitions.items()):
        reconstructed[str(outstanding)] += timestamp-previous
        outstanding += delta
        previous = timestamp
        if not 0 <= outstanding <= depth:
            raise ValueError('queue occupancy violates depth bound')
    if outstanding or {k:v for k,v in durations.items() if v} != {k:v for k,v in reconstructed.items() if v}:
        raise ValueError('queue occupancy distribution differs from lifecycle')
    # Per-request gauges include slots reserved for same-time readmission.
    # Timestamp-only rows cannot order those instantaneous gauge transitions;
    # nonzero-duration occupancy is nevertheless a lower bound on their peak.
    occupied_peak = max((int(k) for k,v in reconstructed.items() if v), default=0)
    if (summary['peak_device_qd'] != max(r['qd'] for r in rows)
            or summary['peak_device_qd'] < occupied_peak):
        raise ValueError('queue occupancy peak contradicts observed lifecycle')


def run_replay(*, profile, arrivals, binary, out, source_kind,
               arrival_mode='fixed_arrival_trace', parallel_units=None, timeout_seconds=60):
    if source_kind not in KINDS or arrival_mode not in MODES:
        raise ValueError('unsupported source kind/arrival policy')
    if timeout_seconds <= 0:
        raise ValueError('timeout must be positive')
    if parallel_units is not None and (type(parallel_units) is not int or parallel_units <= 0):
        raise ValueError('parallel units must be a positive exact topology product')
    profile, arrivals, binary = map(regular_file, (profile, arrivals, binary))
    out = Path(out).absolute()
    if not out.resolve().is_relative_to(ROOT):
        raise ValueError('replay output must remain in the experiment checkout')
    out.mkdir(parents=True, exist_ok=False)
    receipt = dict(schema_version=1, status='RUNNING', created_at=now(),
                   provenance='PROJECTED', resource_class='CPU_ONLY',
                   hardware_validated=False, source_kind=source_kind,
                   arrival_process=arrival_mode, artifacts={},
                   assumptions=['MQSim configured profile; no hardware heldout validation',
                                'Host admission to media callback excludes physical SSD host stack',
                                'Nominal consume deadlines; no causal decode or serving model',
                                'Closed-loop inputs define ordering; actual arrivals follow reported completions'
                                if arrival_mode == 'closed_loop_qd' else 'Fixed input arrivals preserved'])
    manifest_path = out / 'replay-manifest.json'
    started = time.monotonic()
    try:
        receipt['git'] = git_snapshot(ROOT)
        with (out / 'source.patch').open('xb') as stream:
            subprocess.run(['git', 'diff', 'HEAD', '--binary'], cwd=ROOT, stdout=stream, check=True)
        atomic_json(out / 'environment.json', environment_snapshot())
        receipt['artifacts']['profile'] = freeze_file(profile, out / 'profile.json')
        receipt['artifacts']['arrivals'] = freeze_file(arrivals, out / 'arrivals.jsonl')
        build_identity = identity(binary)
        receipt['build'] = dict(path=str(binary), sha256=sha256(binary), identity=build_identity)
        receipt['tools'] = {str(path.relative_to(ROOT)): sha256(path) for path in
                            (Path(__file__), ROOT / 'scripts/eval/run_manifest.py',
                             ROOT / 'benchmarks/replay/hbf_concurrent_trace_timing.cpp')}
        command = [str(binary), '--profile', str(out / 'profile.json'),
                   '--events', str(out / 'arrivals.jsonl'), '--output', str(out / 'raw.json'),
                   '--arrival-mode', arrival_mode]
        if parallel_units is not None:
            command += ['--parallel-units', str(parallel_units)]
        receipt['argv'] = command
        atomic_json(manifest_path, receipt)
        with (out / 'stdout.log').open('xb') as stdout, (out / 'stderr.log').open('xb') as stderr:
            process = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                     timeout=timeout_seconds)
        receipt['exit_code'] = process.returncode
        if process.returncode:
            raise RuntimeError('CPU replay failed; see '+str(out / 'stderr.log'))
        if identity(binary) != build_identity:
            raise ValueError('replay binary changed during execution')
        for key in ('profile', 'arrivals'):
            item = receipt['artifacts'][key]
            if sha256(regular_file(out / item['path'])) != item['sha256']:
                raise ValueError('frozen input changed during execution: '+key)
        inputs = [json.loads(line) for line in (out / 'arrivals.jsonl').read_text().splitlines()]
        raw_path = regular_file(out / 'raw.json')
        raw_identity = identity(raw_path)
        raw_bytes = raw_path.read_bytes()
        raw_digest = hashlib.sha256(raw_bytes).hexdigest()
        validate_raw(json.loads(raw_bytes), inputs, arrival_mode)
        if identity(raw_path) != raw_identity:
            raise ValueError('raw artifact changed during validation')
        receipt['artifacts']['raw.json'] = dict(path='raw.json', sha256=raw_digest)
        for name in ('environment.json', 'source.patch', 'stdout.log', 'stderr.log'):
            path = regular_file(out / name)
            receipt['artifacts'][name] = dict(path=name, sha256=sha256(path))
        receipt['status'] = 'VALIDATED_RAW'
    except BaseException as error:
        receipt['status'] = 'INTERRUPTED' if isinstance(error, KeyboardInterrupt) else 'FAILED'
        receipt['error'] = str(error)
        raise
    finally:
        receipt['completed_at'] = now()
        receipt['elapsed_seconds'] = time.monotonic()-started
        atomic_json(manifest_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('profile', 'arrivals', 'binary', 'out'):
        parser.add_argument('--'+field, required=True, type=Path)
    parser.add_argument('--source-kind', required=True, choices=KINDS)
    parser.add_argument('--arrival-mode', choices=MODES, default=MODES[0])
    parser.add_argument('--parallel-units', type=int)
    parser.add_argument('--timeout-seconds', type=float, default=60)
    args = parser.parse_args()
    try:
        result = run_replay(**vars(args))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(2, f'replay_arrivals: {error}\n')
    print(json.dumps(dict(status=result['status'], manifest=str(args.out / 'replay-manifest.json'))))


if __name__ == '__main__':
    main()

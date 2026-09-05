#!/usr/bin/env python3
"""Compare a completed read-only acquisition with CPU MQSim replay.

Produces raw comparisons only. No payload files are opened, no hardware is
launched, and no scheduler DONE, scientific gate, or VALIDATED_MODEL is issued.
The C++ loader remains the sole profile parser; its reported topology checks QD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import traceback

from freeze_storage_split import matrix_pairs, regular_bytes, verify_bundle
from replay_arrivals import freeze_file, run_replay, validate_raw
from run_manifest import atomic_json, environment_snapshot, git_snapshot, now
from storage_arrivals import convert, convert_records, integer

ROOT = Path(__file__).resolve().parents[2]
MODES = ('observed_arrivals', 'closed_loop_policy')
METRICS = ('p50_us', 'p99_us', 'throughput_gbs', 'iops')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_bytes(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def json_lines(raw):
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def tree_snapshot(root):
    """Read each regular metadata artifact once; parse and hash these same bytes."""
    result = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in dirs + names:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError('non-regular pairing artifact: ' + str(path))
            # Raw ledgers can exceed the metadata helper's profile-size limit.
            # No-follow and fstat still reject devices/FIFOs before any read.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise ValueError('non-regular pairing artifact: ' + str(path))
                raw = stream.read()
                fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
                fingerprint = lambda item: tuple(getattr(item, key) for key in fields)
                if (len(raw) != before.st_size or fingerprint(before) != fingerprint(os.fstat(stream.fileno()))
                        or fingerprint(before) != fingerprint(path.stat(follow_symlinks=False))):
                    raise ValueError('pairing artifact changed while reading')
            result[str(path.relative_to(root))] = raw
    return result


def freeze_bundle(source, target, kind):
    snapshot = verify_bundle(source, kind)
    manifest = regular_bytes(Path(source) / 'manifest.json')
    commit = regular_bytes(Path(source) / 'COMMITTED.json')
    if (digest(manifest) != snapshot['manifest_sha256']
            or json.loads(commit).get('manifest_sha256') != snapshot['manifest_sha256']):
        raise ValueError('bundle changed while freezing')
    for name, raw in dict(snapshot['artifacts'], **{'manifest.json': manifest, 'COMMITTED.json': commit}).items():
        write_bytes(target / name, raw)


def bundle_snapshot(files, prefix, kind):
    """Validate a frozen bundle without reopening its original source paths."""
    local = {name[len(prefix):]: raw for name, raw in files.items() if name.startswith(prefix)}
    manifest = json.loads(local['manifest.json'])
    commit = json.loads(local['COMMITTED.json'])
    if (manifest.get('schema_version') != 1 or manifest.get('kind') != kind
            or commit.get('schema_version') != 1 or commit.get('kind') != kind
            or commit.get('manifest_sha256') != digest(local['manifest.json'])
            or set(local) != set(manifest['artifacts']) | {'manifest.json', 'COMMITTED.json'}):
        raise ValueError('frozen bundle identity/inventory mismatch')
    for name, expected in manifest['artifacts'].items():
        if digest(local[name]) != expected:
            raise ValueError('frozen bundle artifact hash mismatch')
    return local


def source_snapshot(files):
    prefix = 'arrivals/source/'
    manifest_bytes = files[prefix + 'manifest.json']
    manifest = json.loads(manifest_bytes)
    status = json.loads(files[prefix + 'status.json'])
    test_only = manifest.get('test_only')
    state = 'TEST_ONLY_DONE' if test_only else 'DONE'
    if (type(test_only) is not bool or manifest.get('schema_version') != 1
            or manifest.get('complete') is not True or manifest.get('state') != state
            or status.get('state') != state or status.get('manifest_sha256') != digest(manifest_bytes)):
        raise ValueError('incomplete or changed collection')
    for name, expected in manifest['artifact_hashes'].items():
        if Path(name).name != name or digest(files[prefix + name]) != expected:
            raise ValueError('collection artifact hash mismatch')
    summary = json.loads(files[prefix + 'raw.summary.json'])
    if summary['provenance'] != ('TEST_ONLY' if test_only else 'MEASURED'):
        raise ValueError('collection provenance mismatch')
    return manifest, summary, json_lines(files[prefix + 'raw.requests.jsonl'])


def load_pair_inputs(files, *, cell_id, replicate, mode):
    if mode not in MODES:
        raise ValueError('unknown pairing mode')
    manifest, summary, records = source_snapshot(files)
    plan = manifest['plan']
    if mode == 'closed_loop_policy' and plan['kind'] != 'closed_loop_qd':
        raise ValueError('closed-loop policy requires a closed-loop physical source')
    if (type(replicate) is not int or replicate < 1 or type(manifest['replicate']) is not int
            or type(plan['replicate']) is not int or manifest['replicate'] != replicate or plan['replicate'] != replicate):
        raise ValueError('collection replicate mismatch')
    if type(plan['qd']) is not int or plan['qd'] <= 0:
        raise ValueError('collection QD must be a positive integer')
    split_files = bundle_snapshot(files, 'bindings/split/', 'storage_split')
    split = json.loads(split_files['split.json'])
    pairs = matrix_pairs(split_files['matrix.csv'])
    selected = [p for p in pairs if any(c['cell_id'] == cell_id and c['backend'] == 'physical' for c in p['cells'])]
    if len(selected) != 1 or manifest['cell_id'] != cell_id or plan['cell_id'] != cell_id:
        raise ValueError('collection cell identity mismatch')
    matrix_pair = selected[0]
    physical = next(c for c in matrix_pair['cells'] if c['backend'] == 'physical')
    model = next(c for c in matrix_pair['cells'] if c['backend'] == 'mqsim')
    cells = [c for c in split['cells'] if c['cell_id'] == cell_id]
    if (len(cells) != 1 or cells[0]['condition'] != physical or plan['condition'] != physical
            or replicate > int(physical['repeats']) or plan['qd'] != int(physical['qd'])):
        raise ValueError('frozen matrix condition/QD/replicate mismatch')
    pair = next(p for p in split['pairs'] if p['pair_id'] == cells[0]['pair_id'])
    expected = dict(split_id=split['split_id'], split_manifest_sha256=digest(split_files['manifest.json']),
                    matrix_sha256=digest(split_files['matrix.csv']), kind=physical['arrival_process'],
                    role=matrix_pair['role'], seed=pair['seed'], input_sha256=pair['input_sha256'],
                    device_manifest_sha256=digest(split_files['device-manifest.json']))
    if (any(plan.get(k) != v for k, v in expected.items())
            or pair['pair_id'] != matrix_pair['pair_id'] or pair['kind'] != plan['kind']
            or split['matrix_sha256'] != expected['matrix_sha256']
            or split['device_manifest_sha256'] != expected['device_manifest_sha256']
            or split_files[pair['input_path']] != files['arrivals/source/frozen.input']
            or digest(files['arrivals/source/frozen.input']) != plan['input_sha256']):
        raise ValueError('frozen split/input identity mismatch')
    profile_hash = digest(files['profile.json'])
    if profile_hash != plan['profile_sha256']:
        raise ValueError('profile hash differs from acquisition')
    if plan['role'] == 'heldout':
        profile_files = bundle_snapshot(files, 'bindings/profile/', 'storage_profile')
        profile = json.loads(profile_files['profile-freeze.json'])
        if (profile.get('status') != 'PROFILE_FROZEN_METADATA_ONLY'
                or profile.get('validation_id') != plan['validation_id'] or not plan['validation_id']
                or profile.get('split_id') != plan['split_id']
                or profile.get('split_manifest_sha256') != plan['split_manifest_sha256']
                or profile.get('profile_sha256') != profile_hash
                or profile_files['profile'] != files['profile.json']):
            raise ValueError('heldout profile freeze identity mismatch')
    elif (plan['validation_id'] is not None or split['profile_sha256'] != profile_hash
          or split_files['calibration/initial-profile'] != files['profile.json']):
        raise ValueError('calibration profile identity mismatch')
    arrivals, mapping = convert_records(records, summary, request_count=manifest['request_count'], arrival_family=plan['kind'])
    if (arrivals != json_lines(files['arrivals/arrivals.jsonl'])
            or mapping != json.loads(files['arrivals/request-map.json'])['requests']):
        raise ValueError('converted arrivals differ from source ledger')
    frozen = files['arrivals/source/frozen.input']
    requests = json_lines(frozen) if plan['kind'] == 'fixed_arrival_trace' else json.loads(frozen)['requests']
    issued = [r for r in records if r['event'] == 'issue']
    if len(requests) != len(issued):
        raise ValueError('frozen request count differs from collection')
    for ordinal, (request, issue) in enumerate(zip(requests, issued)):
        if (request.get('request_id', ordinal) != issue['request_id']
                or request.get('offset', request.get('logical_address')) != issue['offset']
                or request['bytes'] != issue['bytes'] or request['operation'] != issue['operation']
                or (plan['kind'] == 'fixed_arrival_trace' and request['issue_ns'] != issue['requested_issue_ns'])):
            raise ValueError('frozen request order/extent differs from source ledger')
    if mode == 'closed_loop_policy':
        by_original = {m['collector_request_id']: (r, m) for r, m in zip(arrivals, mapping)}
        arrivals, mapping = [], []
        for ordinal, issue in enumerate(issued, 1):
            row, mapped = by_original[issue['request_id']]
            arrivals.append(dict(row, request_id=ordinal, issue_ns=0, consume_deadline=0,
                                 step=ordinal - 1, sequence=ordinal))
            mapping.append(dict(mapped, replay_request_id=ordinal))
    identity = dict(physical_cell_id=cell_id, model_cell_id=model['cell_id'],
                    physical_run_id=manifest['run_id'], replicate=replicate,
                    source_manifest_sha256=digest(files['arrivals/source/manifest.json']),
                    profile_sha256=profile_hash, **expected, validation_id=plan['validation_id'])
    return manifest, summary, records, arrivals, mapping, identity


def steady_metrics(rows, start_ns, end_ns):
    integer(start_ns, 'window start'); integer(end_ns, 'window end')
    if end_ns <= start_ns:
        raise ValueError('steady interval must be positive')
    for row in rows:
        for key in ('issue_ns', 'completion_ns', 'bytes'):
            integer(row[key], key)
        if row['completion_ns'] < row['issue_ns'] or not row['bytes']:
            raise ValueError('invalid request lifecycle/bytes')
    steady = [r for r in rows if start_ns <= r['completion_ns'] < end_ns]
    latencies = sorted(r['completion_ns'] - r['issue_ns'] for r in steady)
    count, size = len(steady), sum(r['bytes'] for r in steady)
    return dict(completed=count, completed_bytes=size, request_ids=[r['request_id'] for r in steady],
                p50_us=latencies[(count - 1) // 2] / 1000 if count else None,
                p99_us=latencies[(99 * count + 99) // 100 - 1] / 1000 if count else None,
                throughput_gbs=size / (end_ns - start_ns), iops=count * 1e9 / (end_ns - start_ns),
                percentile_status='AVAILABLE' if count else 'NO_STEADY_COMPLETIONS')


def relative_error(model, physical):
    return None if model is None or physical is None or physical == 0 else 100 * (model - physical) / physical


def compare_steady(manifest, summary, records, raw, arrivals, mapping, identity, mode):
    native_mode = 'closed_loop_qd' if mode == 'closed_loop_policy' else 'fixed_arrival_trace'
    if mode == 'closed_loop_policy' and raw['topology']['queue_depth'] != manifest['plan']['qd']:
        raise ValueError('native profile QD differs from physical closed-loop QD')
    validate_raw(raw, arrivals, native_mode)
    settings = manifest['settings']
    for name in ('warmup_seconds', 'steady_seconds'):
        value = settings[name]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or summary[name] != value:
            raise ValueError('steady window/settings mismatch')
    start = int(settings['warmup_seconds'] * 1e9)
    end = start + int(settings['steady_seconds'] * 1e9)
    physical_by_id = {r['request_id']: r for r in records if r['event'] == 'completion'}
    model_by_id = {r['request_id']: r for r in raw['requests']}
    joined, physical_rows, model_rows = [], [], []
    for source, mapped in zip(arrivals, mapping):
        rid = source['request_id']
        real = physical_by_id[mapped['collector_request_id']]
        simulated = model_by_id[rid]
        in_physical = start <= real['completion_ns'] < end
        if real['in_steady'] is not in_physical:
            raise ValueError('physical steady membership mismatch')
        physical_rows.append(dict(request_id=rid, bytes=real['bytes'], issue_ns=real['actual_submit_ns'], completion_ns=real['completion_ns']))
        model_rows.append(dict(request_id=rid, bytes=simulated['bytes'], issue_ns=simulated['issue_ns'], completion_ns=simulated['reported_complete']))
        joined.append(dict(replay_request_id=rid, collector_request_id=real['request_id'], offset=real['offset'], bytes=real['bytes'],
                           requested_issue_ns=real['requested_issue_ns'], physical_issue_ns=real['actual_submit_ns'],
                           physical_completion_ns=real['completion_ns'], physical_latency_ns=real['latency_ns'],
                           physical_in_steady=in_physical, model_issue_ns=simulated['issue_ns'],
                           model_media_completion_ns=simulated['service_complete'], model_completion_ns=simulated['reported_complete'],
                           model_latency_ns=simulated['reported_complete'] - simulated['issue_ns'],
                           model_in_steady=start <= simulated['reported_complete'] < end))
    physical = steady_metrics(physical_rows, start, end)
    model = steady_metrics(model_rows, start, end)
    expected_summary = dict(steady_completed=physical['completed'], steady_completed_bytes=physical['completed_bytes'],
                            throughput_bytes_per_second=physical['completed_bytes'] / settings['steady_seconds'],
                            iops=physical['completed'] / settings['steady_seconds'],
                            latency_p50_ns=None if physical['p50_us'] is None else round(physical['p50_us'] * 1000),
                            latency_p99_ns=None if physical['p99_us'] is None else round(physical['p99_us'] * 1000),
                            percentile_method='nearest_rank_completed_in_steady')
    if any(summary.get(k) != v for k, v in expected_summary.items()):
        raise ValueError('physical summary differs from recomputed ledger statistics')
    test_only = manifest['test_only']
    return dict(schema_version=1, provenance='MOCK' if test_only else 'PROJECTED',
                physical_provenance='MOCK' if test_only else 'MEASURED',
                physical_scope='TEST_ONLY' if test_only else 'physical_storage_read_syscall',
                scientific_validation_passed=False, hardware_validated=False,
                formal_export_eligible=False, pairing_mode=mode, source_arrival_family=manifest['plan']['kind'],
                replay_arrival_mode=native_mode,
                observed_closed_loop_diagnostic=mode == 'observed_arrivals' and manifest['plan']['kind'] == 'closed_loop_qd',
                identity=identity, window=dict(start_ns=start, end_ns=end, membership='completion_in_[start,end)'),
                percentile_method='nearest_rank_per_arm_completion_population', physical=physical, model=model,
                signed_relative_error_pct={k: relative_error(model[k], physical[k]) for k in METRICS}, requests=joined,
                assumptions=['Physical syscall latency includes SSD host stack; MQSim reported completion has a different boundary',
                             'No fitted residual, hardware heldout gate, throughput knee, or causal decode claim',
                             'Observed arrivals preserve actual timestamps; closed-loop policy preserves order and QD, not timestamps'])


def recompute(files, receipt):
    loaded = load_pair_inputs(files, cell_id=receipt['cell_id'], replicate=receipt['replicate'], mode=receipt['mode'])
    manifest, summary, records, arrivals, mapping, identity = loaded
    if (arrivals != json_lines(files['raw.model-input.jsonl'])
            or mapping != json.loads(files['raw.request-map.json'])['requests']):
        raise ValueError('model input/mapping differs from recomputed source')
    replay = json.loads(files['replay/replay-manifest.json'])
    expected_mode = 'closed_loop_qd' if receipt['mode'] == 'closed_loop_policy' else 'fixed_arrival_trace'
    if (replay['status'] != 'VALIDATED_RAW' or replay['provenance'] != 'PROJECTED'
            or replay['hardware_validated'] is not False or replay['resource_class'] != 'CPU_ONLY'
            or replay['arrival_process'] != expected_mode or replay['source_kind'] != 'external_trace_unverified'
            or files['replay/profile.json'] != files['profile.json']
            or files['replay/arrivals.jsonl'] != files['raw.model-input.jsonl']):
        raise ValueError('replay input/provenance identity mismatch')
    for item in replay['artifacts'].values():
        if digest(files['replay/' + item['path']]) != item['sha256']:
            raise ValueError('replay artifact hash mismatch')
    result = compare_steady(manifest, summary, records, json.loads(files['replay/raw.json']), arrivals, mapping, identity, receipt['mode'])
    if receipt['provenance'] != result['provenance']:
        raise ValueError('outer provenance promotion is forbidden')
    return result


def validate_snapshot(files, receipt):
    if (receipt.get('schema_version') != 1 or receipt.get('validation') != 'VALIDATED_PAIR'
            or receipt.get('resource_class') != 'CPU_ONLY' or receipt.get('scientific_validation_passed') is not False
            or receipt.get('artifacts') != {name: digest(raw) for name, raw in files.items()}):
        raise ValueError('pair receipt state/artifact identity mismatch')
    result = recompute(files, receipt)
    if result != json.loads(files['raw.pair.json']):
        raise ValueError('comparison differs from independently recomputed ledgers')
    return result


def validate_pair(out):
    files = tree_snapshot(Path(out))
    receipt = json.loads(files.pop('pair-manifest.json'))
    return validate_snapshot(files, receipt)


def run_pair(*, collection, profile, binary, out, mode, cell_id, replicate, timeout_seconds=60):
    out = Path(out).resolve()
    if not out.is_relative_to(ROOT.resolve()):
        raise ValueError('pair output must remain inside experiment checkout')
    # Read only collection metadata before reserving any output or starting CPU work.
    initial_bytes = regular_bytes(Path(collection) / 'manifest.json')
    initial = json.loads(initial_bytes)
    if type(initial.get('test_only')) is not bool:
        raise ValueError('missing explicit collection provenance')
    if initial['test_only'] and out.is_relative_to((ROOT / 'results/runs').resolve()):
        raise ValueError('MOCK pairing output is forbidden under formal results/runs')
    if mode not in MODES:
        raise ValueError('unknown pairing mode')
    out.mkdir(parents=True, exist_ok=False)
    receipt = dict(schema_version=1, validation='FAILED', created_at=now(), mode=mode,
                   cell_id=cell_id, replicate=replicate, resource_class='CPU_ONLY',
                   provenance='MOCK' if initial['test_only'] else 'PROJECTED',
                   scientific_validation_passed=False, artifacts={})
    try:
        receipt['git'] = git_snapshot(ROOT)
        receipt['argv'] = list(sys.argv)
        atomic_json(out / 'environment.json', environment_snapshot())
        receipt['collector_sha256'] = digest(regular_bytes(Path(__file__)))
        convert(collection, out / 'arrivals')
        if regular_bytes(out / 'arrivals/source/manifest.json') != initial_bytes:
            raise ValueError('collection identity changed during conversion')
        freeze_bundle(initial['split'], out / 'bindings/split', 'storage_split')
        if initial['plan']['role'] == 'heldout':
            if not initial.get('profile_freeze'):
                raise ValueError('heldout collection is missing its profile freeze')
            freeze_bundle(initial['profile_freeze'], out / 'bindings/profile', 'storage_profile')
        freeze_file(profile, out / 'profile.json')
        files = tree_snapshot(out)
        _, _, _, arrivals, mapping, _ = load_pair_inputs(files, cell_id=cell_id, replicate=replicate, mode=mode)
        write_bytes(out / 'raw.model-input.jsonl', ''.join(json.dumps(r, sort_keys=True) + '\n' for r in arrivals).encode())
        atomic_json(out / 'raw.request-map.json', dict(requests=mapping))
        run_replay(profile=out / 'profile.json', arrivals=out / 'raw.model-input.jsonl', binary=binary,
                   out=out / 'replay', source_kind='external_trace_unverified',
                   arrival_mode='closed_loop_qd' if mode == 'closed_loop_policy' else 'fixed_arrival_trace',
                   timeout_seconds=timeout_seconds)
        result = recompute(tree_snapshot(out), receipt)
        atomic_json(out / 'raw.pair.json', result)
        final_files = tree_snapshot(out)
        receipt.update(validation='VALIDATED_PAIR', artifacts={name: digest(raw) for name, raw in final_files.items()})
        # Hash and independently validate the same final byte snapshot before
        # publishing any successful receipt; failure stays durably nonvalidated.
        validate_snapshot(final_files, receipt)
    except BaseException as error:
        receipt['validation'] = 'INTERRUPTED' if isinstance(error, KeyboardInterrupt) else 'FAILED'
        receipt['error'] = str(error)
        atomic_json(out / 'failure.json', dict(error=str(error), traceback=traceback.format_exc()))
        raise
    finally:
        receipt['completed_at'] = now()
        atomic_json(out / 'pair-manifest.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    for name in ('collection', 'profile', 'binary', 'out'):
        run.add_argument('--' + name, type=Path, required=True)
    run.add_argument('--mode', choices=MODES, required=True)
    run.add_argument('--cell-id', required=True)
    run.add_argument('--replicate', type=int, required=True)
    run.add_argument('--timeout-seconds', type=float, default=60)
    validate = sub.add_parser('validate'); validate.add_argument('--out', type=Path, required=True)
    args = vars(parser.parse_args(argv)); command = args.pop('command')
    try:
        result = run_pair(**args) if command == 'run' else validate_pair(args['out'])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.exit(2, f'replay_storage_pair: {error}\n')
    print(json.dumps(dict(provenance=result['provenance'], scientific_validation_passed=False)))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Freeze EQ1-B storage split metadata; never perform storage payload I/O or fit.

Commands (each --out must be a NEW directory):
  prepare --matrix run-matrix.csv --trace-index index.json --profile initial.xml
          --device-manifest storage-device.json --out split-v1
  fit-inputs --split split-v1
  freeze-profile --split split-v1 --profile fitted.xml
                 --calibration-receipt fit-receipt.json --out profile-v1
  heldout-inputs --split split-v1 --profile-freeze profile-v1
  verify --split split-v1 [--profile-freeze profile-v1]

Trace index schema v1: {"schema_version":1,"cells":{CELL_ID:ENTRY,...}}.
Every flash_fidelity cell in the supplied matrix must appear exactly once.
ENTRY has kind (fixed_arrival_trace or closed_loop_qd), absolute path, sha256.
Fixed entries also require metadata_path and metadata_sha256. Fixed JSONL rows
have offset or logical_address, bytes, operation="read", and issue_ns. Their
metadata contains schema_version=1, seed, request_bytes, qd, workload, and
arrival_process. Extra replay fields are preserved verbatim, never generated.

Closed-loop entries point to JSON policies containing those metadata fields,
replenish="on_completion", file_span_bytes, alignment_bytes, and an explicit
ordered requests list (offset/logical_address, bytes, operation="read"). The
list is finite: stop issuing after its last request, then drain. No issue or
arrival timestamps are allowed; actual timestamps must come from future runs.
Physical/MQSim arms share exact policy/arrival and metadata content hashes.

FIT_COMPLETED receipt schema v1: schema_version, status="FIT_COMPLETED",
calibration_id (nonempty string), split_id, fit_input_sha256 (the calibration
fit-inputs.json hash), profile_sha256, calibration_cell_ids (nonempty unique
subset of calibration IDs). This is an explicit caller-supplied fit receipt;
the tool does not execute a fit or prove its scientific validity. Each profile
freeze creates a fresh validation_id; heldout results must bind that exact ID
and profile, and a refit needs new heldout acquisition. No results are produced.

All inputs must be small regular metadata files (at most 16 MiB each). Original
input identities and frozen copies are verified on every use. Write calibrated
profiles to new paths: modifying an original invalidates bundles bound to it.
Returned metadata is decoded from the exact byte snapshots that passed hash
verification. Consumers must verify declared hashes when later opening referenced
paths; this metadata tool does not lock those paths against subsequent writers.
The calibration directory/view excludes heldout records, but does not sandbox
an arbitrary fit process. Enforce that isolation separately before fitting.
Device metadata is recorded, never opened as a payload path or treated as
hardware authorization. Even a frozen profile leaves hardware_ready=false.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import uuid

from run_manifest import atomic_json, canonical_hash, confined_file, now

MAX_METADATA_BYTES = 16 * 1024 * 1024
PAIR_FIELDS = ('request_bytes', 'qd', 'workload', 'arrival_process', 'hardware')
FAMILIES = ('fixed_arrival_trace', 'closed_loop_qd')
PATTERNS = ('sequential_read', 'uniform_random_read')
# Arms may differ in implementation/provenance/planning, but every other matrix
# field (including future scientific dimensions) must match inside a pair.
ARM_METADATA = frozenset('''cell_id backend branch git_sha implementation_status
blocking_gate repeats resource_class gpu_seconds_per_repeat_estimate
cpu_core_seconds_per_repeat_estimate estimated_gpu_hours estimated_cpu_core_hours
cost_provenance minimum_configuration candidate_branch candidate_sha
provenance_after_gate'''.split())
BOUNDARY = ('Metadata view only; this does not sandbox an arbitrary fit process. '
            'External isolation is required before fitting. No hardware readiness '
            'or scientific validation is asserted.')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path):
    """Nonblocking, no-follow open followed by fstat before reading any bytes."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_METADATA_BYTES:
                raise ValueError('input must be a small regular metadata file: ' + str(path))
            data = stream.read(MAX_METADATA_BYTES + 1)
            if len(data) > MAX_METADATA_BYTES:
                raise ValueError('metadata file exceeds size limit: ' + str(path))
            return data
    except OSError as error:
        raise ValueError('cannot read regular metadata file: ' + str(path)) from error


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def decode(data):
    value = json.loads(data, object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError('metadata must be a JSON object')
    return value


def read_object(path):
    return decode(regular_bytes(path))


def integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(label + ' must be an integer >= ' + str(minimum))
    return value


def matrix_pairs(data):
    reader = csv.DictReader(io.StringIO(data.decode('utf-8')))
    required = {'cell_id', 'group', 'backend', 'split', *PAIR_FIELDS}
    if not reader.fieldnames or not required <= set(reader.fieldnames) or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError('missing/duplicate matrix columns')
    grouped = {}
    seen = set()
    for row in reader:
        if None in row or None in row.values() or not row['cell_id'] or row['cell_id'] in seen:
            raise ValueError('malformed matrix row or duplicate cell ID')
        seen.add(row['cell_id'])
        if row['group'] != 'flash_fidelity':
            continue
        try:
            size, qd = int(row['request_bytes']), int(row['qd'])
        except ValueError as error:
            raise ValueError('invalid matrix request size/QD') from error
        if size <= 0 or qd <= 0 or row['workload'] not in PATTERNS or row['arrival_process'] not in FAMILIES:
            raise ValueError('unsupported flash_fidelity dimensions')
        role = 'calibration' if size in (4096, 16384, 65536, 262144) and qd in (1, 4, 16, 64) else 'heldout'
        if row['split'] != role:
            raise ValueError('matrix split conflicts with preregistered size/QD role')
        if row['backend'] not in ('physical', 'mqsim'):
            raise ValueError('flash_fidelity requires physical/mqsim arms')
        dimensions = {key: row[key] for key in PAIR_FIELDS}
        key = tuple(dimensions.values())
        pair = grouped.setdefault(key, dict(pair_id=canonical_hash(dimensions)[:24],
                                           dimensions=dimensions, role=role, cells=[]))
        if any(c['backend'] == row['backend'] for c in pair['cells']):
            raise ValueError('duplicate logical physical/mqsim cell')
        if pair['cells'] and any(value != pair['cells'][0][field]
                                 for field, value in row.items() if field not in ARM_METADATA):
            raise ValueError('paired scientific matrix dimensions disagree')
        pair['cells'].append(row)
    if not grouped or any(len(pair['cells']) != 2 for pair in grouped.values()):
        raise ValueError('empty or unpaired flash_fidelity matrix')
    return sorted(grouped.values(), key=lambda pair: pair['pair_id'])


def load_pairs(matrix):
    """Use the supplied CSV as the sole condition source; never synthesize cells."""
    return matrix_pairs(regular_bytes(matrix))


def source(path, expected=None):
    path = Path(path)
    data = regular_bytes(path)
    identity = dict(path=str(path.absolute()), sha256=digest(data))
    if expected is not None and expected != identity['sha256']:
        raise ValueError('source hash mismatch: ' + str(path))
    return data, identity


def request(record, size, timed):
    if not isinstance(record, dict):
        raise ValueError('request must be an object')
    offset = record.get('offset', record.get('logical_address'))
    integer(offset, 'request offset')
    if 'offset' in record and 'logical_address' in record and record['offset'] != record['logical_address']:
        raise ValueError('conflicting offset/logical_address')
    if integer(record.get('bytes'), 'request bytes', 1) != size or record.get('operation') != 'read':
        raise ValueError('request bytes/read operation mismatch')
    if timed:
        integer(record.get('issue_ns'), 'issue_ns')
    elif any('time' in key or key.endswith('_ns') for key in record):
        raise ValueError('closed-loop request must not invent timestamps')
    return offset


def validate_input(entry, pair):
    family = pair['dimensions']['arrival_process']
    if not isinstance(entry, dict) or entry.get('kind') != family:
        raise ValueError('trace index arrival family mismatch')
    for key in ('path', 'sha256'):
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise ValueError('trace index missing ' + key)
    if not Path(entry['path']).is_absolute():
        raise ValueError('trace index paths must be absolute')
    raw, identity = source(entry['path'], entry['sha256'])
    inputs = {'input': (raw, identity)}
    if family == 'fixed_arrival_trace':
        if not isinstance(entry.get('metadata_path'), str) or not Path(entry['metadata_path']).is_absolute() or not entry.get('metadata_sha256'):
            raise ValueError('fixed arrivals require metadata path/hash')
        metadata_raw, metadata_identity = source(entry['metadata_path'], entry['metadata_sha256'])
        inputs['metadata'] = (metadata_raw, metadata_identity)
        metadata = decode(metadata_raw)
        records = [decode(line) for line in raw.splitlines() if line.strip()]
    else:
        if 'metadata_path' in entry or 'metadata_sha256' in entry:
            raise ValueError('closed-loop metadata belongs inside the policy')
        metadata = decode(raw)
        records = metadata.get('requests')
        if metadata.get('replenish') != 'on_completion' or not isinstance(records, list):
            raise ValueError('closed-loop policy requires completion replenishment and ordered requests')
        if any('time' in key or key.endswith('_ns') for key in metadata):
            raise ValueError('closed-loop policy must not invent timestamps')
    if metadata.get('schema_version') != 1:
        raise ValueError('unsupported input metadata schema')
    seed = integer(metadata.get('seed'), 'seed')
    for key in ('request_bytes', 'qd'):
        if integer(metadata.get(key), key, 1) != int(pair['dimensions'][key]):
            raise ValueError('input metadata does not match matrix ' + key)
    for key in ('workload', 'arrival_process'):
        if metadata.get(key) != pair['dimensions'][key]:
            raise ValueError('input metadata does not match matrix ' + key)
    if not records:
        raise ValueError('input request sequence cannot be empty')
    previous_issue = -1
    size = metadata['request_bytes']
    if family == 'closed_loop_qd':
        span = integer(metadata.get('file_span_bytes'), 'file_span_bytes', 1)
        alignment = integer(metadata.get('alignment_bytes'), 'alignment_bytes', 1)
        if len(records) < metadata['qd']:
            raise ValueError('closed-loop request count cannot fill initial QD')
    for record in records:
        offset = request(record, size, family == 'fixed_arrival_trace')
        if family == 'fixed_arrival_trace':
            if record['issue_ns'] < previous_issue:
                raise ValueError('fixed arrivals must be ordered by issue_ns')
            previous_issue = record['issue_ns']
        elif offset % alignment or size % alignment or offset + size > span:
            raise ValueError('closed-loop offsets violate frozen file span/alignment')
    return dict(kind=family, seed=seed, count=len(records), inputs=inputs)


def check_sources(sources):
    for item in sources:
        source(item['path'], item['sha256'])


def publish(out, kind, artifacts, sources):
    """Reserve a new destination; publish COMMITTED only after durable metadata."""
    out = Path(out)
    check_sources(sources)
    out.mkdir(parents=False, exist_ok=False)
    hashes = {}
    for relative, raw in artifacts.items():
        path = confined_file(out, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        hashes[relative] = digest(raw)
    # Persist every level, including heldout directories with no direct files.
    for parent, _, _ in os.walk(out, topdown=False):
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    check_sources(sources)
    atomic_json(out / 'manifest.json', dict(schema_version=1, kind=kind,
                                            artifacts=hashes, sources=sources))
    atomic_json(out / 'COMMITTED.json', dict(schema_version=1, kind=kind,
                                            manifest_sha256=digest(regular_bytes(out / 'manifest.json'))))


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def verify_bundle(out, kind):
    """Return verified byte snapshots, never paths that callers must reread."""
    out = Path(out)
    if out.is_symlink() or not out.is_dir():
        raise ValueError('bundle must be an ordinary directory')
    # Inventory before any parse/hash, and read with O_NONBLOCK/fstat regardless.
    actual = set()
    for parent, directories, names in os.walk(out, followlinks=False):
        for name in directories + names:
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError('non-regular frozen artifact: ' + str(path))
            actual.add(str(path.relative_to(out)))
    committed = read_object(out / 'COMMITTED.json')
    raw = regular_bytes(out / 'manifest.json')
    if committed.get('schema_version') != 1 or committed.get('kind') != kind or digest(raw) != committed.get('manifest_sha256'):
        raise ValueError('incomplete or changed frozen manifest')
    manifest = decode(raw)
    if manifest.get('kind') != kind or manifest.get('schema_version') != 1:
        raise ValueError('wrong bundle kind/schema')
    hashes = manifest.get('artifacts')
    if not isinstance(hashes, dict) or actual != set(hashes) | {'manifest.json', 'COMMITTED.json'}:
        raise ValueError('frozen artifact inventory mismatch')
    artifacts = {}
    for relative, expected in hashes.items():
        artifact = regular_bytes(confined_file(out, relative))
        if digest(artifact) != expected:
            raise ValueError('frozen artifact hash mismatch: ' + relative)
        artifacts[relative] = artifact
    if not isinstance(manifest.get('sources'), list) or not manifest['sources']:
        raise ValueError('missing source identities')
    check_sources(manifest['sources'])
    return dict(manifest=manifest, manifest_sha256=digest(raw), artifacts=artifacts)


def prepare_split(matrix, trace_index, profile, device_manifest, out):
    artifacts, identities = {}, []
    for name, path in [('matrix.csv', matrix), ('trace-index.json', trace_index),
                       ('calibration/initial-profile', profile), ('device-manifest.json', device_manifest)]:
        raw, identity = source(path)
        artifacts[name] = raw
        identities.append(identity)
    pairs = matrix_pairs(artifacts['matrix.csv'])
    index = decode(artifacts['trace-index.json'])
    expected_ids = {c['cell_id'] for pair in pairs for c in pair['cells']}
    if index.get('schema_version') != 1 or not isinstance(index.get('cells'), dict) or set(index['cells']) != expected_ids:
        raise ValueError('trace index must cover exactly the paired flash_fidelity matrix cells')
    device = decode(artifacts['device-manifest.json'])
    split = dict(schema_version=1, split_id=str(uuid.uuid4()), frozen_at=now(),
                 matrix_sha256=identities[0]['sha256'], trace_index_sha256=identities[1]['sha256'],
                 profile_sha256=identities[2]['sha256'], device_manifest_sha256=identities[3]['sha256'],
                 device_state=device.get('state', device.get('status', 'UNKNOWN')),
                 hardware_ready=False, status='PREPARED_METADATA_ONLY', pairs=[], cells=[])
    for pair in pairs:
        loaded = [validate_input(index['cells'][c['cell_id']], pair) for c in pair['cells']]
        fingerprints = [dict(kind=item['kind'], seed=item['seed'],
                             hashes={k: v[1]['sha256'] for k, v in item['inputs'].items()}) for item in loaded]
        if fingerprints[0] != fingerprints[1]:
            raise ValueError('physical/MQSim pair must share identical input/metadata hashes and seed')
        item = loaded[0]
        pair_record = {key: pair[key] for key in ('pair_id', 'dimensions', 'role')}
        pair_record.update(kind=item['kind'], seed=item['seed'], request_count=item['count'])
        for key, (raw, identity) in item['inputs'].items():
            filename = ('metadata.json' if key == 'metadata' else
                        'arrivals.jsonl' if item['kind'] == 'fixed_arrival_trace' else 'policy.json')
            relative = f"{pair['role']}/{pair['pair_id']}/{filename}"
            artifacts[relative] = raw
            pair_record[key + '_path'] = relative
            pair_record[key + '_sha256'] = identity['sha256']
        for arm in loaded:
            identities.extend(value[1] for value in arm['inputs'].values())
        label = 'arrival_sha256' if item['kind'] == 'fixed_arrival_trace' else 'policy_sha256'
        pair_record[label] = pair_record['input_sha256']
        split['pairs'].append(pair_record)
        for row in pair['cells']:
            split['cells'].append(dict(cell_id=row['cell_id'], backend=row['backend'],
                                       role=pair['role'], pair_id=pair['pair_id'], seed=item['seed'],
                                       input_sha256=pair_record['input_sha256'], condition=row))
    view = dict(schema_version=1, split_id=split['split_id'], frozen_at=split['frozen_at'],
                profile_path='initial-profile', profile_sha256=split['profile_sha256'],
                matrix_sha256=split['matrix_sha256'], device_manifest_sha256=split['device_manifest_sha256'],
                sandbox_enforced=False, boundary=BOUNDARY,
                cells=[c for c in split['cells'] if c['role'] == 'calibration'], pairs=[])
    for pair in split['pairs']:
        if pair['role'] == 'calibration':
            local_pair = dict(pair)
            for key in ('input_path', 'metadata_path'):
                if key in local_pair:
                    local_pair[key] = str(Path(local_pair[key]).relative_to('calibration'))
            view['pairs'].append(local_pair)
    artifacts['calibration/fit-inputs.json'] = encoded(view)
    artifacts['split.json'] = encoded(split)
    # Preserve identities for both arms while avoiding repeated reads of identical paths.
    identities = list({(item['path'], item['sha256']): item for item in identities}.values())
    publish(out, 'storage_split', artifacts, identities)
    return load_split(out)


def split_snapshot(path):
    snapshot = verify_bundle(path, 'storage_split')
    split = decode(snapshot['artifacts']['split.json'])
    if split.get('schema_version') != 1 or split.get('status') != 'PREPARED_METADATA_ONLY' or split.get('hardware_ready') is not False:
        raise ValueError('invalid split metadata state')
    return split, snapshot


def load_split(path):
    return split_snapshot(path)[0]


def fit_inputs(split):
    _, snapshot = split_snapshot(split)
    return decode(snapshot['artifacts']['calibration/fit-inputs.json'])


def freeze_profile(split, profile, calibration_receipt, out):
    frozen, snapshot = split_snapshot(split)
    profile_raw, profile_identity = source(profile)
    receipt_raw, receipt_identity = source(calibration_receipt)
    receipt = decode(receipt_raw)
    fit_hash = digest(snapshot['artifacts']['calibration/fit-inputs.json'])
    if any(receipt.get(key) != value for key, value in dict(
            schema_version=1, status='FIT_COMPLETED', split_id=frozen['split_id'],
            fit_input_sha256=fit_hash, profile_sha256=profile_identity['sha256']).items()):
        raise ValueError('calibration receipt does not bind this split, fit view and profile')
    ids = receipt.get('calibration_cell_ids')
    calibration = {c['cell_id'] for c in frozen['cells'] if c['role'] == 'calibration'}
    if not isinstance(ids, list) or not ids or any(not isinstance(c, str) for c in ids) or len(ids) != len(set(ids)) or not set(ids) <= calibration:
        raise ValueError('calibration receipt must reference only unique calibration cell IDs')
    if not isinstance(receipt.get('calibration_id'), str) or not receipt['calibration_id'].strip():
        raise ValueError('calibration receipt needs a nonempty calibration_id')
    split_identity = dict(path=str(Path(split).absolute() / 'manifest.json'),
                          sha256=snapshot['manifest_sha256'])
    record = dict(schema_version=1, status='PROFILE_FROZEN_METADATA_ONLY',
                  validation_id=str(uuid.uuid4()), frozen_at=now(), split_id=frozen['split_id'],
                  split_path=str(Path(split).absolute()), split_manifest_sha256=snapshot['manifest_sha256'],
                  profile_sha256=profile_identity['sha256'], calibration_id=receipt['calibration_id'],
                  calibration_receipt_sha256=receipt_identity['sha256'], fit_input_sha256=fit_hash,
                  hardware_ready=False, scientific_validation_passed=False,
                  heldout_acquisition_required=True)
    # Reverify the split before final publication, including its original sources.
    _, current = split_snapshot(split)
    if current['manifest_sha256'] != snapshot['manifest_sha256']:
        raise ValueError('split changed during profile freezing')
    publish(out, 'storage_profile', {'profile': profile_raw, 'calibration-receipt.json': receipt_raw,
                                    'profile-freeze.json': encoded(record)},
            [profile_identity, receipt_identity, split_identity])
    return load_profile_freeze(out)


def load_profile_freeze(path):
    snapshot = verify_bundle(path, 'storage_profile')
    record = decode(snapshot['artifacts']['profile-freeze.json'])
    if record.get('schema_version') != 1 or record.get('status') != 'PROFILE_FROZEN_METADATA_ONLY' or not record.get('validation_id'):
        raise ValueError('missing frozen calibrated profile')
    split, split_bundle = split_snapshot(record['split_path'])
    if split['split_id'] != record['split_id'] or split_bundle['manifest_sha256'] != record['split_manifest_sha256']:
        raise ValueError('profile freeze refers to a different split')
    return record


def heldout_inputs(split, profile_freeze):
    if not profile_freeze:
        raise ValueError('heldout requires an independently frozen calibrated profile')
    frozen, snapshot = split_snapshot(split)
    profile = load_profile_freeze(profile_freeze)
    if profile['split_id'] != frozen['split_id'] or profile['split_manifest_sha256'] != snapshot['manifest_sha256']:
        raise ValueError('frozen profile belongs to a different split')
    return dict(schema_version=1, split_id=frozen['split_id'], validation_id=profile['validation_id'],
                profile_path=str(Path(profile_freeze).absolute() / 'profile'),
                profile_sha256=profile['profile_sha256'], split_path=str(Path(split).absolute()),
                cells=[c for c in frozen['cells'] if c['role'] == 'heldout'],
                pairs=[p for p in frozen['pairs'] if p['role'] == 'heldout'],
                hardware_ready=False, device_state=frozen['device_state'],
                status='HELDOUT_INPUT_METADATA_ONLY', scientific_validation_passed=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    for name in ('matrix', 'trace-index', 'profile', 'device-manifest', 'out'):
        prepare.add_argument('--' + name, required=True, type=Path)
    freeze = commands.add_parser('freeze-profile')
    for name in ('split', 'profile', 'calibration-receipt', 'out'):
        freeze.add_argument('--' + name, required=True, type=Path)
    for name in ('verify', 'fit-inputs', 'heldout-inputs'):
        command = commands.add_parser(name)
        command.add_argument('--split', required=True, type=Path)
        if name != 'fit-inputs':
            command.add_argument('--profile-freeze', required=name == 'heldout-inputs', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare_split(args.matrix, args.trace_index, args.profile, args.device_manifest, args.out)
        elif args.command == 'freeze-profile':
            result = freeze_profile(args.split, args.profile, args.calibration_receipt, args.out)
        elif args.command == 'fit-inputs':
            result = fit_inputs(args.split)
        elif args.command == 'heldout-inputs' or args.profile_freeze:
            result = heldout_inputs(args.split, args.profile_freeze)
        else:
            result = load_split(args.split)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, 'storage metadata rejected: ' + str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())

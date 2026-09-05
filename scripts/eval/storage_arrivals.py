#!/usr/bin/env python3
"""Freeze completed collector observations as identical MQSim arrival input.

MQSim must use fixed_arrival_trace on this output even when physical acquisition
was closed-loop: its observed syscall submission times are the matched stream.
Re-running a model-driven closed loop would create a different arrival stream.
Addresses retain absolute benchmark-file offsets. Positive replay IDs map to
the original collector IDs; no physical payload is opened by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from freeze_storage_split import regular_bytes
from collect_storage import request_identity
from replay_arrivals import freeze_file, identity, regular_file
from run_manifest import atomic_json, git_snapshot, sha256

ROOT=Path(__file__).resolve().parents[2]


def integer(value,name):
    if type(value) is not int or value<0:
        raise ValueError(name+' must be a nonnegative integer')
    return value


def convert_records(records,summary,*,request_count,arrival_family='fixed_arrival_trace'):
    issued,completed={},{}
    if arrival_family not in ('fixed_arrival_trace','closed_loop_qd'):
        raise ValueError('unknown acquisition arrival family')
    if summary.get('provenance') not in ('TEST_ONLY','MEASURED'):
        raise ValueError('unknown acquisition provenance')
    event_provenance='TEST_ONLY' if summary['provenance']=='TEST_ONLY' else 'ACQUISITION_DIAGNOSTIC'
    for record in records:
        if record.get('provenance')!=event_provenance:
            raise ValueError('acquisition event/summary provenance mismatch')
        kind=record.get('event')
        if kind not in ('issue','completion'):
            raise ValueError('aborted/failed/unknown acquisition record')
        requested=record['requested_issue_ns']
        if arrival_family=='fixed_arrival_trace':
            integer(requested,'fixed requested time')
        elif requested is not None:
            raise ValueError('closed-loop acquisition cannot have requested timestamps')
        rid=request_identity(record['request_id'])
        target=issued if kind=='issue' else completed
        if rid in target or record['operation']!='read':
            raise ValueError('duplicate or non-read acquisition record')
        for name in ('offset','bytes'):
            integer(record[name],name)
        if not record['bytes'] or record['bytes']%512 or record['offset']%512:
            raise ValueError('acquisition extent is not sector aligned')
        target[rid]=record
    count=integer(request_count,'request count')
    if (not count or len(issued)!=count or set(issued)!=set(completed)
            or summary['issued']!=count or summary['completed']!=count or summary['outstanding']!=0
            or summary['completed_bytes']!=sum(r['bytes'] for r in completed.values())):
        raise ValueError('acquisition request/byte conservation failed')
    for rid,done in completed.items():
        start=issued[rid]
        if any(done[k]!=start[k] for k in ('offset','bytes','requested_issue_ns')) or done['requested_bytes']!=start['bytes']:
            raise ValueError('completion differs from admitted request')
        times=[integer(start['scheduled_ns'],'scheduled time')]+[
            integer(done[k],k) for k in ('actual_submit_ns','completion_ns','observed_completion_ns')]
        requested=done['requested_issue_ns']
        if (times!=sorted(times) or done['latency_ns']!=times[2]-times[1]
                or (requested is None and done['admission_lag_ns'] is not None)
                or (requested is not None and (integer(requested,'requested time')>times[0]
                    or done['admission_lag_ns']!=times[1]-requested))):
            raise ValueError('acquisition lifecycle/latency mismatch')
    arrivals,mapping=[],[]
    issue_order={rid:ordinal for ordinal,rid in enumerate(issued)}
    for ordinal,done in enumerate(sorted(completed.values(),key=lambda r:(r['actual_submit_ns'],issue_order[r['request_id']])),1):
        arrivals.append(dict(request_id=ordinal,issue_ns=done['actual_submit_ns'],
                             consume_deadline=done['actual_submit_ns'],logical_address=done['offset'],
                             bytes=done['bytes'],operation='read',layer=0,step=ordinal-1,
                             sequence=ordinal,resource='mqsim_media',channel='profile'))
        mapping.append(dict(replay_request_id=ordinal,collector_request_id=done['request_id'],
                            requested_issue_ns=done['requested_issue_ns'],actual_submit_ns=done['actual_submit_ns'],
                            physical_completion_ns=done['completion_ns'],physical_latency_ns=done['latency_ns']))
    return arrivals,mapping


def convert(collection,out):
    collection=Path(collection).resolve(strict=True)
    out=Path(out).resolve()
    if not out.is_relative_to(ROOT):
        raise ValueError('output must remain inside experiment checkout')
    manifest_bytes=regular_bytes(collection/'manifest.json')
    status_bytes=regular_bytes(collection/'status.json')
    manifest,status=json.loads(manifest_bytes),json.loads(status_bytes)
    test_only=manifest['test_only']
    state='TEST_ONLY_DONE' if test_only else 'DONE'
    if (manifest['schema_version']!=1 or type(test_only) is not bool or manifest['complete'] is not True
            or manifest['state']!=state or status['state']!=state
            or status['manifest_sha256']!=hashlib.sha256(manifest_bytes).hexdigest()):
        raise ValueError('source collection is incomplete or identity changed')
    if test_only and out.is_relative_to((ROOT/'results/runs').resolve()):
        raise ValueError('TEST_ONLY arrivals cannot enter formal results/runs')
    required={'raw.requests.jsonl','raw.summary.json','frozen.input'}
    family=manifest['plan']['kind']
    if family not in ('fixed_arrival_trace','closed_loop_qd'):
        raise ValueError('unknown acquisition arrival family')
    if not required<=manifest['artifact_hashes'].keys():
        raise ValueError('source collection artifacts are incomplete')
    out.mkdir(parents=True,exist_ok=False)
    receipt=dict(schema_version=1,resource_class='CPU_ONLY',validation='FAILED',
                 provenance='MOCK' if test_only else 'PROJECTED',
                 source_observation='TEST_ONLY' if test_only else 'MEASURED_STORAGE_SYSCALL',
                 scientific_validation_passed=False,source_collection=str(collection),
                 source_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                 source_arrival_family=family,required_replay_arrival_mode='fixed_arrival_trace',
                 timestamp_rule='PRESERVE_ACTUAL_SUBMIT_NS_NO_ORIGIN_SHIFT',
                 timestamp_tie_order='COLLECTOR_ISSUE_RECORD_ORDER',
                 address_rule='PRESERVE_ABSOLUTE_BENCHMARK_FILE_OFFSET',
                 placeholder_fields='layer=0; step/sequence are replay-order metadata, not model decode',
                 inputs={},git=git_snapshot(ROOT),tool_sha256=sha256(Path(__file__)))
    try:
        frozen=out/'source'
        frozen.mkdir()
        (frozen/'manifest.json').write_bytes(manifest_bytes)
        (frozen/'status.json').write_bytes(status_bytes)
        for name,expected in manifest['artifact_hashes'].items():
            if not isinstance(name,str) or Path(name).name!=name or name in ('.','..'):
                raise ValueError('source artifact path must be a confined filename')
            item=freeze_file(collection/name,frozen/name)
            if item['sha256']!=expected:
                raise ValueError('source artifact hash mismatch: '+name)
            item['path']=str((frozen/name).relative_to(out))
            receipt['inputs'][name]=item
        summary_bytes=regular_bytes(frozen/'raw.summary.json')
        if hashlib.sha256(summary_bytes).hexdigest()!=manifest['artifact_hashes']['raw.summary.json']:
            raise ValueError('frozen summary changed')
        summary=json.loads(summary_bytes)
        if summary['provenance']!=('TEST_ONLY' if test_only else 'MEASURED'):
            raise ValueError('source acquisition provenance mismatch')
        path=regular_file(frozen/'raw.requests.jsonl')
        before=identity(path)
        digest=hashlib.sha256()
        def rows(stream):
            for line in stream:
                digest.update(line)
                yield json.loads(line)
        with path.open('rb') as stream:
            arrivals,mapping=convert_records(rows(stream),summary,arrival_family=family,
                                            request_count=manifest['request_count'])
        if digest.hexdigest()!=manifest['artifact_hashes']['raw.requests.jsonl']:
            raise ValueError('parsed request ledger hash mismatch')
        if identity(path)!=before:
            raise ValueError('frozen request ledger changed during validation')
        destination=out/'arrivals.jsonl'
        with destination.open('x') as stream:
            for row in arrivals:
                stream.write(json.dumps(row,sort_keys=True)+'\n')
        atomic_json(out/'request-map.json',dict(requests=mapping))
        receipt.update(validation='VALIDATED_ARRIVALS',requests=len(arrivals),
                       bytes=sum(r['bytes'] for r in arrivals),arrivals_sha256=sha256(destination),
                       request_map_sha256=sha256(out/'request-map.json'))
    except BaseException as error:
        receipt['error']=str(error)
        raise
    finally:
        atomic_json(out/'manifest.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=convert(args.collection,args.out)
    except (OSError,ValueError,KeyError,TypeError) as error:
        parser.exit(2,f'storage_arrivals: {error}\n')
    print(json.dumps(dict(output=str(args.out),validation=result['validation'],provenance=result['provenance'])))


if __name__=='__main__':
    main()

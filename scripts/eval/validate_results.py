#!/usr/bin/env python3
"""Validate the v1 long-form evaluation exchange contract; never infer provenance."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import math
import pathlib
import re
import sys

BASE_COLUMNS = '''provenance eq figure panel run_id git_sha hardware model workload mode backend operation profile delay_us independent_work_us overlap_ratio request_bytes qd rho tR_us parallel_units active_sequences prefetch_policy metric value unit replicate'''.split()
EXTRA_COLUMNS = '''schema_version branch series warps occupancy split num_experts top_k source_file source_function source_line_start source_line_end'''.split()
COLUMNS = BASE_COLUMNS + EXTRA_COLUMNS
PROVENANCE = {'MEASURED', 'VALIDATED_MODEL', 'PROJECTED', 'MOCK'}
NUMERIC = set('delay_us independent_work_us overlap_ratio request_bytes qd rho tR_us parallel_units active_sequences warps num_experts top_k source_line_start source_line_end'.split())
INTEGER = set('request_bytes qd parallel_units active_sequences warps num_experts top_k source_line_start source_line_end replicate'.split())
UNITS = {
    'critical_delta_us': 'us', 'event_delta_us': 'us', 'p50_us': 'us', 'p99_us': 'us',
    'throughput_gbs': 'GB/s', 'iops': 'IOPS', 'delta_hw_us': 'us', 'delta_sim_us': 'us',
    'residual_norm': 'ratio', 'total_stall_norm': 'ratio', 'oracle_residual_us': 'us',
    'residual_us': 'us', 'issue_stall_us': 'us', 'decode_norm': 'ratio',
    'throughput_norm': 'ratio', 'service_gbs': 'GB/s', 'union_fraction': 'ratio',
    'routing_entropy': 'bit', 'gini': 'ratio', 'jaccard': 'ratio',
    'min_service_gbs': 'GB/s', 'relative_error_pct': '%', 'coverage_fraction': 'ratio',
    'extra_traffic_ratio': 'ratio', 'hit_rate': 'ratio', 'miss_rate': 'ratio',
    'utilization': 'ratio', 'hbf_bytes_per_token': 'B/token',
    'checksum_ok': 'bool', 'stale_reads': 'count', 'early_release': 'count',
    'missing_transactions': 'count', 'deadlocks': 'count', 'timeouts': 'count',
    'rewritten_instructions': 'count', 'unsupported_instructions': 'count',
    'bypassed_accesses': 'count', 'covered_bytes': 'B', 'late_prefetch': 'count',
    'useful_prefetch': 'count', 'useless_prefetch_bytes': 'B', 'prefetch_evictions': 'count',
    'timely_coverage': 'ratio', 'decode_step_us': 'us', 'queue_depth_mean': 'count',
    'unique_experts': 'count', 'expert_weight_bytes_touched': 'B',
    'expert_frequency': 'count', 'expert_reuse_distance': 'count',
    'emulator_wall_s': 's', 'gpu_hours': 'h', 'cpu_hours': 'core-h',
}
FRACTIONS = {'rho','union_fraction','gini','jaccard','coverage_fraction','hit_rate','miss_rate','utilization','timely_coverage'}


def fail(message):
    raise ValueError(message)


def read_rows(path):
    path = pathlib.Path(path)
    if path.suffix.lower() == '.json':
        rows = json.loads(path.read_text())
        if not isinstance(rows, list): fail('JSON input must be an array of row objects')
        if any(not isinstance(r, dict) for r in rows): fail('JSON row is not an object')
        return [{k: '' if v is None else str(v) for k,v in r.items()} for r in rows]
    with path.open(newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            fail('empty or duplicate CSV header')
        return list(reader)


def validate_rows(rows, strict_no_mock=False):
    if not rows: fail('empty results')
    identities = set()
    run_provenance = {}
    for index, row in enumerate(rows, 2):
        prefix = f'row {index}: '
        if set(COLUMNS) - row.keys(): fail(prefix + 'missing columns: ' + ','.join(sorted(set(COLUMNS)-row.keys())))
        if None in row or any(v is None for v in row.values()): fail(prefix + 'malformed CSV row')
        if any(not isinstance(v,str) or v != v.strip() for v in row.values()): fail(prefix + 'values must be trimmed strings')
        p = row['provenance']
        if p not in PROVENANCE: fail(prefix + 'invalid provenance')
        if strict_no_mock and p == 'MOCK': fail(prefix + 'MOCK forbidden by --strict-no-mock')
        if row['schema_version'] != '1': fail(prefix + 'unsupported schema version')
        if row['eq'] not in {'EQ1','EQ2','EQ3','EQ4','APPENDIX'}: fail(prefix + 'invalid EQ')
        for k in ['figure','panel','run_id','hardware','model','workload','mode','backend','operation','profile','branch','series','source_file','source_function']:
            if not row[k]: fail(prefix + 'empty ' + k)
        if not re.fullmatch(r'[0-9a-f]{40}',row['git_sha']): fail(prefix + 'git_sha must be full SHA')
        if row['metric'] not in UNITS or row['unit'] != UNITS[row['metric']]: fail(prefix + 'unknown metric or wrong unit')
        for k in NUMERIC | {'value','replicate'}:
            if row[k] == '':
                if k in {'value','replicate','source_line_start','source_line_end'}: fail(prefix + 'empty ' + k)
                continue
            try: v = float(row[k])
            except ValueError: fail(prefix + 'not numeric: ' + k)
            if not math.isfinite(v): fail(prefix + 'nonfinite ' + k)
            if k in INTEGER and (not v.is_integer() or v < 0): fail(prefix + 'invalid integer ' + k)
            if k not in {'value'} and v < 0: fail(prefix + 'negative ' + k)
            if k in {'qd','request_bytes','parallel_units','active_sequences','warps','num_experts','top_k','replicate','source_line_start','source_line_end'} and v <= 0: fail(prefix + 'nonpositive ' + k)
        if int(row['source_line_end']) < int(row['source_line_start']): fail(prefix + 'reversed source lines')
        value = float(row['value'])
        if row['unit'] == 'us' and row['metric'] not in {'critical_delta_us','event_delta_us','delta_hw_us','delta_sim_us'} and value < 0: fail(prefix + 'negative raw time')
        if row['unit'] in {'count','B','B/token','GB/s','IOPS','h','core-h','s','bit'} and value < 0: fail(prefix + 'negative physical quantity')
        if row['unit'] in {'count','B','bool'} and not value.is_integer(): fail(prefix + 'noninteger count')
        if row['unit'] == 'bool' and value not in {0,1}: fail(prefix + 'invalid bool')
        if row['metric'] in FRACTIONS and not 0 <= value <= 1: fail(prefix + 'fraction outside [0,1]')
        if row['rho'] and not 0 <= float(row['rho']) <= 1: fail(prefix + 'rho outside [0,1]')
        if row['num_experts'] and row['top_k'] and int(row['top_k']) > int(row['num_experts']): fail(prefix + 'top_k exceeds E')
        if row['delay_us'] and row['independent_work_us'] and row['overlap_ratio']:
            d,w,r = [float(row[k]) for k in ['delay_us','independent_work_us','overlap_ratio']]
            if d <= 0 or not math.isclose(w/d,r,rel_tol=1e-7,abs_tol=1e-9): fail(prefix + 'inconsistent W/D')
        key = tuple(row[k] for k in COLUMNS if k not in {'value','unit'})
        if key in identities: fail(prefix + 'duplicate metric identity')
        identities.add(key)
        previous = run_provenance.setdefault(row['run_id'],p)
        if previous != p: fail(prefix + 'mixed provenance within run')
    return rows


def validate_manifest(rows, path):
    """Hash checks authenticate the supplied files, not their scientific truth."""
    path = pathlib.Path(path)
    data = json.loads(path.read_text())
    if data.get('schema_version') != 1: fail('manifest schema_version must be 1')
    runs = data.get('runs',{})
    for run_id in {r['run_id'] for r in rows}:
        subset = [r for r in rows if r['run_id'] == run_id]
        record = runs.get(run_id)
        if not record: fail('missing run manifest: ' + run_id)
        for k in ['provenance','git_sha','branch']:
            if any(r[k] != record.get(k) for r in subset): fail('manifest mismatch: '+run_id+'/'+k)
        raw = pathlib.Path(record.get('raw_artifact',''))
        if not raw.is_absolute(): raw = path.parent / raw
        if not raw.is_file() or hashlib.sha256(raw.read_bytes()).hexdigest() != record.get('raw_sha256'):
            fail('raw artifact absent/hash mismatch: '+run_id)
        p = record['provenance']
        if p == 'MEASURED' and record.get('measurement_scope') != 'physical_hardware': fail('MEASURED needs physical_hardware scope')
        if p == 'VALIDATED_MODEL':
            if not record.get('calibration_id') or not record.get('heldout_validation_id') or not record.get('validity_domain'):
                fail('VALIDATED_MODEL needs calibration, heldout evidence and validity domain')
        if p == 'PROJECTED' and not record.get('assumptions'): fail('PROJECTED needs explicit assumptions')
    return data


def load_validated(path, strict_no_mock=False, manifest=None):
    rows = validate_rows(read_rows(path),strict_no_mock)
    if strict_no_mock:
        validate_manifest(rows, manifest or pathlib.Path(path).with_name('manifest.json'))
    elif manifest:
        validate_manifest(rows,manifest)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True)
    parser.add_argument('--strict-no-mock',action='store_true')
    parser.add_argument('--manifest')
    args = parser.parse_args()
    try:
        rows = load_validated(args.input,args.strict_no_mock,args.manifest)
    except (ValueError,OSError,KeyError,TypeError) as e:
        print(f'INVALID: {e}',file=sys.stderr); return 2
    print(json.dumps({'status':'valid','rows':len(rows),'provenance':sorted({r['provenance'] for r in rows})}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

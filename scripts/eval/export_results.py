#!/usr/bin/env python3
"""Export only durable DONE attempts through the existing strict result validator.

Usage: python scripts/eval/export_results.py --results results --out results/export
The destination is a new directory containing eval.csv and manifest.json; existing
exports are never overwritten. Schema validation is not a scientific claim gate.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import pathlib
import shutil
import tempfile
from run_manifest import confined_file, read_json, sha256, atomic_json, canonical_hash, artifact_inventory
from resource_guard import RESOURCE_CLASSES, SHARED_SAFE_METRICS
from validate_results import COLUMNS, load_validated, validate_rows, validate_manifest


# Planning/provenance metadata are not scientific axes. Every other nonempty
# matrix field is required verbatim in each result row (including future axes).
MATRIX_METADATA=frozenset("""cell_id group branch git_sha implementation_status
blocking_gate repeats resource_class gpu_seconds_per_repeat_estimate
cpu_core_seconds_per_repeat_estimate estimated_gpu_hours estimated_cpu_core_hours
cost_provenance minimum_configuration candidate_branch candidate_sha
provenance_after_gate""".split())

def check_rows(rows,manifest):
    """Require exact run identity, frozen dimensions, and declared complete metrics."""
    task=manifest['task'];condition=manifest['condition']
    expected=set(task['expected_metrics'])
    if {r['metric'] for r in rows}!=expected:raise ValueError('missing/unexpected required metrics')
    if task['resource_class']=='GPU_SHARED_SAFE' and (expected-SHARED_SAFE_METRICS or task.get('performance_collection') is not False):
        raise ValueError('GPU_SHARED_SAFE performance export forbidden')
    for row in rows:
        for key,value in dict(run_id=manifest['run_id'],git_sha=manifest['git']['git_sha'],branch=manifest['git']['branch'],replicate=str(manifest['replicate']),eq=condition['eq'],provenance=task['provenance']['provenance']).items():
            if row.get(key)!=value:raise ValueError('result identity mismatch: '+key)
        for key,value in condition.items():
            if value and key not in MATRIX_METADATA and row.get(key)!=value:
                raise ValueError('missing/mismatched frozen matrix dimension: '+key)
        source=pathlib.Path(row['source_file'])
        if not source.is_absolute():source=pathlib.Path(manifest['root'])/source
        if str(source.resolve()) not in {str(pathlib.Path(a['path']).resolve()) for a in task['artifacts']}:
            raise ValueError('metric source is not a frozen task artifact')
    return rows


def validate_attempt(attempt,require_done=True):
    """All export invariants apply before commit; only DONE status is optional."""
    attempt=pathlib.Path(attempt)
    # Classify the whole attempt before opening metadata, receipts, or evidence.
    # A FIFO must never reach read_json()/sha256(), which use blocking readers.
    files,rejected=artifact_inventory(attempt)
    if rejected:raise ValueError('unsafe/non-regular artifact inventory')
    for name in ('manifest.json','status.json'):
        if not confined_file(attempt,name).is_file():raise ValueError('missing/non-regular metadata artifact: '+name)
    manifest=read_json(attempt/'manifest.json')
    if require_done:
        status=read_json(attempt/'status.json')
        if status['state']!='DONE':raise ValueError('attempt is not DONE')
        if status.get('manifest_sha256')!=sha256(attempt/'manifest.json'):raise ValueError('manifest hash mismatch')
    required={'run_id','replicate','condition','matrix_sha256','registry_sha256','task_sha256','bindings','git','argv','validator_argv','resource_class','environment_sha256','raw_hashes','artifact_hashes','validation','gate_receipts','task','complete','runs'}
    if required-manifest.keys():raise ValueError('incomplete execution provenance')
    if manifest['complete'] is not True or manifest['resource_class'] not in RESOURCE_CLASSES:raise ValueError('incomplete/invalid execution manifest')
    validation=manifest['validation']
    if validation.get('semantic_exit_code')!=0 or validation.get('strict_no_mock') is not True:raise ValueError('missing successful actual validation')
    if not manifest['gate_receipts']:raise ValueError('missing frozen gold receipts')
    mandatory={'environment.json','source.patch','stdout.log','stderr.log','validator.stdout.log','validator.stderr.log',manifest['task']['result_file'],*manifest['task']['raw_artifacts']}
    mandatory.update(receipt['path'] for receipt in manifest['gate_receipts'].values())
    for receipt in manifest['gate_receipts'].values():
        if not receipt.get('evidence'):raise ValueError('missing frozen gate evidence artifacts')
        mandatory.update(item['path'] for item in receipt['evidence'])
    if mandatory-files.keys():raise ValueError('missing/non-regular mandatory artifact')
    if mandatory-manifest['artifact_hashes'].keys():raise ValueError('incomplete artifact hash chain')
    if manifest.get('artifact_errors'):raise ValueError('unsafe/unconfined artifact inventory')
    if files.keys()!=manifest['artifact_hashes'].keys():raise ValueError('artifact inventory/hash chain mismatch')
    for gate,receipt in manifest['gate_receipts'].items():
        frozen=read_json(confined_file(attempt,receipt['path']))
        if frozen.get('status')!='PASS' or frozen.get('gate')!=gate or frozen.get('bindings')!=manifest['bindings']:
            raise ValueError('gate provenance mismatch')
        if sha256(confined_file(attempt,receipt['path']))!=receipt['sha256']:raise ValueError('gate receipt hash mismatch')
        for item in receipt['evidence']:
            if sha256(confined_file(attempt,item['path']))!=item['sha256']:raise ValueError('gate evidence hash mismatch')
    if {manifest['task']['result_file'],*manifest['task']['raw_artifacts']}-manifest['raw_hashes'].keys():raise ValueError('incomplete raw artifact hash chain')
    if manifest['task_sha256']!=canonical_hash(manifest['task']) or manifest['bindings'].get('task_sha256')!=manifest['task_sha256']:
        raise ValueError('task provenance hash mismatch')
    if manifest['bindings'].get('condition_sha256')!=canonical_hash(manifest['condition']):raise ValueError('condition provenance hash mismatch')
    for name,expected in manifest['artifact_hashes'].items():
        path=confined_file(attempt,name)
        if not path.is_file() or sha256(path)!=expected:raise ValueError('artifact hash mismatch: '+name)
    if sha256(attempt/'environment.json')!=manifest['environment_sha256']:raise ValueError('environment hash mismatch')
    if not manifest['raw_hashes']:raise ValueError('missing raw hashes')
    for name,expected in manifest['raw_hashes'].items():
        path=confined_file(attempt,name)
        if not path.is_file() or sha256(path)!=expected:raise ValueError('raw hash mismatch: '+name)
    rows=load_validated(confined_file(attempt,manifest['task']['result_file']),True,attempt/'manifest.json')
    check_rows(rows,manifest)
    return rows,manifest


def collect_done(results):
    rows=[];runs={};provenance={}
    for run_dir in sorted((pathlib.Path(results)/'runs').glob('*')):
        status_file=run_dir/'status.json'
        if not status_file.exists() and not status_file.is_symlink():continue
        status_file=confined_file(run_dir,'status.json')
        if not status_file.is_file():raise ValueError('non-regular run status artifact')
        status=read_json(status_file)
        if status.get('state')!='DONE':continue
        attempt=confined_file(run_dir,status['attempt'])
        # validate_attempt checks regular/confined types before any manifest hash.
        attempt_rows,manifest=validate_attempt(attempt)
        if status.get('manifest_sha256')!=sha256(attempt/'manifest.json'):raise ValueError('root manifest hash mismatch')
        run_id=manifest['run_id']
        if run_id!=run_dir.name or run_id in runs:raise ValueError('duplicate/mismatched run identity')
        record=dict(manifest['runs'][run_id])
        raw=pathlib.Path(record['raw_artifact'])
        if not raw.is_absolute():raw=attempt/raw
        record['raw_artifact']=str(raw.resolve())
        record['execution_manifest']=str((attempt/'manifest.json').resolve())
        record['execution_manifest_sha256']=sha256(attempt/'manifest.json')
        runs[run_id]=record;provenance[run_id]=manifest
        rows.extend(attempt_rows)
    if rows:validate_rows(rows,True)
    return rows,dict(schema_version=1,runs=runs)


def export(results,out):
    rows,manifest=collect_done(results)
    if not rows:raise ValueError('no complete validated DONE runs to export')
    out=pathlib.Path(out)
    if out.exists():raise ValueError('export destination already exists')
    out.parent.mkdir(parents=True,exist_ok=True)
    temporary=pathlib.Path(tempfile.mkdtemp(prefix='.'+out.name+'-',dir=out.parent))
    try:
        columns=COLUMNS+sorted(set().union(*(row.keys() for row in rows))-set(COLUMNS))
        with (temporary/'eval.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader();writer.writerows(rows)
            stream.flush();os.fsync(stream.fileno())
        atomic_json(temporary/'manifest.json',manifest)
        load_validated(temporary/'eval.csv',True,temporary/'manifest.json')
        os.rename(temporary,out)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return dict(rows=len(rows),runs=len(manifest['runs']),out=str(out))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results',type=pathlib.Path,default=pathlib.Path('results'))
    parser.add_argument('--out',type=pathlib.Path,required=True)
    args=parser.parse_args()
    try:print(json.dumps(export(args.results,args.out)));return 0
    except (ValueError,OSError,KeyError,TypeError) as error:
        parser.exit(2,'INVALID: '+str(error)+'\n')

if __name__=='__main__':raise SystemExit(main())

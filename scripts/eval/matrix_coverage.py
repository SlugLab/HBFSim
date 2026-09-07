#!/usr/bin/env python3
"""Read-only original-matrix ledger; DONE counts require strict sealed evidence.

Writes a new CSV plus summary JSON. Never creates attempts or changes status.
Gate references on unexecuted registered tasks are candidates, not admission.
"""
import argparse
import collections
import csv
import json
import pathlib

from export_results import validate_attempt
from run_manifest import STATES, canonical_hash, confined_file, read_json, sha256
from run_matrix import ROOT, load_matrix, select_runs


def regular_json(base, name):
    path = confined_file(base, name)
    if not path.is_file():
        raise ValueError('missing/non-regular metadata: ' + name)
    return read_json(path)


def coverage_rows(matrix_rows, results, tasks=(), matrix_sha256=None):
    results = pathlib.Path(results).resolve()
    registered = {}
    for task in tasks:
        for cell in task.get('conditions', {}):
            if cell in registered:
                raise ValueError('ambiguous task registration: ' + cell)
            registered[cell] = task
    ledger = []
    for condition, replicate, run_id in select_runs(matrix_rows):
        task = registered.get(condition['cell_id'])
        row = dict(condition, run_id=run_id, replicate=replicate, state='PLANNED',
                   validation_state='NOT_RUN', claim_state='NOT_EVALUATED',
                   attempt_path='', manifest_path='', raw_paths='', validator_path='',
                   receipt_paths='', missing_items='', handler_id=task.get('id', '') if task else '')
        row.update(input_artifacts=json.dumps([a for a in (task or {}).get('artifacts', []) if a.get('role') == 'input']),
                   producer_argv=json.dumps((task or {}).get('argv', [])),
                   task_artifacts=json.dumps((task or {}).get('artifacts', [])),
                   estimated_peak_bytes='NOT_ESTIMATED', estimated_wall_seconds='NOT_ESTIMATED',
                   minimum_repair='Implement exact handler and validate applicable prerequisite gates' if not task
                   else 'Validate frozen prerequisite evidence and execute registered producer')
        missing = []
        if not task:
            missing.append('NO_REGISTERED_HANDLER; prerequisites not evaluated: ' + condition['blocking_gate'])
        elif task.get('conditions', {}).get(condition['cell_id']) != canonical_hash(condition):
            missing.append('REGISTERED_CONDITION_MISMATCH')
        else:
            row['receipt_paths'] = json.dumps(task.get('gate_receipts', []))
            row['validator_path'] = json.dumps(task.get('validator_argv', []))
            missing.append('PREREQUISITE_ADMISSION_NOT_EVALUATED')
        run_dir = results/'runs'/run_id
        status_path = run_dir/'status.json'
        if status_path.exists() or status_path.is_symlink():
            try:
                status = regular_json(run_dir, 'status.json')
                row['state'] = status['state']
                if status['state'] not in STATES:
                    raise ValueError('unknown scheduler state')
                row['validation_state'] = 'PENDING' if status['state'] == 'RUNNING' else 'NOT_ACCEPTED'
                if status.get('reason'):
                    missing.append(status['reason'])
                attempt = confined_file(run_dir, status['attempt'])
                row['attempt_path'] = str(attempt)
                if status['state'] == 'DONE':
                    _, manifest = validate_attempt(attempt)
                    if status.get('manifest_sha256') != sha256(attempt/'manifest.json'):
                        raise ValueError('root manifest hash mismatch')
                    if (manifest['run_id'] != run_id or manifest['replicate'] != replicate
                            or manifest['condition'] != condition):
                        raise ValueError('original matrix/run identity mismatch')
                    if matrix_sha256 and manifest['matrix_sha256'] != matrix_sha256:
                        raise ValueError('original matrix hash mismatch')
                    row['validation_state'] = 'VALIDATED_DONE'
                    row['minimum_repair'] = ''
                    missing = []
                else:
                    manifest = regular_json(attempt, 'manifest.json')
                row['manifest_path'] = str(attempt/'manifest.json')
                row['raw_paths'] = json.dumps([str(attempt/p) for p in manifest.get('raw_hashes', {})])
                row['validator_path'] = json.dumps(manifest.get('validator_argv', []))
                row['receipt_paths'] = json.dumps([str(attempt/r['path']) for r in manifest.get('gate_receipts', {}).values()])
                row['handler_id'] = (manifest.get('task') or {}).get('id', '')
                actual_task = manifest.get('task') or {}
                row['task_artifacts'] = json.dumps(actual_task.get('artifacts', []))
                row['input_artifacts'] = json.dumps([a for a in actual_task.get('artifacts', []) if a.get('role') == 'input'])
                row['producer_argv'] = json.dumps(manifest.get('argv', actual_task.get('argv', [])))
            except (OSError, ValueError, KeyError, TypeError) as error:
                row['validation_state'] = 'INVALID_EVIDENCE'
                missing.append(str(error))
        row['missing_items'] = ' | '.join(missing)
        ledger.append(row)
    return ledger


def summarize(rows):
    def counts(selected):
        return dict(conditions=len({r['cell_id'] for r in selected}), runs=len(selected),
                    validated_done_runs=sum(r['validation_state'] == 'VALIDATED_DONE' for r in selected),
                    states=dict(collections.Counter(r['state'] for r in selected)),
                    validation_states=dict(collections.Counter(r['validation_state'] for r in selected)))
    return dict(**counts(rows), minimum=counts([r for r in rows if r.get('minimum_configuration') == 'yes']),
                resource_classes=dict(collections.Counter(r['resource_class'] for r in rows)),
                groups={group: counts([r for r in rows if r['group'] == group]) for group in sorted({r['group'] for r in rows})})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix', type=pathlib.Path, default=ROOT/'docs/49-eval-audit/run-matrix.csv')
    parser.add_argument('--results', type=pathlib.Path, default=ROOT/'results')
    parser.add_argument('--registry', type=pathlib.Path)
    parser.add_argument('--out', type=pathlib.Path, required=True)
    args = parser.parse_args()
    tasks = read_json(args.registry)['tasks'] if args.registry else []
    rows = coverage_rows(load_matrix(args.matrix), args.results, tasks, sha256(args.matrix))
    summary = dict(summarize(rows), matrix_sha256=sha256(args.matrix), results=str(args.results.resolve()),
                   claim_note='DONE is measurement validation, not automatic scientific claim acceptance.')
    summary_path = args.out.with_suffix('.summary.json')
    if args.out.exists() or summary_path.exists():
        parser.error('coverage snapshot already exists; choose a new output')
    with args.out.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    with summary_path.open('x') as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps(summary, sort_keys=True))


if __name__ == '__main__':
    main()

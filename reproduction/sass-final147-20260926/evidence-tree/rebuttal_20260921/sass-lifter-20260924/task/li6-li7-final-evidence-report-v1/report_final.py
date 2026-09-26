#!/usr/bin/env python3
"""Derived report only. Requires existing terminal final147 validator PASS."""
import argparse
from collections import Counter
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text())


def rows(path):
    with Path(path).open() as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def need(ok, message):
    if not ok:
        raise RuntimeError(message)


def report(run, ledger_path, historical_path):
    # These two existence checks precede all run reads: no active-run report.
    need((run/'controller/controller-finish.json').is_file() and
         (run/'MODEL_VALIDATION.json').is_file(), 'terminal controller and saved validation required')
    finish=load(run/'controller/controller-finish.json')
    validation=load(run/'MODEL_VALIDATION.json')
    need(validation.get('status')=='MODEL_CONNECTED_PASS_COMBINED_ADDED49' and
         validation.get('registered_storage_count')==147 and
         validation.get('selected_count')==49 and
         finish.get('worker_returncode')==0 and finish.get('guard_returncode')==0 and
         finish.get('guard_clean') is True, 'existing final147 acceptance absent; this script does not grant acceptance')
    ledger=load(ledger_path)
    expected={a:r['storage_bytes'] for r in ledger['entries'] for a in r['aliases']}
    registration=load(run/'result/cell/registration.json')
    result=load(run/'result/cell/result.json')
    plan=load(run/'plan.json')
    reg={r['aliases'][0]:r for r in registration['storages']}
    need(len(expected)==147 and {a:r['bytes'] for a,r in reg.items()}==expected,
         'report inputs disagree with exact147 ledger')
    accepted={r['alias']:r for r in validation['by_storage']}
    need(set(accepted)==set(reg),'accepted storage report differs from registration')
    observations={a:{'modeled_parameter_address_row_count':0,'min_observed_address':None,
                     'max_observed_address':None} for a in reg}
    nonmodeled=Counter()
    grouping=('reason','module_id','kernel','api','cbid','operation','range_policy',
              'allowed','opaque_unmodeled','cubin_only')
    # Match only the exact module/range relation already accepted by frozen validator.
    for row in rows(run/'logs/coverage.jsonl'):
        if row.get('modeled') is not True:
            nonmodeled[tuple(row.get(key) for key in grouping)]+=1
            continue
        address=row.get('address')
        if not isinstance(address,int):
            continue
        for alias,r in reg.items():
            if (r['address']<=address<r['address']+r['bytes'] and
                    row.get('module_id') in accepted[alias]['active_complete_modules']):
                item=observations[alias];item['modeled_parameter_address_row_count']+=1
                item['min_observed_address']=address if item['min_observed_address'] is None else min(address,item['min_observed_address'])
                item['max_observed_address']=address if item['max_observed_address'] is None else max(address,item['max_observed_address'])
    event_path=run/'result/combined-plugin'/f"worker-{validation['target_pid']}.jsonl"
    events=list(rows(event_path))
    selected=[row for row in events if row.get('event')=='selected_output']
    fields=('alias','category','layer','profile','selected_order','request_id','os_thread_id',
            'weight_ptr','weight_bytes','input_sha256','native_sha256','candidate_sha256',
            'output_shape','output_dtype','output_bytes','select_rc','end_rc','status')
    storage_rows=[{'alias':a,'registered_base':r['address'],'registered_extent_bytes':r['bytes'],
                   'accepted_active_complete_modules':accepted[a]['active_complete_modules'],
                   **observations[a]} for a,r in sorted(reg.items())]
    return {'schema':'hbfsim.final147_evidence_report.v1','evidence_class':'DOC_DERIVED',
            'status':'REPORT_OF_EXISTING_FINAL_SINGLE_RUN_PASS_NOT_NEW_VALIDATION',
            'final_single_run':{'run':str(run),'epoch':validation['epoch'],
                'acceptance_status':validation['status'],'registered_storages':len(reg),
                'registered_extent_bytes':sum(r['bytes'] for r in reg.values()),
                'selected_output_count':len(selected),
                'controller_seconds':(finish['finish_unix_ns']-finish['start_unix_ns'])/1e9,
                'output_token_ids':result['output_token_ids'],
                'output_token_ids_sha256':result['output_token_ids_sha256'],
                'request_terminal_status':result['request_terminal_status']},
            'historical_union':{'receipt':str(historical_path),'record':load(historical_path),
                'interpretation':'prior union is provenance, not substituted for this final run'},
            'registered_storage_and_modeled_parameter_addresses':storage_rows,
            'selected_outputs':[{k:r.get(k) for k in fields} for r in selected],
            'observed_nonmodeled_coverage':{
                'row_count':sum(nonmodeled.values()),
                'groups':[{**dict(zip(grouping,key)),'row_count':count}
                          for key,count in nonmodeled.most_common()],
                'missing_field_meaning':'null means not recorded by this coverage schema; API is not inferred from kernel name',
                'interpretation':'Observed nonmodeled rows only. reason=allowed with modeled=false is not automatically an unsupported instruction.'},
            'scheduled_phase_receipts':[r for r in events if r.get('event') in
                                        ('scheduled_prefill','scheduled_decode')],
            'selected49_prefill_scope':{
                'wrapper_policy':'Canonical combined plugin calls original forward during scheduled prefill and activates selected TLS scope only for scheduled decode; head selection is bound to the decode compute_logits path.',
                'observed_phase_evidence':'See copied scheduled phase receipts; coverage rows lack a direct request/phase join, so their nonmodeled groups are not attributed to prefill.',
                'claimed_modeled_consumer_scope':'49 selected scheduled decode consumers only'},
            'module_and_aggregate_accounting':result['access_accounting']['access'],
            'limits':{
                'registered_extent_is_bytes_touched':False,
                'coverage_address_row_semantics':'observed modeled parameter address inside live storage, not exhaustive memory-reference addresses or unique touched-byte extent',
                'service_requests_is_reference_completion_count':False,
                'shared_module_counters_are_per_storage_counts':False,
                'native_prefill_consumer_coverage':'NOT_MEASURED: selected49 claim is scheduled decode; aggregate native-out-of-range counters cannot identify all prefill consumers',
                'unsupported_consumer_enumeration':'Observed nonmodeled categories are listed above; exhaustive unsupported-consumer enumeration UNKNOWN, and no native/unsupported path is promoted to modeled coverage',
                'exact_experts_or_expert_lanes_triggered':'UNKNOWN: registered expert storage/address rows do not enumerate every expert or lane',
                'all_possible_shapes_and_consumers_covered':False},
            'sources':{'plan':str(run/'plan.json'),'validation':str(run/'MODEL_VALIDATION.json'),
                'controller_finish':str(run/'controller/controller-finish.json'),
                'registration':str(run/'result/cell/registration.json'),
                'result':str(run/'result/cell/result.json'),'coverage':str(run/'logs/coverage.jsonl'),
                'plugin':str(event_path),'plugin_source':plan.get('plugin_path'),
                'plugin_source_sha256':plan.get('plugin_sha256'),
                'target_ledger':str(ledger_path)},
            'plan_scope':plan['scope']}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--target-ledger',type=Path,required=True)
    parser.add_argument('--historical-union',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    need(not args.output_dir.exists(),'new report directory required')
    data=report(args.run.resolve(),args.target_ledger.resolve(),args.historical_union.resolve())
    args.output_dir.mkdir(parents=True,exist_ok=False)
    (args.output_dir/'REPORT.json').write_text(json.dumps(data,indent=2)+'\n')
    final=data['final_single_run']
    (args.output_dir/'REPORT.md').write_text(
        '# Final147 single-run evidence\n\n'
        f"Existing validator: {final['acceptance_status']}, epoch {final['epoch']}. "
        f"Registered {final['registered_storages']} storages / {final['registered_extent_bytes']:,} B; "
        f"{final['selected_output_count']} selected output receipts. Final tokens {final['output_token_ids']}. "
        f"Controller {final['controller_seconds']:.3f} seconds.\n\n"
        'REPORT.json separates full registered extents from observed modeled parameter-address rows, '
        'module/aggregate access, admission and completion counters, and selected output comparisons. '
        'Module counts do not prove per-storage latency or every byte touched. Exact triggered experts, '
        'all prefill consumers and exhaustive unsupported-consumer enumeration remain UNKNOWN/NOT_MEASURED. '
        'Observed nonmodeled rows are grouped separately by their recorded reason, module, kernel and API fields; absent fields are not inferred. '
        'The historical147 union is retained separately and is not a substitute for this final run. '
        'This generator reports existing acceptance; it does not change its criteria.\n')


if __name__=='__main__':
    main()

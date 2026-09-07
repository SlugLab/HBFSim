"""Frozen HF route intervals for a bounded counterfactual prefix, never compute gold.

The index pins existing acquisition/review buffers, not a new authentication
scheme. Validation reconciles supplied buffers only; a reviewer must approve
the actual capture separately. This module never imports Torch or queries GPU.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import stat

from budget_fast_tier import budget_fast_tier
from evaluation_inventory import load_hf_snapshot, validate_evaluation_inventory
from hf_route_array import decode_route_array
from prefetch_replay import MAX_TIME, validate_nodes
from routing_metrics import compose_routes
from verify_hf_metadata import canonical, strict_object

SEMANTICS='ROUTE_TO_ROUTE_DEVICE_ELAPSED_INCLUDING_CAPTURE_AND_SCHEDULING'
CACHE_SENSITIVITY_RHO_1_32='RHO_1_32_EXTRA_DIAGNOSTIC'
ORIGINAL_RHO_MATRIX='ORIGINAL_RHO_MATRIX_PROJECTED_CONTROL'
ARTIFACTS=('sidecar','binding','protocol','raw_return','raw_routes','routing_trace',
           'worker','wrapper','triplet','runtime_manifest','project_manifest',
           'tuning_manifest','consistency','capture_review')
LIMITS={name:1<<20 for name in ARTIFACTS}
LIMITS.update(routing_trace=64<<20, runtime_manifest=8<<20)
ROOT=Path(__file__).resolve().parents[2]


def require(condition,message):
    if not condition:raise ValueError('HF route horizon: '+message)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def regular_bytes(path, limit):
    """Read one exact regular file under its artifact-specific byte bound."""
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(descriptor,'rb') as stream:
        before=os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size<=limit,
                'artifact file type/size')
        raw=stream.read(limit+1)
        after=os.fstat(stream.fileno())
    require((before.st_dev,before.st_ino,before.st_size)==
            (after.st_dev,after.st_ino,after.st_size) and len(raw)==before.st_size,
            'artifact changed/oversized during read')
    return raw


def milliseconds(value):
    require(type(value) is float and math.isfinite(value) and value>=0,'invalid device milliseconds')
    scaled=value*1000000
    require(math.isfinite(scaled) and scaled<=MAX_TIME,'device interval exceeds clock range')
    return int(round(scaled))


def acquire(index_raw):
    require(len(index_raw)<=1<<20,'oversized horizon index')
    index=strict_object(index_raw)
    require(set(index)=={'schema_version','kind','member_id','artifacts'} and
            index['schema_version']==1 and index['kind']=='HF_ROUTE_HORIZON_INPUTS_V1' and
            type(index['member_id']) is str and 0<len(index['member_id'])<=128 and
            set(index['artifacts'])==set(ARTIFACTS),'index schema')
    buffers={}
    paths=set()
    for name,item in index['artifacts'].items():
        require(type(item) is dict and set(item)=={'path','sha256'},'artifact reference')
        path=Path(item['path'])
        require(path.is_absolute() and path.is_relative_to(ROOT) and
                path.resolve(strict=True)==path and path not in paths,'artifact path/alias')
        paths.add(path)
        raw=regular_bytes(path,LIMITS[name])
        require(len(raw)<=LIMITS[name] and sha(raw)==item['sha256'],'artifact bytes/hash '+name)
        buffers[name]=raw
    return index,buffers


def validate_capture(buffers, *, test_only, dimensions):
    """Reconcile event slots and actual route buffers; tiny inputs remain MOCK."""
    require(set(buffers)==set(ARTIFACTS),'capture buffer set')
    for name,raw in buffers.items():
        require(type(raw) is bytes and len(raw)<=LIMITS[name],'bounded capture bytes '+name)
    docs={k:strict_object(v) for k,v in buffers.items()
          if k not in ('raw_routes','routing_trace','capture_review')}
    event,binding,protocol,worker,wrapper,triplet=(docs[k] for k in
        ('sidecar','binding','protocol','worker','wrapper','triplet'))
    layers,experts,top_k=dimensions
    require(type(test_only) is bool,'test provenance')
    require(all(type(v) is int and v>0 for v in dimensions),'dimensions')
    require(test_only or dimensions==(48,128,8),'fixed real pilot dimensions')
    require((protocol['layers'],protocol['experts'],protocol['top_k'])==dimensions and
            protocol['input_len']==32 and protocol['output_len']==8 and
            len(protocol['prompt_token_ids'])==32 and protocol['route_shape']==[39,layers,top_k],
            'fixed protocol')
    returned=docs['raw_return']
    require(returned['prompt_token_ids']==protocol['prompt_token_ids'] and
            len(returned['output_token_ids'])==8 and returned['finished'] is True and
            returned['finish_reason']=='length' and returned['route_shape']==protocol['route_shape'] and
            returned['route_dtype']=='int32' and returned['scientific_validation_passed'] is False and
            returned['provenance']==('MOCK' if test_only else 'CHECKPOINT_METADATA') and
            all(type(t) is int and 0<=t<protocol['vocab_size'] for t in
                returned['prompt_token_ids']+returned['output_token_ids']), 'raw return protocol/tokens')
    require(event['status']=='UNVALIDATED_ROUTE_INTERVAL_CAPTURE' and
            event['timing_semantics']==SEMANTICS and event['test_only'] is test_only and
            event['provenance']==('MOCK' if test_only else 'UNVALIDATED_ROUTING_CAPTURE') and
            event['scientific_validation_passed'] is False and
            event['preinitialization_complete'] is True and event['primary_error'] is None and
            event['time_origin']=='FIRST_MEASURED_CAPTURE_EVENT' and
            event['nanosecond_encoding']=='round(milliseconds * 1000000); no nanosecond resolution claim',
            'event capture status/semantics')
    require(len(event['restoration'])==3 and all(r['status']=='RESTORED' for r in event['restoration']),
            'event restoration')
    require(event['stream']['device_index']==0 and type(event['stream']['cuda_stream']) is int and
            event['stream']['cuda_stream']>=0,'device stream')
    expected=dict(arm='capture',run_id=binding['run_id'],input_binding_sha256=sha(buffers['binding']),
                  protocol_sha256=sha(buffers['protocol']),raw_return_sha256=sha(buffers['raw_return']),
                  raw_routes_sha256=sha(buffers['raw_routes']))
    require(binding['arm']=='capture' and event['bindings']==expected and
            binding['protocol_sha256']==expected['protocol_sha256'] and
            binding['test_only'] is test_only and binding['gpu_uuid']==event['gpu_uuid'] and
            binding['scientific_validation_passed'] is False and
            binding['provenance']==('MOCK' if test_only else 'UNVALIDATED_ROUTING_CAPTURE'),
            'event/input binding')
    require(worker['status']=='ARM_RETURNED_UNVALIDATED' and worker['arm']=='capture' and
            worker['test_only'] is test_only and worker['primary_error'] is None and
            worker['cleanup_errors']==[] and worker['compatibility_restored'] is True and
            worker['scientific_validation_passed'] is False and
            worker['provenance']==('MOCK' if test_only else 'UNVALIDATED_ROUTING_CAPTURE') and
            worker['worker_identity']==event['worker_identity'],'worker cleanup/identity')
    summary=dict(enabled=True,status=event['status'],artifact='route-device-events.json',
                 sha256=sha(buffers['sidecar']),counts=event['counts'])
    require(worker['route_cuda_events']==summary,'worker sidecar hash')
    require(wrapper['status']=='ARM_RETURNED_UNVALIDATED' and wrapper['arm']=='capture' and
            wrapper['scientific_validation_passed'] is False and
            wrapper['provenance']==('MOCK' if test_only else 'UNVALIDATED_ROUTING_CAPTURE') and
            wrapper['input_binding_sha256']==sha(buffers['binding']) and
            wrapper['worker_status_sha256']==sha(buffers['worker']),'wrapper binding')
    require(triplet['status']=='PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED' and
            triplet['test_only'] is test_only and triplet['scientific_validation_passed'] is False and
            triplet['provenance']==('MOCK' if test_only else 'UNVALIDATED_ROUTING_CAPTURE') and
            triplet['run_id']==binding['run_id'] and
            triplet['gpu_uuid']==event['gpu_uuid'] and len(triplet['arms'])==3 and
            [r['arm'] for r in triplet['arms']]==['native','capture','repeat'],'triplet status')
    for arm in triplet['arms']:
        require(arm['status']=='ARM_RETURNED_UNVALIDATED' and arm['exit_code']==0 and
                arm['exit_code_observed'] is True and arm['owned_exit_observed'] is True and
                arm['owned_remaining']==[] and arm['uncertain_session'] is False,'owned arm completion')
    arm=triplet['arms'][1]
    require(arm['process_identity']==event['worker_identity'] and arm['worker_status']==worker and
            arm['files']['worker-status']['sha256']==sha(buffers['worker']) and
            arm['files']['input-binding']['sha256']==sha(buffers['binding']) and
            arm['files']['owned-worker-status']['sha256']==sha(buffers['wrapper']),'triplet capture binding')
    tb=triplet['input_bindings']
    require(sha(buffers['runtime_manifest'])==binding['runtime_source_manifest_sha256']==tb['runtime_manifest_sha256'] and
            sha(buffers['tuning_manifest'])==binding['selected_tuning_manifest_sha256']==tb['tuning_manifest_sha256'] and
            sha(buffers['project_manifest'])==wrapper['source_manifest_sha256']==tb['project_source_manifest_sha256'],
            'runtime/tuning/project manifest identity')
    require(type(binding['git_commit']) is str and len(binding['git_commit'])==40 and
            all(character in '0123456789abcdef' for character in binding['git_commit']),
            'capture source HEAD declaration')
    runtime=docs['runtime_manifest']
    require(runtime['source_extension']=='MOE_TUNING_V1' and runtime['cuda_event_extension']=='ROUTE_EVENTS_V1' and
            len(runtime['artifacts'])==135 and all(k in runtime['artifacts'] for k in
            ('site-packages/torch/cuda/__init__.py','site-packages/torch/cuda/streams.py')),
            'extended runtime source contract')
    require(bool(buffers['capture_review'].strip()),'missing separately reviewed capture')
    count=7*layers
    require(event['counts']==dict(batch_events=8*layers,save_batches=8,reader_calls=1,saved_slots=39,
                                 decode_nodes=count,observed_intervals=count-1,missing_terminal=1),'event counts')
    require(len(event['captures'])==8*layers and len(event['saves'])==8 and
            len(event['readers'])==1 and len(event['token_join'])==39 and
            len(event['decode_intervals'])==count,'event arrays')
    prior=0.0
    for i,row in enumerate(event['captures']):
        batch,layer=divmod(i,layers)
        require(all(type(row[k]) is int for k in ('event_index','batch_index','layer_id','batch_rows')) and
                (row['event_index'],row['batch_index'],row['layer_id'],row['batch_rows'])==
                (i,batch,layer,32 if batch==0 else 1),'capture order')
        require(type(row['relative_ns']) is int and milliseconds(row['relative_ms'])==row['relative_ns'] and row['relative_ms']>=prior,
                'relative capture time')
        prior=row['relative_ms']
    require(event['captures'][0]['relative_ms']==0.0,'relative event origin')
    decoded=decode_route_array(buffers['raw_routes'],protocol,expected_dtype='int32')
    all_routes=[[list(decoded.values[(t*layers+l)*top_k:(t*layers+l+1)*top_k])
                 for l in range(layers)] for t in range(39)]
    slots=[]
    for batch,saved in enumerate(event['saves']):
        rows=32 if batch==0 else 1
        start=0 if batch==0 else batch+31
        require(saved['batch_index']==batch and len(saved['indices'])==rows and
                all(type(v) is int and v>=0 for v in saved['indices']) and
                saved['event_indices']==list(range(batch*layers,(batch+1)*layers)) and
                saved['routes']==all_routes[start:start+rows],'saved slot/routes/event join')
        slots.extend(saved['indices'])
    require(len(set(slots))==39 and event['readers'][0]==dict(indices=slots,routes=all_routes),
            'reader slot/order join')
    for token,row in enumerate(event['token_join']):
        batch,offset=(0,token) if token<32 else (token-31,0)
        require(row==dict(route_token_index=token,slot=slots[token],batch_index=batch,batch_row=offset,
                         event_indices=list(range(batch*layers,(batch+1)*layers))),'token slot join')
    lines=buffers['routing_trace'].splitlines()
    require(len(lines)==39*layers,'routing trace coverage')
    decode=[]
    for i,line in enumerate(lines):
        row=strict_object(line);token,layer=divmod(i,layers);phase='prefill' if token<32 else 'decode'
        require(row['run_id']==binding['run_id'] and row['route_token_index']==token and
                row['layer_id']==layer and row['phase']==phase and
                row['token_step']==(token if token<32 else token-32) and
                row['topk_expert_ids']==all_routes[token][layer] and
                row['model_fingerprint']==binding['model_fingerprint'],'routing trace/raw join')
        if phase=='decode':decode.append(row)
    gaps=[]
    for i,row in enumerate(event['decode_intervals']):
        step,layer=divmod(i,layers);event_index=layers+i
        require(all(type(row[k]) is int for k in ('step','layer_id','event_index')) and
                (row['step'],row['layer_id'],row['event_index'])==(step,layer,event_index),'interval order')
        if i==count-1:
            require(row['availability']=='MISSING' and row['reason']=='NO_SUCCESSOR_ROUTE' and
                    row['next_node'] is None and row['elapsed_ms'] is None and row['elapsed_ns'] is None,
                    'terminal successor must remain missing')
            gaps.append(None)
        else:
            next_step,next_layer=divmod(i+1,layers)
            require(row['availability']=='OBSERVED' and row['reason'] is None and
                    row['next_node']==dict(step=next_step,layer_id=next_layer,event_index=event_index+1) and
                    type(row['elapsed_ns']) is int and milliseconds(row['elapsed_ms'])==row['elapsed_ns'],
                    'interval successor/encoding')
            gaps.append(row['elapsed_ns'])
    require(sum(gaps[:-1])<=MAX_TIME,'route span exceeds clock range')
    return docs,decode,gaps


def capacity_contract(inv, budget, mock, cache_sensitivity):
    require(cache_sensitivity is None or type(cache_sensitivity) is str,
            'cache sensitivity marker type')
    if mock:
        require(cache_sensitivity is None, 'cache sensitivity requires captured HF inputs')
        return None
    if cache_sensitivity is None:
        require(budget['C_fast_effective']*16==inv['eligible_expert_bytes'],
                'fixed capacity budget')
        return None
    if cache_sensitivity==CACHE_SENSITIVITY_RHO_1_32:
        require(budget['C_fast_effective']*32==inv['eligible_expert_bytes'] and
                budget['rho_requested']==1/32 and budget['rho']==1/32,
                'unsupported cache sensitivity budget/marker')
        return dict(kind=CACHE_SENSITIVITY_RHO_1_32,rho_requested=budget['rho_requested'],
                    original_requested_matrix=False,parameter_selected_for_win=False,
                    purpose='EXERCISE_PREFETCH_ISSUE_PATH_NOT_REQUIRE_BENEFIT')
    matched=[divisor for divisor in (16,2,1)
             if budget['C_fast_effective']*divisor==inv['eligible_expert_bytes'] and
             budget['rho_requested']==1/divisor and budget['rho']==1/divisor]
    require(cache_sensitivity==ORIGINAL_RHO_MATRIX and len(matched)==1,
            'unsupported original rho budget/marker')
    return dict(kind=ORIGINAL_RHO_MATRIX,rho_requested=budget['rho_requested'],
                original_requested_matrix=True,parameter_selected_for_win=False,
                purpose='COMPLETE_ORIGINAL_REQUESTED_RHO_MATRIX_PROJECTED_CONTROL')


def prepare_route_horizon(snapshots, index, buffers, hf_snapshot, initial_residency,
                          cache_sensitivity=None):
    require(initial_residency=='cold','first horizon experiment requires common cold residency')
    inv,budget,routes,manifest=(strict_object(snapshots[k]) for k in
                              ('inventory','budget','routes','routing_manifest'))
    validate_evaluation_inventory(inv,hf_snapshot=hf_snapshot)
    require(inv['format']=='HF_SAFETENSORS','route horizon requires HF inventory')
    mock=inv['provenance']=='MOCK'
    expected=budget_fast_tier(inv,hf_snapshot=hf_snapshot,inventory_file_bytes=snapshots['inventory'],
        **{k:budget[k] for k in ('fast_bytes','active_sequences','context_tokens','kv_element_bytes',
                                'workspace_bytes','safety_bytes','legacy_ratio')})
    require(expected==budget and budget['active_sequences']==1 and budget['context_tokens']==64 and
            budget['kv_element_bytes']==2 and budget['workspace_bytes']==budget['safety_bytes']==0,
            'fixed capacity budget')
    sensitivity=capacity_contract(inv,budget,mock,cache_sensitivity)
    docs,decode,gaps=validate_capture(buffers,test_only=mock,dimensions=(inv['layers'],inv['E'],inv['k']))
    binding=docs['binding'];model=inv['model_binding'];consistency=docs['consistency']
    for b,k in (('metadata_receipt_sha256','receipt_sha256'),('metadata_complete_sha256','complete_sha256'),
                ('metadata_identity_sha256','metadata_identity_sha256'),('observation_identity_sha256','observation_identity_sha256'),
                ('donor_sha256','legacy_inventory_sha256'),('model_fingerprint','historical_model_fingerprint')):
        require(binding[b]==model[k],'HF observation identity '+b)
    require(consistency['status']=='ROUTE_CONSISTENCY_ONLY' and consistency['checks'] and
            all(v is True for v in consistency['checks'].values()) and
            consistency['scientific_validation_passed'] is False,'frozen consistency status')
    require(consistency['effective_test_only'] is mock and all(consistency['metadata'][k]==model[v] for k,v in
            (('receipt_sha256','receipt_sha256'),('complete_sha256','complete_sha256'),
             ('donor_sha256','legacy_inventory_sha256'),('metadata_identity_sha256','metadata_identity_sha256'),
             ('observation_identity_sha256','observation_identity_sha256'))),'consistency metadata binding')
    capture=[a for a in consistency['arms'] if a['arm']=='capture']
    require(len(capture)==1 and all(capture[0]['artifacts'][k]==sha(buffers[v]) for k,v in
            (('raw-routes.npy','raw_routes'),('routing.jsonl','routing_trace'),('input-binding.json','binding'),
             ('raw-return.json','raw_return'),('protocol.json','protocol'))),
            'consistency capture identity')
    member=index['member_id'];series=routes['series'];provenance='MOCK' if mock else 'PROJECTED'
    require(series in ('real','shuffled') and routes['provenance']==provenance and
            manifest['schema_version']==1 and manifest['provenance']==provenance and
            manifest['input_source_kind']==('SYNTHETIC_CONTROL' if mock else 'CAPTURED_ROUTE') and
            manifest['concurrency_kind']=='TRACE_COMPOSED' and manifest['composition_seed']==0 and
            manifest['inventory_sha256']==model['legacy_inventory_sha256'] and
            (manifest['layers'],manifest['E'],manifest['k'])==(inv['layers'],inv['E'],inv['k']) and
            manifest['outputs'][series]['sha256']==sha(snapshots['routes']) and
            manifest['member_traces']==[member] and len(manifest['input_members'])==1 and
            manifest['input_members'][0]['member_id']==member and
            manifest['input_members'][0]['sha256']==sha(buffers['routing_trace']),'routing manifest binding')
    composed=compose_routes({member:decode},experts=inv['E'],top_k=inv['k'],layers=inv['layers'],seed=0,
                            shuffled=series=='shuffled')
    require(routes['routes']==composed,'route permutation differs from captured source/seed')
    nodes=[dict(step=r['token_step'],layer=r['layer_id'],members={member:r['topk_expert_ids']},route_gap_ns=gap)
           for r,gap in zip(composed,gaps)]
    require(len(composed)==len(gaps),'route/interval coverage')
    objects={};address=0
    for row in sorted(inv['experts'],key=lambda r:(r['layer'],r['expert'])):
        size=row['packed_logical_pages']*inv['page_bytes']
        objects[row['layer'],row['expert']]=dict(bytes=row['bytes'],transfer_bytes=size,logical_address=address)
        address+=size
    validate_nodes(nodes,objects,budget['page_aligned_effective_bytes'],(),route_horizon=True)
    data=dict(inventory=inv,budget=budget,routes=routes,routing_manifest=manifest,
              route_horizon=dict(semantics=SEMANTICS,series=series,member=member,
                capture_validation='SUPPLIED_BUFFER_CONSISTENCY_NOT_CAPTURE_CERTIFICATION',
                input_index_sha256=sha(snapshots['route_horizon']),
                capture_git_commit=binding['git_commit'],runtime_manifest_sha256=sha(buffers['runtime_manifest']),
                project_manifest_sha256=sha(buffers['project_manifest']),
                capture_review_sha256=sha(buffers['capture_review']),
                observed_nodes=len(gaps)-1,terminal_missing=1,
                timing_control='SAME_POSITION_BASE_GAPS_WITH_SHUFFLED_ROUTES' if series=='shuffled' else
                               'CAPTURED_ROUTE_BASE_GAPS',scientific_validation_passed=False))
    if sensitivity is not None:
        data['route_horizon']['capacity_sensitivity']=sensitivity
    return data,nodes,objects,(),provenance

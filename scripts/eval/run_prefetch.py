#!/usr/bin/env python3
"""Run three matched CPU causal prefetch projections using native MQSim.

Inputs are validated GGUF inventory and budget, real/shuffled routing_metrics
output plus its manifest, and a compute-only duration index. The index binds the
inventory and routing manifest hashes, series, prompt_tokens, source_kind, and
one {step,layer,compute_ns} per composed layer batch. No default compute timing
or model dimensions are supplied. All outputs remain PROJECTED (MOCK when any
source is synthetic); no hardware gold receipt or scheduler DONE is produced.

The explicit --route-horizon branch instead joins a frozen HF capture with
335 route intervals and one missing terminal endpoint. Its added media waits
are counterfactual prefix quantities, not compute-only or generation timing.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
import os
from pathlib import Path
import time

from budget_fast_tier import budget_fast_tier
from freeze_storage_split import regular_bytes
from inventory_checkpoint import validate_inventory
from mqsim_service import MqsimService
from prefetch_replay import POLICIES, nonnegative, replay, validate_nodes
from replay_arrivals import identity, regular_file
from run_manifest import atomic_json, environment_snapshot, git_snapshot, sha256

ROOT=Path(__file__).resolve().parents[2]
INPUTS=('inventory','budget','routes','routing_manifest','compute','profile')


def prepare(snapshots, initial_residency):
    data={name:json.loads(payload) for name,payload in snapshots.items() if name!='profile'}
    inv,budget,routes,manifest,compute=(data[k] for k in INPUTS[:-1])
    validate_inventory(inv)
    inventory_hash=hashlib.sha256(snapshots['inventory']).hexdigest()
    routing_hash=hashlib.sha256(snapshots['routing_manifest']).hexdigest()
    if (manifest['schema_version']!=1 or manifest['concurrency_kind']!='TRACE_COMPOSED'
            or manifest['inventory_sha256']!=inventory_hash
            or (manifest['E'],manifest['k'],manifest['layers'])!=(inv['E'],inv['k'],inv['layers'])):
        raise ValueError('routing inventory identity/dimensions mismatch')
    series=routes['series']
    source=manifest['input_source_kind']
    if (source not in ('CAPTURED_ROUTE','SYNTHETIC_CONTROL','EXTERNAL_UNVERIFIED')
            or manifest['provenance']!=('MOCK' if source=='SYNTHETIC_CONTROL' else 'PROJECTED')):
        raise ValueError('routing source/provenance attribution mismatch')
    if (series not in ('real','shuffled') or routes['provenance']!=manifest['provenance']
            or manifest['outputs'][series]['sha256']!=hashlib.sha256(snapshots['routes']).hexdigest()
            or manifest['provenance'] not in ('PROJECTED','MOCK')):
        raise ValueError('routing series identity/provenance mismatch')
    if (compute['schema_version']!=1 or compute['inventory_sha256']!=inventory_hash
            or compute['routing_manifest_sha256']!=routing_hash or compute['routing_series']!=series
            or compute['timing_semantics']!='COMPUTE_ONLY_NO_MEDIA_STALL'
            or compute['source_kind'] not in ('TIMING_TRACE','SYNTHETIC_CONTROL','EXTERNAL_UNVERIFIED')):
        raise ValueError('compute input identity/semantics mismatch')
    recomputed=budget_fast_tier(inv,**{k:budget[k] for k in
        ('fast_bytes','active_sequences','context_tokens','kv_element_bytes','workspace_bytes','safety_bytes','legacy_ratio')})
    if recomputed!=budget:
        raise ValueError('capacity budget differs from inventory-derived accounting')
    grouped=defaultdict(dict)
    for row in routes['routes']:
        step,layer,member=row['token_step'],row['layer_id'],row['member']
        ids=row['topk_expert_ids']
        if (type(step) is not int or step<0 or type(layer) is not int or not 0<=layer<inv['layers']
                or not isinstance(member,str) or not member or member in grouped[step,layer]
                or not isinstance(ids,list) or len(ids)!=inv['k'] or len(set(ids))!=len(ids)
                or any(type(e) is not int or not 0<=e<inv['E'] for e in ids)):
            raise ValueError('invalid/duplicate composed route identity')
        grouped[step,layer][member]=ids
    durations={}
    for node in compute['nodes']:
        key=(node['step'],node['layer'])
        if any(type(k) is not int or k<0 for k in key) or key in durations:
            raise ValueError('invalid/duplicate compute node')
        durations[key]=nonnegative(node['compute_ns'],'compute interval')
    if set(durations)!=set(grouped):
        raise ValueError('compute and routing node coverage differ')
    nodes=[dict(step=s,layer=l,members=members,compute_ns=durations[s,l])
           for (s,l),members in sorted(grouped.items())]
    if (not nodes or max(len(n['members']) for n in nodes)>budget['active_sequences']
            or nonnegative(compute['prompt_tokens'],'prompt tokens')+nodes[-1]['step']+1>budget['context_tokens']):
        raise ValueError('KV budget does not cover active sequences and trace context')
    page=inv['page_bytes']
    if page%512:
        raise ValueError('MQSim expert packing requires sector-aligned pages')
    objects={}
    address=0
    for row in sorted(inv['experts'],key=lambda e:(e['layer'],e['expert'])):
        size=row['packed_logical_pages']*page
        objects[row['layer'],row['expert']]=dict(bytes=row['bytes'],transfer_bytes=size,logical_address=address)
        address+=size
    resident=tuple(tuple(k) for k in budget['selected_experts']) if initial_residency=='budget' else ()
    validate_nodes(nodes,objects,budget['page_aligned_effective_bytes'],resident)
    provenance='MOCK' if (manifest['provenance']=='MOCK' or compute['source_kind']=='SYNTHETIC_CONTROL') else 'PROJECTED'
    return data,nodes,objects,resident,provenance


def run(args):
    output=args.out.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError('output must stay inside experiment checkout')
    route_horizon=getattr(args,'route_horizon',None) is not None
    cache_sensitivity=getattr(args,'cache_sensitivity',None)
    if route_horizon and (getattr(args,'compute',None) is not None or
                          getattr(args,'hf_metadata_refresh',None) is None):
        raise ValueError('route horizon requires HF metadata and excludes compute input')
    if not route_horizon and (getattr(args,'compute',None) is None or
                              getattr(args,'hf_metadata_refresh',None) is not None):
        raise ValueError('legacy compute mode requires compute and excludes horizon-only metadata')
    names=tuple(n for n in INPUTS if n!='compute')+('route_horizon',) if route_horizon else INPUTS
    snapshots={name:regular_bytes(getattr(args,name).absolute()) for name in names}
    auxiliary={};hf_snapshot=None
    if route_horizon:
        from hf_route_horizon_inputs import acquire,load_hf_snapshot,prepare_route_horizon
        index,auxiliary=acquire(snapshots['route_horizon'])
        hf_snapshot=load_hf_snapshot(args.hf_metadata_refresh)
        data,nodes,objects,resident,provenance=prepare_route_horizon(
            snapshots,index,auxiliary,hf_snapshot,args.initial_residency,
            cache_sensitivity=cache_sensitivity)
        profile=json.loads(snapshots['profile'])
        if profile.get('time_scale')!=1:
            raise ValueError('route horizon service requires time_scale=1')
    else:
        if cache_sensitivity is not None:
            raise ValueError('cache sensitivity requires route horizon mode')
        data,nodes,objects,resident,provenance=prepare(snapshots,args.initial_residency)
    if provenance=='MOCK' and output.is_relative_to((ROOT/'results/runs').resolve()):
        raise ValueError('synthetic controls cannot write formal run directories')
    binary=regular_file(args.binary).resolve(strict=True)
    if not binary.is_relative_to(ROOT):
        raise ValueError('native binary must remain inside experiment checkout')
    binary_identity=identity(binary)
    binary_hash=sha256(binary)
    output.mkdir(parents=True,exist_ok=False)
    manifest=dict(schema_version=1,provenance=provenance,resource_class='CPU_ONLY',
                  concurrency_kind='TRACE_COMPOSED',service_source='MQSIM_SIMULATED',
                  scope='LAYER_SYNCHRONOUS_CAUSAL_PROJECTION_NOT_LIVE_SERVING',
                  hardware_validated=False,scientific_validation_passed=False,
                  input_validation='IDENTITIES_AND_ACCOUNTING_ONLY_NOT_CAPTURE_OR_COMPUTE_GOLD',
                  initial_residency=args.initial_residency,budget=data['budget'],
                  address_map='DENSE_SORTED_LAYER_EXPERT_PAGE_PACKING_EXPERIMENT_ONLY',
                  binary=dict(path=str(binary),sha256=binary_hash,identity=binary_identity),
                  inputs={},policies={},git=git_snapshot(ROOT),tools={})
    for name in ('run_prefetch.py','prefetch_replay.py','mqsim_service.py','budget_fast_tier.py',
                 'inventory_checkpoint.py','freeze_storage_split.py','replay_arrivals.py','run_manifest.py'):
        manifest['tools'][name]=sha256(Path(__file__).with_name(name))
    if route_horizon:
        manifest.update(scope='PROJECTED_ROUTE_INTERVAL_PREFIX_REPLAY',
                        input_validation='SUPPLIED_BUFFER_CONSISTENCY_NOT_CAPTURE_CERTIFICATION',
                        route_horizon=data['route_horizon'])
        for name in ('hf_route_horizon_inputs.py','hf_route_array.py','evaluation_inventory.py','verify_hf_metadata.py','routing_metrics.py'):
            manifest['tools'][name]=sha256(Path(__file__).with_name(name))
        if cache_sensitivity is not None:
            manifest['capacity_sensitivity']=data['route_horizon']['capacity_sensitivity']
    try:
        frozen=output/'inputs'
        frozen.mkdir()
        for name,payload in snapshots.items():
            path=frozen/(name+'.json')
            with path.open('xb') as stream:
                stream.write(payload);stream.flush();os.fsync(stream.fileno())
            manifest['inputs'][name]=dict(path=str(path.relative_to(output)),original=str(getattr(args,name).absolute()),
                                          sha256=hashlib.sha256(payload).hexdigest())
        if route_horizon:
            # Retain exact acquired evidence and the accepted metadata snapshot.
            from evaluation_inventory import _unpack
            metadata_report,metadata_files,_,_=_unpack(hf_snapshot)
            retained=dict(auxiliary)
            retained.update({'metadata/'+k:v for k,v in metadata_files.items()})
            retained['metadata/receipt.json']=hf_snapshot.receipt_bytes
            retained['metadata/COMPLETE.json']=hf_snapshot.complete_bytes
            manifest['horizon_evidence']={}
            for name,payload in retained.items():
                path=frozen/'horizon'/name;path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(payload);stream.flush();os.fsync(stream.fileno())
                manifest['horizon_evidence'][name]=dict(path=str(path.relative_to(output)),
                                                       sha256=hashlib.sha256(payload).hexdigest())
        atomic_json(output/'environment.json',environment_snapshot())
        results={}
        for policy in POLICIES:
            directory=output/policy
            started=time.monotonic()
            if route_horizon:manifest['active_policy']=policy
            with MqsimService(binary,frozen/'profile.json',directory,timeout=args.timeout,
                              parallel_units=args.parallel_units) as service:
                result=replay(nodes,objects,capacity_bytes=data['budget']['page_aligned_effective_bytes'],
                              initial_resident=resident,policy=policy,service=service,
                              **({'route_horizon':True} if route_horizon else {}))
                # Preserve the replay ledger before transport/finish validation.
                if route_horizon:atomic_json(directory/'prefix-before-finish.json',result)
                receipt=service.finish()
                result.update(provenance=provenance,service_receipt=receipt,
                              service_observations=service.observations,topology=service.header)
                if cache_sensitivity is not None:
                    result['capacity_sensitivity']=data['route_horizon']['capacity_sensitivity']
                if receipt['issued']!=len(result['requests']) or receipt['issued_bytes']!=result['traffic_bytes']:
                    raise ValueError('controller/native service conservation mismatch')
                results[policy]=result
                manifest['policies'][policy]=dict(command=service.argv,wall_seconds=time.monotonic()-started)
        baseline=results['on_demand']['traffic_bytes']
        for policy,result in results.items():
            unmatched=defaultdict(deque)
            for request in results['on_demand']['requests']:
                unmatched[tuple(request['expert'])].append(request)
            for request in result['requests']:
                prior=unmatched[tuple(request['expert'])]
                match=prior.popleft() if prior else None
                request['baseline_request_id']=match['request_id'] if match else None
                request['extra_bytes']=0 if match else request['bytes']
            result['extra_bytes_matching_rule']='PER_EXPERT_REQUEST_ORDINAL_VS_MATCHED_ON_DEMAND_NOT_CAUSAL_ATTRIBUTION'
            result['gross_extra_bytes']=sum(r['extra_bytes'] for r in result['requests'])
            result['saved_bytes']=sum(r['bytes'] for queue in unmatched.values() for r in queue)
            result['traffic_delta_vs_on_demand_bytes']=result['traffic_bytes']-baseline
            if result['gross_extra_bytes']-result['saved_bytes']!=result['traffic_delta_vs_on_demand_bytes']:
                raise ValueError('matched traffic accounting mismatch')
            result['extra_bytes_vs_on_demand']=max(0,result['traffic_bytes']-baseline)
            result['unused_prefetch_bytes']=sum(r['bytes'] for r in result['requests'] if r['classification']=='useless')
            if route_horizon:
                result.pop('unused_prefetch_bytes',None)
                counts={label:sum(r['bytes'] for r in result['requests'] if r['classification']==label)
                        for label in ('useful','late','useless_prefix','censored_terminal','censored_horizon')}
                denominator=results['on_demand']['prefix_ready_miss_bytes']
                result.update(prefix_timely_prefetch_bytes=counts['useful'],prefix_late_consumed_bytes=counts['late'],
                    prefix_evicted_unconsumed_bytes=counts['useless_prefix'],
                    terminal_censored_prefetch_bytes=counts['censored_terminal'],
                    horizon_censored_prefetch_bytes=counts['censored_horizon'],
                    timely_denominator_on_demand_prefix_miss_bytes=denominator,
                    timely_coverage=None if denominator==0 else counts['useful']/denominator,
                    timely_scope='MODEL_PREFIX_ROUTE_VISIBLE_DEADLINE_NOT_OBSERVED_GPU_CONSUMPTION',
                    extra_traffic_ratio=None if baseline==0 else (result['traffic_bytes']-baseline)/baseline,
                    traffic_scope='ISSUED_BEFORE_TERMINAL_ROUTE_VISIBILITY_NO_TERMINAL_DEMAND_SERVICE')
            path=output/policy/'raw.json'
            atomic_json(path,result)
            manifest['policies'][policy].update(raw_sha256=sha256(path),
                transcript_sha256=sha256(path.with_name('service-transcript.jsonl')),
                stderr_sha256=sha256(path.with_name('service-stderr.log')))
        if identity(binary)!=binary_identity:
            raise ValueError('native binary changed during replay')
        for item in manifest['inputs'].values():
            if sha256(output/item['path'])!=item['sha256']:
                raise ValueError('frozen input changed during replay')
        if route_horizon:
            for item in manifest['horizon_evidence'].values():
                if sha256(output/item['path'])!=item['sha256']:
                    raise ValueError('frozen horizon evidence changed during replay')
            for name,digest in manifest['tools'].items():
                if sha256(Path(__file__).with_name(name))!=digest:
                    raise ValueError('replay source changed during execution')
            current=git_snapshot(ROOT)
            if any(current[k]!=manifest['git'][k] for k in ('git_sha','branch','dirty_patch_sha256')):
                raise ValueError('source HEAD/branch/tracked patch changed during replay')
            manifest.pop('active_policy',None)
        manifest['validation']='VALIDATED_RAW_PROJECTED_CONTROL'
        atomic_json(output/'manifest.json',manifest)
    except BaseException as error:
        manifest.update(validation='FAILED',error=str(error))
        atomic_json(output/'manifest.json',manifest)
        raise
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in INPUTS:
        if name!='compute':parser.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    timing=parser.add_mutually_exclusive_group(required=True)
    timing.add_argument('--compute',type=Path)
    timing.add_argument('--route-horizon',type=Path)
    parser.add_argument('--hf-metadata-refresh',type=Path)
    parser.add_argument('--cache-sensitivity',choices=('RHO_1_32_EXTRA_DIAGNOSTIC',))
    parser.add_argument('--binary',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--initial-residency',choices=('budget','cold'),default='budget')
    parser.add_argument('--parallel-units',type=int)
    parser.add_argument('--timeout',type=float,default=30)
    args=parser.parse_args()
    try:
        result=run(args)
    except (OSError,ValueError,KeyError,TypeError,TimeoutError) as error:
        parser.exit(2,f'run_prefetch: {error}\n')
    print(json.dumps(dict(output=str(args.out),provenance=result['provenance'],validation=result['validation'])))


if __name__=='__main__':
    main()

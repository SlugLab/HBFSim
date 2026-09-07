"""Private composition of one request in an already prepared owned HF process.

There is deliberately no CLI or process launcher. A later parent must establish
resource ownership, controlled imports and current input checks. This body saves
provisional raw/trace/cleanup evidence; it never publishes capture-origin gold.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
for directory in (ROOT,ROOT/'adapters/vllm_capacity'):
    if str(directory) not in sys.path:sys.path.insert(0,str(directory))
import hf_routing_worker as protocol_api
import hf_moe_tuning as tuning_api
import hf_runtime_sources
from evaluation_inventory import _unpack
from run_manifest import process_identity
from verify_hf_metadata import canonical,digest,strict_object


_DEPENDENCIES={'LLM','SamplingParams','RequestOutputKind','capture_module','numpy',
    'OwnedRouteMemory','observe_imports','observe_runtime','retain_tuning_runtime',
    'observe_loaded_tuning','Collector'}
_PLAN_KEYS={'metadata_snapshot','runtime_snapshot','tuning_snapshot','device_name_declared','work_dir','gpu_uuid',
    'device_capability','prompt_token_ids','run_id','git_commit','environment_fingerprint'}
_MULTI_PLAN_KEYS=_PLAN_KEYS|{'prompt_members','routing_capture'}


def _loaded_dependencies(runtime_snapshot, capture_cuda_route_events=False):
    """Resolve fixed already-imported classes, not a configured runtime factory."""
    from hf_owned_routes import OwnedRouteMemory
    from hf_moe_tuning_runtime import observe_loaded_tuning,retain_tuning_runtime
    from hf_runtime_contract import observe_runtime
    from hf_runtime_imports import observe_runtime_imports
    from trace_collector import JsonlTraceCollector
    source=hf_runtime_sources.validate_runtime_sources(runtime_snapshot)
    def loaded(name):
        module=sys.modules.get(name)
        if module is None:raise ValueError('required controlled import missing: '+name)
        relative=name.replace('.','/')+'.py'
        key='site-packages/'+relative
        if key not in source['artifacts']:
            relative=name.replace('.','/')+'/__init__.py';key='site-packages/'+relative
        expected=str(Path(source['source_root'])/relative)
        spec=vars(module).get('__spec__')
        if key not in source['artifacts'] or vars(module).get('__file__') != expected or \
           spec is None or getattr(spec,'origin',None) != expected or getattr(spec,'_initializing',False):
            raise ValueError('loaded worker API origin mismatch: '+name)
        return module
    llm=loaded('vllm.entrypoints.llm');sampling=loaded('vllm.sampling_params')
    extra={}
    if capture_cuda_route_events:
        cuda=loaded('torch.cuda');streams=loaded('torch.cuda.streams')
        if not inspect.isclass(vars(cuda).get('Event')) or vars(cuda).get('Event') is not vars(streams).get('Event'):
            raise ValueError('CUDA Event API origin differs from frozen streams source')
        extra['cuda']=cuda
    return dict(LLM=vars(llm)['LLM'],SamplingParams=vars(sampling)['SamplingParams'],
        RequestOutputKind=vars(sampling)['RequestOutputKind'],
        capture_module=loaded('vllm.model_executor.layers.fused_moe.routed_experts_capturer'),
        numpy=loaded('numpy'),OwnedRouteMemory=OwnedRouteMemory,
        observe_imports=observe_runtime_imports,observe_runtime=observe_runtime,
        retain_tuning_runtime=retain_tuning_runtime,observe_loaded_tuning=observe_loaded_tuning,
        Collector=JsonlTraceCollector,**extra)


def run_loaded_arm(plan,arm,out,*,_test_dependencies=None):
    """Construct/generate/clean once, returning only provisional worker status.

    Explicit dependencies are accessible to CPU tests only through this private
    Python API and force MOCK. The eventual real CLI cannot expose them. Input
    snapshots are frozen validation inputs; original-source and GPU ownership
    checks belong to the later parent and controlled-import entrypoint.
    """
    if type(plan) is not dict or set(plan) not in (_PLAN_KEYS,_PLAN_KEYS|{'capture_cuda_route_events'},
            _MULTI_PLAN_KEYS,_MULTI_PLAN_KEYS|{'capture_cuda_route_events'}) or arm not in ('native','capture','repeat'):
        raise ValueError('invalid loaded-arm plan or arm')
    multi=set(plan) in (_MULTI_PLAN_KEYS,_MULTI_PLAN_KEYS|{'capture_cuda_route_events'})
    timing=plan.get('capture_cuda_route_events',False)
    if type(timing) is not bool or (timing and arm=='native') or (multi and (arm!='capture' or timing)):
        raise ValueError('invalid CUDA route-event plan flag')
    receipt,artifacts,donor,_=_unpack(plan['metadata_snapshot'])
    source=hf_runtime_sources.validate_runtime_sources(plan['runtime_snapshot'])
    if timing and _test_dependencies is None and source.get('cuda_event_extension')!='ROUTE_EVENTS_V1':
        raise ValueError('CUDA route events require their frozen runtime source extension')
    tuning_input=tuning_api.validate_tuning_inputs(plan['tuning_snapshot'],plan['metadata_snapshot'],
        plan['runtime_snapshot'],plan['device_name_declared'])
    config=strict_object(artifacts['metadata/config.json'])
    if multi:
        control=protocol_api.make_capture_set_protocol(
            receipt,config,plan['prompt_members'],plan['routing_capture'])
        member_controls=[protocol_api.make_protocol(receipt,config,prompt)
                         for prompt in control['prompt_members']]
    else:
        control=protocol_api.make_protocol(receipt,config,plan['prompt_token_ids'])
        member_controls=None
    test_only=_test_dependencies is not None or source['test_only'] or receipt['evidence']=='TEST_ONLY' or tuning_input['test_only']
    if test_only:
        control.update(source_kind='TEST_ONLY',provenance='MOCK')
        if member_controls is not None:
            for member_control in member_controls:
                member_control.update(source_kind='TEST_ONLY',provenance='MOCK')
    work=Path(plan['work_dir']).absolute();out=Path(out).absolute()
    protocol_api.make_environment(work,plan['gpu_uuid'],{})
    if out.resolve()!=out or not out.is_relative_to(ROOT/'results/gold') or \
       out==ROOT/'results/gold' or not out.parent.is_dir() or \
       out.is_relative_to(work) or work.is_relative_to(out) or \
       out.is_relative_to(plan['metadata_snapshot'].bundle):
        raise ValueError('arm output must be a fresh separate private gold directory')
    if not (work/'tmp').is_dir():raise ValueError('parent has not prepared private temporary directory')
    for key,length in (('git_commit',40),('environment_fingerprint',64)):
        if type(plan[key]) is not str or re.fullmatch('[0-9a-f]{'+str(length)+'}',plan[key]) is None:
            raise ValueError('invalid arm binding: '+key)
    if type(plan['run_id']) is not str or not 0<len(plan['run_id'])<=128:
        raise ValueError('invalid run identity')
    if _test_dependencies is not None and (type(_test_dependencies) is not dict or set(_test_dependencies)!=(_DEPENDENCIES|({'cuda'} if timing else set()))):
        raise ValueError('invalid test dependency set')
    out.mkdir()
    directory=os.open(out,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    original=os.fstat(directory)
    # Collector's unchanged path API can address this retained owned directory
    # through procfs. A parent-name replacement cannot redirect our file writes.
    owned=Path('/proc/self/fd')/str(directory)
    saved_hashes={}
    def write(name,raw):
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        saved_hashes[name]=digest(raw)
    def document(name,value):write(name,canonical(value))
    result=dict(schema_version=1,arm=arm,status='FAILED',provenance='MOCK' if test_only else 'UNVALIDATED_ROUTING_CAPTURE',
        test_only=bool(test_only),scientific_validation_passed=False,process_exit_required=True,
        worker_identity=process_identity(os.getpid()),primary_error=None,cleanup_errors=[],
        compatibility_restored=True,engine_cleanup_available=False)
    binding=ownership=llm=collector=None;deps=None;construction_started=False;raw_saved=False
    cap_module=cls=None;baseline_empty=False;original_descriptor=None
    client=capturer=reader=None;route_owners_observed=False
    route_events=None
    capture_enabled=arm!='native'
    def error(label,exception):return dict(stage=label,type=type(exception).__name__,message=str(exception))
    stage='input-binding'
    try:
        document('input-binding.json',dict(arm=arm,checkpoint=receipt['checkpoint'],
            metadata_receipt_sha256=digest(plan['metadata_snapshot'].receipt_bytes),
            metadata_complete_sha256=digest(plan['metadata_snapshot'].complete_bytes),
            metadata_identity_sha256=receipt['metadata_identity_sha256'],
            observation_identity_sha256=receipt['observation_identity_sha256'],
            donor_sha256=digest(artifacts['donor.json']),model_fingerprint=donor['ModelFingerprint'],
            runtime_source_manifest_sha256=digest(plan['runtime_snapshot'].manifest_bytes),
            selected_tuning_manifest_sha256=digest(plan['tuning_snapshot'].manifest_bytes),
            selected_tuning_input_report_sha256=digest(canonical(tuning_input)),
            device_name_declared=plan['device_name_declared'],
            protocol_sha256=digest(canonical(control)),gpu_uuid=plan['gpu_uuid'],work_dir=str(work),
            run_id=plan['run_id'],git_commit=plan['git_commit'],environment_fingerprint=plan['environment_fingerprint'],
            provenance=result['provenance'],test_only=bool(test_only),scientific_validation_passed=False))
        document('protocol.json',control);write('frozen-donor.json',artifacts['donor.json'])
        deps=_loaded_dependencies(plan['runtime_snapshot'],timing) if _test_dependencies is None else dict(_test_dependencies)
        stage='import-observation'
        imported=deps['observe_imports'](plan['runtime_snapshot'],work,plan['gpu_uuid'],plan['device_capability'])
        document('runtime-imports.json',imported)
        stage='tuning-retention'
        retained=deps['retain_tuning_runtime'](plan['metadata_snapshot'],plan['runtime_snapshot'],
            plan['tuning_snapshot'],plan['device_name_declared'],work,plan['gpu_uuid'])
        cap_module=deps['capture_module'];cls=cap_module.RoutedExpertsCapturer
        original_descriptor=inspect.getattr_static(cls,'get_instance')
        if cap_module._global_experts_capturer is not None or cap_module._global_experts_reader is not None:
            raise ValueError('fresh owned arm already has route singleton state')
        baseline_empty=True
        stage='ownership-entry'
        ownership=deps['OwnedRouteMemory'](cap_module,work/'tmp');ownership.__enter__()
        if capture_enabled:
            from routed_capture_compat import install_vllm_routed_experts_deferred_binding
            binding=install_vllm_routed_experts_deferred_binding(cls)
        stage='construction';construction_started=True
        llm=deps['LLM'].__new__(deps['LLM'])
        deps['LLM'].__init__(llm,**protocol_api.llm_arguments(receipt['checkpoint'],capture_enabled))
        stage='runtime-observation'
        observed=deps['observe_runtime'](llm,receipt['checkpoint'],capture_enabled,control,config)
        document('runtime-configuration.json',observed)
        stage='tuning-observation'
        tuning_observed=deps['observe_loaded_tuning'](retained,llm,prior_runtime_observation=observed)
        document('runtime-tuning.json',tuning_observed)
        client=llm.llm_engine.engine_core;cfg=llm.llm_engine.vllm_config
        capturer=cap_module._global_experts_capturer;reader=cap_module._global_experts_reader
        route_owners_observed=True
        scheduler_reader=vars(client.engine_core.scheduler).get('routed_experts_reader')
        if capture_enabled:
            if type(capturer) is not cls or type(reader) is not cap_module.RoutedExpertsReader or \
               scheduler_reader is not reader or len(ownership.records)!=2 or \
               [r['create'] for r in ownership.records]!=[True,False] or any(r['complete'] is not True for r in ownership.records):
                raise ValueError('constructed route singleton/reader ownership mismatch')
            ownership.reconcile(cfg.instance_id,cfg.parallel_config.data_parallel_rank)
        elif capturer is not None or reader is not None or scheduler_reader is not None or ownership.records:
            raise ValueError('native arm unexpectedly owns route buffers')
        if timing:
            stage='route-event-install'
            from hf_route_cuda_events import RouteCudaEvents
            route_events=RouteCudaEvents(capturer,reader,cuda=deps['cuda'],numpy=deps['numpy'],
                control=control,gpu_uuid=plan['gpu_uuid'],worker_identity=result['worker_identity'],test_only=bool(test_only))
            route_events.start()
        sampling=deps['SamplingParams'](**protocol_api.sampling_arguments(deps['RequestOutputKind'].FINAL_ONLY))
        if capture_enabled:
            from model_inventory import ModelInventory
            from trace_collector import TraceRequest
            inventory=ModelInventory(owned/'frozen-donor.json')
        else:
            TraceRequest=None;inventory=None
        def emit_one(member_control,ordinal,prefix):
            nonlocal collector,stage,raw_saved
            stage_name='member-'+str(ordinal).zfill(4)
            returned=llm.generate([dict(prompt_token_ids=list(member_control['prompt_token_ids']))],sampling,use_tqdm=False)
            stage='serialization-'+stage_name if multi else 'serialization'
            raw,array=protocol_api.serialize_return(returned,member_control,capture_enabled=capture_enabled)
            return_name=prefix+'raw-return.json';document(return_name,raw)
            routes_name=prefix+'raw-routes.npy'
            if array is not None:
                fd=os.open(routes_name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
                with os.fdopen(fd,'wb') as stream:
                    deps['numpy'].save(stream,array,allow_pickle=False);stream.flush();os.fsync(stream.fileno())
                saved_hashes[routes_name]=digest((owned/routes_name).read_bytes())
            raw_saved=True
            if timing:
                stage='route-event-finalize'
                route_events.finish(array)
            summary=None
            if capture_enabled:
                stage='trace-materialization-'+stage_name if multi else 'trace-materialization'
                routing_name=prefix+'routing.jsonl'
                collector=deps['Collector'](owned/routing_name,inventory,
                    plan['run_id']+'-'+stage_name if multi else plan['run_id'],
                    plan['environment_fingerprint'],plan['git_commit'],test_only=bool(test_only))
                collector.emit_request(TraceRequest(raw['request_id'],ordinal,ordinal,32,8),array)
                collector.close();summary=collector.summary();collector=None
                counts=member_control['expected_counts']
                expected=dict(event_count=counts['event_count'],expert_access_count=counts['expert_access_count'],tensor_access_count=counts['tensor_access_count'])
                if any(summary.get(k)!=v for k,v in expected.items()) or \
                   summary['per_phase_event_count']!={'prefill':counts['prefill_event_count'],'decode':counts['decode_event_count']} or \
                   summary['status']!='CAPTURED_UNVALIDATED' or summary['scientific_validation_passed'] is not False:
                    raise ValueError('collector result differs from protocol-derived counts')
                saved_hashes[routing_name]=digest((owned/routing_name).read_bytes())
                document(prefix+'trace-summary.json',summary)
            return raw,array,summary
        if multi:
            members=[]
            for ordinal,member_control in enumerate(member_controls):
                stage='generation-member-'+str(ordinal).zfill(4)
                prefix='member-'+str(ordinal).zfill(4)+'-'
                raw,array,summary=emit_one(member_control,ordinal,prefix)
                if cap_module._global_experts_capturer is not capturer or \
                   cap_module._global_experts_reader is not reader or \
                   vars(client.engine_core.scheduler).get('routed_experts_reader') is not reader:
                    raise ValueError('route ownership changed between capture members')
                members.append(dict(ordinal=ordinal,prompt_token_ids=raw['prompt_token_ids'],
                    request_id=raw['request_id'],raw_return_sha256=saved_hashes[prefix+'raw-return.json'],
                    raw_routes_sha256=saved_hashes[prefix+'raw-routes.npy'],
                    routing_path=str(out/(prefix+'routing.jsonl')),
                    routing_sha256=saved_hashes[prefix+'routing.jsonl'],
                    trace_summary_sha256=saved_hashes[prefix+'trace-summary.json']))
            indexes=[]
            for wave in range(2):
                name='members-index-wave-'+str(wave)+'.json'
                selected=members[wave*8:(wave+1)*8]
                index=dict(schema_version=1,source_kind='CAPTURED_ROUTE',
                    inventory_sha256=summary['inventory_sha256'],
                    members=[dict(member_id='member-'+str(row['ordinal']).zfill(4),
                                  path=row['routing_path'],sha256=row['routing_sha256'])
                             for row in selected])
                document(name,index)
                indexes.append(dict(wave_index=wave,path=str(out/name),sha256=saved_hashes[name]))
            document('member-manifest.json',dict(schema_version=1,status='CAPTURED_16_MEMBERS_UNVALIDATED',
                cell_id=control['cell_id'],members=members,member_indexes=indexes,
                scientific_validation_passed=False))
            composition=protocol_api.make_trace_composition(control,members,indexes)
            document('trace-composition.json',composition)
            result['routing_capture']=dict(cell_id=control['cell_id'],member_count=len(members),
                member_manifest_sha256=saved_hashes['member-manifest.json'],
                trace_composition_sha256=saved_hashes['trace-composition.json'],
                status=composition['status'])
            raw_saved=len(members)==control['member_count']
        else:
            stage='generation'
            raw,array,summary=emit_one(control,0,'')
            raw_saved=True
    except BaseException as exception:
        result['primary_error']=error(stage,exception)
    finally:
        if timing:
            # Restore the three instance methods before any owner/engine cleanup.
            if route_events is not None:
                try:route_events.close()
                except BaseException as exception:result['cleanup_errors'].append(error('route-event-restore',exception))
                try:
                    from hf_route_cuda_events import MAX_ARTIFACT_BYTES
                    event_document=route_events.snapshot(bindings=dict(
                        arm=arm,run_id=plan['run_id'],
                        input_binding_sha256=saved_hashes.get('input-binding.json'),
                        protocol_sha256=saved_hashes.get('protocol.json'),
                        raw_return_sha256=saved_hashes.get('raw-return.json'),
                        raw_routes_sha256=saved_hashes.get('raw-routes.npy')),
                        primary_error=result['primary_error'])
                    event_raw=canonical(event_document)
                    if len(event_raw)>MAX_ARTIFACT_BYTES:raise ValueError('route event artifact exceeds fixed byte bound')
                    write('route-device-events.json',event_raw)
                    result['route_cuda_events']=dict(enabled=True,status=event_document['status'],
                        artifact='route-device-events.json',sha256=digest(event_raw),counts=event_document['counts'])
                    if event_document['status']!='UNVALIDATED_ROUTE_INTERVAL_CAPTURE':
                        result['cleanup_errors'].append(dict(stage='route-event-result',type='IncompleteRouteEvents',message='route event acquisition/restore was not complete'))
                except BaseException as exception:result['cleanup_errors'].append(error('route-event-persistence',exception))
            else:
                result['route_cuda_events']=dict(enabled=True,status='NOT_INSTALLED')
        if collector is not None:
            try:collector.close()
            except BaseException as exception:result['cleanup_errors'].append(error('collector-close',exception))
        try:
            if llm is not None:
                engine=vars(llm).get('llm_engine')
                reachable=vars(engine).get('engine_core') if engine is not None else None
                if client is not None and reachable is not client:raise ValueError('constructed engine client binding changed')
                client=reachable
        except BaseException as exception:result['cleanup_errors'].append(error('client-observation',exception))
        result['engine_cleanup_available']=client is not None
        result['engine_cleanup_boundary']='REACHABLE_CLIENT' if client is not None else 'PARTIAL_CONSTRUCTION_REQUIRES_OWNED_PROCESS_EXIT' if construction_started else 'NOT_CONSTRUCTED'
        if ownership is not None:
            owners={'capturer':capturer,'reader':reader}
            if baseline_empty:
                # A missing/replaced singleton must not skip the other owners,
                # saved handles, engine shutdown, or module restoration.
                for name in owners:
                    try:
                        current_owner=vars(cap_module)['_global_experts_'+name]
                        if route_owners_observed and current_owner is not owners[name]:raise ValueError('constructed route singleton binding changed')
                        owners[name]=current_owner
                    except BaseException as exception:result['cleanup_errors'].append(error(name+'-observation',exception))
            try:
                cleaned=ownership.finish(capturer=owners['capturer'],reader=owners['reader'],client=client)
                result['owned_memory_cleanup']=cleaned
                if cleaned['status']!='OWNED_MEMORY_CLOSED':result['cleanup_errors'].append(dict(stage='owned-cleanup',type='FailedCleanup',message='owned cleanup report is not successful'))
            except BaseException as exception:result['cleanup_errors'].append(error('owned-cleanup',exception))
        if binding is not None:
            try:binding.close();result['compatibility']=binding.summary()
            except BaseException as exception:result['cleanup_errors'].append(error('binding-close',exception))
        if cap_module is not None and original_descriptor is not None:
            result['compatibility_restored']=False
            try:
                result['compatibility_restored']=vars(cap_module).get('RoutedExpertsCapturer') is cls and inspect.getattr_static(cls,'get_instance') is original_descriptor and '_hbfsim_deferred_capture_binding' not in vars(cls)
                if not result['compatibility_restored']:raise ValueError('original capturer class/getter binding was not restored')
            except BaseException as exception:result['cleanup_errors'].append(error('binding-restoration',exception))
        try:
            current=out.lstat()
            if out.resolve()!=out or (current.st_dev,current.st_ino)!=(original.st_dev,original.st_ino):raise ValueError('arm output directory binding changed')
        except BaseException as exception:result['cleanup_errors'].append(error('output-binding',exception))
        result['raw_return_saved']=raw_saved
        if result['primary_error'] is None and not result['cleanup_errors'] and raw_saved:
            result['status']='ARM_RETURNED_UNVALIDATED'
        try:document('worker-status.json',result);os.fsync(directory)
        except BaseException as exception:
            result['status']='FAILED'
            result['cleanup_errors'].append(error('status-persistence',exception))
        finally:
            try:os.close(directory)
            except BaseException as exception:
                result['status']='FAILED'
                result['cleanup_errors'].append(error('output-close',exception))
    return result

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


def _loaded_dependencies(runtime_snapshot):
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
    return dict(LLM=vars(llm)['LLM'],SamplingParams=vars(sampling)['SamplingParams'],
        RequestOutputKind=vars(sampling)['RequestOutputKind'],
        capture_module=loaded('vllm.model_executor.layers.fused_moe.routed_experts_capturer'),
        numpy=loaded('numpy'),OwnedRouteMemory=OwnedRouteMemory,
        observe_imports=observe_runtime_imports,observe_runtime=observe_runtime,
        retain_tuning_runtime=retain_tuning_runtime,observe_loaded_tuning=observe_loaded_tuning,
        Collector=JsonlTraceCollector)


def run_loaded_arm(plan,arm,out,*,_test_dependencies=None):
    """Construct/generate/clean once, returning only provisional worker status.

    Explicit dependencies are accessible to CPU tests only through this private
    Python API and force MOCK. The eventual real CLI cannot expose them. Input
    snapshots are frozen validation inputs; original-source and GPU ownership
    checks belong to the later parent and controlled-import entrypoint.
    """
    if type(plan) is not dict or set(plan)!=_PLAN_KEYS or arm not in ('native','capture','repeat'):
        raise ValueError('invalid loaded-arm plan or arm')
    receipt,artifacts,donor,_=_unpack(plan['metadata_snapshot'])
    source=hf_runtime_sources.validate_runtime_sources(plan['runtime_snapshot'])
    tuning_input=tuning_api.validate_tuning_inputs(plan['tuning_snapshot'],plan['metadata_snapshot'],
        plan['runtime_snapshot'],plan['device_name_declared'])
    config=strict_object(artifacts['metadata/config.json'])
    control=protocol_api.make_protocol(receipt,config,plan['prompt_token_ids'])
    test_only=_test_dependencies is not None or source['test_only'] or receipt['evidence']=='TEST_ONLY' or tuning_input['test_only']
    if test_only:control.update(source_kind='TEST_ONLY',provenance='MOCK')
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
    if _test_dependencies is not None and (type(_test_dependencies) is not dict or set(_test_dependencies)!=_DEPENDENCIES):
        raise ValueError('invalid test dependency set')
    out.mkdir()
    directory=os.open(out,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    original=os.fstat(directory)
    # Collector's unchanged path API can address this retained owned directory
    # through procfs. A parent-name replacement cannot redirect our file writes.
    owned=Path('/proc/self/fd')/str(directory)
    def write(name,raw):
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    def document(name,value):write(name,canonical(value))
    result=dict(schema_version=1,arm=arm,status='FAILED',provenance='MOCK' if test_only else 'UNVALIDATED_ROUTING_CAPTURE',
        test_only=bool(test_only),scientific_validation_passed=False,process_exit_required=True,
        worker_identity=process_identity(os.getpid()),primary_error=None,cleanup_errors=[],
        compatibility_restored=True,engine_cleanup_available=False)
    binding=ownership=llm=collector=None;deps=None;construction_started=False;raw_saved=False
    cap_module=cls=None;baseline_empty=False;original_descriptor=None
    client=capturer=reader=None;route_owners_observed=False
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
        deps=_loaded_dependencies(plan['runtime_snapshot']) if _test_dependencies is None else dict(_test_dependencies)
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
        sampling=deps['SamplingParams'](**protocol_api.sampling_arguments(deps['RequestOutputKind'].FINAL_ONLY))
        stage='generation'
        returned=llm.generate([dict(prompt_token_ids=list(control['prompt_token_ids']))],sampling,use_tqdm=False)
        stage='serialization'
        raw,array=protocol_api.serialize_return(returned,control,capture_enabled=capture_enabled)
        # Freeze the private copy before any collector or teardown callback.
        document('raw-return.json',raw)
        if array is not None:
            fd=os.open('raw-routes.npy',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
            with os.fdopen(fd,'wb') as stream:
                deps['numpy'].save(stream,array,allow_pickle=False);stream.flush();os.fsync(stream.fileno())
        raw_saved=True
        if capture_enabled:
            stage='trace-materialization'
            from model_inventory import ModelInventory
            from trace_collector import TraceRequest
            inventory=ModelInventory(owned/'frozen-donor.json')
            collector=deps['Collector'](owned/'routing.jsonl',inventory,plan['run_id'],plan['environment_fingerprint'],plan['git_commit'],test_only=bool(test_only))
            collector.emit_request(TraceRequest(raw['request_id'],0,0,32,8),array)
            collector.close();summary=collector.summary()
            counts=control['expected_counts']
            expected=dict(event_count=counts['event_count'],expert_access_count=counts['expert_access_count'],tensor_access_count=counts['tensor_access_count'])
            if any(summary.get(k)!=v for k,v in expected.items()) or \
               summary['per_phase_event_count']!={'prefill':counts['prefill_event_count'],'decode':counts['decode_event_count']} or \
               summary['status']!='CAPTURED_UNVALIDATED' or summary['scientific_validation_passed'] is not False:
                raise ValueError('collector result differs from protocol-derived counts')
            document('trace-summary.json',summary)
    except BaseException as exception:
        result['primary_error']=error(stage,exception)
    finally:
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

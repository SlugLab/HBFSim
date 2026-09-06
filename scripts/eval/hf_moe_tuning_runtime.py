"""Private passive MoE tuning-state observation around guarded construction.

Only already-loaded modules and tensor metadata are read. No selector, cache,
platform, inference import, model method or payload API is called. The caller
must first run the separate full runtime contract observer; its primitive report
is reconciled here but is not authenticated as proof that code executed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import functools
import math
import os
from pathlib import Path
import re
import sys
from types import FunctionType, MethodType, ModuleType

import hf_moe_tuning as tuning
import hf_runtime_sources as sources
from evaluation_inventory import _unpack
from verify_hf_metadata import canonical, digest, strict_object
from hf_runtime_imports import make_environment

BASE='vllm.model_executor.layers.fused_moe'
CLASS_MODULES={
    'LLM':'vllm.entrypoints.llm','LLMEngine':'vllm.v1.engine.llm_engine',
    'InprocClient':'vllm.v1.engine.core_client','EngineCore':'vllm.v1.engine.core',
    'UniProcExecutor':'vllm.v1.executor.uniproc_executor','Worker':'vllm.v1.worker.gpu_worker',
    'GPUModelRunner':'vllm.v1.worker.gpu_model_runner','Qwen3MoeForCausalLM':'vllm.model_executor.models.qwen3_moe',
    'SharedFusedMoE':BASE+'.shared_fused_moe','UnquantizedFusedMoEMethod':BASE+'.unquantized_fused_moe_method',
    'FusedMoEModularKernel':BASE+'.modular_kernel','MoEPrepareAndFinalizeNoEP':BASE+'.prepare_finalize',
    'TritonExperts':BASE+'.fused_moe','UnquantizedMoeBackend':BASE+'.oracle.unquantized',
    'FusedMoEConfig':BASE+'.config','FusedMoEParallelConfig':BASE+'.config',
    'FusedMoEQuantConfig':BASE+'.config','FusedMoEQuantDesc':BASE+'.config'}
MODULES=tuple(sorted(set(CLASS_MODULES.values())|{BASE,'torch','vllm.envs','vllm.model_executor.layers.batch_invariant'}))
DESC_FIELDS=('dtype','shape','scale','alpha_or_gscale','zp','bias')
GENERATED_ENV='VLLM_OBJECT_STORAGE_SHM_BUFFER_NAME'


def _require(condition,message):
    if not condition:raise ValueError('HF tuning runtime: '+message)


def _raw(obj,name):
    """Read stored state, including nn.Module registrations, without properties."""
    state=vars(obj)
    if name in state:return state[name]
    for registration in ('_modules','_parameters'):
        table=state.get(registration)
        if type(table) is dict and name in table:return table[name]
    raise ValueError('HF tuning runtime: missing stored field '+name)


def _primitive(value):
    remaining=[100000]
    def walk(item,depth):
        remaining[0]-=1
        _require(remaining[0]>=0 and depth<=16,'prior observation exceeds finite bounds')
        if type(item) in (str,int,float,bool,type(None)):
            _require(type(item) is not str or len(item)<=16384,'oversized observation string')
            _require(type(item) is not float or math.isfinite(item),'nonfinite observation')
            return
        _require(type(item) in (dict,list),'prior observation must contain primitives only')
        if type(item) is dict:
            _require(all(type(k) is str for k in item),'invalid observation keys')
            for k,v in item.items():walk(k,depth+1);walk(v,depth+1)
        else:
            for v in item:walk(v,depth+1)
    walk(value,0);raw=canonical(value);_require(len(raw)<=4<<20,'oversized prior observation')
    return raw


def _origins(modules,report):
    result={}
    for name in MODULES:
        module=modules.get(name);_require(type(module) is ModuleType,'required loaded module '+name)
        key='site-packages/'+name.replace('.','/')+'.py'
        if key not in report['artifacts']:key='site-packages/'+name.replace('.','/')+'/__init__.py'
        _require(key in report['artifacts'],'module outside frozen source set')
        path=str(Path(report['source_root'])/key.split('/',1)[1]);spec=vars(module).get('__spec__')
        _require(vars(module).get('__file__')==path and spec is not None and
                 vars(spec).get('origin')==path and not vars(spec).get('_initializing',False),'module origin/initialization '+name)
        result[name]=dict(path=path,source_sha256=report['artifacts'][key])
    return result


def _function(function,module):
    _require(type(function) is FunctionType and function.__globals__ is vars(module) and
             function.__module__==module.__name__ and function.__code__.co_filename==vars(module)['__file__'],
             'function globals/source binding')
    return function,function.__code__


def _environment(environment,work,gpu,*,postconstruction=False):
    _require(len(environment)<=512,'environment exceeds finite bounds');env=dict(environment)
    _require(all(type(k) is str and type(v) is str and len(v)<=16384 for k,v in env.items()),'environment fields')
    _require(not any(k in env for k in ('VLLM_TUNED_CONFIG_FOLDER','VLLM_BATCH_INVARIANT')),'tuning/batch override')
    generated=env.pop(GENERATED_ENV,None)
    # Frozen envs.py get_env_or_set_default is evaluated by EngineCore's
    # enable_envs_cache. Observe its string only; never allocate/open that SHM.
    _require(generated is None or (postconstruction and
             re.fullmatch(r'VLLM_OBJECT_STORAGE_SHM_BUFFER_[0-9a-f]{32}',generated) is not None),
             'unexpected generated environment state')
    _require(env==make_environment(work,gpu,env),'environment differs from prepared allowlist')
    return env,generated


def _bindings(modules):
    package=modules[BASE];fused=modules[BASE+'.fused_moe'];envs=modules['vllm.envs']
    batch=modules['vllm.model_executor.layers.batch_invariant'];config=modules[BASE+'.config']
    _require(_raw(package,'_config') is None,'package tuning override')
    _require(_raw(batch,'VLLM_BATCH_INVARIANT') is False,'batch-invariant state')
    # The config flag and the installed mode are separate mutable states.
    # Reject an active or partially initialized mode without invoking its APIs.
    _require(_raw(batch,'_batch_invariant_MODE') is False,'batch-invariant runtime mode')
    for name in ('_batch_invariant_LIB','_original_torch_bmm',
                 '_original_fp16_reduction_precision','_original_bf16_reduction_precision',
                 '_original_cublas_workspace_cfg','_original_cublaslt_workspace_size'):
        _require(_raw(batch,name) is None,'batch-invariant runtime state '+name)
    _require(vars(envs).get('VLLM_TUNED_CONFIG_FOLDER') is None,'direct tuning override')
    result={name:_raw(modules[module],name) for name,module in CLASS_MODULES.items()}
    for name,cls in result.items():
        _require(isinstance(cls,type) and cls.__module__==CLASS_MODULES[name] and cls.__qualname__==name,'concrete class binding '+name)
    _require(_raw(package,'TritonExperts') is result['TritonExperts'] and
             _raw(fused,'envs') is envs,'fused package/module alias')
    batch_fn=_raw(batch,'vllm_is_batch_invariant')
    _require(_raw(fused,'vllm_is_batch_invariant') is batch_fn,'batch function alias')
    result['batch_function']=_function(batch_fn,batch)
    result['get_config']=_function(_raw(package,'get_config'),package)
    for name in ('get_config_file_name','get_default_config','try_get_optimal_moe_config'):
        result[name]=_function(_raw(fused,name),fused)
    wrapper=_raw(fused,'get_moe_configs')
    _require(type(wrapper) is functools._lru_cache_wrapper,'selector must retain native LRU wrapper')
    wrapped=_raw(wrapper,'__wrapped__');result['selector']=(wrapper,*_function(wrapped,fused))
    # try_get_optimal imports get_config inside its body. Its actual globals
    # bind these selectors; there is intentionally no global get_config check.
    result['apply']=_function(vars(result['TritonExperts'])['apply'],fused)
    quant=_raw(config,'FUSED_MOE_UNQUANTIZED_CONFIG')
    _require(type(quant) is result['FusedMoEQuantConfig'] and
             _raw(fused,'FUSED_MOE_UNQUANTIZED_CONFIG') is quant and
             _raw(modules[BASE+'.unquantized_fused_moe_method'],'FUSED_MOE_UNQUANTIZED_CONFIG') is quant,'unquantized config alias')
    result['quant']=quant
    torch=modules['torch'];nn=modules.get('torch.nn')
    _require(type(nn) is ModuleType and _raw(torch,'nn') is nn,'loaded torch.nn alias')
    parameter=_raw(nn,'Parameter');_require(isinstance(parameter,type),'Parameter class binding')
    result.update(nn=nn,Parameter=parameter,bfloat16=_raw(torch,'bfloat16'))
    return result


def _same_bindings(expected,current):
    _require(expected.keys()==current.keys(),'binding keys changed')
    for name,value in expected.items():
        actual=current[name]
        if type(value) is tuple:
            _require(type(actual) is tuple and len(value)==len(actual) and all(a is b for a,b in zip(value,actual)),'function binding changed '+name)
        else:_require(value is actual,'class/config binding changed '+name)


@dataclass(frozen=True)
class _RetainedTuningRuntime:
    metadata:object
    runtime:object
    tuning:object
    device:str
    work:object
    gpu:str
    modules:object
    environment:object
    module_bindings:dict
    origins:dict
    env:dict
    bindings:dict
    env_function:tuple
    input_report:dict
    hf:dict
    test_only:bool
    post_environment:dict=field(default_factory=dict)


def retain_tuning_runtime(metadata_snapshot,runtime_snapshot,tuning_snapshot,device_name,work,gpu_uuid,*,modules=None,environment=None):
    """Call before construction; this neither imports nor initializes runtime."""
    try:
        report=tuning.validate_tuning_inputs(tuning_snapshot,metadata_snapshot,runtime_snapshot,device_name)
        source=sources.validate_runtime_sources(runtime_snapshot)
        _require(len(source['artifacts'])==133 and source.get('source_extension')=='MOE_TUNING_V1','exact 133-file tuning source contract')
        test_only=report['test_only'] or modules is not None or environment is not None
        modules=sys.modules if modules is None else modules;environment=os.environ if environment is None else environment
        _require(len(modules)<=32768,'module table exceeds finite bounds')
        origins=_origins(modules,source);env,_=_environment(environment,work,gpu_uuid);bindings=_bindings(modules)
        env_function=_function(_raw(modules['vllm.envs'],'__getattr__'),modules['vllm.envs'])
        _,artifacts,_,_=_unpack(metadata_snapshot);hf=strict_object(artifacts['metadata/config.json'])
        _require(type(hf['num_hidden_layers']) is int and 1<=hf['num_hidden_layers']<=1024,'layer bound')
        retained=_RetainedTuningRuntime(metadata_snapshot,runtime_snapshot,tuning_snapshot,device_name,work,gpu_uuid,
            modules,environment,{name:modules[name] for name in MODULES+('torch.nn',)},origins,env,bindings,
            env_function,report,hf,bool(test_only))
        _recheck(retained,preconstruction=True)
        return retained
    except (AttributeError,KeyError,TypeError) as error:
        raise ValueError('HF tuning runtime: missing/incompatible preconstruction state') from error


def _recheck(retained,*,preconstruction=False):
    r=retained
    _require(all(r.modules.get(name) is module for name,module in r.module_bindings.items()),'loaded module replaced')
    report=tuning.validate_tuning_inputs(r.tuning,r.metadata,r.runtime,r.device)
    _require(canonical(report)==canonical(r.input_report),'frozen input binding changed')
    _require(_origins(r.modules,sources.validate_runtime_sources(r.runtime))==r.origins,'module origins changed')
    env,generated=_environment(r.environment,r.work,r.gpu,postconstruction=not preconstruction)
    _require(env==r.env,'prepared environment changed')
    if not preconstruction:
        _require(r.post_environment.setdefault(GENERATED_ENV,generated)==generated,'generated environment changed during/after observation')
    _same_bindings(r.bindings,_bindings(r.modules))
    current=_raw(r.modules['vllm.envs'],'__getattr__');original,code=r.env_function
    _require(original.__code__ is code and original.__globals__ is vars(r.modules['vllm.envs']),'environment getter changed')
    if current is original:return 'RETAINED_FUNCTION'
    _require(not preconstruction and type(current) is functools._lru_cache_wrapper and
             _raw(current,'__wrapped__') is original,'unexpected environment getter transition')
    return 'WRAPPED_RETAINED_FUNCTION'


def _class(obj,name,r):
    _require(type(obj) is r.bindings[name],'unexpected concrete '+name)
    return obj


def _layer_sequence(container,count):
    if type(container) in (list,tuple):layers=list(container)
    else:
        table=_raw(container,'_modules')
        _require(type(table) is dict and list(table)==[str(i) for i in range(count)],'stored ModuleList order')
        layers=list(table.values())
    _require(len(layers)==count,'layer coverage')
    return layers


def _observe_layers(r,llm,prior,*,read_weight_metadata=True):
    refs=[]
    def member(obj,name):
        value=_raw(obj,name);refs.append(value);return value
    current=_class(llm,'LLM',r);refs.append(current)
    for name,cls in (('llm_engine','LLMEngine'),('engine_core','InprocClient'),('engine_core','EngineCore'),
                     ('model_executor','UniProcExecutor')):
        current=_class(member(current,name),cls,r)
    current=member(current,'driver_worker');current=_class(member(current,'worker'),'Worker',r)
    runner=_class(member(current,'model_runner'),'GPUModelRunner',r)
    model=_class(member(runner,'model'),'Qwen3MoeForCausalLM',r)
    layers=_layer_sequence(member(member(model,'model'),'layers'),r.hf['num_hidden_layers'])
    _require(type(prior) is dict and set(prior)=={'schema_version','evidence','scientific_validation_passed','classes','settings','instance_id','layers','kv_layout','sampler'} and
             prior['schema_version']==1 and type(prior['schema_version']) is int and prior['evidence']=='RUNTIME_CONFIGURATION_OBSERVATION' and
             prior['scientific_validation_passed'] is False,'missing separate runtime contract observation')
    for key,cls in (('llm','LLM'),('engine','LLMEngine'),('client','InprocClient'),('core','EngineCore'),
                    ('executor','UniProcExecutor'),('worker','Worker'),('runner','GPUModelRunner'),('model','Qwen3MoeForCausalLM')):
        _require(prior['classes'][key]==CLASS_MODULES[cls]+'.'+cls,'prior runtime class binding')
    geometry=prior['settings']['geometry']
    for key in ('model_type','num_hidden_layers','num_experts','num_experts_per_tok','vocab_size','hidden_size','num_attention_heads','num_key_value_heads','head_dim'):
        _require(type(geometry[key]) is type(r.hf[key]) and geometry[key]==r.hf[key],'prior runtime geometry')
    _require(type(prior['layers']) is list and len(prior['layers'])==len(layers),'prior layer coverage')
    rows=[];E,H,I=r.hf['num_experts'],r.hf['hidden_size'],r.hf['moe_intermediate_size']
    for index,decoder in enumerate(layers):
        refs.append(decoder);moe=_class(member(member(decoder,'mlp'),'experts'),'SharedFusedMoE',r)
        # layer_id is an import-capable inherited property in the pinned source.
        # Its exact stored layer_name already binds this decoder index.
        for name,expected in dict(layer_name=f'model.layers.{index}.mlp.experts',
                                  global_num_experts=E,local_num_experts=E,top_k=r.hf['num_experts_per_tok']).items():
            value=_raw(moe,name);_require(type(value) is type(expected) and value==expected,'actual MoE layer identity')
        method=_class(member(moe,'quant_method'),'UnquantizedFusedMoEMethod',r)
        backend=_raw(r.bindings['UnquantizedMoeBackend'],'TRITON')
        _require(member(method,'unquantized_backend') is backend,'actual backend differs')
        kernel=_class(member(method,'kernel'),'FusedMoEModularKernel',r)
        _class(member(kernel,'prepare_finalize'),'MoEPrepareAndFinalizeNoEP',r)
        impl=_class(member(kernel,'fused_experts'),'TritonExperts',r)
        apply=object.__getattribute__(impl,'apply')
        _require(type(apply) is MethodType and apply.__self__ is impl and apply.__func__ is r.bindings['apply'][0],'actual apply binding')
        mc=_class(member(moe,'moe_config'),'FusedMoEConfig',r)
        _require(member(method,'moe') is mc and member(impl,'moe_config') is mc,'stored MoE config alias')
        for name,expected in dict(num_experts=E,num_local_experts=E,experts_per_token=r.hf['num_experts_per_tok'],hidden_dim=H,
                                  intermediate_size_per_partition=I,has_bias=False,is_act_and_mul=True,is_lora_enabled=False).items():
            value=_raw(mc,name);_require(type(value) is type(expected) and value==expected,'stored MoE geometry '+name)
        _require(member(mc,'in_dtype') is r.bindings['bfloat16'],'stored MoE dtype')
        parallel=_class(member(mc,'moe_parallel_config'),'FusedMoEParallelConfig',r)
        for name,expected in dict(tp_size=1,dp_size=1,pcp_size=1,ep_size=1,tp_rank=0,dp_rank=0,pcp_rank=0,ep_rank=0,use_ep=False).items():
            value=_raw(parallel,name);_require(type(value) is type(expected) and value==expected,'stored parallel geometry')
        quant=member(method,'moe_quant_config') # Never moe.moe_quant_config: it may initialize.
        _require(quant is r.bindings['quant'] and member(impl,'quant_config') is quant,'actual quant config alias')
        for name in ('_a1','_a2','_w1','_w2'):
            desc=_class(member(quant,name),'FusedMoEQuantDesc',r)
            _require(all(_raw(desc,field) is None for field in DESC_FIELDS),'raw quantization descriptor')
        shapes={}
        for name,field,expected in (('w13','w13_weight',[E,2*I,H]),('w2','w2_weight',[E,H,I])):
            weight=member(moe,field);_require(type(weight) is r.bindings['Parameter'],'exact Parameter required')
            if not read_weight_metadata:continue
            shape=tuple(weight.shape);device=weight.device
            device_type,device_index=device.type,device.index
            _require(len(shape)==3 and all(type(n) is int for n in shape) and list(shape)==expected,'weight shape')
            _require(weight.dtype is r.bindings['bfloat16'],'weight dtype')
            _require(type(device_type) is str and device_type=='cuda' and
                     type(device_index) is int and device_index==0,'weight selected CUDA index')
            shapes[name]=list(shape)
        observed=prior['layers'][index];info=observed['moe']
        _require(type(observed['layer']) is int and observed['layer']==index and info['backend']=='TRITON','prior layer/backend')
        for key,cls in (('concrete_class','SharedFusedMoE'),('method','UnquantizedFusedMoEMethod'),('kernel','FusedMoEModularKernel'),
                        ('prepare_finalize','MoEPrepareAndFinalizeNoEP'),('fused_experts','TritonExperts')):
            _require(info[key]==CLASS_MODULES[cls]+'.'+cls,'prior MoE class binding')
        if read_weight_metadata:
            rows.append(dict(layer=index,weight_shapes=shapes,dtype='torch.bfloat16',device_type='cuda',device_index=0,
                             filename_N=shapes['w2'][2],backend='TRITON',raw_quantization='UNQUANTIZED',has_bias=False,is_act_and_mul=True,tp_size=1))
    return rows,refs


def observe_loaded_tuning(retained,llm,*,prior_runtime_observation):
    """Call after the full runtime observer succeeds, before any generation.

    The supplied report is bounded and reconciled, not an authenticated receipt.
    Device name, CUDA identity, private caches and native binaries remain outside
    this passive helper's proof. Explicit fixture injection always yields MOCK.
    """
    _require(type(retained) is _RetainedTuningRuntime,'retained preconstruction state required')
    try:
        prior_bytes=_primitive(prior_runtime_observation);prior=strict_object(prior_bytes)
        _recheck(retained);rows,refs=_observe_layers(retained,llm,prior)
        _recheck(retained);again,other=_observe_layers(retained,llm,prior)
        _require(rows==again and len(refs)==len(other) and all(a is b for a,b in zip(refs,other)),'runtime state changed during observation')
        _recheck(retained)
        # Tensor metadata descriptors can change stored state even on their last
        # read. Close with one bounded pass over raw bindings/config/descriptors,
        # including registered weight identities, without any tensor getters.
        _,final_refs=_observe_layers(retained,llm,prior,read_weight_metadata=False)
        _require(len(refs)==len(final_refs) and all(a is b for a,b in zip(refs,final_refs)),
                 'runtime storage changed after tensor metadata observation')
        env_state=_recheck(retained)
        _require(_primitive(prior_runtime_observation)==prior_bytes,'prior observation mutated')
        r=retained
        return dict(schema_version=1,provenance='MOCK' if r.test_only else 'RUNTIME_TUNING_STATE',test_only=r.test_only,
            source_manifest_sha256=digest(r.runtime.manifest_bytes),metadata_receipt_sha256=digest(r.metadata.receipt_bytes),
            selected_tuning_manifest_sha256=digest(r.tuning.manifest_bytes),prior_runtime_observation_sha256=digest(prior_bytes),
            module_origins=strict_object(canonical(r.origins)),device_name_declared=r.device,
            selection=r.input_report['selection'],selected_path=r.input_report['selected_path'],
            selected_config_sha256=r.input_report['config_sha256'],layers=rows,env_getattr_state=env_state,
            object_storage_shm_name=dict(value=r.post_environment[GENERATED_ENV],authenticated=False,allocated_by_observer=False),
            package_override=None,batch_invariant=False,batch_invariant_runtime_mode=False,private_cache_contents_authenticated=False,
            prior_runtime_observation_authenticated=False,device_identity_authenticated=False,
            all_runtime_binaries_authenticated=False,torch_parameter_registration_source_authenticated=False,
            effective_kernel_configuration_observed=False,scientific_validation_passed=False,
            boundary='Passive bindings and weight metadata only; declared device and prior report are unauthenticated. No selector/cache/platform/GPU/payload API called.')
    except (AttributeError,KeyError,TypeError) as error:
        raise ValueError('HF tuning runtime: missing/incompatible loaded state') from error

"""TEST_ONLY passive modules/Parameter metadata; no inference imports or payload."""
import copy
import functools
from importlib.machinery import ModuleSpec
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS, MethodType, FunctionType
import unittest
from unittest import mock

import hf_moe_tuning_runtime as observer
import hf_moe_tuning as tuning
import hf_runtime_sources as sources
from evaluation_inventory import _unpack
import test_hf_moe_tuning as input_fixtures

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'adapters/vllm_capacity/tests'))
import test_hf_runtime_contract as contract_fixtures
from adapters.vllm_capacity.hf_routing_worker import make_environment

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


class TuningRuntimeTests(unittest.TestCase):
    def setUp(self):
        inputs=input_fixtures.TuningInputTests();inputs.setUp();self.addCleanup(inputs.doCleanups)
        self.inputs=inputs;self.metadata=inputs.metadata;self.runtime=inputs.runtime
        self.tuning=inputs.collect();self.device=inputs.device;self.work=inputs.base/'worker'
        self.gpu='GPU-TEST_ONLY';self.env=make_environment(self.work,self.gpu,{'HOME':'/unused','PATH':'/unused'})
        self.import_environment={'KMP_DUPLICATE_LIB_OK':'True','KMP_INIT_AT_FORK':'FALSE',
                                 'LD_LIBRARY_PATH':'/opt/miniconda3/lib/python3.13/site-packages/cv2/../../lib64:'}
        # Match the fixed runtime union's observed post-import environment.
        self.env.update(self.import_environment)
        fixture=contract_fixtures.RuntimeContractTests();fixture.setUp();self.fixture=fixture;self.llm=fixture.llm
        _,artifacts,_,_=_unpack(self.metadata);hf=json.loads(artifacts['metadata/config.json'])
        fixture.hf={key:hf[key] for key in fixture.hf}
        fixture.protocol.update(layers=hf['num_hidden_layers'],experts=hf['num_experts'],top_k=hf['num_experts_per_tok'],vocab_size=hf['vocab_size'])
        fixture.cfg.model_config.hf_config=NS(**fixture.hf);fixture.cfg.model_config.hf_text_config=NS(**fixture.hf)
        for layer in fixture.layers:
            attn=layer.self_attn.attn;attn.head_size=hf['head_dim']
            moe=layer.mlp.experts;moe.global_num_experts=moe.local_num_experts=hf['num_experts'];moe.top_k=hf['num_experts_per_tok']
        spec=fixture.runner.kv_cache_config.kv_cache_groups[0].kv_cache_spec
        spec.head_size=spec.head_size_v=hf['head_dim'];spec.page_size_bytes=4*16*hf['num_key_value_heads']*hf['head_dim']
        for tensor in fixture.runner.kv_cache_config.kv_cache_tensors:tensor.size=5*spec.page_size_bytes
        self.modules={};report=sources.validate_runtime_sources(self.runtime)
        for name in set(CLASS_MODULES.values())|{BASE,'vllm.envs','torch','vllm.model_executor.layers.batch_invariant'}:
            key='site-packages/'+name.replace('.','/')+'.py'
            if key not in report['artifacts']:key='site-packages/'+name.replace('.','/')+'/__init__.py'
            path=str(inputs.site/key.split('/',1)[1]);module=ModuleType(name)
            module.__file__=path;module.__spec__=ModuleSpec(name,None,origin=path);self.modules[name]=module
        for name,module in CLASS_MODULES.items():
            cls=fixture.types.get(name) or type(name,(),{})
            cls.__module__=module;cls.__qualname__=name;setattr(self.modules[module],name,cls)
        def define(module,code):exec(compile(code,module.__file__,'exec'),vars(module))
        package=self.modules[BASE];fused=self.modules[BASE+'.fused_moe'];batch=self.modules['vllm.model_executor.layers.batch_invariant'];envs=self.modules['vllm.envs']
        define(package,'_config=None\ndef get_config():\n    raise AssertionError("get_config called")\n')
        define(batch,'VLLM_BATCH_INVARIANT=False\ndef vllm_is_batch_invariant():\n    raise AssertionError("batch getter called")\n')
        vars(batch).update(_batch_invariant_MODE=False,_batch_invariant_LIB=None,
            _original_torch_bmm=None,_original_fp16_reduction_precision=None,
            _original_bf16_reduction_precision=None,_original_cublas_workspace_cfg=None,
            _original_cublaslt_workspace_size=None)
        define(envs,'def __getattr__(name):\n    raise AssertionError("env cache/getter called")\n')
        define(fused,'def get_moe_configs(*args):\n    raise AssertionError("selector called")\ndef get_config_file_name(*args):\n    raise AssertionError("filename getter called")\ndef get_default_config(*args):\n    raise AssertionError("default selector called")\ndef try_get_optimal_moe_config(*args):\n    raise AssertionError("tuner called")\ndef apply(self,*args):\n    raise AssertionError("apply called")\n')
        fused.get_moe_configs=functools.lru_cache()(fused.get_moe_configs)
        fused.get_moe_configs.cache_info=mock.Mock(side_effect=AssertionError('cache API called'))
        fused.get_moe_configs.cache_clear=mock.Mock(side_effect=AssertionError('cache API called'))
        fused.get_moe_configs.cache_parameters=mock.Mock(side_effect=AssertionError('cache API called'))
        fused.vllm_is_batch_invariant=batch.vllm_is_batch_invariant;fused.envs=envs
        fused.TritonExperts.apply=fused.apply;del fused.apply
        package.TritonExperts=fused.TritonExperts
        config=self.modules[BASE+'.config'];quant=config.FusedMoEQuantConfig()
        for field in ('_a1','_a2','_w1','_w2'):
            desc=config.FusedMoEQuantDesc();vars(desc).update({key:None for key in ('dtype','shape','scale','alpha_or_gscale','zp','bias')});setattr(quant,field,desc)
        config.FUSED_MOE_UNQUANTIZED_CONFIG=quant;fused.FUSED_MOE_UNQUANTIZED_CONFIG=quant
        method_module=self.modules[BASE+'.unquantized_fused_moe_method'];method_module.FUSED_MOE_UNQUANTIZED_CONFIG=quant
        self.quant=quant;self.fused=fused;self.package=package;self.batch=batch;self.envs=envs
        torch=self.modules['torch'];torch.bfloat16=fixture.types['bfloat16']
        class Parameter:
            def __init__(self,shape):self.shape=tuple(shape);self.dtype=torch.bfloat16;self.device=NS(type='cuda',index=0)
        torch.nn=ModuleType('torch.nn');torch.nn.Parameter=Parameter
        self.modules['torch.nn']=torch.nn
        for layer in fixture.layers:
            moe=layer.mlp.experts;method=moe.quant_method;impl=method.kernel.fused_experts
            parallel=config.FusedMoEParallelConfig();vars(parallel).update(tp_size=1,dp_size=1,pcp_size=1,ep_size=1,tp_rank=0,dp_rank=0,pcp_rank=0,ep_rank=0,use_ep=False)
            mc=config.FusedMoEConfig();vars(mc).update(num_experts=hf['num_experts'],num_local_experts=hf['num_experts'],experts_per_token=hf['num_experts_per_tok'],hidden_dim=hf['hidden_size'],intermediate_size_per_partition=hf['moe_intermediate_size'],has_bias=False,is_act_and_mul=True,is_lora_enabled=False,in_dtype=torch.bfloat16,moe_parallel_config=parallel)
            moe.moe_config=method.moe=impl.moe_config=mc;method.moe_quant_config=impl.quant_config=quant
            moe._parameters={'w13_weight':Parameter([hf['num_experts'],2*hf['moe_intermediate_size'],hf['hidden_size']]),'w2_weight':Parameter([hf['num_experts'],hf['hidden_size'],hf['moe_intermediate_size']])}
        # The separate existing observer really succeeds before this helper is used.
        self.prior=fixture.observe()
        # Installed FusedMoE stores layer_name; layer_id is an inherited property
        # that imports extract_layer_index. The passive observer must avoid it.
        def forbidden_layer_id(_):raise AssertionError('importing layer_id property read')
        installed_base=type('InstalledFusedMoE',(),{'layer_id':property(forbidden_layer_id)})
        shared=type('SharedFusedMoE',(installed_base,),{'__module__':CLASS_MODULES['SharedFusedMoE']})
        fixture.types['SharedFusedMoE']=self.modules[CLASS_MODULES['SharedFusedMoE']].SharedFusedMoE=shared
        for layer in fixture.layers:
            old=layer.mlp.experts;replacement=shared();vars(replacement).update(vars(old))
            del vars(replacement)['layer_id'];layer.mlp.experts=replacement
        def forbidden(_):raise AssertionError('MoE initializing quantization property read')
        fixture.types['SharedFusedMoE'].moe_quant_config=property(forbidden)
        for name in ('quant_dtype','block_shape','config_name','use_fp8_w8a8'):
            setattr(config.FusedMoEQuantConfig,name,property(forbidden))

    def retain(self):
        return observer.retain_tuning_runtime(self.metadata,self.runtime,self.tuning,self.device,
            self.work,self.gpu,modules=self.modules,environment=self.env)

    def observe(self,retained=None):
        retained=retained or self.retain()
        # Simulate the constructor only for this observation window. Restore
        # the fixture's pre-construction state for independent retain cycles.
        key='RAY_CLIENT_MODE';injected=key not in self.env
        if injected:self.env[key]='0'
        try:
            return observer.observe_loaded_tuning(retained,self.llm,prior_runtime_observation=self.prior)
        finally:
            if injected:self.env.pop(key,None)

    def test_passive_complete_observation_retains_exact_bindings_and_detaches(self):
        with mock.patch('builtins.open',side_effect=AssertionError('file read')),mock.patch('os.open',side_effect=AssertionError('file read')):
            retained=self.retain();result=self.observe(retained)
        self.assertEqual(result['provenance'],'MOCK');self.assertTrue(result['test_only'])
        self.assertFalse(result['effective_kernel_configuration_observed']);self.assertFalse(result['scientific_validation_passed'])
        self.assertFalse(result['private_cache_contents_authenticated']);self.assertFalse(result['prior_runtime_observation_authenticated'])
        self.assertEqual(result['layers'][0]['weight_shapes'],{'w13':[2,128,128],'w2':[2,128,64]})
        self.assertEqual(result['layers'][0]['filename_N'],64);json.dumps(result,allow_nan=False)
        result['layers'][0]['weight_shapes']['w2'][2]=1
        self.assertEqual(self.observe(retained)['layers'][0]['filename_N'],64)

    def test_final_metadata_read_reports_current_environment_wrapper(self):
        original=vars(self.envs)['__getattr__']
        retained,counts=self.final_metadata_hook(
            lambda:setattr(self.envs,'__getattr__',functools.cache(original)))
        result=self.observe(retained)
        self.assertEqual(result['env_getattr_state'],'WRAPPED_RETAINED_FUNCTION')
        self.assertTrue(all(count==2 for count in counts.values()))

    def test_enabled_batch_runtime_state_rejects_even_with_false_config_flag(self):
        fields={'_batch_invariant_MODE':True,'_batch_invariant_LIB':object(),
                '_original_torch_bmm':object(), '_original_fp16_reduction_precision':False,
                '_original_bf16_reduction_precision':False,
                '_original_cublas_workspace_cfg':':4096:8',
                '_original_cublaslt_workspace_size':'1'}
        for field,value in fields.items():
            old=vars(self.batch)[field]
            with self.subTest(field=field,stage='preconstruction'):
                setattr(self.batch,field,value)
                with self.assertRaises(ValueError):self.retain()
                setattr(self.batch,field,old)
            with self.subTest(field=field,stage='postconstruction'):
                retained=self.retain();setattr(self.batch,field,value)
                with self.assertRaises(ValueError):self.observe(retained)
                setattr(self.batch,field,old)

    def test_installed_layer_identity_uses_stored_name_without_inherited_getter(self):
        for layer in self.fixture.layers:
            moe=layer.mlp.experts
            self.assertNotIn('layer_id',vars(moe))
            self.assertNotIn('layer_id',vars(type(moe)))
            self.assertIsInstance(vars(type(moe).__bases__[0])['layer_id'],property)
        result=self.observe()
        self.assertEqual([row['layer'] for row in result['layers']],[0,1])
        self.fixture.layers[1].mlp.experts.layer_name='model.layers.0.mlp.experts'
        with self.assertRaises(ValueError):self.observe()

    def test_expected_cached_env_transition_never_queries_private_cache(self):
        # Simulate construction prepopulating its wrapper. This is a pure fixture
        # function; the observer must not call either it or any cache API.
        exec(compile('calls=0\ndef __getattr__(name):\n    global calls\n    calls+=1\n    return None\n',self.envs.__file__,'exec'),vars(self.envs))
        retained=self.retain();original=vars(self.envs)['__getattr__'];self.envs.__getattr__=functools.cache(original)
        self.envs.__getattr__('VLLM_TUNED_CONFIG_FOLDER')
        self.envs.__getattr__.cache_clear=mock.Mock(side_effect=AssertionError('cache clear'))
        self.envs.__getattr__.cache_info=mock.Mock(side_effect=AssertionError('cache info'))
        result=self.observe(retained);self.assertEqual(result['env_getattr_state'],'WRAPPED_RETAINED_FUNCTION')
        self.assertEqual(self.envs.calls,1)

    def test_preconstruction_overrides_types_origins_and_bindings_fail(self):
        cases=[(self.package,'_config',{}),(self.batch,'VLLM_BATCH_INVARIANT',0),(self.batch,'VLLM_BATCH_INVARIANT',True),
               (self.fused,'envs',ModuleType('foreign')),(self.package,'TritonExperts',type('Other',(),{})),
               (self.fused,'vllm_is_batch_invariant',lambda:False),(self.package,'get_config',lambda:None),
               (self.fused,'get_moe_configs',lambda:None)]
        for owner,key,value in cases:
            old=vars(owner)[key];setattr(owner,key,value)
            with self.subTest(key=key),self.assertRaises(ValueError):self.retain()
            setattr(owner,key,old)
        self.fused.__spec__.origin='/wrong/source'
        with self.assertRaises(ValueError):self.retain()

    def test_environment_override_and_rebinding_after_retention_fail(self):
        for key in ('VLLM_TUNED_CONFIG_FOLDER','VLLM_BATCH_INVARIANT'):
            retained=self.retain();self.env[key]='0'
            with self.subTest(key=key),self.assertRaises(ValueError):self.observe(retained)
            del self.env[key]
        for owner,key,value in [(self.fused,'try_get_optimal_moe_config',lambda:None),
                                (self.fused,'get_default_config',lambda:None),
                                (self.fused,'get_moe_configs',functools.lru_cache()(lambda:None)),
                                (self.envs,'__getattr__',functools.cache(lambda name:None))]:
            retained=self.retain();old=vars(owner)[key];setattr(owner,key,value)
            with self.subTest(key=key),self.assertRaises(ValueError):self.observe(retained)
            setattr(owner,key,old)
        retained=self.retain();self.modules[BASE+'.fused_moe']=ModuleType('replacement')
        with self.assertRaises(ValueError):self.observe(retained)

    def test_parameter_shape_dtype_device_and_exact_type_fail(self):
        moe=self.fixture.layers[0].mlp.experts;weight=moe._parameters['w2_weight']
        for key,value in [('shape',(2,128,128)),('shape',(2,128,True)),('dtype',object()),('device',NS(type='cpu',index=0)),('device',NS(type='cuda',index=1)),('device',NS(type='cuda',index=False))]:
            old=getattr(weight,key);setattr(weight,key,value)
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):self.observe()
            setattr(weight,key,old)
        moe._parameters['w2_weight']=NS(**vars(weight))
        with self.assertRaises(ValueError):self.observe()

    def test_device_metadata_requires_exact_primitive_kind(self):
        class ClaimsCuda:
            def __eq__(self,other):return True
        weight=self.fixture.layers[0].mlp.experts._parameters['w2_weight']
        weight.device=NS(type=ClaimsCuda(),index=0)
        with self.assertRaises(ValueError):self.observe()

    def test_actual_layer_identity_cannot_reuse_another_layers_prior_report(self):
        self.fixture.layers[1].mlp.experts=self.fixture.layers[0].mlp.experts
        with self.assertRaises(ValueError):self.observe()

    def test_wrapped_function_globals_code_and_raw_config_class_are_retained(self):
        original=self.fused.get_moe_configs.__wrapped__
        self.fused.get_moe_configs.__wrapped__=FunctionType(original.__code__,dict(original.__globals__))
        with self.assertRaises(ValueError):self.retain()
        self.fused.get_moe_configs.__wrapped__=original
        retained=self.retain();saved=self.fused.get_default_config.__code__
        self.fused.get_default_config.__code__=self.fused.try_get_optimal_moe_config.__code__
        with self.assertRaises(ValueError):self.observe(retained)
        self.fused.get_default_config.__code__=saved
        saved=self.quant._w1;self.quant._w1=NS(**vars(saved))
        with self.assertRaises(ValueError):self.observe()
        self.quant._w1=saved
        method=self.fixture.layers[0].mlp.experts.quant_method;method.moe_quant_config=copy.copy(self.quant)
        with self.assertRaises(ValueError):self.observe()

    def test_filename_selector_rebinding_cannot_inherit_the_retained_input(self):
        retained=self.retain()
        self.fused.get_config_file_name=lambda *args:'different.json'
        with self.assertRaises(ValueError):self.observe(retained)

    def test_source_proved_environment_addition_is_only_postconstruction_and_latched(self):
        key='VLLM_OBJECT_STORAGE_SHM_BUFFER_NAME';value='VLLM_OBJECT_STORAGE_SHM_BUFFER_'+'a'*32
        retained=self.retain();self.env[key]=value
        result=self.observe(retained)
        self.assertEqual(result['object_storage_shm_name']['value'],value)
        self.assertFalse(result['object_storage_shm_name']['authenticated'])
        self.assertEqual(self.observe(retained)['object_storage_shm_name']['value'],value)
        self.env[key]=value[:-1]+'b'
        with self.assertRaises(ValueError):self.observe(retained)
        with self.assertRaises(ValueError):self.retain()

    def test_ray_client_mode_is_exact_postconstruction_state_and_latched(self):
        key='RAY_CLIENT_MODE'
        self.env[key]='0'
        with self.assertRaises(ValueError):self.retain()
        del self.env[key];retained=self.retain()
        with self.assertRaisesRegex(ValueError,'unexpected Ray client environment state'):
            observer.observe_loaded_tuning(retained,self.llm,prior_runtime_observation=self.prior)
        self.env[key]='1'
        with self.assertRaisesRegex(ValueError,'unexpected Ray client environment state'):self.observe(retained)
        self.env[key]='0';result=self.observe(retained)
        self.assertEqual(result['ray_client_mode'],'0')
        self.assertEqual(retained.post_environment[key],'0')
        del self.env[key]
        with self.assertRaisesRegex(ValueError,'unexpected Ray client environment state'):
            observer.observe_loaded_tuning(retained,self.llm,prior_runtime_observation=self.prior)
        self.env[key]='1'
        with self.assertRaisesRegex(ValueError,'unexpected Ray client environment state'):self.observe(retained)

    def test_unproved_or_malformed_environment_additions_still_reject(self):
        key='VLLM_OBJECT_STORAGE_SHM_BUFFER_NAME'
        for value in ('foreign', 'VLLM_OBJECT_STORAGE_SHM_BUFFER_'+'a'*31,'VLLM_OBJECT_STORAGE_SHM_BUFFER_'+'A'*32):
            retained=self.retain();self.env[key]=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.observe(retained)
            del self.env[key]
        retained=self.retain();self.env['CUDA_MODULE_LOADING']='LAZY'
        with self.assertRaises(ValueError):self.observe(retained)

    def test_environment_mismatch_reports_only_sorted_key_names(self):
        retained=self.retain()
        del self.env['OMP_NUM_THREADS']
        self.env['KMP_DUPLICATE_LIB_OK']='sensitive-changed-value'
        self.env['CUDA_MODULE_LOADING']='sensitive-added-value'
        with self.assertRaises(ValueError) as caught:
            self.observe(retained)
        self.assertEqual(str(caught.exception),
            "HF tuning runtime: environment differs from prepared allowlist; "
            "added_keys=['CUDA_MODULE_LOADING']; missing_keys=['OMP_NUM_THREADS']; "
            "changed_keys=['KMP_DUPLICATE_LIB_OK']")
        self.assertNotIn('sensitive-changed-value',str(caught.exception))
        self.assertNotIn('sensitive-added-value',str(caught.exception))

    def test_fixed_runtime_import_environment_is_required_exact_and_retained(self):
        retained=self.retain()
        self.assertEqual(retained.env,self.env)
        for key,value in self.import_environment.items():
            with self.subTest(key=key,case='missing'):
                del self.env[key]
                with self.assertRaisesRegex(ValueError,'environment differs from prepared allowlist'):self.retain()
                self.env[key]=value
            with self.subTest(key=key,case='wrong'):
                self.env[key]=value+'-wrong'
                with self.assertRaisesRegex(ValueError,'environment differs from prepared allowlist'):self.retain()
                self.env[key]=value
            with self.subTest(key=key,case='changed-after-retention'):
                retained=self.retain();self.env[key]=value+'-changed'
                with self.assertRaises(ValueError):self.observe(retained)
                self.env[key]=value
        self.env['UNOBSERVED_IMPORT_ENV']='1'
        with self.assertRaisesRegex(ValueError,'environment differs from prepared allowlist'):self.retain()

    def test_retention_accepts_only_legacy_or_exact_routed_event_source_sets(self):
        legacy=sources.validate_runtime_sources(self.runtime)
        self.assertEqual(len(legacy['artifacts']),133)
        self.assertNotIn('cuda_event_extension',legacy)
        self.assertIs(self.retain().runtime,self.runtime)
        for name in sources.CUDA_EVENT_SOURCE_FILES:
            path=self.inputs.site/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'# TEST_ONLY CUDA event API source; never execute\n')
        self.runtime=sources.collect_runtime_sources(
            source_root=self.inputs.site,stdlib_root=self.inputs.fixture.stdlib,
            interpreter=self.inputs.fixture.interpreter,include_tuning=True,
            include_cuda_events=True)
        self.inputs.runtime=self.runtime;self.tuning=self.inputs.collect()
        routed=sources.validate_runtime_sources(self.runtime)
        self.assertEqual(len(routed['artifacts']),135)
        self.assertEqual(routed['cuda_event_extension'],'ROUTE_EVENTS_V1')
        self.assertEqual(
            {key for key in routed['artifacts'] if key in observer.ROUTE_EVENT_SOURCE_ARTIFACTS},
            set(observer.ROUTE_EVENT_SOURCE_ARTIFACTS))
        self.assertIs(self.retain().runtime,self.runtime)
        missing=copy.deepcopy(routed)
        del missing['artifacts'][observer.ROUTE_EVENT_SOURCE_ARTIFACTS[1]]
        wrong=copy.deepcopy(routed);wrong['cuda_event_extension']='ROUTE_EVENTS_V2'
        for label,report in (('missing-api-source',missing),('wrong-extension',wrong)):
            with self.subTest(label=label),mock.patch.object(
                sources,'validate_runtime_sources',return_value=report),self.assertRaisesRegex(
                    ValueError,'routed-event tuning source contract'):
                self.retain()

    def test_registered_submodules_are_read_without_getters_and_rebinding_is_detected(self):
        runner=self.fixture.runner;model=runner.model
        del runner.model;runner._modules={'model':model}
        container=NS(_modules={str(i):layer for i,layer in enumerate(self.fixture.layers)})
        model.model.layers=container
        self.observe()
        retained=self.retain();weight=self.fixture.layers[0].mlp.experts._parameters['w2_weight']
        cls=type(weight);shape=weight.shape
        def mutate(obj):
            if obj is weight:
                self.fixture.layers[0].mlp.experts._parameters['w2_weight']=copy.copy(weight)
            return vars(obj)['saved_shape']
        for layer in self.fixture.layers:
            for item in layer.mlp.experts._parameters.values():item.saved_shape=item.shape;del item.shape
        cls.shape=property(mutate)
        with self.assertRaises(ValueError):self.observe(retained)

    def test_raw_quant_descriptor_and_alias_mutations_fail_without_properties(self):
        for desc in ('_a1','_a2','_w1','_w2'):
            for field in ('dtype','shape','scale','alpha_or_gscale','zp','bias'):
                obj=getattr(self.quant,desc);setattr(obj,field,object())
                with self.subTest(desc=desc,field=field),self.assertRaises(ValueError):self.observe()
                setattr(obj,field,None)
        impl=self.fixture.layers[0].mlp.experts.quant_method.kernel.fused_experts
        impl.quant_config=copy.copy(self.quant)
        with self.assertRaises(ValueError):self.observe()

    def test_apply_binding_geometry_and_prior_report_must_match(self):
        moe=self.fixture.layers[0].mlp.experts;impl=moe.quant_method.kernel.fused_experts
        impl.apply=MethodType(type(impl).apply,object())
        with self.assertRaises(ValueError):self.observe()
        del impl.apply
        for key,value in [('has_bias',True),('is_act_and_mul',False),('hidden_dim',256),('experts_per_token',2)]:
            old=getattr(moe.moe_config,key);setattr(moe.moe_config,key,value)
            with self.subTest(key=key),self.assertRaises(ValueError):self.observe()
            setattr(moe.moe_config,key,old)
        moe.moe_config.moe_parallel_config.tp_size=2
        with self.assertRaises(ValueError):self.observe()
        moe.moe_config.moe_parallel_config.tp_size=1
        self.prior['layers'][0]['moe']['backend']='OTHER'
        with self.assertRaises(ValueError):self.observe()

    def test_mid_observation_weight_property_rebinding_fails_final_recheck(self):
        retained=self.retain();moe=self.fixture.layers[0].mlp.experts;weight=moe._parameters['w2_weight'];cls=type(weight)
        shape=weight.shape;del weight.shape
        def mutating(obj):
            self.package._config={}
            return shape
        cls.shape=property(mutating)
        with self.assertRaises(ValueError):self.observe(retained)

    def final_metadata_hook(self,callback):
        retained=self.retain();weights=[weight for layer in self.fixture.layers for weight in layer.mlp.experts._parameters.values()]
        last=weights[-1];counts={id(weight):0 for weight in weights}
        for weight in weights:
            weight.saved_shape=weight.shape;del weight.shape
        def shape(weight):
            counts[id(weight)]+=1
            if counts[id(weight)]>2:raise AssertionError('final stored pass read tensor metadata again')
            if weight is last and counts[id(weight)]==2:callback()
            return vars(weight)['saved_shape']
        type(last).shape=property(shape)
        return retained,counts

    def test_last_tensor_metadata_read_cannot_change_raw_quantization_after_validation(self):
        retained,counts=self.final_metadata_hook(lambda:setattr(self.quant._w1,'dtype','int8'))
        with self.assertRaises(ValueError):self.observe(retained)
        self.assertTrue(all(count==2 for count in counts.values()))

    def test_final_stored_validation_never_repeats_tensor_metadata_reads(self):
        retained,counts=self.final_metadata_hook(lambda:None)
        self.observe(retained)
        self.assertTrue(all(count==2 for count in counts.values()))

    def test_last_metadata_read_cannot_rebind_config_parallel_or_weight_storage(self):
        for change in ('config','parallel','quant_alias','weight'):
            with self.subTest(change=change):
                case=TuningRuntimeTests();case.setUp();self.addCleanup(case.doCleanups)
                moe=case.fixture.layers[-1].mlp.experts
                def mutate():
                    if change=='config':moe.moe_config.has_bias=True
                    elif change=='parallel':moe.moe_config.moe_parallel_config.tp_size=2
                    elif change=='quant_alias':moe.quant_method.kernel.fused_experts.quant_config=copy.copy(case.quant)
                    else:moe._parameters['w13_weight']=copy.copy(moe._parameters['w13_weight'])
                retained,counts=case.final_metadata_hook(mutate)
                with self.assertRaises(ValueError):case.observe(retained)
                self.assertTrue(all(count==2 for count in counts.values()))

    def test_frozen_input_binding_mutation_rejects_and_no_runtime_imports_occur(self):
        from dataclasses import replace
        self.tuning=replace(self.tuning,manifest_bytes=self.tuning.manifest_bytes.replace(b'INSTALLED_DEFAULTS',b'PACKAGED_JSON'))
        with self.assertRaises(ValueError):self.retain()
        self.assertFalse(any(name in sys.modules for name in ('torch','vllm','triton','flashinfer')))


if __name__=='__main__':unittest.main()

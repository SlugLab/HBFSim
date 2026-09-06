"""Passive cache observations from fake modules; no inference imports or I/O."""
from importlib.machinery import ModuleSpec
import ctypes
import io
import json
import logging
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from adapters.vllm_capacity.hf_routing_worker import make_environment
import test_hf_runtime_sources as source_tests
import hf_runtime_imports as observer


class ImportedCacheTests(unittest.TestCase):
    def setUp(self):
        fixture = source_tests.RuntimeSourceTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        self.snapshot=fixture.collect(); self.site=fixture.site; self.stdlib=fixture.stdlib
        self.work=fixture.base/'private-arm'; self.gpu='GPU-TEST_ONLY'; self.modules={}
        self.env=make_environment(self.work,self.gpu,{'HOME':'/never-read-home','PATH':'/never-exec'})
        names=set(observer.REQUIRED_MODULES)|set(observer.OPTIONAL_MODULES)
        for name in names:
            package='site-packages/'+name.replace('.','/')+'/__init__.py'
            ordinary='site-packages/'+name.replace('.','/')+'.py'
            artifacts=dict(self.snapshot.artifacts)
            key = 'stdlib/'+name.replace('.','/')+'.py' if name in ('tempfile','multiprocessing.shared_memory') else package if package in artifacts else ordinary
            root=self.stdlib if key.startswith('stdlib/') else self.site
            path=root/key.split('/',1)[1]
            module=ModuleType(name); module.__file__=str(path); module.__spec__=ModuleSpec(name,None,origin=str(path))
            self.modules[name]=module
        w=self.work; m=self.modules
        def put(name,**values):vars(m[name]).update(values)
        put('vllm.envs', VLLM_CACHE_ROOT=str(w/'cache/vllm'),VLLM_CONFIG_ROOT=str(w/'config/vllm'),VLLM_ASSETS_CACHE=str(w/'cache/vllm/assets'),VLLM_ENABLE_V1_MULTIPROCESSING=False,VLLM_USE_FLASHINFER_MOE_FP16=False,VLLM_USE_FLASHINFER_SAMPLER=False,VLLM_NO_USAGE_STATS=True,VLLM_DO_NOT_TRACK=True,VLLM_TUNED_CONFIG_FOLDER=None)
        put('vllm.usage.usage_lib',_config_home=str(w/'config/vllm'),_USAGE_STATS_JSON_PATH=str(w/'config/vllm/usage_stats.json'),_USAGE_STATS_DO_NOT_TRACK_PATH=str(w/'config/vllm/do_not_track'),_USAGE_STATS_ENABLED=None)
        put('huggingface_hub.constants',HF_HOME=str(w/'cache/hf'),HF_HUB_CACHE=str(w/'cache/hf/hub'),HF_XET_CACHE=str(w/'cache/hf/xet'),HF_ASSETS_CACHE=str(w/'cache/hf/assets'),HF_TOKEN_PATH=str(w/'cache/hf/token'),HF_STORED_TOKENS_PATH=str(w/'cache/hf/stored_tokens'),HF_HUB_OFFLINE=True,HF_HUB_DISABLE_TELEMETRY=True,HF_HUB_DISABLE_IMPLICIT_TOKEN=True)
        put('transformers.utils.hub',HF_MODULES_CACHE=str(w/'cache/hf/modules'))
        put('torch.hub',_hub_dir=None); put('torch.version',__version__='2.9.1+cu128',cuda='12.8',hip=None)
        class cache_knobs:pass
        class compilation_knobs:pass
        cache=cache_knobs(); compilation=compilation_knobs()
        vars(cache).update(home_dir=str(w/'cache/triton-home'),dir=str(w/'cache/triton'),dump_dir=str(w/'cache/triton-dump'),override_dir=str(w/'cache/triton-override'),manager_class=None,remote_manager_class=None)
        vars(compilation).update(override=False,dump_ir=False)
        put('triton.knobs',cache=cache,compilation=compilation,cache_knobs=cache_knobs,compilation_knobs=compilation_knobs,getenv=lambda key,default=None:self.env.get(key,default))
        put('tempfile',tempdir=str(w/'tmp'))
        put('vllm.model_executor.layers.fused_moe.routed_experts_capturer',_TMP_DIR=str(w/'tmp'),_LOCK_FILE_PREFIX=str(w/'tmp/vllm_routed_experts'),_BUFFER_PREFIX='vllm_routed_experts_buffer',shared_memory=m['multiprocessing.shared_memory'])
        base=w/'cache/flashinfer-workspace'; cachepath=base/'.cache/flashinfer'; workspace=cachepath/'0.6.1/120a'
        self.workspace=workspace
        put('flashinfer.jit.env',FLASHINFER_BASE_DIR=base,FLASHINFER_CACHE_DIR=cachepath,FLASHINFER_WORKSPACE_DIR=workspace,FLASHINFER_JIT_DIR=workspace/'cached_ops',FLASHINFER_GEN_SRC_DIR=workspace/'generated',FLASHINFER_CUBIN_DIR=self.site/'flashinfer_cubin/cubins',FLASHINFER_AOT_DIR=self.site/'flashinfer/data/aot',FLASHINFER_DATA=self.site/'flashinfer/data',FLASHINFER_INCLUDE_DIR=self.site/'flashinfer/data/include',FLASHINFER_CSRC_DIR=self.site/'flashinfer/data/csrc')
        put('flashinfer_cubin',CUBIN_DIR=self.site/'flashinfer_cubin/cubins')
        put('flashinfer.version',__version__='0.6.1')
        put('flashinfer.jit.core',current_compilation_context=SimpleNamespace(TARGET_CUDA_ARCHS={(12,'0a')}))
        handler=logging.FileHandler.__new__(logging.FileHandler); handler.baseFilename=str(workspace/'flashinfer_jit.log')
        handler.stream=io.StringIO();handler.stream.name=handler.baseFilename
        self.addCleanup(handler.stream.close)
        stream=logging.StreamHandler.__new__(logging.StreamHandler);stream.stream=sys.stderr
        put('flashinfer.jit.core',logger=SimpleNamespace(handlers=[stream,handler],parent=None))
        put('flashinfer.api_logging',_API_LOG_LEVEL=0,_API_LOG_DEST='stderr',_DUMP_DIR='flashinfer_dumps',_DUMP_SAFETENSORS=False,_dump_count=0,_dump_total_size_bytes=0,_dump_call_counter={})
        put('tvm_ffi._optional_torch_c_dlpack')
        for name,version in {**observer.RUNTIME_VERSIONS,'numpy':'2.2.6','safetensors':'0.7.0'}.items():put(name,__version__=version)

    def observe(self):
        return observer.observe_runtime_imports(self.snapshot,self.work,self.gpu,(12,0),modules=self.modules,environment=self.env)

    def test_complete_observation_is_detached_mock_and_never_calls_side_effect_apis(self):
        for module in self.modules.values():
            for name in ('gettempdir','get_dir','is_usage_stats_enabled',
                         'load_torch_c_dlpack_extension','patch_torch_cuda_stream_protocol',
                         'get_artifacts_status','get_cache_manager','cache_dir','download_file'):
                setattr(module,name,mock.Mock(side_effect=AssertionError('observer called '+name)))
        with mock.patch('builtins.open',side_effect=AssertionError('observer opened file')),mock.patch('os.open',side_effect=AssertionError('observer opened file')):
            result=self.observe()
        self.assertEqual(result['provenance'],'MOCK');self.assertFalse(result['scientific_validation_passed'])
        self.assertEqual(result['flashinfer']['package_cubin_dir'],str(self.site/'flashinfer_cubin/cubins'))
        self.assertFalse(result['all_runtime_binaries_authenticated'])
        self.assertIsNone(result['optional_dlpack_library'])
        self.assertEqual(result['optional_dlpack_none_reason'],
                         'DISABLED_BY_DECLARED_CONFIGURATION')
        result['effective_paths']['torch.hub']= 'mutated'
        self.assertEqual(self.observe()['effective_paths']['torch.hub'],str(self.work/'cache/torch/hub'))
        json.dumps(result)

    def test_required_module_origin_initialization_and_partial_flashinfer_reject(self):
        for change in ('missing','shadow','initializing','partial'):
            with self.subTest(change=change):
                name='flashinfer.jit.env' if change=='partial' else 'torch.hub';module=self.modules[name]
                if change in ('missing','partial'):del self.modules[name]
                elif change=='shadow':module.__spec__.origin='/outside/shadow.py'
                else:module.__spec__._initializing=True
                with self.assertRaises(ValueError):self.observe()
                self.modules[name]=module;module.__spec__.origin=module.__file__;module.__spec__._initializing=False

    def test_effective_cache_override_rejects_despite_correct_environment(self):
        for name,attribute in [('vllm.envs','VLLM_CACHE_ROOT'),('huggingface_hub.constants','HF_TOKEN_PATH'),('torch.hub','_hub_dir'),('tempfile','tempdir'),('flashinfer.jit.env','FLASHINFER_CUBIN_DIR')]:
            module=self.modules[name];before=getattr(module,attribute);setattr(module,attribute,'/outside/cache')
            with self.subTest(name=name),self.assertRaises(ValueError):self.observe()
            setattr(module,attribute,before)

    def test_forbidden_environment_is_rejected_before_any_manager_descriptor(self):
        class dangerous_cache:
            @property
            def manager_class(self):raise AssertionError('manager descriptor reached')
        knobs=self.modules['triton.knobs'];knobs.cache=dangerous_cache();knobs.cache_knobs=dangerous_cache
        self.env['TRITON_CACHE_MANAGER']='untrusted.module:Class'
        with self.assertRaises(ValueError):self.observe()

    def test_declared_startup_controls_reject_missing_or_unexpected_values(self):
        for key in ('TVM_FFI_DISABLE_TORCH_C_DLPACK','VLLM_PLUGINS'):
            expected=self.env.pop(key)
            with self.subTest(key=key,value='missing'),self.assertRaises(ValueError):self.observe()
            self.env[key]=expected
        for value in ('0','true',''):
            self.env['TVM_FFI_DISABLE_TORCH_C_DLPACK']=value
            with self.subTest(key='TVM_FFI_DISABLE_TORCH_C_DLPACK',value=value),self.assertRaises(ValueError):
                self.observe()
        self.env['TVM_FFI_DISABLE_TORCH_C_DLPACK']='1'
        self.env['VLLM_PLUGINS']='unexpected.plugin'
        with self.assertRaises(ValueError):self.observe()

    def test_injected_environment_cannot_reach_a_manager_import_from_live_environment(self):
        knobs=self.modules['triton.knobs'];original=knobs.cache
        class import_capable_cache(type(original)):
            @property
            def manager_class(self):
                if os.environ.get('TRITON_CACHE_MANAGER') is not None:
                    raise AssertionError('manager descriptor would import here')
                return None
        knobs.cache=import_capable_cache();knobs.cache_knobs=import_capable_cache
        vars(knobs.cache).update({k:v for k,v in vars(original).items() if k!='manager_class'})
        knobs.getenv=lambda key,default=None:os.environ.get(key,default)
        with mock.patch.dict(os.environ,{'TRITON_CACHE_MANAGER':'untrusted.module:Class'}):
            with self.assertRaises(ValueError):self.observe()

    def test_usage_cache_none_false_only_and_bool_types_strict(self):
        module=self.modules['vllm.usage.usage_lib']
        for value in (None,False):module._USAGE_STATS_ENABLED=value;self.observe()
        for value in (True,0,'false'):
            module._USAGE_STATS_ENABLED=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.observe()

    def test_dlpack_library_state_must_be_absent(self):
        module=self.modules['tvm_ffi._optional_torch_c_dlpack']
        self.assertNotIn('_LIB',vars(module));self.observe()
        library=ctypes.CDLL.__new__(ctypes.CDLL)
        library._name=str(self.work/'cache/tvm-ffi/private-looking.so')
        for value in (None,library,object()):
            module._LIB=value
            with self.subTest(value=repr(value)),self.assertRaises(ValueError):self.observe()
        del module._LIB

    def test_logger_destination_dump_activity_and_architecture_mismatch_reject(self):
        core=self.modules['flashinfer.jit.core'];handler=core.logger.handlers[1]
        handler.baseFilename='/outside/log'
        with self.assertRaises(ValueError):self.observe()
        handler.baseFilename=str(self.workspace/'flashinfer_jit.log')
        self.modules['flashinfer.api_logging']._dump_count=1
        with self.assertRaises(ValueError):self.observe()
        self.modules['flashinfer.api_logging']._dump_count=0
        core.current_compilation_context.TARGET_CUDA_ARCHS={(9,'0a')}
        with self.assertRaises(ValueError):self.observe()

    def test_optional_shadow_and_forbidden_import_reject(self):
        self.modules['flashinfer.artifacts'].__file__='/outside/artifacts.py'
        with self.assertRaises(ValueError):self.observe()
        del self.modules['flashinfer.artifacts'];self.observe()
        for name in ('torch_c_dlpack_ext','torch_c_dlpack_ext.submodule'):
            self.modules[name]=ModuleType(name)
            with self.subTest(name=name),self.assertRaises(ValueError):self.observe()
            del self.modules[name]

    def test_module_replacement_cannot_inherit_a_prior_verified_origin(self):
        module=self.modules['vllm.envs'];value=module.VLLM_CACHE_ROOT
        del module.VLLM_CACHE_ROOT
        def lazy(name):
            if name != 'VLLM_CACHE_ROOT':raise AttributeError(name)
            replacement=ModuleType('torch.hub');replacement._hub_dir=None
            replacement.__file__='/outside/shadow.py'
            replacement.__spec__=ModuleSpec('torch.hub',None,origin='/outside/shadow.py')
            self.modules['torch.hub']=replacement
            return value
        module.__getattr__=lazy
        with self.assertRaises(ValueError):self.observe()

    def test_active_log_stream_must_match_its_private_declared_destination(self):
        handler=self.modules['flashinfer.jit.core'].logger.handlers[1]
        stream=handler.stream
        for change in ('redirected','missing','closed'):
            if change=='redirected':stream.name='/outside/redirected.log'
            elif change=='missing':handler.stream=None
            else:handler.stream=stream;stream.name=handler.baseFilename;stream.close()
            with self.subTest(change=change),self.assertRaises(ValueError):self.observe()


if __name__=='__main__':unittest.main()

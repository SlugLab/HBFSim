"""Passive observations of already imported, pinned HF runtime cache state.

No imports of inference packages, discovery, CUDA queries, downloads or cache
constructors are performed here. The later owned worker must establish private
paths and its resource gate before any runtime import. These observations alone
are neither a sandbox nor capture-origin or complete binary authentication.
"""
from __future__ import annotations

import ctypes
import io
import logging
import os
from pathlib import Path
import sys
from types import ModuleType

import hf_runtime_sources as sources
from verify_hf_metadata import digest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from adapters.vllm_capacity.hf_routing_worker import make_environment

RUNTIME_VERSIONS = {'vllm':'0.15.1','torch':'2.9.1+cu128','transformers':'5.5.4',
    'triton':'3.5.1','huggingface_hub':'1.11.0','flashinfer':'0.6.1',
    'flashinfer_cubin':'0.6.1','tvm_ffi':'0.1.6'}
REQUIRED_MODULES = tuple(RUNTIME_VERSIONS) + (
    'vllm.envs','vllm.usage.usage_lib','huggingface_hub.constants',
    'transformers.utils.hub','torch.hub','torch.version','triton.knobs',
    'tempfile','multiprocessing.shared_memory',
    'vllm.model_executor.layers.fused_moe.routed_experts_capturer',
    'flashinfer.version','flashinfer.compilation_context','flashinfer.jit.env',
    'flashinfer.jit.core','flashinfer.api_logging','tvm_ffi._optional_torch_c_dlpack')
OPTIONAL_MODULES = ('numpy','safetensors','torch.utils.cpp_extension',
    'torch._inductor.runtime.cache_dir_utils','triton.runtime.cache',
    'flashinfer.autotuner','flashinfer.artifacts','flashinfer.jit.cubin_loader',
    'tvm_ffi.cpp.extension')
FORBIDDEN_IMPORTS = ('flashinfer_jit_cache','torch_c_dlpack_ext')
# The complete parent allowlist excludes all of these before importing. Recheck
# these overrides before inspecting Triton's manager state. Never invoke its
# env_class or NvidiaTool descriptors: they can import or run subprocesses.
FORBIDDEN_ENV = tuple('''TRITON_CACHE_MANAGER TRITON_REMOTE_CACHE_BACKEND
TRITON_KERNEL_OVERRIDE TRITON_KERNEL_DUMP TRITON_OVERRIDE_ARCH TRITON_PTXAS_PATH
TRITON_CUOBJDUMP_PATH TRITON_CUDACRT_PATH TRITON_CUDART_PATH TRITON_LIBCUDA_PATH
VLLM_TUNED_CONFIG_FOLDER FLASHINFER_CUBIN_DIR FLASHINFER_CUBINS_REPOSITORY
FLASHINFER_CUDA_ARCH_LIST FLASHINFER_DISABLE_VERSION_CHECK
FLASHINFER_CUBIN_CHECKSUM_DISABLED TVM_FFI_DISABLE_TORCH_C_DLPACK
HUGGINGFACE_CO_STAGING HF_TOKEN_PATH HUGGINGFACE_HUB_CACHE HUGGINGFACE_ASSETS_CACHE
TORCH_HUB LD_PRELOAD LD_AUDIT PYTHONPATH'''.split())


def observe_runtime_imports(snapshot, work, gpu_uuid, device_capability, *,
                            modules=None, environment=None):
    """Inspect the complete preconstruction import stage, never importing it.

    device_capability is supplied by the owned worker's separate CUDA identity
    check; this passive helper does not authenticate that observation. Explicit
    module/environment injections or an injected source snapshot force MOCK.
    """
    report = sources.validate_runtime_sources(snapshot)
    test_only = report['test_only'] or modules is not None or environment is not None
    modules = sys.modules if modules is None else modules
    environment = os.environ if environment is None else environment
    if len(modules) > 32768 or len(environment) > 512:
        raise ValueError('runtime observation exceeds finite input bounds')
    original_modules=modules
    modules=dict(modules)
    env = dict(environment)
    expected_env = make_environment(work, gpu_uuid, env)
    if any(type(k) is not str or type(v) is not str or len(v) > 16384 for k,v in env.items()):
        raise ValueError('invalid runtime environment fields')
    if any(env.get(k) != v for k,v in expected_env.items()) or \
       any(k in env for k in FORBIDDEN_ENV) or \
       any(k.startswith('FLASHINFER_') and k not in expected_env for k in env):
        raise ValueError('runtime environment differs from prepared cache/control boundary')
    if type(device_capability) not in (tuple,list) or len(device_capability) != 2 or \
       any(type(v) is not int for v in device_capability) or \
       not 1 <= device_capability[0] <= 99 or not 0 <= device_capability[1] <= 9:
        raise ValueError('expected a finite observed CUDA capability')
    if any(type(name) is not str for name in modules) or any(
        name == prefix or name.startswith(prefix+'.') for name in modules for prefix in FORBIDDEN_IMPORTS):
        raise ValueError('unexpected optional runtime import')
    site=Path(report['source_root']);stdlib=Path(report['stdlib_root']);work=Path(work).absolute()
    origins={}
    for name in REQUIRED_MODULES + OPTIONAL_MODULES:
        module=modules.get(name)
        if module is None and name in OPTIONAL_MODULES:continue
        if not isinstance(module,ModuleType):raise ValueError('required runtime module missing: '+name)
        path=name.replace('.','/')
        key='stdlib/'+path+'.py' if name in ('tempfile','multiprocessing.shared_memory') else \
            'site-packages/'+path+'/__init__.py' if 'site-packages/'+path+'/__init__.py' in report['artifacts'] else 'site-packages/'+path+'.py'
        if key not in report['artifacts']:raise ValueError('observed module has no frozen source: '+name)
        expected=str((stdlib if key.startswith('stdlib/') else site)/key.split('/',1)[1])
        spec=vars(module).get('__spec__')
        if vars(module).get('__file__') != expected or spec is None or \
           getattr(spec,'origin',None) != expected or getattr(spec,'_initializing',False):
            raise ValueError('runtime module origin/initialization mismatch: '+name)
        origins[name]=dict(path=expected,source_sha256=report['artifacts'][key])
    def read(module,field):
        # vllm.envs intentionally uses a reviewed __getattr__; all other module
        # fields must already exist, avoiding lazy imports during observation.
        try:
            return getattr(modules[module],field) if module=='vllm.envs' else vars(modules[module])[field]
        except (AttributeError,KeyError) as error:
            raise ValueError('required runtime field missing: '+module+'.'+field) from error
    paths={};flags={};versions={}
    def path(module,field,expected):
        value=read(module,field)
        if not isinstance(value,(str,Path)) or os.fspath(value) != str(expected):
            raise ValueError('effective runtime path differs: '+module+'.'+field)
        paths[module+'.'+field]=str(expected)
    def scalar(module,field,expected):
        value=read(module,field)
        if type(value) is not type(expected) or value != expected:
            raise ValueError('effective runtime setting differs: '+module+'.'+field)
        flags[module+'.'+field]=value
    for name,version in {**RUNTIME_VERSIONS,'flashinfer.version':'0.6.1','numpy':'2.2.6','safetensors':'0.7.0'}.items():
        if name not in modules:continue
        value=read(name,'__version__')
        if not isinstance(value,str) or value != version:raise ValueError('runtime version mismatch: '+name)
        versions[name]=str(value)
    for field,suffix in {'VLLM_CACHE_ROOT':'cache/vllm','VLLM_CONFIG_ROOT':'config/vllm','VLLM_ASSETS_CACHE':'cache/vllm/assets'}.items():path('vllm.envs',field,work/suffix)
    for field,value in {'VLLM_ENABLE_V1_MULTIPROCESSING':False,'VLLM_USE_FLASHINFER_MOE_FP16':False,'VLLM_USE_FLASHINFER_SAMPLER':False,'VLLM_NO_USAGE_STATS':True,'VLLM_DO_NOT_TRACK':True,'VLLM_TUNED_CONFIG_FOLDER':None}.items():scalar('vllm.envs',field,value)
    for field,suffix in {'_config_home':'config/vllm','_USAGE_STATS_JSON_PATH':'config/vllm/usage_stats.json','_USAGE_STATS_DO_NOT_TRACK_PATH':'config/vllm/do_not_track'}.items():path('vllm.usage.usage_lib',field,work/suffix)
    usage=read('vllm.usage.usage_lib','_USAGE_STATS_ENABLED')
    if usage is not None and usage is not False:raise ValueError('runtime usage reporting already enabled')
    flags['vllm.usage.usage_lib._USAGE_STATS_ENABLED']=usage
    for field,suffix in {'HF_HOME':'cache/hf','HF_HUB_CACHE':'cache/hf/hub','HF_XET_CACHE':'cache/hf/xet','HF_ASSETS_CACHE':'cache/hf/assets','HF_TOKEN_PATH':'cache/hf/token','HF_STORED_TOKENS_PATH':'cache/hf/stored_tokens'}.items():path('huggingface_hub.constants',field,work/suffix)
    for field in ('HF_HUB_OFFLINE','HF_HUB_DISABLE_TELEMETRY','HF_HUB_DISABLE_IMPLICIT_TOKEN'):scalar('huggingface_hub.constants',field,True)
    path('transformers.utils.hub','HF_MODULES_CACHE',work/'cache/hf/modules')
    hub=read('torch.hub','_hub_dir')
    if hub is not None and hub != str(work/'cache/torch/hub'):raise ValueError('torch hub override escapes exact private root')
    paths['torch.hub']=str(work/'cache/torch/hub')
    scalar('torch.version','__version__','2.9.1+cu128');scalar('torch.version','cuda','12.8');scalar('torch.version','hip',None)
    knobs=modules['triton.knobs'];cache=read('triton.knobs','cache');compilation=read('triton.knobs','compilation')
    if type(cache) is not vars(knobs)['cache_knobs'] or type(compilation) is not vars(knobs)['compilation_knobs']:
        raise ValueError('unexpected Triton knob object type')
    for field,suffix in {'home_dir':'cache/triton-home','dir':'cache/triton','dump_dir':'cache/triton-dump','override_dir':'cache/triton-override'}.items():
        if getattr(cache,field) != str(work/suffix):raise ValueError('Triton cache path override')
        paths['triton.cache.'+field]=str(work/suffix)
    # env_class.get() reads the C environment, not necessarily os.environ or the
    # injected test mapping. Read only the existing value and the already-loaded
    # native getenv reader; never invoke either import-capable descriptor.
    getenv=read('triton.knobs','getenv')
    for field,key in (('manager_class','TRITON_CACHE_MANAGER'),
                      ('remote_manager_class','TRITON_REMOTE_CACHE_BACKEND')):
        if vars(cache).get(field) is not None or getenv(key) is not None:
            raise ValueError('unexpected live Triton cache manager override')
    if compilation.override is not False or compilation.dump_ir is not False:
        raise ValueError('unexpected Triton manager or kernel override')
    flags.update(triton_manager=None,triton_remote_manager=None,triton_kernel_override=False,triton_kernel_dump=False)
    path('tempfile','tempdir',work/'tmp')
    capture='vllm.model_executor.layers.fused_moe.routed_experts_capturer'
    path(capture,'_TMP_DIR',work/'tmp');path(capture,'_LOCK_FILE_PREFIX',work/'tmp/vllm_routed_experts')
    scalar(capture,'_BUFFER_PREFIX','vllm_routed_experts_buffer')
    if read(capture,'shared_memory') is not modules['multiprocessing.shared_memory']:
        raise ValueError('capturer shared-memory binding changed before ownership scope')
    major,minor=device_capability;arch_minor=str(minor)+('a' if major>=9 else '')
    arch=str(major)+arch_minor
    context=read('flashinfer.jit.core','current_compilation_context')
    if vars(context).get('TARGET_CUDA_ARCHS') != {(major,arch_minor)}:
        raise ValueError('FlashInfer compilation target differs from supplied owned device')
    base=work/'cache/flashinfer-workspace';cachepath=base/'.cache/flashinfer';workspace=cachepath/'0.6.1'/arch
    flashpaths={'FLASHINFER_BASE_DIR':base,'FLASHINFER_CACHE_DIR':cachepath,'FLASHINFER_WORKSPACE_DIR':workspace,'FLASHINFER_JIT_DIR':workspace/'cached_ops','FLASHINFER_GEN_SRC_DIR':workspace/'generated','FLASHINFER_CUBIN_DIR':site/'flashinfer_cubin/cubins','FLASHINFER_AOT_DIR':site/'flashinfer/data/aot','FLASHINFER_DATA':site/'flashinfer/data','FLASHINFER_INCLUDE_DIR':site/'flashinfer/data/include','FLASHINFER_CSRC_DIR':site/'flashinfer/data/csrc'}
    for field,value in flashpaths.items():path('flashinfer.jit.env',field,value)
    path('flashinfer_cubin','CUBIN_DIR',site/'flashinfer_cubin/cubins')
    logger=read('flashinfer.jit.core','logger');handlers=vars(logger).get('handlers')
    if type(handlers) is not list or len(handlers)!=2 or vars(logger).get('parent') is not None:
        raise ValueError('unexpected FlashInfer log handler graph')
    files=[];streams=0
    for handler in handlers:
        if type(handler) is logging.FileHandler and vars(handler).get('baseFilename')==str(workspace/'flashinfer_jit.log'):
            stream=vars(handler).get('stream')
            if not isinstance(stream,io.TextIOBase) or stream.closed or \
               getattr(stream,'name',None) != str(workspace/'flashinfer_jit.log'):
                raise ValueError('active FlashInfer log stream differs from private destination')
            files.append(str(workspace/'flashinfer_jit.log'))
        elif type(handler) is logging.StreamHandler and vars(handler).get('stream') is sys.stderr:streams+=1
        else:raise ValueError('unexpected FlashInfer log destination')
    if len(files)!=1 or streams!=1:raise ValueError('missing FlashInfer private log handler')
    for field,value in {'_API_LOG_LEVEL':0,'_API_LOG_DEST':'stderr','_DUMP_SAFETENSORS':False,'_dump_count':0,'_dump_total_size_bytes':0,'_dump_call_counter':{}}.items():scalar('flashinfer.api_logging',field,value)
    dump=read('flashinfer.api_logging','_DUMP_DIR')
    if type(dump) is not str or len(dump)>4096:raise ValueError('invalid inactive dump path')
    library=read('tvm_ffi._optional_torch_c_dlpack','_LIB');library_path=None
    if library is not None:
        library_path=str(work/'cache/tvm-ffi/libtorch_c_dlpack_addon_torch29-cuda.so')
        if type(library) is not ctypes.CDLL or vars(library).get('_name') != library_path:
            raise ValueError('optional DLPack addon origin differs from private CUDA path')
    # Only primitives created by this function escape; even an empty mutable
    # source field must be detached from its runtime owner.
    flags['flashinfer.api_logging._dump_call_counter']={}
    if any(original_modules.get(name) is not modules.get(name)
           for name in REQUIRED_MODULES + OPTIONAL_MODULES):
        raise ValueError('runtime module was replaced during cache observation')
    return dict(schema_version=1,provenance='MOCK' if test_only else 'RUNTIME_CONFIGURATION',
        test_only=bool(test_only),source_manifest_sha256=digest(snapshot.manifest_bytes),
        module_origins=origins,runtime_versions=versions,effective_paths=paths,settings=flags,
        environment_derived_paths={key:env[key] for key in ('TORCH_EXTENSIONS_DIR','TORCHINDUCTOR_CACHE_DIR','TVM_FFI_CACHE_DIR','CUDA_CACHE_PATH')},
        flashinfer=dict(package_cubin_dir=str(site/'flashinfer_cubin/cubins'),aot_dir=str(site/'flashinfer/data/aot'),
            aot_presence_checked_here=False,architecture_from_caller=[major,minor],jit_log_files=files,
            inactive_dump_dir=dump,dumping_active=False),
        optional_dlpack_library=library_path,optional_dlpack_none_reason='UNDETERMINED' if library is None else None,
        inference_imports_performed=False,all_runtime_binaries_authenticated=False,scientific_validation_passed=False)

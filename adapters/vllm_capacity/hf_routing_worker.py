"""Strict protocol helpers for the planned owned HF routing worker.

Importing this module does not import an inference runtime or create caches.
These helpers alone perform no model launch and establish no capture origin.
The parent runner must validate the complete HF snapshot and resource gate before
the runtime worker is enabled. Its execution/cleanup entry point is still absent.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(name + ' must be a positive integer')
    return value


def _tokens(values, count, vocab, name):
    if type(values) not in (list, tuple) or len(values) != count or \
       any(type(token) is not int or not 0 <= token < vocab for token in values):
        raise ValueError(name + ' must contain exactly the requested integer token IDs')
    return list(values)


def make_protocol(receipt, config, prompt_token_ids):
    """Derive one fixed control from metadata already accepted by the parent."""
    summary = receipt['summary']
    layers, experts, top_k = [_positive(summary[key], key)
                             for key in ('layers', 'experts_per_layer', 'top_k')]
    for key, expected in (('num_hidden_layers', layers), ('num_experts', experts),
                          ('num_experts_per_tok', top_k)):
        if _positive(config[key], key) != expected:
            raise ValueError('config and receipt routing geometry differ')
    if top_k > experts or 3 * layers * experts + 9 * layers + 3 > 25000:
        raise ValueError('unsupported routing geometry')
    vocab = _positive(config['vocab_size'], 'vocab_size')
    prompt = _tokens(prompt_token_ids, 32, vocab, 'prompt')
    if (receipt['evidence'], receipt['provenance']) not in (
            ('TEST_ONLY', 'MOCK'), ('CHECKPOINT_METADATA', 'CHECKPOINT_METADATA')):
        raise ValueError('unrecognized metadata evidence')
    return dict(schema_version=1, prompt_token_ids=prompt, input_len=32, output_len=8,
        vocab_size=vocab, layers=layers, experts=experts, top_k=top_k,
        route_shape=[39, layers, top_k], source_kind=receipt['evidence'],
        provenance=receipt['provenance'],
        expected_counts=dict(event_count=39 * layers, prefill_event_count=32 * layers,
            decode_event_count=7 * layers, expert_access_count=39 * layers * top_k,
            tensor_access_count=39 * layers * top_k * 3))


def llm_arguments(checkpoint, capture_enabled):
    if type(capture_enabled) is not bool or not isinstance(checkpoint, str) or not checkpoint:
        raise ValueError('invalid worker configuration')
    return dict(model=checkpoint, tokenizer=checkpoint,
        runner='generate', dtype='bfloat16', quantization=None,
        load_format='safetensors', safetensors_load_strategy='lazy',
        trust_remote_code=False, skip_tokenizer_init=True,
        tensor_parallel_size=1, pipeline_parallel_size=1, data_parallel_size=1,
        distributed_executor_backend='uni', enable_expert_parallel=False, seed=0,
        attention_config={'backend': 'TRITON_ATTN'},
        enforce_eager=True, enable_prefix_caching=False,
        enable_chunked_prefill=False, async_scheduling=False,
        speculative_config=None, max_model_len=64,
        max_num_seqs=1, max_num_batched_tokens=64,
        kv_cache_memory_bytes=64 * 1024 * 1024,
        gpu_memory_utilization=0.9, swap_space=0, cpu_offload_gb=0,
        generation_config='vllm', disable_log_stats=True, use_tqdm_on_load=False,
        enable_return_routed_experts=capture_enabled)


def sampling_arguments(final_output_kind):
    # The enum is supplied only after the worker's controlled lazy runtime import.
    return dict(n=1, temperature=0.0, top_p=1.0, top_k=-1, seed=0,
        max_tokens=8, min_tokens=8, ignore_eos=True,
        stop=None, stop_token_ids=None, detokenize=False,
        truncate_prompt_tokens=None, output_kind=final_output_kind)


def make_environment(work, gpu_uuid, inherited):
    """Return the allowlist after a parent verifies one physical GPU at NVML index 0.

    gpu_uuid remains the externally bound identity input; topology and device
    identity authentication belong to the parent and later worker gates. No
    directory, cache, environment or driver is changed here.
    """
    work = Path(os.path.abspath(work))
    if work.resolve() != work or not work.is_relative_to(ROOT) or work == ROOT:
        raise ValueError('worker caches must stay in a private project directory')
    if not isinstance(gpu_uuid, str) or not gpu_uuid.startswith('GPU-') or ',' in gpu_uuid:
        raise ValueError('one physical GPU UUID is required')
    platform = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ')
    env = {key: inherited[key] for key in platform if key in inherited}
    env.update(CUDA_VISIBLE_DEVICES='0', CUDA_DEVICE_ORDER='PCI_BUS_ID',
        PYTORCH_NVML_BASED_CUDA_CHECK='1', TORCHINDUCTOR_COMPILE_THREADS='1',
        VLLM_PLUGINS='', TVM_FFI_DISABLE_TORCH_C_DLPACK='1',
        VLLM_ENABLE_V1_MULTIPROCESSING='0',
        VLLM_USE_FLASHINFER_MOE_FP16='0', VLLM_USE_FLASHINFER_SAMPLER='0',
        VLLM_NO_USAGE_STATS='1', VLLM_DO_NOT_TRACK='1', DO_NOT_TRACK='1',
        HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
        HF_HUB_DISABLE_IMPLICIT_TOKEN='1', PYTHONNOUSERSITE='1',
        PYTHONDONTWRITEBYTECODE='1', TOKENIZERS_PARALLELISM='false',
        FLASHINFER_DISABLE_JIT='1', FLASHINFER_LOGLEVEL='0', FLASHINFER_LOGDEST='stderr',
        FLASHINFER_AUTOTUNER_LOAD_FROM_FILE='0')
    roots = dict(XDG_CACHE_HOME='cache', VLLM_CACHE_ROOT='cache/vllm',
        VLLM_CONFIG_ROOT='config/vllm', VLLM_ASSETS_CACHE='cache/vllm/assets',
        HF_HOME='cache/hf', HF_HUB_CACHE='cache/hf/hub', HF_XET_CACHE='cache/hf/xet',
        HF_MODULES_CACHE='cache/hf/modules', TORCH_HOME='cache/torch',
        TORCH_EXTENSIONS_DIR='cache/torch-extensions', TORCHINDUCTOR_CACHE_DIR='cache/inductor',
        TVM_FFI_CACHE_DIR='cache/tvm-ffi', TRITON_HOME='cache/triton-home',
        TRITON_CACHE_DIR='cache/triton', TRITON_DUMP_DIR='cache/triton-dump',
        TRITON_OVERRIDE_DIR='cache/triton-override',
        FLASHINFER_WORKSPACE_BASE='cache/flashinfer-workspace', CUDA_CACHE_PATH='cache/cuda',
        TMPDIR='tmp')
    env.update({key: str(work / suffix) for key, suffix in roots.items()})
    return env


def serialize_return(returned, protocol, *, capture_enabled):
    """Copy actual API outputs; callers cannot turn serialization into origin proof."""
    if type(capture_enabled) is not bool or type(returned) is not list or len(returned) != 1:
        raise ValueError('expected exactly one returned request')
    request = returned[0]
    if not isinstance(request.request_id, str) or not request.request_id or request.finished is not True:
        raise ValueError('request identity/finished state mismatch')
    if type(request.outputs) is not list or len(request.outputs) != 1:
        raise ValueError('expected exactly one completion')
    completion = request.outputs[0]
    if type(completion.index) is not int or completion.index != 0 or \
       completion.finish_reason != 'length' or completion.stop_reason is not None:
        raise ValueError('unexpected completion index/finish/stop reason')
    cached = request.num_cached_tokens
    if cached is not None and (type(cached) is not int or cached != 0):
        raise ValueError('prefix cache was disabled for this control')
    prompt = _tokens(request.prompt_token_ids, 32, protocol['vocab_size'], 'returned prompt')
    if prompt != protocol['prompt_token_ids']:
        raise ValueError('returned prompt differs from frozen request')
    output = _tokens(completion.token_ids, 8, protocol['vocab_size'], 'returned output')
    array = completion.routed_experts
    if not capture_enabled:
        if array is not None:
            raise ValueError('native control unexpectedly returned routes')
        shape = dtype = None
    else:
        import numpy as np
        if type(array) is not np.ndarray or list(array.shape) != protocol['route_shape'] or array.dtype.kind not in 'iu':
            raise ValueError('returned route array dtype/shape mismatch')
        # Bounds above prevent copying an unexpected large/object array. All
        # semantic checks below inspect the private bytes that will be saved.
        array = array.copy()
        if list(array.shape) != protocol['route_shape'] or array.dtype.kind not in 'iu':
            raise ValueError('returned route metadata changed while copying')
        if np.any(array < 0) or np.any(array >= protocol['experts']) or \
           np.any(np.diff(np.sort(array, axis=-1), axis=-1) == 0):
            raise ValueError('returned top-k expert IDs are invalid or duplicated')
        shape, dtype = list(array.shape), str(array.dtype)
    raw = dict(schema_version=1, request_id=request.request_id, prompt_token_ids=prompt,
        output_index=completion.index, output_token_ids=output, finished=request.finished,
        finish_reason=completion.finish_reason, stop_reason=completion.stop_reason,
        num_cached_tokens=cached, route_shape=shape, route_dtype=dtype,
        source_kind=protocol['source_kind'], provenance=protocol['provenance'],
        origin_validation='UNVALIDATED_RETURN_SERIALIZATION', scientific_validation_passed=False)
    return raw, array

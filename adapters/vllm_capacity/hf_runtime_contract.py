"""Private observation of the pinned vLLM HF control, after guarded construction.

This module imports no inference runtime until explicitly called. It launches
nothing, writes nothing, and proves neither capture origin nor kernel execution.
The worker still needs source/import/cache verification and owned cleanup.
"""
from __future__ import annotations

from importlib import import_module
from copy import deepcopy
import math


def _runtime_symbols():
    modules = {
        'vllm.entrypoints.llm': ('LLM',),
        'vllm.v1.engine.llm_engine': ('LLMEngine',),
        'vllm.v1.engine.core_client': ('InprocClient',),
        'vllm.v1.engine.core': ('EngineCore',),
        'vllm.v1.executor.uniproc_executor': ('UniProcExecutor',),
        'vllm.v1.worker.gpu_worker': ('Worker',),
        'vllm.v1.worker.gpu_model_runner': ('GPUModelRunner',),
        'vllm.model_executor.models.qwen3_moe': ('Qwen3MoeForCausalLM',),
        'vllm.v1.sample.ops.topk_topp_sampler': ('TopKTopPSampler',),
        'vllm.v1.attention.backends.triton_attn': ('TritonAttentionBackend', 'TritonAttentionImpl'),
        'vllm.v1.attention.backends.registry': ('AttentionBackendEnum',),
        'vllm.attention.layer': ('Attention',),
        'vllm.model_executor.layers.fused_moe.shared_fused_moe': ('SharedFusedMoE',),
        'vllm.model_executor.layers.fused_moe.unquantized_fused_moe_method': ('UnquantizedFusedMoEMethod',),
        'vllm.model_executor.layers.fused_moe.oracle.unquantized': ('UnquantizedMoeBackend',),
        'vllm.model_executor.layers.fused_moe.modular_kernel': ('FusedMoEModularKernel',),
        'vllm.model_executor.layers.fused_moe.prepare_finalize': ('MoEPrepareAndFinalizeNoEP',),
        'vllm.model_executor.layers.fused_moe.fused_moe': ('TritonExperts',),
        'vllm.config.compilation': ('CompilationMode', 'CUDAGraphMode'),
        'vllm.config.vllm': ('OptimizationLevel',),
        'vllm.v1.kv_cache_interface': ('FullAttentionSpec',),
        'torch': ('bfloat16',),
    }
    return {name: getattr(import_module(module), name)
            for module, names in modules.items() for name in names}


def _require(condition, message):
    if not condition:
        raise ValueError('HF runtime contract: ' + message)


def _equal(value, expected):
    if type(expected) is float:
        return type(value) in (int, float) and math.isfinite(value) and value == expected
    return type(value) is type(expected) and value == expected


def _fields(obj, expected):
    result = {}
    for name, wanted in expected.items():
        actual = getattr(obj, name)
        _require(_equal(actual, wanted), name + ' differs from fixed control')
        result[name] = deepcopy(actual)
    return result


def _positive(value, name):
    _require(type(value) is int and value > 0, name + ' must be a positive integer')
    return value


def _class(obj, cls):
    _require(type(obj) is cls, 'unexpected concrete ' + cls.__name__)
    return cls.__module__ + '.' + cls.__qualname__


def _bound_method(obj, attr, cls, name):
    method = getattr(obj, attr)
    _require(getattr(method, '__self__', None) is obj and
             getattr(method, '__func__', None) is getattr(cls, name),
             attr + ' is not the fixed native binding')


def observe_runtime(llm, checkpoint, capture_enabled, protocol, hf_config):
    """Inspect real constructed objects; CPU tests replace lazy imports only.

    No factory, routes, module path, or backend override is accepted. The future
    owned worker must call this after its import/source and GPU resource gates.
    """
    try:
        return _observe(llm, checkpoint, capture_enabled, protocol, hf_config)
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError('HF runtime contract: missing/incompatible runtime field') from error


def _observe(llm, checkpoint, capture_enabled, protocol, hf):
    _require(type(capture_enabled) is bool and type(checkpoint) is str and bool(checkpoint),
             'invalid requested control')
    symbols = _runtime_symbols()
    classes = {'llm': _class(llm, symbols['LLM'])}
    engine = llm.llm_engine
    classes['engine'] = _class(engine, symbols['LLMEngine'])
    client = engine.engine_core
    classes['client'] = _class(client, symbols['InprocClient'])
    core = client.engine_core
    classes['core'] = _class(core, symbols['EngineCore'])
    executor = core.model_executor
    classes['executor'] = _class(executor, symbols['UniProcExecutor'])
    _require(engine.model_executor is executor, 'engine executor alias mismatch')
    worker = executor.driver_worker.worker
    classes['worker'] = _class(worker, symbols['Worker'])
    runner = worker.model_runner
    classes['runner'] = _class(runner, symbols['GPUModelRunner'])
    model = runner.get_model()
    classes['model'] = _class(model, symbols['Qwen3MoeForCausalLM'])
    # get_model unwraps graph/microbatch wrappers in this installed runtime.
    _require(runner.model is model, 'actual execution model has an unexpected wrapper')
    cfg = engine.vllm_config
    for owner in (core, executor, executor.driver_worker, worker, runner):
        _require(owner.vllm_config is cfg, 'execution-facing config identity mismatch')
    for owner in (executor, worker, runner):
        for section in ('model_config', 'cache_config', 'parallel_config', 'scheduler_config'):
            _require(getattr(owner, section) is getattr(cfg, section), section + ' alias mismatch')
    for owner in (worker, runner):
        for section in ('load_config', 'compilation_config', 'speculative_config'):
            _require(getattr(owner, section) is getattr(cfg, section), section + ' alias mismatch')
    settings = dict(model=_fields(cfg.model_config, dict(model=checkpoint, tokenizer=checkpoint,
        runner='generate', runner_type='generate', quantization=None, trust_remote_code=False,
        skip_tokenizer_init=True, seed=0, max_model_len=64, enforce_eager=True,
        enable_return_routed_experts=capture_enabled, generation_config='vllm',
        override_generation_config={})),
        loader=_fields(cfg.load_config, dict(load_format='safetensors', safetensors_load_strategy='lazy',
            download_dir=None, model_loader_extra_config={}, use_tqdm_on_load=False)),
        parallel=_fields(cfg.parallel_config, dict(tensor_parallel_size=1, pipeline_parallel_size=1,
            data_parallel_size=1, data_parallel_rank=0, distributed_executor_backend='uni',
            enable_expert_parallel=False, enable_eplb=False, enable_dbo=False, world_size=1)),
        scheduler=_fields(cfg.scheduler_config, dict(max_num_seqs=1, max_num_batched_tokens=64,
            enable_chunked_prefill=False, async_scheduling=False)),
        cache=_fields(cfg.cache_config, dict(kv_cache_memory_bytes=64 << 20, cache_dtype='auto',
            enable_prefix_caching=False, gpu_memory_utilization=0.9, swap_space=0.0, cpu_offload_gb=0.0)))
    dtype = symbols['bfloat16']
    _require(cfg.model_config.dtype is dtype and runner.dtype is dtype and runner.kv_cache_dtype is dtype,
             'model and resolved KV dtype must be BF16')
    settings['model']['dtype'] = settings['cache']['resolved_dtype'] = 'torch.bfloat16'
    geometry = {key: _positive(hf[key], key) for key in (
        'num_hidden_layers', 'num_experts', 'num_experts_per_tok', 'vocab_size', 'hidden_size',
        'num_attention_heads', 'num_key_value_heads', 'head_dim')}
    _require(hf['model_type'] == 'qwen3_moe', 'unsupported HF model type')
    geometry['model_type'] = 'qwen3_moe'
    for key, target in (('layers', 'num_hidden_layers'), ('experts', 'num_experts'),
                        ('top_k', 'num_experts_per_tok'), ('vocab_size', 'vocab_size')):
        _require(_equal(protocol[key], geometry[target]), 'protocol geometry mismatch')
    _fields(cfg.model_config.hf_config, geometry)
    _fields(cfg.model_config.hf_text_config, geometry)
    settings['geometry'] = geometry
    _require(cfg.optimization_level is symbols['OptimizationLevel'].O0 and
             cfg.compilation_config.mode is symbols['CompilationMode'].NONE and
             cfg.compilation_config.cudagraph_mode is symbols['CUDAGraphMode'].NONE,
             'eager compilation/graph mode mismatch')
    _require(cfg.attention_config.backend is symbols['AttentionBackendEnum'].TRITON_ATTN and
             cfg.speculative_config is None and cfg.quant_config is None,
             'attention/speculation/quantization mismatch')
    _require(type(cfg.compilation_config.backend) is str and
             type(cfg.compilation_config.custom_ops) is list and
             all(type(op) is str for op in cfg.compilation_config.custom_ops),
             'invalid compilation observations')
    settings['compilation'] = dict(optimization_level='O0', mode='NONE', cudagraph_mode='NONE',
        backend=cfg.compilation_config.backend, custom_ops=list(cfg.compilation_config.custom_ops))
    _require(type(cfg.instance_id) is str and cfg.instance_id.isascii() and cfg.instance_id.isdecimal(),
             'instance ID is not the installed generated numeric identity')
    sampler = runner.sampler.topk_topp_sampler
    sampler_class = _class(sampler, symbols['TopKTopPSampler'])
    _bound_method(sampler, 'forward', symbols['TopKTopPSampler'], 'forward_native')
    layers = model.model.layers
    _require(len(layers) == geometry['num_hidden_layers'], 'decoder layer coverage mismatch')
    observed = []; attention_names = []
    for index, decoder in enumerate(layers):
        attn, moe = decoder.self_attn.attn, decoder.mlp.experts
        attn_class = _class(attn, symbols['Attention'])
        _require(attn.get_attn_backend() is symbols['TritonAttentionBackend'] and
                 symbols['TritonAttentionBackend'].get_name() == 'TRITON_ATTN', 'attention backend mismatch')
        impl = _class(attn.impl, symbols['TritonAttentionImpl'])
        _require(attn.dtype is dtype and attn.kv_cache_torch_dtype is dtype,
                 'attention dtype mismatch')
        attn_info = _fields(attn, dict(head_size=geometry['head_dim'],
            num_heads=geometry['num_attention_heads'], num_kv_heads=geometry['num_key_value_heads'],
            kv_cache_dtype='auto', layer_name=f'model.layers.{index}.self_attn.attn'))
        attention_names.append(attn_info['layer_name'])
        attn_info.update(concrete_class=attn_class, implementation=impl, backend='TRITON_ATTN', dtype='torch.bfloat16')
        moe_class = _class(moe, symbols['SharedFusedMoE'])
        _require(moe.vllm_config is cfg, 'MoE config alias mismatch')
        _require(callable(moe.capture) if capture_enabled else moe.capture is None,
                 'MoE capture callback configuration mismatch')
        moe_info = _fields(moe, dict(use_ep=False, dp_size=1, layer_id=index,
            layer_name=f'model.layers.{index}.mlp.experts', global_num_experts=geometry['num_experts'],
            local_num_experts=geometry['num_experts'], top_k=geometry['num_experts_per_tok']))
        _fields(moe.moe_parallel_config, dict(use_all2all_kernels=False))
        method = moe.quant_method
        method_class = _class(method, symbols['UnquantizedFusedMoEMethod'])
        _require(method.unquantized_backend is symbols['UnquantizedMoeBackend'].TRITON, 'MoE backend mismatch')
        _bound_method(method, '_forward_method', symbols['UnquantizedFusedMoEMethod'], 'forward_cuda')
        kernel = method.kernel
        moe_info.update(concrete_class=moe_class, method=method_class, backend='TRITON',
            capture_callback_configured=capture_enabled,
            kernel=_class(kernel, symbols['FusedMoEModularKernel']),
            prepare_finalize=_class(kernel.prepare_finalize, symbols['MoEPrepareAndFinalizeNoEP']),
            fused_experts=_class(kernel.fused_experts, symbols['TritonExperts']))
        observed.append(dict(layer=index, attention=attn_info, moe=moe_info))
    layout = runner.kv_cache_config
    blocks = _positive(layout.num_blocks, 'KV blocks')
    block_size = _positive(cfg.cache_config.block_size, 'KV block size')
    # BlockPool reserves one null block; it cannot hold request tokens.
    _require(_equal(cfg.cache_config.num_gpu_blocks, blocks) and (blocks - 1) * block_size >= 64,
             'KV block count cannot hold fixed context')
    _require(len(layout.kv_cache_groups) == 1, 'only one full-attention KV group is supported')
    group = layout.kv_cache_groups[0]
    _require(group.layer_names == attention_names, 'KV group layer coverage/order mismatch')
    spec = group.kv_cache_spec
    spec_class = _class(spec, symbols['FullAttentionSpec'])
    spec_info = _fields(spec, dict(block_size=block_size, num_kv_heads=geometry['num_key_value_heads'],
        head_size=geometry['head_dim'], head_size_v=geometry['head_dim'],
        page_size_padded=None, sliding_window=None, attention_chunk_size=None))
    page_bytes = 4 * block_size * geometry['num_key_value_heads'] * geometry['head_dim']
    _require(spec.dtype is dtype and _equal(spec.page_size_bytes, page_bytes), 'KV page dtype/size mismatch')
    _require(len(layout.kv_cache_tensors) == len(attention_names), 'KV tensor count mismatch')
    tensors = []
    for tensor, name in zip(layout.kv_cache_tensors, attention_names):
        _require(type(tensor.shared_by) is list and tensor.shared_by == [name] and
                 _equal(tensor.size, blocks * page_bytes), 'KV tensor allocation/sharing mismatch')
        tensors.append(dict(size=tensor.size, shared_by=[name]))
    allocated = sum(tensor['size'] for tensor in tensors)
    _require(allocated <= cfg.cache_config.kv_cache_memory_bytes, 'KV allocation exceeds fixed budget')
    settings['cache'].update(block_size=block_size, num_gpu_blocks=blocks)
    spec_info.update(concrete_class=spec_class, dtype='torch.bfloat16', page_size_bytes=page_bytes)
    return dict(schema_version=1, evidence='RUNTIME_CONFIGURATION_OBSERVATION',
        scientific_validation_passed=False, classes=classes, settings=settings, instance_id=cfg.instance_id,
        layers=observed, kv_layout=dict(num_blocks=blocks, reserved_null_blocks=1,
            usable_token_capacity=(blocks - 1) * block_size, allocated_bytes=allocated,
            tensors=tensors, group=dict(layer_names=list(attention_names), spec=spec_info)),
        sampler=dict(concrete_class=sampler_class, bound_forward='forward_native',
            execution_boundary='The greedy argmax path can return before this sampler; binding is not execution proof.'))

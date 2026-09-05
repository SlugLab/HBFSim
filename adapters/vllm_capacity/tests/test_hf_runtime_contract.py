"""CPU object-graph controls; these fixtures never import the inference runtime."""
import copy
from enum import Enum, IntEnum
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'adapters/vllm_capacity'))
import hf_runtime_contract as contract


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        names = ('LLM LLMEngine InprocClient EngineCore UniProcExecutor Worker GPUModelRunner '
                 'Qwen3MoeForCausalLM TopKTopPSampler TritonAttentionBackend TritonAttentionImpl '
                 'Attention SharedFusedMoE UnquantizedFusedMoEMethod FusedMoEModularKernel '
                 'MoEPrepareAndFinalizeNoEP TritonExperts FullAttentionSpec').split()
        self.types = {name: type(name, (), {}) for name in names}
        self.types.update(bfloat16=object(),
            UnquantizedMoeBackend=Enum('UnquantizedMoeBackend', 'TRITON FLASHINFER'),
            AttentionBackendEnum=Enum('AttentionBackendEnum', 'TRITON_ATTN FLASHINFER'),
            CompilationMode=IntEnum('CompilationMode', {'NONE': 0, 'VLLM_COMPILE': 3}),
            CUDAGraphMode=Enum('CUDAGraphMode', {'NONE': 0, 'FULL': 2}),
            OptimizationLevel=IntEnum('OptimizationLevel', {'O0': 0, 'O2': 2}))
        self.types['TopKTopPSampler'].forward_native = lambda self: None
        self.types['UnquantizedFusedMoEMethod'].forward_cuda = lambda self: None
        self.types['TritonAttentionBackend'].get_name = staticmethod(lambda: 'TRITON_ATTN')
        self.hf = dict(model_type='qwen3_moe', num_hidden_layers=2, num_experts=4,
            num_experts_per_tok=2, vocab_size=4096, hidden_size=256,
            num_attention_heads=4, num_key_value_heads=2, head_dim=64)
        self.protocol = dict(layers=2, experts=4, top_k=2, vocab_size=4096)
        dtype = self.types['bfloat16']
        model_cfg = NS(model='/frozen/checkpoint', tokenizer='/frozen/checkpoint', runner='generate',
            runner_type='generate', dtype=dtype, quantization=None, trust_remote_code=False,
            skip_tokenizer_init=True, seed=0, max_model_len=64, enforce_eager=True,
            enable_return_routed_experts=True, generation_config='vllm', override_generation_config={},
            hf_config=NS(**self.hf), hf_text_config=NS(**self.hf))
        self.cfg = NS(model_config=model_cfg,
            load_config=NS(load_format='safetensors', safetensors_load_strategy='lazy', download_dir=None,
                model_loader_extra_config={}, use_tqdm_on_load=False),
            parallel_config=NS(tensor_parallel_size=1, pipeline_parallel_size=1, data_parallel_size=1,
                data_parallel_rank=0, distributed_executor_backend='uni', enable_expert_parallel=False,
                enable_eplb=False, enable_dbo=False, world_size=1),
            scheduler_config=NS(max_num_seqs=1, max_num_batched_tokens=64, enable_chunked_prefill=False,
                async_scheduling=False),
            cache_config=NS(kv_cache_memory_bytes=64 << 20, block_size=16, num_gpu_blocks=5,
                cache_dtype='auto', enable_prefix_caching=False, gpu_memory_utilization=0.9,
                swap_space=0.0, cpu_offload_gb=0.0),
            optimization_level=self.types['OptimizationLevel'].O0,
            compilation_config=NS(mode=self.types['CompilationMode'].NONE,
                cudagraph_mode=self.types['CUDAGraphMode'].NONE, backend='inductor', custom_ops=['all']),
            attention_config=NS(backend=self.types['AttentionBackendEnum'].TRITON_ATTN),
            speculative_config=None, quant_config=None, instance_id='1234567890')
        self.layers = []
        for index in range(2):
            attention = self.new('Attention', dtype=dtype, head_size=64, num_heads=4, num_kv_heads=2,
                kv_cache_dtype='auto', kv_cache_torch_dtype=dtype,
                impl=self.new('TritonAttentionImpl'), layer_name=f'model.layers.{index}.self_attn.attn')
            attention.get_attn_backend = lambda: self.types['TritonAttentionBackend']
            method = self.new('UnquantizedFusedMoEMethod',
                unquantized_backend=self.types['UnquantizedMoeBackend'].TRITON,
                kernel=self.new('FusedMoEModularKernel', prepare_finalize=self.new('MoEPrepareAndFinalizeNoEP'),
                    fused_experts=self.new('TritonExperts')))
            method._forward_method = method.forward_cuda
            moe = self.new('SharedFusedMoE', quant_method=method, use_ep=False, dp_size=1,
                vllm_config=self.cfg, capture=lambda ids: None,
                moe_parallel_config=NS(use_all2all_kernels=False), layer_id=index,
                layer_name=f'model.layers.{index}.mlp.experts', global_num_experts=4,
                local_num_experts=4, top_k=2)
            self.layers.append(NS(self_attn=NS(attn=attention), mlp=NS(experts=moe)))
        self.model = self.new('Qwen3MoeForCausalLM', model=NS(layers=self.layers))
        sampler = self.new('TopKTopPSampler'); sampler.forward = sampler.forward_native
        layer_names = [layer.self_attn.attn.layer_name for layer in self.layers]
        spec = self.new('FullAttentionSpec', block_size=16, num_kv_heads=2, head_size=64,
            head_size_v=64, dtype=dtype, page_size_padded=None, sliding_window=None,
            attention_chunk_size=None, page_size_bytes=8192)
        self.runner = self.new('GPUModelRunner', get_model=lambda: self.model, model=self.model,
            sampler=NS(topk_topp_sampler=sampler), kv_cache_dtype=dtype,
            kv_cache_config=NS(num_blocks=5,
                kv_cache_tensors=[NS(size=40960, shared_by=[name]) for name in layer_names],
                kv_cache_groups=[NS(layer_names=layer_names, kv_cache_spec=spec)]))
        executor = self.new('UniProcExecutor', driver_worker=NS(worker=self.new('Worker', model_runner=self.runner)))
        core = self.new('EngineCore', model_executor=executor)
        engine = self.new('LLMEngine', vllm_config=self.cfg, model_executor=executor,
            engine_core=self.new('InprocClient', engine_core=core))
        self.llm = self.new('LLM', llm_engine=engine)
        for owner in (core, executor, executor.driver_worker, executor.driver_worker.worker, self.runner):
            owner.vllm_config = self.cfg
        for owner in (executor, executor.driver_worker.worker, self.runner):
            for key in ('model_config', 'cache_config', 'parallel_config', 'scheduler_config'):
                setattr(owner, key, getattr(self.cfg, key))
        for owner in (executor.driver_worker.worker, self.runner):
            for key in ('load_config', 'compilation_config', 'speculative_config'):
                setattr(owner, key, getattr(self.cfg, key))
        self.runner.dtype = dtype

    def new(self, name, **attrs):
        value = self.types[name](); value.__dict__.update(attrs); return value

    def observe(self):
        with mock.patch.object(contract, '_runtime_symbols', return_value=self.types):
            return contract.observe_runtime(self.llm, '/frozen/checkpoint', True, self.protocol, self.hf)

    def test_actual_backend_and_layout_are_serializable_configuration_evidence_only(self):
        report = self.observe()
        json.dumps(report, allow_nan=False)
        self.assertEqual(len(report['layers']), 2)
        self.assertEqual(report['kv_layout']['allocated_bytes'], 81920)
        self.assertEqual(report['layers'][1]['moe']['backend'], 'TRITON')
        self.assertFalse(report['scientific_validation_passed'])
        self.assertEqual(report['evidence'], 'RUNTIME_CONFIGURATION_OBSERVATION')
        self.assertIn('greedy', report['sampler']['execution_boundary'])

    def test_requested_flags_cannot_hide_wrong_runtime_backend(self):
        for change in ('attention', 'moe', 'wrapped_moe', 'kernel', 'prepare', 'sampler', 'dispatch'):
            self.setUp()
            attn = self.layers[1].self_attn.attn; method = self.layers[1].mlp.experts.quant_method
            if change == 'attention': attn.impl = NS()
            elif change == 'moe': method.unquantized_backend = self.types['UnquantizedMoeBackend'].FLASHINFER
            elif change == 'wrapped_moe': self.layers[1].mlp.experts.quant_method = NS(old_quant_method=method)
            elif change == 'kernel': method.kernel.fused_experts = NS()
            elif change == 'prepare': method.kernel.prepare_finalize = NS()
            elif change == 'sampler': self.runner.sampler.topk_topp_sampler.forward = lambda: None
            else: method._forward_method = lambda: None
            with self.subTest(change=change), self.assertRaises(ValueError): self.observe()

    def test_actual_config_and_model_geometry_must_match_frozen_inputs(self):
        mutations = [('model_config', 'dtype', 'float16'), ('model_config', 'enable_return_routed_experts', False),
            ('model_config', 'skip_tokenizer_init', False), ('model_config', 'seed', True),
            ('load_config', 'load_format', 'auto'), ('parallel_config', 'world_size', 2),
            ('scheduler_config', 'async_scheduling', True), ('cache_config', 'enable_prefix_caching', True)]
        for owner, name, value in mutations:
            self.setUp(); setattr(getattr(self.cfg, owner), name, value)
            with self.subTest(name=name), self.assertRaises(ValueError): self.observe()
        self.setUp(); self.cfg.model_config.hf_text_config.num_experts = 8
        with self.assertRaises(ValueError): self.observe()
        self.setUp(); del self.cfg.load_config.safetensors_load_strategy
        with self.assertRaises(ValueError): self.observe()

    def test_layer_coverage_kv_shape_budget_and_object_graph_cannot_be_substituted(self):
        for change in ('layer_count', 'duplicate', 'kv_bytes', 'kv_dtype', 'kv_names', 'blocks', 'executor'):
            self.setUp(); layout = self.runner.kv_cache_config
            if change == 'layer_count': self.layers.pop()
            elif change == 'duplicate': self.layers[1].mlp.experts.layer_id = 0
            elif change == 'kv_bytes': layout.kv_cache_tensors[0].size = 64 << 20
            elif change == 'kv_dtype': self.runner.kv_cache_dtype = 'float16'
            elif change == 'kv_names': layout.kv_cache_groups[0].layer_names = ['unknown']
            elif change == 'blocks': layout.num_blocks = 3
            else: self.llm.llm_engine.model_executor = NS()
            with self.subTest(change=change), self.assertRaises(ValueError): self.observe()

    def test_import_alone_has_no_runtime_side_effect(self):
        code = 'import sys;sys.path.insert(0,sys.argv[1]);import hf_runtime_contract;assert not any(k in sys.modules for k in ("vllm","torch","numpy","flashinfer"))'
        result = subprocess.run([sys.executable, '-c', code, str(ROOT/'adapters/vllm_capacity')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_raw_execution_model_cannot_hide_a_wrapper_behind_get_model(self):
        self.runner.model = NS(model=self.model)
        with self.assertRaises(ValueError): self.observe()

    def test_null_block_cannot_be_counted_as_usable_context_capacity(self):
        self.cfg.cache_config.num_gpu_blocks = self.runner.kv_cache_config.num_blocks = 4
        for tensor in self.runner.kv_cache_config.kv_cache_tensors: tensor.size = 32768
        with self.assertRaises(ValueError): self.observe()

    def test_report_does_not_retain_mutable_runtime_config_references(self):
        report = self.observe()
        before = json.dumps(report, sort_keys=True)
        self.cfg.model_config.override_generation_config['temperature'] = 2.0
        self.cfg.load_config.model_loader_extra_config['unexpected'] = True
        self.assertEqual(json.dumps(report, sort_keys=True), before)

    def test_execution_facing_configuration_aliases_must_be_bound(self):
        for change in ('model', 'scheduler', 'dtype', 'core'):
            self.setUp()
            if change == 'model':
                self.runner.model_config = copy.copy(self.cfg.model_config)
                self.runner.model_config.enable_return_routed_experts = False
            elif change == 'scheduler':
                self.runner.scheduler_config = copy.copy(self.cfg.scheduler_config)
                self.runner.scheduler_config.async_scheduling = True
            elif change == 'dtype': self.runner.dtype = 'torch.float16'
            else: self.llm.llm_engine.engine_core.engine_core.vllm_config = NS()
            with self.subTest(change=change), self.assertRaises(ValueError): self.observe()

    def test_each_moe_layer_must_have_requested_capture_configuration(self):
        self.layers[1].mlp.experts.capture = None
        with self.assertRaises(ValueError): self.observe()


if __name__ == '__main__': unittest.main()

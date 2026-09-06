"""CPU return/config controls; no installed inference runtime is imported."""
import copy
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'adapters/vllm_capacity'))
import hf_routing_worker as worker


class HFWorkerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.receipt = dict(summary=dict(layers=2, experts_per_layer=4, top_k=2),
                            evidence='TEST_ONLY', provenance='MOCK')
        self.config = dict(num_hidden_layers=2, num_experts=4, num_experts_per_tok=2,
                           vocab_size=4096)
        self.prompt = list(range(1000, 1032))
        self.protocol = worker.make_protocol(self.receipt, self.config, self.prompt)

    def returned(self, *, capture=True):
        routes = np.array([[[(t + l) % 4, (t + l + 1) % 4] for l in range(2)]
                           for t in range(39)], dtype=np.int64) if capture else None
        completion = SimpleNamespace(index=0, token_ids=list(range(2000, 2008)),
                                     finish_reason='length', stop_reason=None, routed_experts=routes)
        return [SimpleNamespace(request_id='actual-request-29', prompt_token_ids=self.prompt[:],
                                outputs=[completion], finished=True, num_cached_tokens=0)]

    def test_geometry_requires_observed_fields_without_defaults(self):
        self.assertEqual(self.protocol['route_shape'], [39, 2, 2])
        self.assertEqual(self.protocol['expected_counts'], dict(event_count=78,
            prefill_event_count=64, decode_event_count=14, expert_access_count=156,
            tensor_access_count=468))
        for change in ('missing', 'boolean', 'mismatch', 'short', 'token_bool', 'token_oob'):
            receipt = copy.deepcopy(self.receipt); config = self.config.copy(); prompt = self.prompt[:]
            if change == 'missing': del receipt['summary']['layers']
            elif change == 'boolean': receipt['summary']['layers'] = True
            elif change == 'mismatch': config['num_experts'] = 128
            elif change == 'short': prompt.pop()
            elif change == 'token_bool': prompt[0] = True
            else: prompt[0] = config['vocab_size']
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                worker.make_protocol(receipt, config, prompt)

    def test_actual_return_ids_and_integer_array_are_copied_without_invented_final_route(self):
        returned = self.returned()
        raw, array = worker.serialize_return(returned, self.protocol, capture_enabled=True)
        self.assertEqual(raw['request_id'], 'actual-request-29')
        self.assertEqual(raw['route_shape'], [39, 2, 2])
        self.assertEqual(raw['route_dtype'], 'int64')
        self.assertEqual(raw['prompt_token_ids'], self.prompt)
        self.assertEqual(raw['output_token_ids'], list(range(2000, 2008)))
        self.assertFalse(raw['scientific_validation_passed'])
        self.assertEqual(raw['origin_validation'], 'UNVALIDATED_RETURN_SERIALIZATION')
        returned[0].outputs[0].routed_experts[0, 0, 0] = 3
        self.assertEqual(array[0, 0, 0], 0)

    def test_native_must_have_no_returned_routes(self):
        raw, array = worker.serialize_return(self.returned(capture=False), self.protocol, capture_enabled=False)
        self.assertIsNone(array); self.assertIsNone(raw['route_dtype'])
        with self.assertRaises(ValueError):
            worker.serialize_return(self.returned(), self.protocol, capture_enabled=False)
        with self.assertRaises(ValueError):
            worker.serialize_return(self.returned(capture=False), self.protocol, capture_enabled=True)

    def test_validation_uses_private_copy_when_return_owner_mutates(self):
        returned = self.returned(); owner = returned[0].outputs[0].routed_experts
        sort = np.sort
        def changing(*args, **kwargs):
            result = sort(*args, **kwargs)
            owner[:] = self.protocol['experts']
            return result
        with mock.patch.object(np, 'sort', side_effect=changing):
            _, array = worker.serialize_return(returned, self.protocol, capture_enabled=True)
        self.assertTrue(np.all(array < self.protocol['experts']))

    def test_wrong_route_dtype_shape_and_expert_ids_rejected_before_serialization(self):
        mutations = [lambda a: a.astype(float), lambda a: a.astype(bool), lambda a: a.astype(object),
                     lambda a: a[:-1], lambda a: np.concatenate((a, a[:1])),
                     lambda a: np.full_like(a, -1), lambda a: np.full_like(a, 4),
                     lambda a: np.zeros_like(a)]
        for mutation in mutations:
            returned = self.returned(); completion = returned[0].outputs[0]
            completion.routed_experts = mutation(completion.routed_experts)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                worker.serialize_return(returned, self.protocol, capture_enabled=True)

    def test_return_cardinality_tokens_and_finished_contract_are_strict(self):
        for change in ('request_count', 'completion_count', 'index_bool', 'unfinished', 'empty_id',
                       'wrong_prompt', 'short_output', 'boolean_output', 'cached_tokens'):
            returned = self.returned(); request = returned[0]; completion = request.outputs[0]
            if change == 'request_count': returned.append(request)
            elif change == 'completion_count': request.outputs.append(completion)
            elif change == 'index_bool': completion.index = False
            elif change == 'unfinished': request.finished = False
            elif change == 'empty_id': request.request_id = ''
            elif change == 'wrong_prompt': request.prompt_token_ids[0] += 1
            elif change == 'short_output': completion.token_ids.pop()
            elif change == 'boolean_output': completion.token_ids[0] = True
            else: request.num_cached_tokens = 1
            with self.subTest(change=change), self.assertRaises(ValueError):
                worker.serialize_return(returned, self.protocol, capture_enabled=True)

    def test_observed_full_geometry_counts(self):
        receipt = dict(summary=dict(layers=48, experts_per_layer=128, top_k=8),
                       evidence='TEST_ONLY', provenance='MOCK')
        config = dict(num_hidden_layers=48, num_experts=128, num_experts_per_tok=8, vocab_size=4096)
        protocol = worker.make_protocol(receipt, config, self.prompt)
        self.assertEqual(protocol['expected_counts'], dict(event_count=1872,
            prefill_event_count=1536, decode_event_count=336, expert_access_count=14976,
            tensor_access_count=44928))
        returned = self.returned()
        returned[0].outputs[0].routed_experts = np.array([
            [[(t + 3*l + k) % 128 for k in range(8)] for l in range(48)] for t in range(39)], dtype=np.int32)
        raw, array = worker.serialize_return(returned, protocol, capture_enabled=True)
        self.assertEqual(raw['route_shape'], [39, 48, 8])
        self.assertEqual(array[-1, -1].tolist(), [(38 + 3*47 + k) % 128 for k in range(8)])

    def test_arm_configuration_diff_is_only_route_return(self):
        native = worker.llm_arguments('/checkpoint', False)
        capture = worker.llm_arguments('/checkpoint', True)
        self.assertEqual([k for k in native if native[k] != capture[k]], ['enable_return_routed_experts'])
        self.assertEqual(native['dtype'], 'bfloat16')
        self.assertEqual(native['attention_config'], {'backend': 'TRITON_ATTN'})
        self.assertFalse(native['enable_expert_parallel'])
        self.assertFalse(native['enable_prefix_caching'])
        self.assertEqual(native['kv_cache_memory_bytes'], 64 << 20)

    def test_cache_and_platform_environment_are_allowlisted_before_import(self):
        inherited = dict(PATH='/usr/bin:/bin', HOME='/retained-original-home',
                         CUDA_VISIBLE_DEVICES='GPU-caller-choice',
                         CUDA_DEVICE_ORDER='FASTEST_FIRST',
                         PYTORCH_NVML_BASED_CUDA_CHECK='0', TORCHINDUCTOR_COMPILE_THREADS='64',
                         OMP_NUM_THREADS='64', MKL_NUM_THREADS='64', OPENBLAS_NUM_THREADS='64',
                         VLLM_PLUGINS='caller.plugin', TVM_FFI_DISABLE_TORCH_C_DLPACK='0',
                         FLASHINFER_WORKSPACE_BASE='/forbidden', TORCH_EXTENSIONS_DIR='/forbidden',
                         TVM_FFI_CACHE_DIR='/forbidden', TRITON_CACHE_MANAGER='remote.module',
                         FLASHINFER_CUBIN_CHECKSUM_DISABLED='1', FLASHINFER_DISABLE_VERSION_CHECK='1',
                         LD_PRELOAD='/forbidden', PYTHONPATH='/forbidden', HF_TOKEN='TEST_ONLY_secret')
        with tempfile.TemporaryDirectory(prefix='.worker-env-', dir=ROOT) as directory:
            work = Path(directory)
            env = worker.make_environment(work, 'GPU-test-fixture', inherited)
            self.assertEqual(env['HOME'], inherited['HOME'])
            self.assertEqual({key: env[key] for key in (
                'CUDA_VISIBLE_DEVICES', 'CUDA_DEVICE_ORDER',
                'PYTORCH_NVML_BASED_CUDA_CHECK', 'TORCHINDUCTOR_COMPILE_THREADS',
                'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                'VLLM_PLUGINS', 'TVM_FFI_DISABLE_TORCH_C_DLPACK')}, {
                    'CUDA_VISIBLE_DEVICES': '0',
                    'CUDA_DEVICE_ORDER': 'PCI_BUS_ID',
                    'PYTORCH_NVML_BASED_CUDA_CHECK': '1',
                    'TORCHINDUCTOR_COMPILE_THREADS': '1',
                    'OMP_NUM_THREADS': '1',
                    'MKL_NUM_THREADS': '1',
                    'OPENBLAS_NUM_THREADS': '1',
                    'VLLM_PLUGINS': '',
                    'TVM_FFI_DISABLE_TORCH_C_DLPACK': '1'})
            for key in ('FLASHINFER_WORKSPACE_BASE', 'TORCH_EXTENSIONS_DIR', 'TVM_FFI_CACHE_DIR',
                        'TRITON_HOME', 'TRITON_CACHE_DIR', 'HF_HOME', 'CUDA_CACHE_PATH', 'TMPDIR'):
                self.assertTrue(Path(env[key]).is_relative_to(work))
            for key in ('TRITON_CACHE_MANAGER', 'FLASHINFER_CUBIN_CHECKSUM_DISABLED',
                        'FLASHINFER_DISABLE_VERSION_CHECK', 'LD_PRELOAD', 'PYTHONPATH', 'HF_TOKEN'):
                self.assertNotIn(key, env)
            self.assertEqual(env['FLASHINFER_DISABLE_JIT'], '1')
            self.assertEqual(env['VLLM_USE_FLASHINFER_MOE_FP16'], '0')
            self.assertEqual(env['VLLM_USE_FLASHINFER_SAMPLER'], '0')
            self.assertEqual(list(work.iterdir()), [])

    def test_environment_still_requires_an_externally_bound_physical_gpu_uuid(self):
        with tempfile.TemporaryDirectory(prefix='.worker-env-', dir=ROOT) as directory:
            for value in (None, 0, '0', 'MIG-test-fixture', 'GPU-a,GPU-b'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    worker.make_environment(directory, value, {})

    def test_module_import_does_not_load_runtime_or_numpy(self):
        code = 'import sys;sys.path.insert(0,sys.argv[1]);import hf_routing_worker;assert not any(n in sys.modules for n in ("torch","vllm","flashinfer","numpy"))'
        result = subprocess.run([sys.executable, '-c', code, str(ROOT/'adapters/vllm_capacity')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__': unittest.main()

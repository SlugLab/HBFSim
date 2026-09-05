"""TEST_ONLY routed arrays and inventories; no checkpoint, torch, or GPU use."""
import copy
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

from model_inventory import ModelInventory
from trace_validation import validate_trace

ROOT = Path(__file__).resolve().parents[3]


class Vector:
    def __init__(self, values):
        self.values = values
    def tolist(self):
        return self.values


class RoutedArray:
    def __init__(self, rows, shape=None):
        self.rows = rows
        self.shape = shape or (len(rows), len(rows[0]), len(rows[0][0]))
    def __getitem__(self, key):
        token, layer, span = key
        return Vector(self.rows[token][layer][span])


class TraceCollectorTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / 'adapters/vllm_capacity/trace_collector.py').is_file(), 'capture closure is missing')
        self.api = importlib.import_module('trace_collector')
        self.temp = tempfile.TemporaryDirectory(prefix='.routing-capture-test-', dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manifest = self.base / 'inventory.json'
        experts = []
        def tensor(name, offset, size):
            return dict(tensor=name, source_shard='TEST_ONLY.safetensors', source_shard_sha256='a' * 64,
                        file_offset_begin=offset, file_offset_end=offset + size,
                        bytes=size, dtype='BF16', shape=[size // 128, 64])
        for layer in range(2):
            for expert in range(4):
                begin = (layer * 4 + expert) * 16384
                experts.append(dict(layer=layer, expert_id=expert, total_bytes=16384,
                    w13=dict(bytes=8192, tensors=[tensor(f'{layer}.{expert}.gate', begin, 4096),
                                                 tensor(f'{layer}.{expert}.up', begin + 4096, 4096)]),
                    w2=dict(bytes=8192, tensor=tensor(f'{layer}.{expert}.down', begin + 8192, 8192))))
        self.document = dict(ModelFingerprint='b' * 64, configuration=dict(num_hidden_layers=2,
                             num_experts=4, num_experts_per_tok=2), expert_weight_bytes=8 * 16384,
                             expert_weights=experts)
        self.manifest.write_text(json.dumps(self.document))
        self.inventory = ModelInventory(self.manifest)
        self.path = self.base / 'trace.jsonl'
        self.rows = [[[0, 1], [2, 3]], [[3, 0], [1, 2]], [[1, 3], [0, 2]]]
        self.request = self.api.TraceRequest('req-17', 3, 7, 2, 2)

    def collector(self, **kwargs):
        result = self.api.JsonlTraceCollector(self.path, self.inventory, 'TEST_ONLY-run', 'e' * 64, 'c' * 40, **kwargs)
        self.addCleanup(result.close)
        return result

    def test_real_format_routes_preserve_ids_and_full_inventory_identity(self):
        collector = self.collector(test_only=True)
        collector.emit_request(self.request, RoutedArray(self.rows))
        summary = collector.summary()
        self.assertEqual(summary['status'], 'CAPTURED_UNVALIDATED')
        self.assertEqual(summary['evidence_class'], 'TEST_ONLY')
        self.assertFalse(summary['scientific_validation_passed'])
        self.assertEqual(summary['inventory_sha256'], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        events = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual(len(events), 6)
        self.assertEqual([(e['request_id'], e['sequence_id'], e['prompt_id']) for e in events], [('req-17', 3, 7)] * 6)
        self.assertEqual([e['topk_expert_ids'] for e in events], [ids for token in self.rows for ids in token])
        self.assertEqual([e['phase'] for e in events], ['prefill'] * 4 + ['decode'] * 2)
        self.assertEqual([e['token_step'] for e in events], [0, 0, 1, 1, 0, 0])
        for event in events:
            self.assertEqual(event['model_fingerprint'], self.inventory.model_fingerprint)
            self.assertEqual(event['expert_access_bytes'], 32768)
            self.assertIsNone(event['gpu_event_timestamp_ns'])
            self.assertIsNone(event['previous_compute_gap_ns'])
            self.assertIn('post-request', event['host_timestamp_semantics'])
            self.assertEqual({t['source_shard_sha256'] for t in event['expert_tensors']}, {'a' * 64})
        self.assertEqual([t['access_order_sequence'] for e in events for t in e['expert_tensors']], list(range(36)))
        summary_path = self.base / 'summary.json'
        summary_path.write_text(json.dumps(dict(protocol=dict(num_prompts=1, input_len=2, output_len=2), trace=summary)))
        self.assertTrue(all(validate_trace(self.path, summary_path, self.inventory)['validation']['checks'].values()))

    def test_arbitrary_arrays_never_become_real_gold_by_materialization(self):
        collector = self.collector()
        self.assertEqual(collector.summary()['status'], 'EMPTY')
        collector.emit_request(self.request, RoutedArray(self.rows))
        summary = collector.summary()
        self.assertEqual(summary['evidence_class'], 'UNVALIDATED_ROUTING_CAPTURE')
        self.assertNotEqual(summary['status'], 'PASS')

    def test_validation_cli_cannot_promote_missing_controls_or_cpu_fixture(self):
        collector=self.collector(test_only=True)
        collector.emit_request(self.request,RoutedArray(self.rows))
        summary=self.base/'summary.json'
        summary.write_text(json.dumps(dict(protocol=dict(num_prompts=1,input_len=2,output_len=2),
                                           trace=collector.summary())))
        output=self.base/'validation.json'
        command=[sys.executable,str(ROOT/'adapters/vllm_capacity/trace_validation.py'),
                 '--trace',str(self.path),'--summary',str(summary),
                 '--model-manifest',str(self.manifest),'--output',str(output)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=10)
        report=json.loads(output.read_text())
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(report['status'],'INCOMPLETE')
        self.assertEqual(report['evidence_class'],'TEST_ONLY')
        self.assertFalse(report['scientific_validation_passed'])

    def test_fraction_bool_duplicate_and_out_of_range_ids_reject_whole_request(self):
        collector = self.collector(test_only=True)
        for ids in ([1.5, 2], [True, 2], [1, 1], [0, 4], [-1, 2]):
            with self.subTest(ids=ids):
                rows = copy.deepcopy(self.rows)
                rows[-1][-1] = ids
                with self.assertRaises(ValueError):
                    collector.emit_request(self.request, RoutedArray(rows))
                self.assertEqual(self.path.read_bytes(), b'')
        self.assertEqual(collector.summary()['status'], 'FAILED')

    def test_shape_and_request_lengths_fail_before_any_event(self):
        collector = self.collector(test_only=True)
        with self.assertRaises(ValueError):
            collector.emit_request(self.request, RoutedArray(self.rows, shape=(3, 1, 2)))
        for request in (self.api.TraceRequest('r', 0, 0, 0, 2), self.api.TraceRequest('r', 0, 0, 2, 0),
                        self.api.TraceRequest('', 0, 0, 2, 2), self.api.TraceRequest('r', -1, 0, 2, 2)):
            with self.assertRaises(ValueError):
                collector.emit_request(request, RoutedArray(self.rows))
        self.assertEqual(self.path.read_bytes(), b'')

    def test_duplicate_request_does_not_append_or_report_success(self):
        collector = self.collector(test_only=True)
        collector.emit_request(self.request, RoutedArray(self.rows))
        before = collector.summary()['trace_sha256']
        with self.assertRaises(ValueError):
            collector.emit_request(self.request, RoutedArray(self.rows))
        self.assertEqual(collector.summary()['trace_sha256'], before)
        self.assertEqual(collector.summary()['status'], 'FAILED')

    def test_inventory_source_or_loaded_object_changes_are_rejected(self):
        collector = self.collector(test_only=True)
        self.manifest.write_text(json.dumps(self.document) + '\n')
        with self.assertRaises(ValueError):
            collector.emit_request(self.request, RoutedArray(self.rows))
        self.assertEqual(self.path.read_bytes(), b'')

    def test_mutated_inventory_object_cannot_bind_unchanged_manifest(self):
        self.inventory.top_k = 1
        with self.assertRaises(ValueError):
            self.collector(test_only=True)
        self.assertFalse(self.path.exists())

    def test_new_destination_only_and_summary_after_close(self):
        collector = self.collector(test_only=True)
        collector.emit_request(self.request, RoutedArray(self.rows))
        before = collector.summary()
        collector.close()
        self.assertEqual(collector.summary(), before)
        with self.assertRaises(FileExistsError):
            self.collector(test_only=True)

    def test_trace_mutation_cannot_reuse_stale_digest(self):
        collector = self.collector(test_only=True)
        collector.emit_request(self.request, RoutedArray(self.rows))
        collector.summary()
        with self.path.open('ab') as stream:
            stream.write(b'{}\n')
        with self.assertRaises(ValueError):
            collector.summary()


if __name__ == '__main__':
    unittest.main()

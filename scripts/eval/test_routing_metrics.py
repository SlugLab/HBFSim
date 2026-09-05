"""Analytical routing controls; no checkpoint or GPU measurements are mocked."""
import collections
import contextlib
import copy
import hashlib
import json
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import routing_metrics
from routing_metrics import analyze_routes, compose_routes

ROOT = Path(__file__).resolve().parents[2]


def member(routes):
    return [dict(phase='decode', token_step=step, layer_id=layer,
                 topk_expert_ids=ids) for step, layers in enumerate(routes)
            for layer, ids in enumerate(layers)]


class RoutingMetricsTests(unittest.TestCase):
    def setUp(self):
        self.members = {
            'a': member([[[0, 1]], [[1, 2]], [[0, 1]]]),
            'b': member([[[0, 1]], [[2, 3]]]),
        }

    def test_union_active_sequences_and_null_are_distinct(self):
        result = analyze_routes(self.members, experts=4, top_k=2, layers=1, seed=17)
        real, null = result['real']['steps'], result['null']['steps']
        self.assertEqual([r['active_sequences'] for r in real], [2, 2, 1])
        self.assertEqual([r['unique_expert_union'] for r in real], [2, 3, 2])
        self.assertEqual([r['union_fraction'] for r in real], [.5, .75, .5])
        self.assertEqual([r['expected_union_fraction'] for r in null], [.75, .75, .5])
        self.assertEqual(result['concurrency_kind'], 'TRACE_COMPOSED')
        self.assertNotIn('decode_time_ns', result)

    def test_frequency_entropy_gini_and_jaccard(self):
        real = analyze_routes(self.members, experts=4, top_k=2, layers=1, seed=17)['real']
        first = real['steps'][0]
        self.assertEqual(first['expert_frequency'], [2, 2, 0, 0])
        self.assertEqual(first['entropy_bits'], 1)
        self.assertEqual(first['gini'], .5)
        self.assertIsNone(first['jaccard_previous_step'])
        self.assertAlmostEqual(real['steps'][1]['jaccard_previous_step'], .25)
        self.assertEqual(real['layers'][0]['expert_frequency'], [3, 4, 2, 1])

    def test_shuffling_preserves_whole_topk_marginals_and_seed(self):
        source = {'a': member([[[i % 4, (i+1) % 4]] for i in range(12)]),
                  'b': member([[[i % 4, (i+1) % 4]] for i in range(12)])}
        original = copy.deepcopy(source)
        first = compose_routes(source, experts=4, top_k=2, layers=1, seed=8, shuffled=True)
        repeat = compose_routes(source, experts=4, top_k=2, layers=1, seed=8, shuffled=True)
        self.assertEqual(first, repeat)
        self.assertEqual(source, original)
        for name in source:
            before = collections.Counter(tuple(r['topk_expert_ids']) for r in source[name])
            after = collections.Counter(tuple(r['topk_expert_ids']) for r in first if r['member'] == name)
            self.assertEqual(before, after)
        self.assertTrue(any(r['topk_expert_ids'] != source[r['member']][r['token_step']]['topk_expert_ids'] for r in first))

    def test_reuse_distance_counts_distinct_intervening_experts(self):
        routes = {'a': member([[[0]], [[1]], [[1]], [[0]]])}
        real = analyze_routes(routes, experts=2, top_k=1, layers=1, seed=0)['real']
        self.assertEqual([r['distance'] for r in real['reuse']], [None, None, 0, 1])
        self.assertEqual(real['layers'][0]['entropy_bits'], 1)
        self.assertEqual(real['layers'][0]['gini'], 0)

    def test_incomplete_duplicate_and_invalid_routes_reject(self):
        for corruption in ('missing_step', 'duplicate', 'expert', 'topk'):
            with self.subTest(corruption=corruption):
                source = copy.deepcopy(self.members)
                if corruption == 'missing_step': del source['a'][1]
                if corruption == 'duplicate': source['a'].append(copy.deepcopy(source['a'][0]))
                if corruption == 'expert': source['a'][0]['topk_expert_ids'] = [0, 4]
                if corruption == 'topk': source['a'][0]['topk_expert_ids'] = [0, 0]
                with self.assertRaises(ValueError):
                    analyze_routes(source, experts=4, top_k=2, layers=1, seed=0)
        with self.assertRaisesRegex(ValueError, 'layer'):
            analyze_routes(self.members, experts=4, top_k=2, layers=2, seed=0)

    def test_cli_preserves_separate_series_and_rejects_trace_mutation(self):
        import gguf
        import numpy as np
        from inventory_checkpoint import inventory_checkpoint
        base = ROOT / 'results/gold/routing-metrics/tmp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            directory = Path(directory)
            checkpoint = directory / 'TEST_ONLY.gguf'
            writer = gguf.GGUFWriter(checkpoint, 'qwen3moe')
            writer.add_block_count(1); writer.add_expert_count(4); writer.add_expert_used_count(2)
            writer.add_head_count_kv(1); writer.add_key_length(2); writer.add_value_length(2)
            writer.add_context_length(32)
            for projection in ('up', 'gate', 'down'):
                writer.add_tensor(f'blk.0.ffn_{projection}_exps.weight', np.zeros((4, 2, 2), dtype=np.float16))
            writer.write_header_to_file(); writer.write_kv_data_to_file(); writer.write_tensors_to_file(); writer.close()
            inventory = inventory_checkpoint(checkpoint)
            inventory_path = directory / 'inventory.json'
            inventory_path.write_text(json.dumps(inventory))
            index = dict(schema_version=1, source_kind='SYNTHETIC_CONTROL',
                         inventory_sha256=hashlib.sha256(inventory_path.read_bytes()).hexdigest(), members=[])
            for name, rows in self.members.items():
                path = directory / (name+'.jsonl')
                path.write_text(''.join(json.dumps(dict(**row, request_id=name, sequence_id=0,
                                     model_fingerprint=inventory['tensor_manifest_sha256']))+'\n' for row in rows))
                index['members'].append(dict(member_id=name, path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            index_path = directory / 'members.json'
            index_path.write_text(json.dumps(index))
            command = [sys.executable, str(ROOT / 'scripts/eval/routing_metrics.py'),
                       '--inventory', str(inventory_path), '--members', str(index_path), '--seed', '17']
            process = subprocess.run(command+['--out', str(directory/'out')], capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            manifest = json.loads((directory/'out/manifest.json').read_text())
            self.assertEqual(manifest['provenance'], 'MOCK')
            self.assertEqual(manifest['concurrency_kind'], 'TRACE_COMPOSED')
            self.assertEqual(set(manifest['outputs']), {'real', 'shuffled', 'null'})
            for artifact in manifest['outputs'].values():
                self.assertEqual(hashlib.sha256((directory/'out'/artifact['path']).read_bytes()).hexdigest(), artifact['sha256'])
            # The manifest must bind the bytes analyzed, even if an original
            # metadata path changes after its snapshot has been read.
            original_index = index_path.read_bytes()
            original_inventory = inventory_path.read_bytes()
            original_analyze = routing_metrics.analyze_routes
            def mutate_index(*args, **kwargs):
                changed = json.loads(original_index)
                changed['members'][0]['member_id'] = 'changed-after-read'
                index_path.write_text(json.dumps(changed))
                return original_analyze(*args, **kwargs)
            snapshot_out = directory/'snapshot'
            with mock.patch.object(routing_metrics, 'analyze_routes', side_effect=mutate_index), \
                    mock.patch.object(sys, 'argv', command[1:]+['--out', str(snapshot_out)]), \
                    contextlib.redirect_stdout(io.StringIO()):
                routing_metrics.main()
            saved = json.loads((snapshot_out/'manifest.json').read_text())
            self.assertEqual(saved['members_index_sha256'], hashlib.sha256(original_index).hexdigest())
            self.assertEqual((snapshot_out/'members-index.json').read_bytes(), original_index)
            self.assertEqual((snapshot_out/'inventory.json').read_bytes(), original_inventory)
            index_path.write_bytes(original_index)
            original_loader = routing_metrics.load_inventory
            def mutate_inventory(snapshot):
                inventory_path.write_text('changed between identity and decode')
                return original_loader(snapshot)
            with mock.patch.object(routing_metrics, 'load_inventory', side_effect=mutate_inventory), \
                    mock.patch.object(sys, 'argv', command[1:]+['--out', str(directory/'inventory-snapshot')]), \
                    contextlib.redirect_stdout(io.StringIO()):
                routing_metrics.main()
            inventory_path.write_bytes(original_inventory)
            (directory/'a.jsonl').write_text('changed input')
            process = subprocess.run(command+['--out', str(directory/'bad')], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn('trace hash mismatch', process.stderr)
            self.assertFalse((directory/'bad').exists())


if __name__ == '__main__':
    unittest.main()

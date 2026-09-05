"""Frozen metadata controls; explicit MOCK fixtures never load model weights."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import evaluation_inventory as adapter
import budget_fast_tier as budget_module
import verify_hf_metadata as verifier
from test_verify_hf_metadata import fixture, encoded
from budget_fast_tier import budget_fast_tier


def hf_fixture(base, **config_changes):
    checkpoint, donor_path, _ = fixture(base)
    config_path = checkpoint / 'config.json'
    config = json.loads(config_path.read_bytes())
    config.update(max_position_embeddings=64, use_sliding_window=False,
                  sliding_window=None, rope_scaling=None)
    config.update(config_changes)
    config_path.write_bytes(encoded(config))
    donor = json.loads(donor_path.read_bytes())
    entry = next(f for f in donor['files'] if f['path'] == 'config.json')
    entry.update(size_bytes=config_path.stat().st_size,
                 mtime_ns=config_path.stat().st_mtime_ns,
                 sha256=verifier.digest(config_path.read_bytes()))
    donor['configuration'].update({k: config[k] for k in
        ('max_position_embeddings', 'use_sliding_window', 'sliding_window', 'rope_scaling')})
    donor['total_model_file_bytes'] = sum(f['size_bytes'] for f in donor['files'])
    donor['ModelFingerprint'] = verifier.digest(encoded([
        {k: f[k] for k in ('path', 'size_bytes', 'sha256')} for f in donor['files']]))
    donor_path.write_bytes(encoded(donor))
    bundle = base / 'refresh'
    verifier.verify(checkpoint, donor_path, bundle, test_only=True)
    return checkpoint, bundle


class HFEvaluationInventoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='.test-hf-inventory-', dir=verifier.ROOT)
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)

    def inventory(self, **changes):
        checkpoint, bundle = hf_fixture(self.base, **changes)
        snapshot = adapter.load_hf_snapshot(bundle)
        return checkpoint, bundle, snapshot, adapter.adapt_hf_inventory(snapshot, 16384)

    def test_frozen_only_adaptation_conserves_shard_local_extents_and_bytes(self):
        checkpoint, bundle = hf_fixture(self.base)
        checkpoint.rename(self.base / 'unavailable')
        with mock.patch.object(verifier, 'check_current_inputs', side_effect=AssertionError('source read')):
            snapshot = adapter.load_hf_snapshot(bundle)
            inv = adapter.adapt_hf_inventory(snapshot, 16384)
            adapter.validate_evaluation_inventory(inv, hf_snapshot=snapshot)
        self.assertEqual((inv['layers'], inv['E'], inv['k']), (2, 2, 1))
        self.assertEqual(inv['eligible_expert_bytes'], 196608)
        self.assertEqual(inv['tensor_bytes'], inv['eligible_expert_bytes'] + inv['resident_non_offloaded_bytes'])
        self.assertEqual(inv['packed_logical_pages'], 12)
        self.assertEqual((inv['provenance'], inv['source_kind']), ('MOCK', 'TEST_ONLY'))
        self.assertFalse(inv['weight_payload_rehashed'])
        self.assertIsNone(inv['weight_payload_hash'])
        self.assertEqual(inv['kv_shape']['key_length'], 32)
        self.assertEqual(inv['kv_shape']['maximum_context_tokens'], 64)
        starts = [t for t in inv['tensors'] if t['data_offset_begin'] == 0]
        self.assertEqual(len(starts), 2)
        self.assertNotEqual(starts[0]['source_shard'], starts[1]['source_shard'])
        self.assertEqual(sum(e['bytes'] for e in inv['experts']), 196608)

    def test_schema_dispatch_cannot_accept_hf_without_snapshot(self):
        _, _, snapshot, inv = self.inventory()
        with self.assertRaises(ValueError): adapter.validate_evaluation_inventory(inv)
        for change in ({'format': 'GGUF'}, {'schema_version': 1}, {'format': 'unknown'}):
            changed = dict(inv, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                adapter.validate_evaluation_inventory(changed, hf_snapshot=snapshot)

    def test_derived_fields_cannot_be_resealed_or_relabelled(self):
        _, _, snapshot, inv = self.inventory()
        mutations = [
            lambda i: i.update(weight_dtype='F16'),
            lambda i: i.update(E=True),
            lambda i: i['experts'].pop(),
            lambda i: i['experts'][1].update(expert=0),
            lambda i: i['experts'][0]['segments'][0].update(tensor='model.norm.weight'),
            lambda i: i['tensors'][0].update(source_shard='wrong.safetensors'),
            lambda i: i['tensors'][0].update(data_offset_begin=16),
            lambda i: i['tensors'][0].update(bytes=1),
            lambda i: i['kv_shape'].update(key_length=64),
            lambda i: i.update(resident_non_offloaded_bytes=0),
            lambda i: i['model_binding'].update(observation_identity_sha256='0' * 64),
            lambda i: i.update(provenance='CHECKPOINT_METADATA', source_kind='CHECKPOINT_METADATA'),
        ]
        for mutation in mutations:
            changed = copy.deepcopy(inv); mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                adapter.validate_evaluation_inventory(changed, hf_snapshot=snapshot)

    def test_unpublished_or_changed_frozen_bundle_refused(self):
        _, bundle = hf_fixture(self.base)
        marker = bundle / 'COMPLETE.json'; raw = marker.read_bytes(); marker.unlink()
        with self.assertRaises(ValueError): adapter.load_hf_snapshot(bundle)
        marker.write_bytes(raw)
        path = bundle / 'tensors.json'; path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError): adapter.load_hf_snapshot(bundle)

    def test_mutation_between_validation_and_acquisition_refused(self):
        _, bundle = hf_fixture(self.base)
        original = verifier.validate_refresh
        def changing(path):
            receipt = original(path)
            p = bundle / 'tensors.json'; p.write_bytes(p.read_bytes() + b' ')
            return receipt
        with mock.patch.object(verifier, 'validate_refresh', side_effect=changing):
            with self.assertRaises(ValueError): adapter.load_hf_snapshot(bundle)

    def test_frozen_source_link_refused_before_target_open(self):
        _, bundle = hf_fixture(self.base)
        target = self.base / 'external'; path = bundle / 'metadata/config.json'
        path.rename(target); path.symlink_to(target)
        opened = []; original = os.open
        def observed(path, *args, **kwargs):
            if str(path) == str(target): opened.append(path)
            return original(path, *args, **kwargs)
        with mock.patch.object(verifier.os, 'open', side_effect=observed):
            with self.assertRaises(ValueError): adapter.load_hf_snapshot(bundle)
        self.assertEqual(opened, [])

    def test_unsupported_context_accounting_refused(self):
        _, bundle = hf_fixture(self.base, use_sliding_window=True)
        with self.assertRaises(ValueError):
            adapter.adapt_hf_inventory(adapter.load_hf_snapshot(bundle), 16384)

    def test_exclusive_publication_and_input_alias_refusal(self):
        _, bundle, snapshot, inv = self.inventory()
        path = self.base / 'inventory.json'
        adapter.publish_inventory(path, inv, hf_snapshot=snapshot)
        original = path.read_bytes()
        with self.assertRaises((FileExistsError, ValueError)):
            adapter.publish_inventory(path, inv, hf_snapshot=snapshot)
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(ValueError):
            adapter.publish_inventory(bundle / 'new.json', inv, hf_snapshot=snapshot)
        alias = self.base / 'alias'; alias.symlink_to(self.base / 'checkpoint', target_is_directory=True)
        with self.assertRaises(ValueError):
            adapter.publish_inventory(alias / 'new.json', inv, hf_snapshot=snapshot)
        self.assertFalse((self.base / 'checkpoint/new.json').exists())

    def test_hf_budget_uses_explicit_head_dim_and_distinguishes_hashes(self):
        _, _, snapshot, inv = self.inventory()
        raw = (json.dumps(inv, indent=2) + '\n').encode()
        result = budget_fast_tier(inv, fast_bytes=inv['resident_non_offloaded_bytes'] + 512 + 49152,
            active_sequences=1, context_tokens=1, kv_element_bytes=2, workspace_bytes=0,
            safety_bytes=0, hf_snapshot=snapshot, inventory_file_bytes=raw)
        self.assertEqual(result['kv_bytes'], 512)
        self.assertEqual(result['budget_fully_covered_experts'], 1)
        self.assertEqual(result['achieved_rho'], 0.25)
        self.assertEqual(result['inventory_file_sha256'], verifier.digest(raw))
        self.assertNotEqual(result['inventory_sha256'], result['inventory_file_sha256'])
        self.assertEqual(result['model_binding'], inv['model_binding'])
        with self.assertRaises(ValueError):
            budget_fast_tier(inv, fast_bytes=1000000, active_sequences=1, context_tokens=1,
                kv_element_bytes=2, workspace_bytes=0, safety_bytes=0,
                hf_snapshot=snapshot, inventory_file_bytes=b'{}')

    def test_budget_cli_rejects_symlink_before_reading_inventory(self):
        _, bundle, snapshot, inv = self.inventory()
        target = self.base / 'inventory.json'; target.write_bytes(encoded(inv))
        link = self.base / 'inventory-link.json'; link.symlink_to(target)
        output = self.base / 'budget.json'
        argv = ['budget_fast_tier.py', str(link), '--hf-metadata-refresh', str(bundle),
                '--output', str(output), '--fast-bytes', '1000000', '--active-sequences', '1',
                '--context-tokens', '1', '--kv-element-bytes', '2', '--workspace-bytes', '0',
                '--safety-bytes', '0']
        with mock.patch('sys.argv', argv), \
             mock.patch.object(budget_module, 'load_hf_snapshot', return_value=snapshot):
            with self.assertRaises(ValueError): budget_module.main()
        self.assertFalse(output.exists())

    def test_snapshot_claim_contract_cannot_be_resealed_by_a_caller(self):
        _, _, snapshot, _ = self.inventory()
        for change in ({'status': 'INVALID_METADATA'}, {'scientific_validation_passed': True},
                       {'weight_payload_rehashed': True}, {'checkpoint_origin_authenticated': True},
                       {'schema_version': True}, {'hardware_validated': True}):
            receipt = json.loads(snapshot.receipt_bytes); receipt.update(change)
            raw = encoded(receipt)
            marker = json.loads(snapshot.complete_bytes); marker['receipt_sha256'] = verifier.digest(raw)
            forged = replace(snapshot, receipt_bytes=raw, complete_bytes=encoded(marker))
            with self.subTest(change=change), self.assertRaises(ValueError):
                adapter.adapt_hf_inventory(forged, 16384)

    def test_publication_never_traverses_recorded_source_paths(self):
        checkpoint, _, snapshot, inv = self.inventory()
        original = Path.resolve
        def guarded(path, *args, **kwargs):
            if path == checkpoint or path.is_relative_to(checkpoint):
                raise AssertionError('recorded source path traversed')
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, 'resolve', guarded):
            adapter.publish_inventory(self.base / 'inventory.json', inv, hf_snapshot=snapshot)

    def test_constructed_snapshot_reconciles_full_payload_contract(self):
        _, _, snapshot, _ = self.inventory()
        for change in ('extra_artifact', 'input_digest', 'read_ranges', 'total_bytes'):
            receipt = json.loads(snapshot.receipt_bytes); artifacts = dict(snapshot.artifacts)
            if change == 'extra_artifact':
                artifacts['unexpected.json'] = b'{}'
                receipt['artifacts']['unexpected.json'] = verifier.digest(b'{}')
            elif change == 'input_digest':
                receipt['inputs']['config.json']['metadata_sha256'] = '0' * 64
            elif change == 'read_ranges':
                receipt['inputs']['config.json']['read_ranges'] = [[1, 1]]
            else:
                receipt['metadata_bytes_read'] += 1
            receipt['metadata_identity_sha256'], receipt['observation_identity_sha256'] = verifier.identities(receipt)
            raw = encoded(receipt); marker = json.loads(snapshot.complete_bytes)
            marker.update(receipt_sha256=verifier.digest(raw),
                          observation_identity_sha256=receipt['observation_identity_sha256'])
            forged = replace(snapshot, receipt_bytes=raw, complete_bytes=encoded(marker),
                             artifacts=tuple(sorted(artifacts.items())))
            with self.subTest(change=change), self.assertRaises(ValueError):
                adapter.adapt_hf_inventory(forged, 16384)


if __name__ == '__main__':
    unittest.main()

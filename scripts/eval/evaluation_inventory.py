#!/usr/bin/env python3
"""Format-specific evaluation inventories from published, frozen HF metadata.

This adapter never opens recorded checkpoint paths. Source shard offsets remain
source offsets; logical cache packing is a separate capacity calculation.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import tempfile

import verify_hf_metadata as metadata
from inventory_checkpoint import identity, positive, validate_inventory

ROOT = Path(__file__).resolve().parents[2]
MAX_OUTPUT_BYTES = 16 << 20


@dataclass(frozen=True)
class HFMetadataSnapshot:
    bundle: Path
    receipt_bytes: bytes
    complete_bytes: bytes
    artifacts: tuple[tuple[str, bytes], ...]


def load_hf_snapshot(bundle):
    """Acquire the exact buffers accepted by a frozen-only metadata validator."""
    bundle = Path(bundle).resolve(strict=True)
    receipt = metadata.validate_refresh(bundle)
    expected = {*receipt['artifacts'], 'receipt.json', 'COMPLETE.json'}
    before = {name: metadata.path_state(bundle / name) for name in expected}
    budget = dict(remaining=metadata.LIMITS['donor_bytes'] +
                  metadata.LIMITS['current_total_bytes'] + 2 * metadata.LIMITS['file_bytes'])
    loaded = {}
    for name in sorted(expected):
        limit = metadata.LIMITS['donor_bytes'] if name == 'donor.json' else metadata.LIMITS['file_bytes'] + 8
        raw, state = metadata.snapshot(bundle / name, header=False, budget=budget,
                                       limit=limit, confined_to=bundle)
        if any(state[k] != before[name][k] for k in before[name]):
            raise ValueError('HF artifact changed during snapshot acquisition')
        if name in receipt['artifacts'] and metadata.digest(raw) != receipt['artifacts'][name]:
            raise ValueError('HF artifact differs from validated receipt')
        loaded[name] = raw
    if metadata.strict_object(loaded['receipt.json']) != receipt:
        raise ValueError('HF receipt changed after validation')
    marker = metadata.strict_object(loaded['COMPLETE.json'])
    if marker != dict(schema_version=1, status='METADATA_VERIFIED',
                      receipt_sha256=metadata.digest(loaded['receipt.json']),
                      observation_identity_sha256=receipt['observation_identity_sha256']):
        raise ValueError('HF complete marker changed after validation')
    metadata.assert_current(before)
    metadata.check_artifact_set(bundle, expected)
    return HFMetadataSnapshot(bundle, loaded.pop('receipt.json'), loaded.pop('COMPLETE.json'),
                              tuple(sorted(loaded.items())))


def _unpack(snapshot):
    if type(snapshot) is not HFMetadataSnapshot:
        raise ValueError('HF inventory requires a validated metadata snapshot')
    if type(snapshot.receipt_bytes) is not bytes or len(snapshot.receipt_bytes) > metadata.LIMITS['file_bytes'] or \
       type(snapshot.complete_bytes) is not bytes or len(snapshot.complete_bytes) > 4096:
        raise ValueError('HF snapshot receipt exceeds bounded metadata size')
    receipt = metadata.strict_object(snapshot.receipt_bytes)
    metadata.validate_receipt_contract(receipt)
    artifacts = dict(snapshot.artifacts)
    if len(artifacts) != len(snapshot.artifacts) or set(artifacts) != set(receipt['artifacts']):
        raise ValueError('HF snapshot artifact identity mismatch')
    remaining = metadata.LIMITS['donor_bytes'] + metadata.LIMITS['current_total_bytes'] + metadata.LIMITS['file_bytes']
    for name, raw in artifacts.items():
        limit = metadata.LIMITS['donor_bytes'] if name == 'donor.json' else metadata.LIMITS['file_bytes'] + 8
        if not isinstance(name, str) or Path(name).is_absolute() or '..' in Path(name).parts or \
           type(raw) is not bytes or len(raw) > limit or len(raw) > remaining:
            raise ValueError('HF snapshot artifact exceeds bounded metadata size/path')
        remaining -= len(raw)
        if metadata.digest(raw) != receipt['artifacts'][name]:
            raise ValueError('HF snapshot buffer changed')
    marker = metadata.strict_object(snapshot.complete_bytes)
    if type(marker.get('schema_version')) is not int or marker != dict(schema_version=1, status='METADATA_VERIFIED',
                      receipt_sha256=metadata.digest(snapshot.receipt_bytes),
                      observation_identity_sha256=receipt['observation_identity_sha256']):
        raise ValueError('HF snapshot marker mismatch')
    # Use exactly the disk validator's full payload/observation reconciliation.
    donor, table = metadata.validate_frozen_payloads(receipt, artifacts)
    return receipt, artifacts, donor, table


def adapt_hf_inventory(snapshot, page_bytes):
    positive(page_bytes, 'page_bytes')
    receipt, artifacts, donor, table = _unpack(snapshot)
    config = metadata.strict_object(artifacts['metadata/config.json'])
    if (config.get('use_sliding_window') is not None and config.get('use_sliding_window') is not False) or config.get('sliding_window') is not None or \
       config.get('rope_scaling') is not None:
        raise ValueError('unsupported HF context accounting')
    context = positive(config.get('max_position_embeddings'), 'max_position_embeddings')
    layers = positive(config['num_hidden_layers'], 'layers')
    experts = positive(config['num_experts'], 'experts')
    top_k = positive(config['num_experts_per_tok'], 'top_k')
    head_dim = positive(config['head_dim'], 'head_dim')
    tensors = []
    by_name = {}
    for name, record in sorted(table.items()):
        category = ('eligible_expert' if re.fullmatch(
            r'model\.layers\.\d+\.mlp\.experts\.\d+\.(gate|up|down)_proj\.weight', name)
            else 'attention' if '.self_attn.' in name else 'other_resident')
        tensor = {key: value for key, value in record.items() if key not in ('tensor', 'source_shard_sha256')}
        tensor.update(name=name, category=category,
                      historical_source_shard_sha256=record['source_shard_sha256'])
        by_name[name] = tensor
        tensors.append(tensor)
    rows = []
    for layer in range(layers):
        for expert in range(experts):
            segments = [dict(tensor=f'model.layers.{layer}.mlp.experts.{expert}.{p}_proj.weight',
                             projection=p) for p in ('gate', 'up', 'down')]
            size = sum(by_name[s['tensor']]['bytes'] for s in segments)
            rows.append(dict(layer=layer, expert=expert, bytes=size,
                             packed_logical_pages=(size + page_bytes - 1) // page_bytes,
                             segments=segments))
    binding = dict(format='HF_SAFETENSORS', weight_dtype='BF16',
                   receipt_sha256=metadata.digest(snapshot.receipt_bytes),
                   complete_sha256=metadata.digest(snapshot.complete_bytes),
                   tensor_table_sha256=receipt['artifacts']['tensors.json'],
                   config_artifact_sha256=receipt['artifacts']['metadata/config.json'])
    binding.update({k: receipt[k] for k in ('metadata_identity_sha256', 'observation_identity_sha256',
                                          'legacy_inventory_sha256', 'historical_model_fingerprint')})
    shards = [dict(name=f['path'], bytes=f['size_bytes'], historical_sha256=f['sha256'])
              for f in donor['files'] if f['path'].endswith('.safetensors')]
    result = dict(schema_version=2, format='HF_SAFETENSORS', architecture='qwen3_moe',
        weight_dtype='BF16', source_kind=receipt['evidence'], provenance=receipt['provenance'],
        checkpoint_path=receipt['checkpoint'], model_binding=binding,
        config=config, config_sha256=identity(config),
        E=experts, k=top_k, layers=layers,
        kv_shape=dict(layers=layers, heads_kv=positive(config['num_key_value_heads'], 'heads_kv'),
                      key_length=head_dim, value_length=head_dim, maximum_context_tokens=context),
        tensors=tensors, tensor_count=len(tensors), tensor_manifest_sha256=identity(tensors),
        tensor_bytes=receipt['summary']['tensor_payload_bytes'],
        eligible_expert_bytes=receipt['summary']['expert_weight_bytes'], shared_expert_bytes=0,
        attention_bytes=sum(t['bytes'] for t in tensors if t['category'] == 'attention'),
        resident_non_offloaded_bytes=receipt['summary']['resident_non_offloaded_bytes'],
        page_bytes=page_bytes, packed_logical_pages=sum(r['packed_logical_pages'] for r in rows),
        experts=rows, source_shards=sorted(shards, key=lambda r: r['name']),
        source_extent_semantics='SAFETENSORS_SHARD_LOCAL_NOT_LOGICAL_CACHE_ADDRESSES',
        weight_payload_rehashed=False, weight_payload_hash=None,
        payload_identity_status='HISTORICAL_ONLY_NOT_CURRENTLY_AUTHENTICATED',
        checkpoint_origin_authenticated=False, hardware_validated=False,
        backing_materialized=False, scientific_validation_passed=False)
    if len(metadata.canonical(result)) > MAX_OUTPUT_BYTES:
        raise ValueError('HF evaluation inventory exceeds bounded output size')
    return result


def validate_evaluation_inventory(inv, *, hf_snapshot=None):
    if type(inv) is not dict:
        raise ValueError('evaluation inventory must be an object')
    if type(inv.get('schema_version')) is int and (inv['schema_version'], inv.get('format')) == (1, 'GGUF'):
        validate_inventory(inv)
    elif type(inv.get('schema_version')) is int and (inv['schema_version'], inv.get('format')) == (2, 'HF_SAFETENSORS'):
        expected = adapt_hf_inventory(hf_snapshot, inv.get('page_bytes'))
        if metadata.canonical(inv) != metadata.canonical(expected):
            raise ValueError('HF evaluation inventory differs from frozen metadata derivation')
    else:
        raise ValueError('unsupported evaluation inventory schema/format')


def publish_inventory(path, inv, *, hf_snapshot):
    validate_evaluation_inventory(inv, hf_snapshot=hf_snapshot)
    publish_hf_document(path, inv, hf_snapshot=hf_snapshot)


def publish_hf_document(path, document, *, hf_snapshot):
    """Exclusively publish a prevalidated HF consumer document within the project."""
    path = Path(os.path.abspath(path))
    receipt, _, _, _ = _unpack(hf_snapshot)
    forbidden = [hf_snapshot.bundle, Path(receipt['checkpoint']), ROOT / 'results/runs']
    forbidden.extend(Path(row['realpath']).parent for row in receipt['inputs'].values())
    resolved = path.resolve()
    # Source endpoints are already canonical observations. Never traverse them:
    # consumers may run after the checkpoint/cache becomes unavailable.
    if not resolved.is_relative_to(ROOT) or any(resolved.is_relative_to(p) for p in forbidden):
        raise ValueError('HF inventory output crosses the authorized boundary')
    if resolved != path:
        raise ValueError('HF inventory output may not traverse a symlink')
    if path.exists() or path.is_symlink():
        raise FileExistsError(path)
    raw = metadata.canonical(document) + b'\n'
    if len(raw) > MAX_OUTPUT_BYTES:
        raise ValueError('HF evaluation inventory exceeds bounded output size')
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = path.parent.stat()
    fd, name = tempfile.mkstemp(prefix='.hf-inventory-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
        current = path.parent.stat()
        if (parent.st_dev, parent.st_ino) != (current.st_dev, current.st_ino) or path.resolve() != path:
            raise ValueError('HF output directory changed before publication')
        os.link(temporary, path, follow_symlinks=False)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metadata-refresh', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--page-bytes', type=int, default=16384)
    args = parser.parse_args()
    snapshot = load_hf_snapshot(args.metadata_refresh)
    inv = adapt_hf_inventory(snapshot, args.page_bytes)
    publish_inventory(args.output, inv, hf_snapshot=snapshot)
    print(metadata.canonical({key: inv[key] for key in
        ('format', 'layers', 'E', 'k', 'tensor_bytes', 'eligible_expert_bytes', 'provenance')}).decode())


if __name__ == '__main__':
    main()

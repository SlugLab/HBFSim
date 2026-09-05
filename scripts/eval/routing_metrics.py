#!/usr/bin/env python3
"""Routing statistics and explicitly trace-composed concurrency, without timing.

Each member is one complete sequence of decode routes, with all layers at each
step. Composition aligns token_step; finished sequences disappear. A seeded
member order defines ties for reuse distance, and top-k IDs are linearized in
sorted order. Shuffling permutes whole top-k sets independently per member/layer.
The uniform independent null supplies expected union only, not invented routes.

CLI: --inventory inventory.json --members members.json --seed N --out NEW_DIR.
The member index has schema_version=1, inventory_sha256, source_kind
(CAPTURED_ROUTE/SYNTHETIC_CONTROL/EXTERNAL_UNVERIFIED), and members: a list of
{member_id,path,sha256}. Every event's model_fingerprint must match the inventory.
CAPTURED_ROUTE records source attribution only; this tool does not certify a
capture or hardware correctness gate. Outputs are MOCK for synthetic controls,
otherwise PROJECTED; no serving/timing claim follows from composed statistics.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sys
import tempfile

from freeze_storage_split import regular_bytes
from inventory_checkpoint import validate_inventory
from run_manifest import atomic_json, git_snapshot, sha256

ROOT = Path(__file__).resolve().parents[2]


def positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(name+' must be a positive integer')
    return value


def compose_routes(members, *, experts, top_k, layers, seed, shuffled=False):
    positive(experts, 'experts'); positive(top_k, 'top_k'); positive(layers, 'layers')
    if top_k > experts or type(seed) is not int or not members:
        raise ValueError('invalid routing dimensions/seed/members')
    member_order = sorted(members)
    random.Random(seed).shuffle(member_order)
    composed = []
    for name in member_order:
        if not isinstance(name, str) or not name:
            raise ValueError('member requires a stable nonempty identity')
        events = {}
        for row in members[name]:
            if row.get('phase') == 'prefill':
                continue
            if row.get('phase') != 'decode':
                raise ValueError('unknown routing phase')
            step, layer = row['token_step'], row['layer_id']
            ids = row['topk_expert_ids']
            if (type(step) is not int or step < 0 or type(layer) is not int
                    or not 0 <= layer < layers or (step, layer) in events):
                raise ValueError('invalid/duplicate step or layer')
            if (not isinstance(ids, list) or len(ids) != top_k
                    or any(type(e) is not int or not 0 <= e < experts for e in ids)
                    or len(set(ids)) != top_k):
                raise ValueError('invalid top-k experts')
            events[step, layer] = list(ids)
        if not events:
            raise ValueError('member has no decode routes')
        steps = max(step for step, _ in events)+1
        if len(events) != steps*layers:
            raise ValueError('member has missing step/layer coverage')
        for layer in range(layers):
            source_steps = list(range(steps))
            if shuffled:
                local_seed = hashlib.sha256(json.dumps([seed, name, layer]).encode()).digest()
                random.Random(local_seed).shuffle(source_steps)
            for step, source_step in enumerate(source_steps):
                composed.append(dict(member=name, token_step=step, layer_id=layer,
                                     source_token_step=source_step,
                                     topk_expert_ids=events[source_step, layer]))
    order = {name:i for i,name in enumerate(member_order)}
    return sorted(composed, key=lambda r:(r['token_step'], r['layer_id'], order[r['member']]))


def distribution_metrics(frequencies):
    total = sum(frequencies)
    probabilities = [count/total for count in frequencies if count]
    entropy = -sum(p*math.log2(p) for p in probabilities)
    count = len(frequencies)
    gini = sum((2*i-count-1)*value for i,value in enumerate(sorted(frequencies), 1))/(count*total)
    return dict(expert_frequency=frequencies, entropy_bits=entropy, gini=gini)


def summarize_routes(routes, experts, layers):
    by_step = collections.defaultdict(list)
    frequencies = [[0]*experts for _ in range(layers)]
    recency = [collections.OrderedDict() for _ in range(layers)]
    reuse = []
    for row in routes:
        layer = row['layer_id']
        by_step[row['token_step'], layer].append(row)
        for expert in sorted(row['topk_expert_ids']):
            stack = recency[layer]
            distance = list(stack).index(expert) if expert in stack else None
            reuse.append(dict(member=row['member'], token_step=row['token_step'],
                              layer_id=layer, expert_id=expert, distance=distance))
            stack[expert] = None
            stack.move_to_end(expert, last=False)
            frequencies[layer][expert] += 1
    steps, previous = [], {}
    for (step, layer), rows in sorted(by_step.items()):
        counts = [0]*experts
        for row in rows:
            for expert in row['topk_expert_ids']:
                counts[expert] += 1
        union = {e for e,count in enumerate(counts) if count}
        prior = previous.get(layer)
        jaccard = None if prior is None else len(prior & union)/len(prior | union)
        steps.append(dict(token_step=step, layer_id=layer, active_sequences=len(rows),
                          unique_expert_union=len(union), union_fraction=len(union)/experts,
                          jaccard_previous_step=jaccard, **distribution_metrics(counts)))
        previous[layer] = union
    return dict(routes=routes, steps=steps, reuse=reuse,
                layers=[dict(layer_id=layer, **distribution_metrics(frequencies[layer])) for layer in range(layers)])


def analyze_routes(members, *, experts, top_k, layers, seed):
    arguments = dict(experts=experts, top_k=top_k, layers=layers, seed=seed)
    real = summarize_routes(compose_routes(members, **arguments), experts, layers)
    shuffled = summarize_routes(compose_routes(members, **arguments, shuffled=True), experts, layers)
    null = [dict(token_step=r['token_step'], layer_id=r['layer_id'],
                 active_sequences=r['active_sequences'],
                 expected_union_fraction=1-(1-top_k/experts)**r['active_sequences']) for r in real['steps']]
    return dict(schema_version=1, concurrency_kind='TRACE_COMPOSED',
                composition_seed=seed, composition_rule='ALIGN_DECODE_STEP_DROP_FINISHED_SEQUENCES',
                reuse_order='STEP_LAYER_SEEDED_MEMBER_SORTED_TOPK_IDS',
                reuse_distance_scope='DISTINCT_EXPERTS_BETWEEN_ACCESSES_WITHIN_LAYER_COMPOSED_ORDER',
                member_traces=sorted(members), E=experts, k=top_k, layers=layers,
                real=real, shuffled=shuffled,
                null=dict(assumptions='Uniform unique top-k set independently drawn across active sequences', steps=null))


def load_inventory(payload):
    document = json.loads(payload)
    if document.get('source_kind') == 'CHECKPOINT_METADATA':
        validate_inventory(document)
        return document['E'], document['k'], document['layers'], document['tensor_manifest_sha256']
    # Reuse the frozen legacy validator rather than guessing from a model name.
    sys.path.insert(0, str(ROOT / 'adapters/vllm_capacity'))
    from model_inventory import ModelInventory
    # The legacy API reads a filename. Give it the already hashed snapshot,
    # not another read of an original path that may have changed meanwhile.
    with tempfile.TemporaryDirectory(prefix='.routing-inventory-', dir=ROOT) as directory:
        path = Path(directory) / 'inventory.json'
        path.write_bytes(payload)
        inventory = ModelInventory(path)
    return inventory.num_experts, inventory.top_k, inventory.num_layers, inventory.model_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--members', type=Path, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        index_bytes = regular_bytes(args.members.absolute())
        inventory_bytes = regular_bytes(args.inventory.absolute())
        index = json.loads(index_bytes)
        inventory_hash = hashlib.sha256(inventory_bytes).hexdigest()
        if index['schema_version'] != 1 or index['inventory_sha256'] != inventory_hash:
            raise ValueError('member/inventory identity mismatch')
        if index['source_kind'] not in ('CAPTURED_ROUTE', 'SYNTHETIC_CONTROL', 'EXTERNAL_UNVERIFIED'):
            raise ValueError('unknown route input attribution')
        experts, top_k, layers, fingerprint = load_inventory(inventory_bytes)
        members = {}
        for item in index['members']:
            path = Path(item['path'])
            if not path.is_absolute() or not path.is_file():
                raise ValueError('member must identify an absolute regular trace file')
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != item['sha256']:
                raise ValueError('member trace hash mismatch')
            rows = [json.loads(line) for line in payload.splitlines()]
            if any(r['model_fingerprint'] != fingerprint for r in rows):
                raise ValueError('route model fingerprint mismatch')
            identities = {(r['request_id'], r['sequence_id']) for r in rows}
            if len(identities) != 1 or item['member_id'] in members:
                raise ValueError('each unique member must contain exactly one sequence')
            members[item['member_id']] = rows
        result = analyze_routes(members, experts=experts, top_k=top_k, layers=layers, seed=args.seed)
        if not args.out.resolve().is_relative_to(ROOT):
            raise ValueError('output must stay in the experiment checkout')
        args.out.mkdir(parents=True, exist_ok=False)
        for name, payload in (('members-index.json', index_bytes), ('inventory.json', inventory_bytes)):
            with (args.out / name).open('xb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        provenance = 'MOCK' if index['source_kind'] == 'SYNTHETIC_CONTROL' else 'PROJECTED'
        manifest = {k:v for k,v in result.items() if k not in ('real', 'shuffled', 'null')}
        manifest.update(provenance=provenance, input_source_kind=index['source_kind'],
                        capture_validation='NOT_CERTIFIED_BY_POSTPROCESSOR',
                        git=git_snapshot(ROOT), inventory_sha256=index['inventory_sha256'],
                        input_members=index['members'], members_index_sha256=hashlib.sha256(index_bytes).hexdigest(),
                        metadata_snapshots=dict(inventory='inventory.json', members_index='members-index.json'),
                        tool_sha256=sha256(Path(__file__)), outputs={})
        for series in ('real', 'shuffled', 'null'):
            path = args.out / (series+'.json')
            atomic_json(path, dict(series=series, provenance=provenance, **result[series]))
            manifest['outputs'][series] = dict(path=path.name, sha256=sha256(path))
        atomic_json(args.out / 'manifest.json', manifest)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f'routing_metrics: {error}\n')
    print(json.dumps(dict(output=str(args.out), provenance=provenance, concurrency_kind='TRACE_COMPOSED')))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Bounded HF metadata refresh; historical weight hashes are never reissued."""
from __future__ import annotations

import argparse
from collections import deque
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile

from run_manifest import atomic_json, artifact_inventory, environment_snapshot, git_snapshot

ROOT = Path(__file__).resolve().parents[2]
SMALL = ('config.json', 'generation_config.json', 'tokenizer_config.json',
         'tokenizer.json', 'merges.txt', 'vocab.json', 'model.safetensors.index.json')
LIMITS = dict(donor_bytes=32 << 20, file_bytes=16 << 20, header_bytes=16 << 20,
              current_total_bytes=64 << 20, tensors=25000, symlink_hops=32)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode()


def strict_object(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value): raise ValueError('nonfinite JSON constant: ' + value)
    def finite_float(token):
        value=float(token)
        if not math.isfinite(value):raise ValueError('nonfinite JSON number')
        return value
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant,parse_float=finite_float)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('invalid bounded JSON') from error
    if not isinstance(value, dict): raise ValueError('JSON root must be an object')
    return value


def positive(value):
    if type(value) is not int or not 0 < value <= (1 << 63)-1:
        raise ValueError('expected bounded positive integer')
    return value


def file_identity(info):
    if not stat.S_ISREG(info.st_mode): raise ValueError('input is not a regular file')
    return dict(device=info.st_dev, inode=info.st_ino, size=info.st_size,
                mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)


def path_state(path):
    """Record directory identity and every followed link, without opening payloads."""
    path = Path(os.path.abspath(path)); pending = deque(path.parts[1:])
    current = Path('/'); chain = []; hops = 0
    while pending:
        part = pending.popleft()
        if part in ('', '.'): continue
        if part == '..': current = current.parent; continue
        candidate = current/part; info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            hops += 1
            if hops > LIMITS['symlink_hops']: raise ValueError('too many symlink hops')
            target = os.readlink(candidate)
            chain.append(dict(path=str(candidate), device=info.st_dev, inode=info.st_ino,
                mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns, target=target))
            pieces = Path(target).parts
            if os.path.isabs(target): current = Path('/'); pieces = pieces[1:]
            pending.extendleft(reversed(pieces))
        elif pending:
            if not stat.S_ISDIR(info.st_mode): raise ValueError('non-directory in input path')
            chain.append(dict(path=str(candidate), device=info.st_dev, inode=info.st_ino))
            current = candidate
        else:
            return dict(path=str(path), realpath=str(candidate), chain=chain,
                        file_identity=file_identity(info))
    raise ValueError('input must name a file')


def read_exact(fd, count, offset, budget):
    if count < 0 or count > budget['remaining']: raise ValueError('metadata read budget exceeded')
    budget['remaining'] -= count
    parts = []; read = 0
    while read < count:
        raw = os.pread(fd, count-read, offset+read)
        if not raw: raise ValueError('truncated metadata read')
        parts.append(raw); read += len(raw)
    return b''.join(parts)


def snapshot(path, *, header, budget, limit, expected=None, confined_to=None):
    before = path_state(path)
    if confined_to is not None:
        boundary=Path(confined_to).resolve()
        if not Path(before['realpath']).is_relative_to(boundary) or any(
            'target' in hop and Path(hop['path']).is_relative_to(boundary) for hop in before['chain']):
            raise ValueError('frozen artifact link/escape rejected before open')
    if expected is not None:
        ident=before['file_identity']
        if before['realpath']!=expected['realpath'] or ident['size']!=expected['size_bytes'] or ident['mtime_ns']!=expected['mtime_ns']:
            raise ValueError('historical path/size/mtime drift before read')
    fd = os.open(before['realpath'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if file_identity(os.fstat(fd)) != before['file_identity']:
            raise ValueError('input replaced before read')
        size = before['file_identity']['size']
        if header:
            prefix = read_exact(fd, 8, 0, budget)
            length = struct.unpack('<Q', prefix)[0]
            if not 0 < length <= limit or length+8 > size:
                raise ValueError('invalid/truncated/oversized safetensors header')
            raw = prefix + read_exact(fd, length, 8, budget)
        else:
            if size > limit: raise ValueError('metadata file exceeds size limit')
            raw = read_exact(fd, size, 0, budget)
        if file_identity(os.fstat(fd)) != before['file_identity'] or path_state(path) != before:
            raise ValueError('input changed during metadata read')
    finally: os.close(fd)
    return raw, dict(before, read_bytes=len(raw), metadata_sha256=digest(raw),
                     read_ranges=[[0,8],[8,len(raw)-8]] if header else [[0,len(raw)]])


def interpreter_identity():
    # Executable provenance is separate from the checkpoint metadata budget.
    before=path_state(sys.executable);size=before['file_identity']['size']
    if size>512<<20:raise ValueError('interpreter exceeds executable provenance limit')
    fd=os.open(before['realpath'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    sha=hashlib.sha256();budget={'remaining':size}
    try:
        if file_identity(os.fstat(fd))!=before['file_identity']:raise ValueError('interpreter replaced')
        for offset in range(0,size,1<<20):sha.update(read_exact(fd,min(1<<20,size-offset),offset,budget))
        if file_identity(os.fstat(fd))!=before['file_identity'] or path_state(sys.executable)!=before:
            raise ValueError('interpreter changed')
    finally:os.close(fd)
    return dict(path=before['realpath'],sha256=sha.hexdigest(),file_identity=before['file_identity'])


def donor_files(donor):
    if donor.get('schema_version') != 1 or not isinstance(donor.get('files'), list):
        raise ValueError('unsupported legacy donor')
    records = {}
    for row in donor['files']:
        name = row['path']
        if not isinstance(name, str) or Path(name).name != name or name in ('.', '..') or name in records:
            raise ValueError('unsafe or duplicate donor filename')
        if name not in SMALL and not re.fullmatch(r'model-\d{5}-of-\d{5}\.safetensors', name):
            raise ValueError('unsupported donor file')
        positive(row['size_bytes'])
        if not re.fullmatch('[0-9a-f]{64}', row['sha256']): raise ValueError('invalid historical SHA')
        records[name] = row
    if set(SMALL) - set(records): raise ValueError('missing donor metadata file')
    shards = sorted(set(records)-set(SMALL))
    if not shards or len(shards) != donor['shard_count']: raise ValueError('donor shard count mismatch')
    # The historical generator used stored file order, despite its prose label.
    identity = [{k: row[k] for k in ('path', 'size_bytes', 'sha256')} for row in donor['files']]
    if digest(canonical(identity)) != donor['ModelFingerprint']:
        raise ValueError('historical fingerprint differs from stored file records')
    return records, shards


def expected_tensors(config):
    if config.get('architectures') != ['Qwen3MoeForCausalLM'] or config.get('model_type') != 'qwen3_moe':
        raise ValueError('unsupported HF architecture')
    if config.get('torch_dtype') != 'bfloat16' or config.get('attention_bias') is not False or \
       config.get('tie_word_embeddings') is not False or config.get('mlp_only_layers') != [] or \
       config.get('decoder_sparse_step') != 1 or config.get('shared_expert_intermediate_size') not in (None, 0):
        raise ValueError('unsupported Qwen variant/dtype')
    L,E,H,M,A,K,D,V = [positive(config[k]) for k in ('num_hidden_layers','num_experts',
        'hidden_size','moe_intermediate_size','num_attention_heads','num_key_value_heads','head_dim','vocab_size')]
    if positive(config['num_experts_per_tok']) > E or 3*L*E+9*L+3 > LIMITS['tensors']:
        raise ValueError('unsupported expert count/top-k')
    result = {'model.embed_tokens.weight':[V,H], 'lm_head.weight':[V,H], 'model.norm.weight':[H]}
    for layer in range(L):
        p = f'model.layers.{layer}.'
        for name, shape in {'mlp.gate.weight':[E,H], 'input_layernorm.weight':[H],
            'post_attention_layernorm.weight':[H], 'self_attn.q_proj.weight':[A*D,H],
            'self_attn.k_proj.weight':[K*D,H], 'self_attn.v_proj.weight':[K*D,H],
            'self_attn.o_proj.weight':[H,A*D], 'self_attn.q_norm.weight':[D],
            'self_attn.k_norm.weight':[D]}.items(): result[p+name] = shape
        for expert in range(E):
            for projection,shape in (('gate',[M,H]),('up',[M,H]),('down',[H,M])):
                result[p+f'mlp.experts.{expert}.{projection}_proj.weight'] = shape
    return result


def validate_tensor_inventory(donor, blobs, states, *, table=None):
    files, shards = donor_files(donor)
    config = strict_object(blobs['config.json']); index = strict_object(blobs['model.safetensors.index.json'])
    expected = expected_tensors(config)
    for key,value in donor['configuration'].items():
        if config.get(key) != value: raise ValueError('donor configuration mismatch: '+key)
    if donor['architecture'] != config['architectures'] or donor['dtype'] != 'bfloat16':
        raise ValueError('donor architecture/dtype mismatch')
    if donor['has_shared_expert'] is not False or donor['shared_expert_tensors'] != []:
        raise ValueError('unsupported shared experts')
    if index['weight_map'] != donor['weight_map'] or index['metadata'] != donor['index_metadata']:
        raise ValueError('donor/index mismatch')
    for name in SMALL:
        if digest(blobs[name]) != files[name]['sha256']: raise ValueError('small metadata SHA mismatch: '+name)
    for name,sha in donor['tokenizer_hashes'].items():
        if name not in SMALL or sha != digest(blobs[name]): raise ValueError('tokenizer hash mismatch')
    tensors = {}; total = 0
    for shard in shards:
        raw = blobs[shard]
        if len(raw) < 8 or struct.unpack('<Q',raw[:8])[0] != len(raw)-8:
            raise ValueError('frozen header prefix mismatch')
        header = strict_object(raw[8:]); spans = []
        for name,row in header.items():
            if name == '__metadata__':
                if not isinstance(row,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in row.items()):
                    raise ValueError('invalid safetensors metadata')
                continue
            if name in tensors or name not in expected or not isinstance(row,dict) or set(row) != {'dtype','shape','data_offsets'}:
                raise ValueError('unexpected/duplicate tensor descriptor')
            shape = row['shape']; offsets = row['data_offsets']
            if row['dtype'] != 'BF16' or shape != expected[name] or any(type(n) is not int or n <= 0 for n in shape):
                raise ValueError('unsupported tensor dtype/shape')
            if not isinstance(offsets,list) or len(offsets)!=2 or any(type(n) is not int or n<0 for n in offsets):
                raise ValueError('invalid tensor offsets')
            lo,hi = offsets; size = math.prod(shape)*2
            if hi-lo != size or hi+len(raw) > states[shard]['file_identity']['size']:
                raise ValueError('tensor byte extent mismatch')
            if index['weight_map'].get(name) != shard: raise ValueError('index shard assignment mismatch')
            tensors[name] = dict(tensor=name, dtype='BF16', shape=shape, bytes=size,
                data_offset_begin=lo, data_offset_end=hi, file_offset_begin=lo+len(raw),
                file_offset_end=hi+len(raw), source_shard=shard, source_shard_sha256=files[shard]['sha256'])
            spans.append((lo,hi)); total += size
        end = 0
        for lo,hi in sorted(spans):
            if lo != end: raise ValueError('overlapping or missing tensor payload extents')
            end = hi
        if end+len(raw) != states[shard]['file_identity']['size']:
            raise ValueError('unaccounted shard payload bytes')
    if set(tensors) != set(expected) or set(tensors) != set(index['weight_map']):
        raise ValueError('incomplete tensor/index inventory')
    layers=config['num_hidden_layers']; experts=config['num_experts']; seen=set(); sizes=set(); expert_total=0
    for row in donor['expert_weights']:
        layer=row['layer']; expert=row['expert_id']; key=(layer,expert)
        if type(layer) is not int or type(expert) is not int or not 0<=layer<layers or not 0<=expert<experts or key in seen:
            raise ValueError('invalid or duplicate donor expert')
        seen.add(key); prefix=f'model.layers.{layer}.mlp.experts.{expert}.'
        names=[prefix+p+'_proj.weight' for p in ('gate','up','down')]
        actual=row['w13']['tensors']+[row['w2']['tensor']]
        if len(actual)!=3 or any(a != tensors[n] for a,n in zip(actual,names)):
            raise ValueError('historical expert tensor location mismatch')
        size=sum(tensors[n]['bytes'] for n in names)
        if row['total_bytes'] != size or row['w13']['bytes'] != sum(tensors[n]['bytes'] for n in names[:2]) or \
           row['w2']['bytes'] != tensors[names[2]]['bytes'] or size%16384:
            raise ValueError('expert aggregate/alignment mismatch')
        if row['w13']['dtype'] != 'BF16' or row['w2']['dtype'] != 'BF16' or \
           row['w13']['shape_components'] != [tensors[n]['shape'] for n in names[:2]] or row['w2']['shape'] != tensors[names[2]]['shape']:
            raise ValueError('expert aggregate shape/dtype mismatch')
        sizes.add(size); expert_total+=size
    shard_bytes=sum(states[s]['file_identity']['size'] for s in shards)
    aggregates = dict(expert_count_total=layers*experts,expert_weight_bytes=expert_total,
        tensor_payload_bytes=total,non_expert_tensor_bytes=total-expert_total,
        safetensors_shard_bytes=shard_bytes,total_model_file_bytes=sum(s['file_identity']['size'] for s in states.values()))
    if len(seen)!=layers*experts or len(sizes)!=1 or donor['per_expert_bytes_unique'] != sorted(sizes):
        raise ValueError('incomplete expert inventory')
    if any(donor[k]!=v for k,v in aggregates.items()) or index['metadata']['total_size']!=total:
        raise ValueError('donor aggregate byte/count mismatch')
    if table is not None: table.update(tensors)
    return dict(layers=layers, experts_per_layer=experts, top_k=config['num_experts_per_tok'],
        tensor_count=len(tensors),expert_count=len(seen),expert_weight_bytes=expert_total,
        tensor_payload_bytes=total,resident_non_offloaded_bytes=total-expert_total,
        shard_count=len(shards),dtype='BF16',tensor_metadata_sha256=digest(canonical(tensors)))


def current_snapshots(checkpoint, donor):
    records,shards = donor_files(donor)
    if sorted(p.name for p in checkpoint.glob('*.safetensors')) != shards:
        raise ValueError('checkpoint shard set mismatch')
    blobs={}; states={}; budget=dict(remaining=LIMITS['current_total_bytes'])
    for name,row in records.items():
        raw,state = snapshot(checkpoint/name,header=name in shards,budget=budget,expected=row,
                             limit=LIMITS['header_bytes'] if name in shards else LIMITS['file_bytes'])
        ident=state['file_identity']
        if state['realpath']!=row['realpath'] or ident['size']!=row['size_bytes'] or ident['mtime_ns']!=row['mtime_ns']:
            raise ValueError('historical path/size/mtime drift: '+name)
        blobs[name]=raw;states[name]=state
    assert_current(states)
    return blobs,states


def assert_current(states):
    for record in states.values():
        observed=path_state(record['path'])
        if any(observed[k]!=record[k] for k in observed): raise ValueError('input changed across metadata acquisition')
    shards=[record for name,record in states.items() if name.endswith('.safetensors')]
    if shards:
        checkpoint=Path(shards[0]['path']).parent
        expected=sorted(Path(record['path']).name for record in shards)
        if any(Path(record['path']).parent!=checkpoint for record in shards) or \
           sorted(path.name for path in checkpoint.glob('*.safetensors'))!=expected:
            raise ValueError('source shard set changed during metadata acquisition')


def artifact_path(name):
    return ('metadata/'+name) if name in SMALL else ('headers/'+name+'.header')


def identities(receipt):
    metadata=digest(canonical(dict(contract_version=1,limits=LIMITS,summary=receipt['summary'],
        metadata_hashes={name:row['metadata_sha256'] for name,row in receipt['inputs'].items()})))
    observation=digest(canonical(dict(metadata=metadata,inputs=receipt['inputs'],
        artifacts=receipt['artifacts'],created_at=receipt['created_at'],started_at=receipt['started_at'],
        verifier=receipt['source_sha256'],execution=receipt['execution'])))
    return metadata,observation


def publish_complete(out, receipt):
    raw,_=snapshot(out/'receipt.json',header=False,budget=dict(remaining=LIMITS['file_bytes']),limit=LIMITS['file_bytes'],confined_to=out)
    if strict_object(raw)!=receipt:raise ValueError('receipt changed before final publication')
    marker=dict(schema_version=1,status='METADATA_VERIFIED',receipt_sha256=digest(raw),
                observation_identity_sha256=receipt['observation_identity_sha256'])
    fd,name=tempfile.mkstemp(prefix='.complete-',dir=out)
    try:
        with os.fdopen(fd,'wb') as handle:
            handle.write(canonical(marker));handle.flush();os.fsync(handle.fileno())
        os.link(name,out/'COMPLETE.json')  # exclusive publication; never replace a prior marker
    finally:os.unlink(name)
    directory=os.open(out,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)


def check_artifact_set(out, expected):
    inventory,rejected=artifact_inventory(out)
    # The scheduler's inventory deliberately omits these root names. This
    # standalone bundle has no scheduler files, so they are unexpected here.
    if any(os.path.lexists(out/name) for name in ('manifest.json','status.json')) or rejected or set(inventory)!=expected:
        raise ValueError('unexpected or unsafe bundle artifact')


def verify(checkpoint, donor, out, *, test_only=False):
    checkpoint=Path(os.path.abspath(checkpoint)); donor=Path(os.path.abspath(donor)); out=Path(out).resolve()
    if not checkpoint.is_relative_to(ROOT.parent) or not out.is_relative_to(ROOT) or \
       out.is_relative_to(ROOT/'results/runs') or out.is_relative_to(checkpoint) or out.is_relative_to(checkpoint.resolve(strict=True)):
        raise ValueError('metadata paths must stay in the experiment boundary, outside formal runs/checkpoint')
    out.mkdir(parents=True,exist_ok=False)
    started_at=datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        donor_raw,donor_state=snapshot(donor,header=False,budget=dict(remaining=LIMITS['donor_bytes']),limit=LIMITS['donor_bytes'])
        document=strict_object(donor_raw)
        if str(checkpoint)!=document['model_input_path']: raise ValueError('checkpoint view differs from donor')
        blobs,states=current_snapshots(checkpoint,document)
        table={};summary=validate_tensor_inventory(document,blobs,states,table=table)
        table_raw=canonical(table)
        if len(table_raw)>LIMITS['file_bytes']:raise ValueError('normalized tensor table too large')
        artifacts={'donor.json':donor_raw,'tensors.json':table_raw}
        artifacts.update({artifact_path(name):raw for name,raw in blobs.items()})
        for name,raw in artifacts.items():
            path=out/name;path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as handle:
                handle.write(raw);handle.flush();os.fsync(handle.fileno())
        if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
        from adapters.vllm_capacity.model_inventory import ModelInventory
        compatible=ModelInventory(out/'donor.json')
        if compatible.expert_weight_bytes!=summary['expert_weight_bytes']:
            raise ValueError('legacy ModelInventory compatibility mismatch')
        assert_current(states);assert_current({'donor':donor_state})
        interpreter=interpreter_identity()
        receipt=dict(schema_version=1,status='METADATA_VERIFIED',
            started_at=started_at,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            evidence='TEST_ONLY' if test_only else 'CHECKPOINT_METADATA',provenance='MOCK' if test_only else 'CHECKPOINT_METADATA',
            scientific_validation_passed=False,weight_payload_rehashed=False,weight_payload_sha256=None,
            checkpoint_origin_authenticated=False,hardware_validated=False,resource_class='CPU_ONLY',
            checkpoint_format='HF_SAFETENSORS',weight_payload_matches_historical='NOT_CHECKED',
            payload_identity_status='HISTORICAL_ONLY_NOT_CURRENTLY_AUTHENTICATED',
            historical_model_fingerprint=document['ModelFingerprint'],historical_generated_at=document['generated_at_utc'],
            checkpoint=str(checkpoint),donor_source=donor_state,inputs=states,summary=summary,limits=LIMITS,
            metadata_bytes_read=sum(len(raw) for raw in blobs.values()),
            artifacts={name:digest(raw) for name,raw in artifacts.items()},
            legacy_inventory_sha256=digest(donor_raw),
            source_sha256=digest(Path(__file__).read_bytes()),
            execution=dict(argv=list(sys.argv),invocation=dict(checkpoint=str(checkpoint),donor=str(donor),out=str(out)),
                           git=git_snapshot(ROOT),environment=environment_snapshot(),interpreter=interpreter))
        receipt['metadata_identity_sha256'],receipt['observation_identity_sha256']=identities(receipt)
        assert_current(states);assert_current({'donor':donor_state})
        atomic_json(out/'receipt.json',receipt)
        _validate_refresh(out,require_complete=False)
        assert_current(states);assert_current({'donor':donor_state})
        publish_complete(out,receipt)
        validate_refresh(out)
        return receipt
    except BaseException as error:
        if (out/'receipt.json').exists():
            atomic_json(out/'receipt.json',dict(status='INVALID_METADATA',scientific_validation_passed=False,
                                              weight_payload_rehashed=False,error=str(error)))
        atomic_json(out/'failure.json',dict(status='INVALID_METADATA',error=str(error),
            scientific_validation_passed=False,weight_payload_rehashed=False))
        raise


def _validate_refresh(out, *, require_complete):
    out=Path(out).resolve()
    if not out.is_relative_to(ROOT) or out.is_relative_to(ROOT/'results/runs'):
        raise ValueError('invalid metadata bundle boundary')
    raw,receipt_state=snapshot(out/'receipt.json',header=False,budget=dict(remaining=LIMITS['file_bytes']),limit=LIMITS['file_bytes'],confined_to=out)
    receipt=strict_object(raw)
    if receipt.get('schema_version')!=1 or receipt.get('status')!='METADATA_VERIFIED' or \
       receipt.get('scientific_validation_passed') is not False or receipt.get('weight_payload_rehashed') is not False or \
       receipt.get('weight_payload_sha256') is not None or receipt.get('limits')!=LIMITS or \
       receipt.get('payload_identity_status')!='HISTORICAL_ONLY_NOT_CURRENTLY_AUTHENTICATED' or \
       receipt.get('checkpoint_origin_authenticated') is not False or receipt.get('hardware_validated') is not False or \
       receipt.get('resource_class')!='CPU_ONLY' or receipt.get('checkpoint_format')!='HF_SAFETENSORS' or \
       receipt.get('weight_payload_matches_historical')!='NOT_CHECKED':
        raise ValueError('invalid metadata-only claim boundary')
    if (receipt['evidence'],receipt['provenance']) not in (('TEST_ONLY','MOCK'),('CHECKPOINT_METADATA','CHECKPOINT_METADATA')):
        raise ValueError('invalid evidence attribution')
    marker_state=None
    if require_complete:
        if not (out/'COMPLETE.json').is_file():raise ValueError('metadata publication is incomplete')
        marker_raw,marker_state=snapshot(out/'COMPLETE.json',header=False,budget=dict(remaining=4096),limit=4096,confined_to=out)
        marker=strict_object(marker_raw)
        if marker!=dict(schema_version=1,status='METADATA_VERIFIED',receipt_sha256=digest(raw),
                        observation_identity_sha256=receipt['observation_identity_sha256']):
            raise ValueError('metadata publication marker mismatch')
    expected_files={*receipt['artifacts'],'receipt.json'}
    if require_complete:expected_files.add('COMPLETE.json')
    check_artifact_set(out,expected_files)
    loaded={};frozen_states={'receipt.json':receipt_state};budget=dict(remaining=LIMITS['donor_bytes']+LIMITS['current_total_bytes']+LIMITS['file_bytes'])
    if marker_state is not None:frozen_states['COMPLETE.json']=marker_state
    for name,sha in receipt['artifacts'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or (out/name).is_symlink():
            raise ValueError('unsafe frozen artifact')
        raw,state=snapshot(out/name,header=False,budget=budget,confined_to=out,
                           limit=LIMITS['donor_bytes'] if name=='donor.json' else LIMITS['file_bytes']+8)
        if not Path(state['realpath']).is_relative_to(out) or state['chain']!=path_state(out/name)['chain'] or digest(raw)!=sha:
            raise ValueError('frozen artifact hash/path mismatch')
        loaded[name]=raw
        frozen_states[name]=state
    document=strict_object(loaded['donor.json']);records,_=donor_files(document)
    if set(loaded)!={'donor.json','tensors.json',*(artifact_path(n) for n in records)} or set(receipt['inputs'])!=set(records):
        raise ValueError('incomplete frozen bundle')
    blobs={name:loaded[artifact_path(name)] for name in records}
    for name,state in receipt['inputs'].items():
        ranges=[[0,len(blobs[name])]] if name in SMALL else [[0,8],[8,len(blobs[name])-8]]
        if state['read_bytes']!=len(blobs[name]) or state['metadata_sha256']!=digest(blobs[name]) or state['read_ranges']!=ranges:
            raise ValueError('snapshot metadata mismatch')
    table={};summary=validate_tensor_inventory(document,blobs,receipt['inputs'],table=table)
    if strict_object(loaded['tensors.json'])!=table:raise ValueError('normalized tensor table mismatch')
    metadata,observation=identities(receipt)
    if receipt['summary']!=summary or receipt['metadata_identity_sha256']!=metadata or receipt['observation_identity_sha256']!=observation or \
       receipt['historical_model_fingerprint']!=document['ModelFingerprint'] or \
       receipt['legacy_inventory_sha256']!=digest(loaded['donor.json']) or \
       receipt['metadata_bytes_read']!=sum(len(b) for b in blobs.values()):
        raise ValueError('metadata receipt reconciliation failed')
    assert_current(frozen_states)
    check_artifact_set(out,expected_files)
    return receipt


def validate_refresh(out):
    """A provisional receipt is never accepted without its final commit marker."""
    return _validate_refresh(out,require_complete=True)


def check_current_inputs(out):
    receipt=validate_refresh(out)
    raw,_=snapshot(Path(out)/'donor.json',header=False,budget=dict(remaining=LIMITS['donor_bytes']),limit=LIMITS['donor_bytes'],confined_to=out)
    if digest(raw)!=receipt['legacy_inventory_sha256']:raise ValueError('donor changed after frozen validation')
    donor=strict_object(raw)
    blobs,states=current_snapshots(Path(receipt['checkpoint']),donor)
    if states!=receipt['inputs']: raise ValueError('current file identities differ from frozen metadata')
    if validate_tensor_inventory(donor,blobs,states)!=receipt['summary']: raise ValueError('current metadata mismatch')
    return dict(status='CURRENT_METADATA_MATCH',metadata_identity_sha256=receipt['metadata_identity_sha256'],
                weight_payload_rehashed=False,scientific_validation_passed=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    verify_parser=sub.add_parser('verify');verify_parser.add_argument('--checkpoint',required=True,type=Path)
    verify_parser.add_argument('--inventory',required=True,type=Path);verify_parser.add_argument('--out',required=True,type=Path)
    for command in ('validate','check-current'):sub.add_parser(command).add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    try:
        result=verify(args.checkpoint,args.inventory,args.out) if args.command=='verify' else \
            (validate_refresh(args.out) if args.command=='validate' else check_current_inputs(args.out))
        print(json.dumps({k:result[k] for k in ('status','metadata_identity_sha256','weight_payload_rehashed','scientific_validation_passed')}))
    except (ValueError,OSError,KeyError,TypeError) as error:parser.exit(2,str(error)+'\n')


if __name__=='__main__':main()

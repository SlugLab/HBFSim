"""Read-only identity of one packaged MoE tuning file, or its exact absence.

This module does not import inference packages, inspect GPU state, execute a
tuner or inspect the private LRU cache. Device name is a declared input whose
actual runtime binding must be checked by the later owned worker.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat

import hf_runtime_sources as sources
from evaluation_inventory import _unpack
from verify_hf_metadata import canonical,digest,positive,snapshot,strict_object,assert_current

MAX_CONFIG_BYTES=1<<20


@dataclass(frozen=True)
class TuningInputsSnapshot:
    manifest_bytes:bytes
    config_bytes:bytes|None


def _binding(metadata_snapshot,runtime_snapshot,device_name):
    receipt,artifacts,_,_=_unpack(metadata_snapshot)
    source=sources.validate_runtime_sources(runtime_snapshot)
    if source.get('source_extension')!='MOE_TUNING_V1':
        raise ValueError('selected tuning requires the explicit runtime source extension')
    if type(device_name) is not str or not 0<len(device_name)<=256 or \
       re.fullmatch(r'[A-Za-z0-9 _().+-]+',device_name) is None:
        raise ValueError('device name must be a bounded safe filename component')
    config=strict_object(artifacts['metadata/config.json'])
    experts=positive(config['num_experts']);hidden=positive(config['hidden_size'])
    intermediate=positive(config['moe_intermediate_size'])
    normalized=device_name.replace(' ','_')
    if 'H200' in normalized.split('_'):normalized='NVIDIA_H200'
    filename=f'E={experts},N={intermediate},device_name={normalized}.json'
    path=Path(source['source_root'])/'vllm/model_executor/layers/fused_moe/configs'/filename
    ancestors={}
    for state in source['source_states'].values():
        for row in state['chain']:ancestors[row['path']]=(row['device'],row['inode'])
    return dict(metadata_receipt_sha256=digest(metadata_snapshot.receipt_bytes),
        metadata_complete_sha256=digest(metadata_snapshot.complete_bytes),
        metadata_identity_sha256=receipt['metadata_identity_sha256'],
        runtime_manifest_sha256=digest(runtime_snapshot.manifest_bytes),
        device_name_declared=device_name,selected_path=str(path),
        weight_shapes=dict(w13=[experts,2*intermediate,hidden],w2=[experts,hidden,intermediate]),
        dtype='bfloat16',config_dtype_selector=None,block_shape=None,
        test_only=bool(receipt['evidence']=='TEST_ONLY' or source['test_only'])),ancestors


def _directory_chain(directory):
    chain=[]
    for path in (*reversed(directory.parents),directory):
        if path==Path('/'):continue
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode):raise ValueError('selected tuning directory is missing or aliased')
        chain.append(dict(path=str(path),device=info.st_dev,inode=info.st_ino))
    return chain


def _validate_chain(directory,chain,ancestors):
    parents=[str(path) for path in (*reversed(directory.parents),directory) if path!=Path('/')]
    if type(chain) is not list or len(chain)!=len(parents):raise ValueError('invalid tuning directory chain')
    joined=dict(ancestors)
    for expected,row in zip(parents,chain):
        if type(row) is not dict or set(row)!={'path','device','inode'} or row['path']!=expected or \
           any(type(row[k]) is not int or row[k]<0 for k in ('device','inode')):
            raise ValueError('invalid tuning directory identity')
        identity=(row['device'],row['inode'])
        if joined.setdefault(expected,identity)!=identity:
            raise ValueError('tuning directory differs from the frozen runtime ancestor')
    return joined


def _derive(binding,ancestors,chain,state,raw):
    path=Path(binding['selected_path']);joined=_validate_chain(path.parent,chain,ancestors)
    if raw is None:
        if state is not None:raise ValueError('absent tuning input has a file observation')
        selection='INSTALLED_DEFAULTS'
    else:
        sources.validate_selected_buffers({'config':path},{'config':raw},{'config':state},
            per_file_bytes=MAX_CONFIG_BYTES,total_bytes=MAX_CONFIG_BYTES,ancestor_bindings=joined)
        strict_object(raw)
        selection='PACKAGED_JSON'
    return dict(schema_version=1,**binding,directory_chain=chain,file_state=state,
        selection=selection,config_sha256=None if raw is None else digest(raw),
        config_bytes=0 if raw is None else len(raw),
        provenance='MOCK' if binding['test_only'] else 'RUNTIME_TUNING_INPUT',
        device_identity_authenticated=False,scientific_validation_passed=False,
        effective_kernel_configuration_observed=False,
        boundary='One declared-device packaged JSON or absence; loaded weights, device, override/batch state and cached kernel selection require separate runtime observation.')


def collect_tuning_inputs(metadata_snapshot,runtime_snapshot,device_name):
    """Read only the exact derived file; no tuning-directory enumeration."""
    binding,ancestors=_binding(metadata_snapshot,runtime_snapshot,device_name)
    path=Path(binding['selected_path']);chain=_directory_chain(path.parent)
    _validate_chain(path.parent,chain,ancestors)
    try:info=path.lstat()
    except FileNotFoundError:info=None
    if info is None:raw=state=None
    else:
        if not stat.S_ISREG(info.st_mode):raise ValueError('selected tuning input must be a regular file')
        raw,state=snapshot(path,header=False,budget={'remaining':MAX_CONFIG_BYTES},
            limit=MAX_CONFIG_BYTES,confined_to=path.parent,
            expected=dict(realpath=str(path),size_bytes=info.st_size,mtime_ns=info.st_mtime_ns))
    report=_derive(binding,ancestors,chain,state,raw)
    if _directory_chain(path.parent)!=chain:raise ValueError('tuning directory changed during acquisition')
    if state is not None:assert_current({'config':state})
    else:
        try:path.lstat()
        except FileNotFoundError:pass
        else:raise ValueError('absent tuning file appeared during acquisition')
    return TuningInputsSnapshot(canonical(report),raw)


def validate_tuning_inputs(frozen,metadata_snapshot,runtime_snapshot,device_name):
    """Recompute the frozen binding without opening any original input path."""
    if type(frozen) is not TuningInputsSnapshot or type(frozen.manifest_bytes) is not bytes or \
       len(frozen.manifest_bytes)>1<<20:
        raise ValueError('invalid bounded tuning input snapshot')
    binding,ancestors=_binding(metadata_snapshot,runtime_snapshot,device_name)
    report=strict_object(frozen.manifest_bytes)
    expected=_derive(binding,ancestors,report['directory_chain'],report['file_state'],frozen.config_bytes)
    if canonical(report)!=canonical(expected):raise ValueError('tuning manifest differs from frozen derivation')
    return expected


def recheck_tuning_inputs(frozen,metadata_snapshot,runtime_snapshot,device_name):
    validate_tuning_inputs(frozen,metadata_snapshot,runtime_snapshot,device_name)
    current=collect_tuning_inputs(metadata_snapshot,runtime_snapshot,device_name)
    if current!=frozen:raise ValueError('selected tuning input changed after freezing')
    return dict(status='TUNING_INPUT_UNCHANGED',manifest_sha256=digest(frozen.manifest_bytes),
        scientific_validation_passed=False)

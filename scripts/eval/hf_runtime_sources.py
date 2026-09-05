"""Finite, read-only installed-source snapshots for the owned HF control.

This module imports no inference package and never follows distribution RECORD
entries. Selected sources, package metadata and interpreter identity are not a
complete native-binary authentication or capture-origin receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser
import fnmatch
import os
from pathlib import Path
import re
import stat
import sys
import sysconfig

import verify_hf_metadata as metadata

MAX_FILE_BYTES = 16 << 20
MAX_TOTAL_BYTES = 128 << 20
DEFAULT_SOURCE_ROOT = Path(sysconfig.get_path('purelib')).resolve()
DEFAULT_STDLIB_ROOT = Path(sysconfig.get_path('stdlib')).resolve()
DEFAULT_INTERPRETER = Path(sys.executable).resolve()
DISTRIBUTIONS = dict(vllm='0.15.1', torch='2.9.1', transformers='5.5.4',
    safetensors='0.7.0', numpy='2.2.6', triton='3.5.1',
    **{'flashinfer-python': '0.6.1', 'flashinfer-cubin': '0.6.1',
       'apache-tvm-ffi': '0.1.6', 'huggingface-hub': '1.11.0'})
DIST_FILES = ('METADATA', 'WHEEL', 'INSTALLER', 'RECORD')
REQUIRED_DIRECTORIES = ('flashinfer/data/csrc', 'flashinfer_cubin/cubins')
ABSENT_PATTERNS = ('flashinfer/data/aot', 'flashinfer_jit_cache*', 'torch_c_dlpack_ext*')
STDLIB_FILES = ('multiprocessing/shared_memory.py', 'multiprocessing/resource_tracker.py', 'tempfile.py')
TUNING_SOURCE_FILES = ('vllm/model_executor/layers/fused_moe/__init__.py',
                       'vllm/model_executor/layers/batch_invariant.py')
# The initial 80-file source audit is extended with the selected loader/iterator,
# execution config aliases, greedy sampler and no-EP dispatch decisions.
SOURCE_FILES = tuple('''
vllm/__init__.py
vllm/_version.py
vllm/version.py
vllm/envs.py
vllm/attention/layer.py
vllm/config/attention.py
vllm/config/cache.py
vllm/config/compilation.py
vllm/config/load.py
vllm/config/model.py
vllm/config/parallel.py
vllm/config/scheduler.py
vllm/config/vllm.py
vllm/entrypoints/llm.py
vllm/inputs/data.py
vllm/model_executor/custom_op.py
vllm/model_executor/layers/fused_moe/all2all_utils.py
vllm/model_executor/layers/fused_moe/config.py
vllm/model_executor/layers/fused_moe/fused_moe.py
vllm/model_executor/layers/fused_moe/layer.py
vllm/model_executor/layers/fused_moe/modular_kernel.py
vllm/model_executor/layers/fused_moe/oracle/unquantized.py
vllm/model_executor/layers/fused_moe/prepare_finalize.py
vllm/model_executor/layers/fused_moe/routed_experts_capturer.py
vllm/model_executor/layers/fused_moe/shared_fused_moe.py
vllm/model_executor/layers/fused_moe/unquantized_fused_moe_method.py
vllm/model_executor/model_loader/__init__.py
vllm/model_executor/model_loader/base_loader.py
vllm/model_executor/model_loader/default_loader.py
vllm/model_executor/model_loader/weight_utils.py
vllm/model_executor/models/qwen3_moe.py
vllm/outputs.py
vllm/platforms/cuda.py
vllm/sampling_params.py
vllm/usage/usage_lib.py
vllm/utils/flashinfer.py
vllm/v1/attention/backends/registry.py
vllm/v1/attention/backends/triton_attn.py
vllm/v1/core/block_pool.py
vllm/v1/core/kv_cache_utils.py
vllm/v1/core/sched/scheduler.py
vllm/v1/engine/core.py
vllm/v1/engine/core_client.py
vllm/v1/engine/llm_engine.py
vllm/v1/executor/abstract.py
vllm/v1/executor/uniproc_executor.py
vllm/v1/kv_cache_interface.py
vllm/v1/sample/ops/topk_topp_sampler.py
vllm/v1/sample/sampler.py
vllm/v1/worker/gpu_model_runner.py
vllm/v1/worker/gpu_worker.py
vllm/v1/worker/utils.py
vllm/v1/worker/worker_base.py
flashinfer/__init__.py
flashinfer/_build_meta.py
flashinfer/api_logging.py
flashinfer/artifacts.py
flashinfer/autotuner.py
flashinfer/compilation_context.py
flashinfer/jit/core.py
flashinfer/jit/cpp_ext.py
flashinfer/jit/cubin_loader.py
flashinfer/jit/env.py
flashinfer/version.py
flashinfer_cubin/__init__.py
flashinfer_cubin/_build_meta.py
huggingface_hub/__init__.py
huggingface_hub/constants.py
huggingface_hub/utils/_http.py
numpy/__init__.py
numpy/version.py
safetensors/__init__.py
torch/__init__.py
torch/_inductor/runtime/cache_dir_utils.py
torch/hub.py
torch/utils/cpp_extension.py
torch/version.py
transformers/__init__.py
transformers/models/qwen3_moe/configuration_qwen3_moe.py
transformers/utils/hub.py
triton/__init__.py
triton/knobs.py
triton/runtime/cache.py
tvm_ffi/__init__.py
tvm_ffi/_optional_torch_c_dlpack.py
tvm_ffi/_version.py
tvm_ffi/cpp/extension.py
tvm_ffi/utils/_build_optional_torch_c_dlpack.py
'''.split())


@dataclass(frozen=True)
class RuntimeSourcesSnapshot:
    manifest_bytes: bytes
    artifacts: tuple[tuple[str, bytes], ...]


def _dist_directory(name, version):
    return name.replace('-', '_') + '-' + version + '.dist-info'


def _paths(site, stdlib, include_tuning=False):
    paths = {'site-packages/'+name: site/name
             for name in SOURCE_FILES + (TUNING_SOURCE_FILES if include_tuning else ())}
    paths.update({'stdlib/'+name: stdlib/name for name in STDLIB_FILES})
    for name, version in DISTRIBUTIONS.items():
        directory = _dist_directory(name, version)
        paths.update({'site-packages/'+directory+'/'+file: site/directory/file for file in DIST_FILES})
    return paths


def _presence(site):
    result = {}
    entries = []
    with os.scandir(site) as iterator:
        for index, entry in enumerate(iterator):
            if index >= 4096:
                raise ValueError('installed root exceeds bounded directory inventory')
            entries.append(entry.name)
    for name in REQUIRED_DIRECTORIES:
        path = site/name
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or path.resolve() != path:
            raise ValueError('required installed package directory is missing or aliased')
        result[name] = dict(path=str(path), device=info.st_dev, inode=info.st_ino, present=True)
    for pattern in ABSENT_PATTERNS:
        present = ((site/pattern).exists() or (site/pattern).is_symlink()) if '/' in pattern else \
            any(fnmatch.fnmatchcase(name.lower(), pattern) for name in entries)
        if present:
            raise ValueError('unexpected installed optional package/artifact: ' + pattern)
    discovered = {name: [] for name in DISTRIBUTIONS}
    for filename in entries:
        lower = filename.lower()
        if lower.endswith(('.dist-info', '.egg-info')):
            # Match Python 3.13 importlib.metadata.Lookup, including legacy
            # metadata and version suffixes with additional hyphens. No files
            # within an unexpected distribution need to be opened.
            stem = lower.rpartition('.')[0].partition('-')[0]
            normalized = re.sub(r'[-_.]+', '-', stem)
            if normalized in discovered: discovered[normalized].append(filename)
    for name, version in DISTRIBUTIONS.items():
        directory = _dist_directory(name, version)
        if sorted(discovered[name]) != [directory]:
            raise ValueError('ambiguous/missing pinned distribution: ' + name)
        if (site/directory/'direct_url.json').exists() or (site/directory/'direct_url.json').is_symlink():
            raise ValueError('unexpected distribution direct_url metadata')
    return result


def _distribution_metadata(artifacts):
    result = {}
    for name, expected in DISTRIBUTIONS.items():
        directory = _dist_directory(name, expected)
        parsed = BytesParser().parsebytes(artifacts['site-packages/'+directory+'/METADATA'])
        names, versions = parsed.get_all('Name', []), parsed.get_all('Version', [])
        if len(names) != 1 or len(versions) != 1 or \
           re.sub(r'[-_.]+', '-', names[0]).lower() != name or versions[0] != expected:
            raise ValueError('distribution name/version differs from pinned control')
        result[name] = dict(version=expected, directory=directory, direct_url_present=False,
            metadata_sha256={file: metadata.digest(artifacts['site-packages/'+directory+'/'+file])
                             for file in DIST_FILES})
    return result


def _valid_file_identity(value):
    return type(value) is dict and set(value) == {'device','inode','size','mtime_ns','ctime_ns'} and \
        all(type(number) is int and number >= 0 for number in value.values())


def _assemble(site, stdlib, artifacts, states, presence, interpreter, test_only, include_tuning=False):
    if type(include_tuning) is not bool:
        raise ValueError('runtime source extension must be an explicit boolean')
    if any(not path.is_absolute() or str(path) != os.path.normpath(str(path))
           for path in (site, stdlib)):
        raise ValueError('runtime roots must be canonical absolute paths')
    paths = _paths(site, stdlib, include_tuning)
    if set(artifacts) != set(paths) or set(states) != set(paths):
        raise ValueError('runtime source artifact set differs from finite contract')
    total = 0; ancestors = {}
    for name, path in paths.items():
        raw = artifacts[name]; state = states[name]
        if type(raw) is not bytes or len(raw) > MAX_FILE_BYTES:
            raise ValueError('runtime source exceeds per-file byte bound')
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError('runtime source exceeds aggregate byte bound')
        if type(state) is not dict or set(state) != {
            'path','realpath','chain','file_identity','read_bytes','metadata_sha256','read_ranges'} or \
           not _valid_file_identity(state['file_identity']) or \
           type(state['read_bytes']) is not int:
            raise ValueError('runtime source file identity has invalid fields/types')
        parents = [str(parent) for parent in reversed(path.parents) if parent != Path('/')]
        chain = state['chain']
        if type(chain) is not list or len(chain) != len(parents) or any(
            type(row) is not dict or set(row) != {'path','device','inode'} or row['path'] != parent or
            any(type(row[key]) is not int or row[key] < 0 for key in ('device','inode'))
            for row, parent in zip(chain, parents)):
            raise ValueError('runtime source path observation differs from canonical ancestor chain')
        for row in chain:
            identity = (row['device'], row['inode'])
            if ancestors.setdefault(row['path'], identity) != identity:
                raise ValueError('runtime source files disagree on a common ancestor identity')
        if state['path'] != str(path) or state['realpath'] != str(path) or \
           state['file_identity']['size'] != len(raw) or state['read_bytes'] != len(raw) or \
           metadata.canonical(state['read_ranges']) != metadata.canonical([[0, len(raw)]]) or \
           state['metadata_sha256'] != metadata.digest(raw):
            raise ValueError('runtime source buffer differs from observed identity/read range')
    if set(presence) != set(REQUIRED_DIRECTORIES) or any(
        row != dict(path=str(site/name), device=row.get('device'), inode=row.get('inode'), present=True) or
        any(type(row.get(key)) is not int or row[key] < 0 for key in ('device', 'inode'))
        for name, row in presence.items()):
        raise ValueError('invalid installed directory observations')
    if type(test_only) is not bool or not test_only and (site != DEFAULT_SOURCE_ROOT or stdlib != DEFAULT_STDLIB_ROOT):
        raise ValueError('injected runtime roots require TEST_ONLY evidence')
    if type(interpreter) is not dict or set(interpreter) != {'path','sha256','file_identity'} or \
       not _valid_file_identity(interpreter['file_identity']) or \
       not Path(interpreter['path']).is_absolute() or \
       not re.fullmatch('[0-9a-f]{64}', interpreter['sha256']) or \
       type(interpreter['file_identity']['size']) is not int or not 0 < interpreter['file_identity']['size'] <= 512 << 20:
        raise ValueError('invalid bounded interpreter identity')
    if not test_only and interpreter['path'] != str(DEFAULT_INTERPRETER):
        raise ValueError('overridden interpreter requires TEST_ONLY evidence')
    document = dict(schema_version=1, evidence='TEST_ONLY' if test_only else 'RUNTIME_SOURCE_METADATA',
        provenance='MOCK' if test_only else 'RUNTIME_SOURCE_METADATA', test_only=test_only,
        source_root=str(site), stdlib_root=str(stdlib), artifacts={k: metadata.digest(v) for k,v in artifacts.items()},
        source_states=states, directory_observations=presence, absent_patterns=list(ABSENT_PATTERNS),
        distributions=_distribution_metadata(artifacts), source_file_count=len(SOURCE_FILES)+(len(TUNING_SOURCE_FILES) if include_tuning else 0),
        stdlib_file_count=len(STDLIB_FILES), total_source_metadata_bytes=total, interpreter=interpreter,
        weight_payload_rehashed=False, all_runtime_binaries_authenticated=False,
        scientific_validation_passed=False,
        boundary='Selected Python sources and distribution metadata only; RECORD targets, native package binaries and directory contents are not authenticated.')
    if include_tuning:document['source_extension']='MOE_TUNING_V1'
    return document


def collect_runtime_sources(*, source_root=None, stdlib_root=None, interpreter=None, include_tuning=False):
    """Read the finite installed set; explicit path overrides always yield MOCK."""
    if type(include_tuning) is not bool:
        raise ValueError('runtime source extension must be an explicit boolean')
    test_only = any(value is not None for value in (source_root, stdlib_root, interpreter))
    site = Path(source_root or DEFAULT_SOURCE_ROOT).absolute()
    stdlib = Path(stdlib_root or DEFAULT_STDLIB_ROOT).absolute()
    return _collect(site, stdlib, interpreter, test_only, include_tuning)


def _collect(site, stdlib, interpreter, test_only, include_tuning=False):
    if site.resolve() != site or stdlib.resolve() != stdlib:
        raise ValueError('runtime roots must be canonical directories')
    presence = _presence(site)
    budget = dict(remaining=MAX_TOTAL_BYTES); artifacts = {}; states = {}
    for name, path in sorted(_paths(site, stdlib, include_tuning).items()):
        boundary = site if name.startswith('site-packages/') else stdlib
        raw, state = metadata.snapshot(path, header=False, budget=budget, limit=MAX_FILE_BYTES, confined_to=boundary)
        artifacts[name], states[name] = raw, state
    identity = metadata.interpreter_identity(executable=interpreter)
    metadata.assert_current(states)
    if _presence(site) != presence:
        raise ValueError('runtime directory/package presence changed while reading')
    document = _assemble(site, stdlib, artifacts, states, presence, identity, test_only, include_tuning)
    return RuntimeSourcesSnapshot(metadata.canonical(document), tuple(sorted(artifacts.items())))


def validate_runtime_sources(snapshot):
    """Validate exact frozen buffers without opening any recorded runtime path."""
    if type(snapshot) is not RuntimeSourcesSnapshot or type(snapshot.manifest_bytes) is not bytes or \
       len(snapshot.manifest_bytes) > 8 << 20:
        raise ValueError('invalid bounded runtime source snapshot')
    document = metadata.strict_object(snapshot.manifest_bytes)
    include_tuning='source_extension' in document
    if include_tuning and document['source_extension']!='MOE_TUNING_V1':
        raise ValueError('unsupported runtime source extension')
    artifacts = dict(snapshot.artifacts)
    if len(artifacts) != len(snapshot.artifacts):
        raise ValueError('duplicate runtime source artifact')
    expected = _assemble(Path(document['source_root']), Path(document['stdlib_root']), artifacts,
        document['source_states'], document['directory_observations'], document['interpreter'], document['test_only'], include_tuning)
    if metadata.canonical(document) != metadata.canonical(expected):
        raise ValueError('runtime source manifest differs from frozen derivation')
    return expected


def recheck_runtime_sources(snapshot):
    """Rehash only the same finite runtime inputs; never follow RECORD entries."""
    document = validate_runtime_sources(snapshot)
    current = _collect(Path(document['source_root']), Path(document['stdlib_root']),
        document['interpreter']['path'], document['test_only'], 'source_extension' in document)
    if current.manifest_bytes != metadata.canonical(document):
        raise ValueError('installed runtime sources changed after freezing')
    return dict(status='RUNTIME_SOURCES_UNCHANGED',
        manifest_sha256=metadata.digest(snapshot.manifest_bytes),
        scientific_validation_passed=False, provenance=document['provenance'])

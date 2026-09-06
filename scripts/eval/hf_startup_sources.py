"""Bounded fixed-contract startup Python source snapshots."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat

import hf_runtime_sources as runtime
import verify_hf_metadata as metadata

MAX_FILE_BYTES = runtime.MAX_FILE_BYTES
MAX_TOTAL_BYTES = runtime.MAX_TOTAL_BYTES
STARTUP_CONTRACT = 'SINGLE_GPU_NO_ADDON_V1'
SCOPE = 'Selected Python startup sources and stub source identities only; native binaries and model payloads are outside this snapshot.'
STARTUP_SOURCE_FILES = (
    'torch/cuda/__init__.py',
    'torch/_C/__init__.pyi',
    'vllm/env_override.py',
    'vllm/platforms/__init__.py',
    'vllm/platforms/interface.py',
    'vllm/plugins/__init__.py',
    'vllm/utils/torch_utils.py',
)


@dataclass(frozen=True)
class StartupSourcesSnapshot:
    manifest_bytes: bytes
    artifacts: tuple[tuple[str, bytes], ...]


def _paths(source_root):
    root = Path(source_root)
    return {'site-packages/' + name: root / name for name in STARTUP_SOURCE_FILES}


def _ancestor_bindings(states):
    bindings = {}
    for state in states.values():
        for row in state['chain']:
            identity = (row['device'], row['inode'])
            if bindings.setdefault(row['path'], identity) != identity:
                raise ValueError('runtime and startup sources disagree on an ancestor identity')
    return bindings


def _primary(runtime_snapshot):
    primary = runtime.validate_runtime_sources(runtime_snapshot)
    if primary.get('source_extension') != 'MOE_TUNING_V1':
        raise ValueError('startup sources require the MOE_TUNING_V1 runtime snapshot')
    if type(primary.get('source_root')) is not str or type(primary.get('source_states')) is not dict:
        raise ValueError('runtime source binding is invalid')
    primary['_raw_manifest'] = runtime_snapshot.manifest_bytes
    return primary


def _checked_artifacts(snapshot):
    if type(snapshot) is not StartupSourcesSnapshot or type(snapshot.manifest_bytes) is not bytes:
        raise ValueError('invalid bounded startup source snapshot')
    if len(snapshot.manifest_bytes) > 8 << 20:
        raise ValueError('startup source manifest exceeds bound')
    if type(snapshot.artifacts) is not tuple or len(snapshot.artifacts) != 7:
        raise ValueError('startup source artifact count must be exactly seven')
    artifacts = {}
    for entry in snapshot.artifacts:
        if type(entry) is not tuple or len(entry) != 2:
            raise ValueError('startup source artifact entry is invalid')
        name, raw = entry
        if type(name) is not str or type(raw) is not bytes:
            raise ValueError('startup source artifact key or bytes are invalid')
        if name in artifacts:
            raise ValueError('duplicate startup source artifact')
        artifacts[name] = raw
    return artifacts


def _observe_selected(primary, paths):
    root = Path(primary['source_root'])
    bindings = _ancestor_bindings(primary['source_states'])
    if str(root) not in bindings:
        raise ValueError('primary snapshot does not bind the startup source root')
    states = {}
    total = 0
    for name, path in sorted(paths.items()):
        if not path.is_absolute() or not path.is_relative_to(root) or path == root:
            raise ValueError('startup source path is outside the fixed primary root')
        state = metadata.path_state(path)
        parents = [str(parent) for parent in reversed(path.parents) if parent != Path('/')]
        chain = state['chain']
        if state['path'] != str(path) or state['realpath'] != str(path) or \
           type(chain) is not list or len(chain) != len(parents) or any(
               type(row) is not dict or set(row) != {'path', 'device', 'inode'} or
               row['path'] != parent
               for row, parent in zip(chain, parents)):
            raise ValueError('startup source path is not a canonical non-symlink chain')
        for row in chain:
            identity = (row['device'], row['inode'])
            if bindings.setdefault(row['path'], identity) != identity:
                raise ValueError('startup sources differ from the primary ancestor identity')
        size = state['file_identity']['size']
        if type(size) is not int or size < 0 or size > MAX_FILE_BYTES:
            raise ValueError('startup source exceeds per-file byte bound')
        total += size
        if total > MAX_TOTAL_BYTES - primary['total_source_metadata_bytes']:
            raise ValueError('startup sources exceed combined byte bound')
        states[name] = state
    return states, bindings


def _read_anchored(path, before, bindings, budget):
    descriptors = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open('/', directory_flags)
        descriptors.append(root_fd)
        if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
            raise ValueError('filesystem root is not a directory')
        for component, row in zip(path.parts[1:-1], before['chain']):
            directory_fd = os.open(component, directory_flags, dir_fd=descriptors[-1])
            descriptors.append(directory_fd)
            info = os.fstat(directory_fd)
            expected = (row['device'], row['inode'])
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != expected or \
               bindings.get(row['path']) != expected:
                raise ValueError('startup source ancestor changed before read')
        leaf_fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                          dir_fd=descriptors[-1])
        descriptors.append(leaf_fd)
        if metadata.file_identity(os.fstat(leaf_fd)) != before['file_identity']:
            raise ValueError('startup source file changed before read')
        size = before['file_identity']['size']
        if size > MAX_FILE_BYTES:
            raise ValueError('startup source exceeds per-file byte bound')
        raw = metadata.read_exact(leaf_fd, size, 0, budget)
        if metadata.file_identity(os.fstat(leaf_fd)) != before['file_identity'] or \
           metadata.path_state(path) != before:
            raise ValueError('startup source changed during read')
    except OSError as error:
        raise ValueError('startup source namespace changed before read') from error
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    return raw, dict(before, read_bytes=len(raw), metadata_sha256=metadata.digest(raw),
                     read_ranges=[[0, len(raw)]])


def _capture_selected(primary):
    paths = _paths(primary['source_root'])
    observed, bindings = _observe_selected(primary, paths)
    budget = {'remaining': MAX_TOTAL_BYTES - primary['total_source_metadata_bytes']}
    artifacts, states = {}, {}
    for name, path in sorted(paths.items()):
        raw, state = _read_anchored(path, observed[name], bindings, budget)
        artifacts[name], states[name] = raw, state
    runtime.validate_selected_buffers(
        paths, artifacts, states, per_file_bytes=MAX_FILE_BYTES,
        total_bytes=MAX_TOTAL_BYTES - primary['total_source_metadata_bytes'],
        ancestor_bindings=_ancestor_bindings(primary['source_states']))
    metadata.assert_current(states)
    return artifacts, states


def _manifest(primary, artifacts, states):
    primary_bytes = primary['total_source_metadata_bytes']
    startup_bytes, _ = runtime.validate_selected_buffers(
        _paths(primary['source_root']), artifacts, states,
        per_file_bytes=MAX_FILE_BYTES,
        total_bytes=MAX_TOTAL_BYTES - primary_bytes,
        ancestor_bindings=_ancestor_bindings(primary['source_states']))
    test_only = primary['test_only']
    return dict(
        schema_version=1,
        startup_contract=STARTUP_CONTRACT,
        primary_manifest_sha256=metadata.digest(primary['_raw_manifest']),
        source_root=primary['source_root'],
        artifacts={name: metadata.digest(artifacts[name]) for name in sorted(artifacts)},
        source_states=states,
        source_file_count=7,
        primary_source_metadata_bytes=primary_bytes,
        startup_source_bytes=startup_bytes,
        combined_source_metadata_bytes=primary_bytes + startup_bytes,
        test_only=test_only,
        provenance='MOCK' if test_only else 'STARTUP_SOURCE_METADATA',
        all_runtime_binaries_authenticated=False,
        scientific_validation_passed=False,
        weight_payload_rehashed=False,
        scope=SCOPE,
    )


def _validated(snapshot, runtime_snapshot):
    artifacts = _checked_artifacts(snapshot)
    primary = _primary(runtime_snapshot)
    document = metadata.strict_object(snapshot.manifest_bytes)
    fields = {
        'schema_version', 'startup_contract', 'primary_manifest_sha256', 'source_root',
        'artifacts', 'source_states', 'source_file_count',
        'primary_source_metadata_bytes', 'startup_source_bytes',
        'combined_source_metadata_bytes', 'test_only', 'provenance',
        'all_runtime_binaries_authenticated', 'scientific_validation_passed',
        'weight_payload_rehashed', 'scope',
    }
    if set(document) != fields:
        raise ValueError('startup source manifest fields differ from fixed contract')
    if document['startup_contract'] != STARTUP_CONTRACT or document['schema_version'] != 1:
        raise ValueError('unsupported startup source contract')
    if document['primary_manifest_sha256'] != metadata.digest(runtime_snapshot.manifest_bytes):
        raise ValueError('startup source primary manifest binding differs')
    if document['source_root'] != primary['source_root']:
        raise ValueError('startup source root differs from primary binding')
    if type(document['artifacts']) is not dict or set(document['artifacts']) != set(artifacts):
        raise ValueError('startup source artifact names differ from fixed contract')
    if any(type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value)
           for value in document['artifacts'].values()):
        raise ValueError('startup source artifact digest is invalid')
    if type(document['source_states']) is not dict:
        raise ValueError('startup source states are invalid')
    if type(document['source_file_count']) is not int or document['source_file_count'] != 7:
        raise ValueError('startup source file count is invalid')
    for name in ('primary_source_metadata_bytes', 'startup_source_bytes',
                 'combined_source_metadata_bytes'):
        if type(document[name]) is not int or document[name] < 0:
            raise ValueError('startup source byte totals are invalid')
    if type(document['test_only']) is not bool or type(document['provenance']) is not str:
        raise ValueError('startup source provenance is invalid')
    expected_provenance = 'MOCK' if primary['test_only'] else 'STARTUP_SOURCE_METADATA'
    if document['test_only'] != primary['test_only'] or document['provenance'] != expected_provenance:
        raise ValueError('startup source provenance differs from primary evidence')
    for name in ('all_runtime_binaries_authenticated', 'scientific_validation_passed',
                 'weight_payload_rehashed'):
        if type(document[name]) is not bool or document[name] is not False:
            raise ValueError('startup source attestation field cannot be upgraded')
    if document['scope'] != SCOPE:
        raise ValueError('startup source scope differs from fixed contract')
    paths = _paths(primary['source_root'])
    try:
        runtime.validate_selected_buffers(
            paths, artifacts, document['source_states'],
            per_file_bytes=MAX_FILE_BYTES,
            total_bytes=MAX_TOTAL_BYTES - primary['total_source_metadata_bytes'],
            ancestor_bindings=_ancestor_bindings(primary['source_states']))
        expected = _manifest(primary, artifacts, document['source_states'])
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('invalid startup source state structure') from error
    if metadata.canonical(document) != metadata.canonical(expected):
        raise ValueError('startup source manifest differs from frozen derivation')
    return primary, document, paths


def collect_startup_sources(runtime_snapshot):
    """Capture exactly the seven fixed startup source files under the primary root."""
    primary = _primary(runtime_snapshot)
    artifacts, states = _capture_selected(primary)
    return StartupSourcesSnapshot(
        metadata.canonical(_manifest(primary, artifacts, states)),
        tuple(sorted(artifacts.items())))


def validate_startup_sources(snapshot, runtime_snapshot):
    """Validate frozen startup buffers without opening any recorded path."""
    return _validated(snapshot, runtime_snapshot)[1]


def recheck_startup_sources(snapshot, runtime_snapshot):
    """Reread only the seven fixed files and compare their canonical manifest."""
    primary, document, _ = _validated(snapshot, runtime_snapshot)
    artifacts, states = _capture_selected(primary)
    current = _manifest(primary, artifacts, states)
    if metadata.canonical(current) != metadata.canonical(document):
        raise ValueError('startup source files changed after freezing')
    return dict(status='STARTUP_SOURCES_UNCHANGED',
                manifest_sha256=metadata.digest(snapshot.manifest_bytes),
                provenance=document['provenance'],
                scientific_validation_passed=False)

"""Materialize returned routes while preserving the base tensor-identity schema.

Selective donor: X 37144843906b3bd71f3fbac1fecc6b5080d82b95, same path.
This module imports neither vLLM nor torch and does not launch a capture. The
caller supplies the returned [input_len+output_len-1, layers, top_k] array and
actual request/sequence/prompt identities. The final generated token has no
subsequent forward route in that API. Host timestamps below describe JSONL
materialization, not kernel timing or live scheduler concurrency.

Materialization and compatible shapes cannot establish route origin, native
token equivalence, repeat determinism, callback coverage, or hardware gold.
Summary status remains CAPTURED_UNVALIDATED; CPU fixtures must use test_only=True.
Use the unchanged trace_validation.validate_trace API for tensor consistency,
then acquire independent native/repeat/runtime evidence before claiming gold.
Selected expert tensor bytes are not measured transferred GPU bytes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import operator
import os
from pathlib import Path
import stat
import time
from typing import Any

from model_inventory import ModelInventory

DONOR_COMMIT = '37144843906b3bd71f3fbac1fecc6b5080d82b95'


def integer(value: Any, name: str, minimum: int = 0) -> int:
    try:
        if isinstance(value, bool):
            raise TypeError('boolean is not an ID')
        result = operator.index(value)
    except TypeError as error:
        raise ValueError(name + ' must be an integer') from error
    if result < minimum:
        raise ValueError(name + ' is below its minimum')
    return result


def file_hash(path: Path) -> str:
    """Hash regular files without opening a FIFO or following a leaf symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('capture metadata must be a regular file')
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if any(getattr(before, key) != getattr(after, key)
               for key in ('st_size', 'st_mtime_ns', 'st_ctime_ns')):
            raise ValueError('capture metadata changed while hashing')
    return digest.hexdigest()


def inventory_identity(inventory: ModelInventory) -> dict[str, Any]:
    layers = integer(inventory.num_layers, 'layer count', 1)
    experts = integer(inventory.num_experts, 'expert count', 1)
    top_k = integer(inventory.top_k, 'top-k', 1)
    if top_k > experts:
        raise ValueError('top-k exceeds the inventory expert count')
    return dict(model_fingerprint=inventory.model_fingerprint, num_layers=layers,
                num_experts=experts, top_k=top_k, page_bytes=inventory.page_bytes,
                expert_weight_bytes=inventory.expert_weight_bytes,
                expert_bytes=inventory.expert_bytes,
                experts=[asdict(inventory.expert(layer, expert))
                         for layer in range(layers) for expert in range(experts)])


@dataclass(frozen=True)
class TraceRequest:
    request_id: str
    sequence_id: int
    prompt_id: int
    input_len: int
    output_len: int


class JsonlTraceCollector:
    def __init__(self, path: Path, inventory: ModelInventory, run_id: str,
                 environment_fingerprint: str, git_commit: str, *, test_only: bool = False):
        for value in (run_id, environment_fingerprint, git_commit):
            if not isinstance(value, str) or not value:
                raise ValueError('run/environment/git identity must be nonempty')
        if type(test_only) is not bool:
            raise ValueError('test_only must be boolean')
        # Reconstruct an owned immutable-in-use object and bind the caller's
        # supplied inventory to the same file identity before creating output.
        self.inventory_sha256 = file_hash(inventory.path)
        self.inventory = ModelInventory(inventory.path)
        self._inventory_identity = inventory_identity(self.inventory)
        if (file_hash(inventory.path) != self.inventory_sha256 or
                inventory_identity(inventory) != self._inventory_identity):
            raise ValueError('loaded inventory differs from its frozen manifest')
        self.path = Path(path)
        if self.path.exists() or self.path.is_symlink():
            raise FileExistsError(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open('xb')
        self.run_id, self.environment_fingerprint, self.git_commit = run_id, environment_fingerprint, git_commit
        self.test_only = test_only
        self._digest = hashlib.sha256()
        self._event_count = self._tensor_access_count = self._expert_access_count = self._access_sequence = 0
        self._per_layer = {layer: 0 for layer in range(self.inventory.num_layers)}
        self._per_phase = {'prefill': 0, 'decode': 0}
        self._requests: set[str] = set()
        self._failed = False

    def _check_inventory(self) -> None:
        if (file_hash(self.inventory.path) != self.inventory_sha256 or
                inventory_identity(self.inventory) != self._inventory_identity):
            raise ValueError('frozen inventory identity changed during capture')

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()

    def __enter__(self) -> JsonlTraceCollector:
        return self

    def __exit__(self, error_type, *_: object) -> None:
        self._failed |= error_type is not None
        self.close()

    def emit_request(self, request: TraceRequest, routed_experts: Any) -> None:
        try:
            self._check_inventory()
            if self._stream.closed:
                raise ValueError('capture output is closed')
            if not isinstance(request.request_id, str) or not request.request_id or request.request_id in self._requests:
                raise ValueError('missing or duplicate request ID')
            sequence = integer(request.sequence_id, 'sequence ID')
            prompt = integer(request.prompt_id, 'prompt ID')
            input_len = integer(request.input_len, 'input length', 1)
            output_len = integer(request.output_len, 'output length', 1)
            tokens = input_len + output_len - 1
            shape = tuple(integer(value, 'route dimension') for value in routed_experts.shape)
            expected = (tokens, self.inventory.num_layers, self.inventory.top_k)
            if shape != expected:
                raise ValueError(f'routed expert shape {shape} differs from {expected}')
            # Validate/copy every route before appending any event for a request.
            # A bad last token must not silently leave an apparently valid prefix.
            routes = []
            for token in range(tokens):
                layers = []
                for layer in range(self.inventory.num_layers):
                    ids = tuple(integer(value, 'expert ID')
                                for value in routed_experts[token, layer, :].tolist())
                    if len(ids) != self.inventory.top_k or len(set(ids)) != len(ids):
                        raise ValueError('routed experts are not unique top-k IDs')
                    if any(value >= self.inventory.num_experts for value in ids):
                        raise ValueError('routed expert ID is out of range')
                    layers.append(ids)
                routes.append(layers)
            for token, layers in enumerate(routes):
                phase = 'prefill' if token < input_len else 'decode'
                for layer, ids in enumerate(layers):
                    compact = self.inventory.compact_tensor_accesses(layer, list(ids))
                    tensor_hashes = {tensor.tensor: tensor.source_shard_sha256
                                     for expert in ids for tensor in self.inventory.expert(layer, expert).tensors}
                    begin = self._access_sequence
                    for tensor in compact:
                        tensor.update(access_order_sequence=self._access_sequence,
                                      source_shard_sha256=tensor_hashes[tensor['tensor']],
                                      page_begin=tensor['source_offset_begin'] // self.inventory.page_bytes,
                                      page_end=(tensor['source_offset_end'] + self.inventory.page_bytes - 1) // self.inventory.page_bytes)
                        self._access_sequence += 1
                    event = dict(schema_version=1, run_id=self.run_id, request_id=request.request_id,
                        sequence_id=sequence, prompt_id=prompt, phase=phase,
                        token_step=token if phase == 'prefill' else token - input_len,
                        route_token_index=token, layer_id=layer, topk_expert_ids=list(ids),
                        topk_weights=None, topk_weights_availability='not exposed by returned-routes API',
                        expert_tensors=compact,
                        expert_access_bytes=sum(self.inventory.expert(layer, expert).total_bytes for expert in ids),
                        access_order_sequence_begin=begin, access_order_sequence_end=self._access_sequence,
                        host_monotonic_timestamp_ns=time.monotonic_ns(),
                        host_timestamp_semantics='post-request JSONL materialization',
                        gpu_event_timestamp_ns=None, previous_compute_gap_ns=None,
                        capture_source='TEST_ONLY routed-array fixture' if self.test_only else 'caller-supplied vLLM returned routes; origin unvalidated',
                        capture_validation_status='UNVALIDATED', model_fingerprint=self.inventory.model_fingerprint,
                        inventory_sha256=self.inventory_sha256, environment_fingerprint=self.environment_fingerprint,
                        git_commit=self.git_commit)
                    encoded = (json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
                    self._stream.write(encoded)
                    self._digest.update(encoded)
                    self._event_count += 1
                    self._tensor_access_count += len(compact)
                    self._expert_access_count += len(ids)
                    self._per_layer[layer] += 1
                    self._per_phase[phase] += 1
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._requests.add(request.request_id)
        except BaseException:
            self._failed = True
            raise

    def summary(self) -> dict[str, Any]:
        if not self._stream.closed:
            self._stream.flush()
            os.fsync(self._stream.fileno())
        self._check_inventory()
        if file_hash(self.path) != self._digest.hexdigest():
            self._failed = True
            raise ValueError('trace bytes changed after materialization')
        return dict(schema_version=1,
            status='FAILED' if self._failed else 'CAPTURED_UNVALIDATED' if self._event_count else 'EMPTY',
            evidence_class='TEST_ONLY' if self.test_only else 'UNVALIDATED_ROUTING_CAPTURE',
            scientific_validation_passed=False, donor_commit=DONOR_COMMIT,
            trace_path=str(self.path.resolve()), trace_sha256=self._digest.hexdigest(),
            request_count=len(self._requests), event_count=self._event_count,
            expert_access_count=self._expert_access_count, tensor_access_count=self._tensor_access_count,
            access_order_sequence_count=self._access_sequence,
            per_layer_event_count={str(key): value for key, value in self._per_layer.items()},
            per_phase_event_count=dict(self._per_phase), model_fingerprint=self.inventory.model_fingerprint,
            inventory_sha256=self.inventory_sha256, environment_fingerprint=self.environment_fingerprint,
            git_commit=self.git_commit,
            boundary='Returned route metadata only; no live concurrency, GPU timing, transferred-byte or independent gold proof.')

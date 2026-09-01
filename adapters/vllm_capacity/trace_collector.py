"""Materialize vLLM routed-expert arrays as auditable JSONL events."""

from __future__ import annotations

import hashlib
import json
import pathlib
import time
from dataclasses import dataclass
from typing import Any

from model_inventory import ModelInventory


@dataclass(frozen=True)
class TraceRequest:
    request_id: str
    sequence_id: int
    prompt_id: int
    input_len: int
    output_len: int


class JsonlTraceCollector:
    def __init__(
        self,
        path: pathlib.Path,
        inventory: ModelInventory,
        run_id: str,
        environment_fingerprint: str,
        git_commit: str,
    ) -> None:
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.inventory = inventory
        self.run_id = run_id
        self.environment_fingerprint = environment_fingerprint
        self.git_commit = git_commit
        self._stream = path.open("xb")
        self._digest = hashlib.sha256()
        self._event_count = 0
        self._tensor_access_count = 0
        self._expert_access_count = 0
        self._access_sequence = 0
        self._per_layer: dict[int, int] = {
            layer: 0 for layer in range(inventory.num_layers)
        }
        self._per_phase: dict[str, int] = {"prefill": 0, "decode": 0}

    def close(self) -> None:
        if not self._stream.closed:
            self._stream.close()

    def __enter__(self) -> JsonlTraceCollector:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def emit_request(self, request: TraceRequest, routed_experts: Any) -> None:
        shape = tuple(int(value) for value in routed_experts.shape)
        expected_tokens = request.input_len + request.output_len - 1
        expected_shape = (
            expected_tokens,
            self.inventory.num_layers,
            self.inventory.top_k,
        )
        if shape != expected_shape:
            raise ValueError(
                f"routed expert shape {shape} differs from {expected_shape}"
            )
        for token_index in range(expected_tokens):
            if token_index < request.input_len:
                phase = "prefill"
                token_step = token_index
            else:
                phase = "decode"
                token_step = token_index - request.input_len
            for layer_id in range(self.inventory.num_layers):
                ids = [
                    int(value)
                    for value in routed_experts[token_index, layer_id, :].tolist()
                ]
                if len(ids) != self.inventory.top_k or len(set(ids)) != len(ids):
                    raise ValueError("routed experts are not unique top-k IDs")
                if any(value < 0 or value >= self.inventory.num_experts for value in ids):
                    raise ValueError("routed expert ID is out of range")
                compact = self.inventory.compact_tensor_accesses(layer_id, ids)
                begin = self._access_sequence
                for tensor in compact:
                    tensor["access_order_sequence"] = self._access_sequence
                    tensor["page_begin"] = (
                        tensor["source_offset_begin"] // self.inventory.page_bytes
                    )
                    tensor["page_end"] = (
                        tensor["source_offset_end"] + self.inventory.page_bytes - 1
                    ) // self.inventory.page_bytes
                    self._access_sequence += 1
                event = {
                    "schema_version": 1,
                    "run_id": self.run_id,
                    "request_id": request.request_id,
                    "sequence_id": request.sequence_id,
                    "prompt_id": request.prompt_id,
                    "phase": phase,
                    "token_step": token_step,
                    "route_token_index": token_index,
                    "layer_id": layer_id,
                    "topk_expert_ids": ids,
                    "topk_weights": None,
                    "topk_weights_availability": (
                        "not exposed by vLLM 0.15.1 routed-experts API"
                    ),
                    "expert_tensors": compact,
                    "expert_access_bytes": sum(
                        self.inventory.expert(layer_id, expert).total_bytes
                        for expert in ids
                    ),
                    "access_order_sequence_begin": begin,
                    "access_order_sequence_end": self._access_sequence,
                    "host_monotonic_timestamp_ns": time.monotonic_ns(),
                    "host_timestamp_semantics": "post-request JSONL materialization",
                    "gpu_event_timestamp_ns": None,
                    "previous_compute_gap_ns": None,
                    "capture_source": "vllm enable_return_routed_experts",
                    "model_fingerprint": self.inventory.model_fingerprint,
                    "environment_fingerprint": self.environment_fingerprint,
                    "git_commit": self.git_commit,
                }
                encoded = (
                    json.dumps(
                        event,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("utf-8")
                self._stream.write(encoded)
                self._digest.update(encoded)
                self._event_count += 1
                self._tensor_access_count += len(compact)
                self._expert_access_count += len(ids)
                self._per_layer[layer_id] += 1
                self._per_phase[phase] += 1

    def summary(self) -> dict[str, Any]:
        self._stream.flush()
        return {
            "schema_version": 1,
            "status": "PASS",
            "evidence_class": "REAL_QWEN_TRACE",
            "trace_path": str(self.path.resolve()),
            "trace_sha256": self._digest.hexdigest(),
            "event_count": self._event_count,
            "expert_access_count": self._expert_access_count,
            "tensor_access_count": self._tensor_access_count,
            "access_order_sequence_count": self._access_sequence,
            "per_layer_event_count": {
                str(key): value for key, value in self._per_layer.items()
            },
            "per_phase_event_count": self._per_phase,
            "model_fingerprint": self.inventory.model_fingerprint,
            "environment_fingerprint": self.environment_fingerprint,
            "git_commit": self.git_commit,
        }

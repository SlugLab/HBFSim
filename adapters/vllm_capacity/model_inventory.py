"""Load and validate the frozen Qwen expert inventory."""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TensorLocation:
    tensor: str
    tensor_kind: str
    source_shard: str
    source_shard_sha256: str
    file_offset_begin: int
    file_offset_end: int
    bytes: int
    dtype: str
    shape: tuple[int, ...]


@dataclass(frozen=True)
class ExpertRecord:
    layer_id: int
    expert_id: int
    total_bytes: int
    w13_bytes: int
    w2_bytes: int
    tensors: tuple[TensorLocation, ...]


class ModelInventory:
    def __init__(self, manifest: pathlib.Path) -> None:
        self.path = manifest.resolve(strict=True)
        document = json.loads(self.path.read_text(encoding="utf-8"))
        self.model_fingerprint = str(document["ModelFingerprint"])
        config = document["configuration"]
        self.num_layers = int(config["num_hidden_layers"])
        self.num_experts = int(config["num_experts"])
        self.top_k = int(config["num_experts_per_tok"])
        self.expert_weight_bytes = int(document["expert_weight_bytes"])
        self.page_bytes = 16_384
        self._experts: dict[tuple[int, int], ExpertRecord] = {}
        for raw in document["expert_weights"]:
            tensors: list[TensorLocation] = []
            for item in raw["w13"]["tensors"]:
                tensors.append(self._tensor(item, "w13"))
            tensors.append(self._tensor(raw["w2"]["tensor"], "w2"))
            record = ExpertRecord(
                layer_id=int(raw["layer"]),
                expert_id=int(raw["expert_id"]),
                total_bytes=int(raw["total_bytes"]),
                w13_bytes=int(raw["w13"]["bytes"]),
                w2_bytes=int(raw["w2"]["bytes"]),
                tensors=tuple(tensors),
            )
            key = (record.layer_id, record.expert_id)
            if key in self._experts:
                raise ValueError(f"duplicate expert record: {key}")
            if record.total_bytes != record.w13_bytes + record.w2_bytes:
                raise ValueError(f"expert byte mismatch: {key}")
            if record.total_bytes % self.page_bytes:
                raise ValueError(f"expert is not page aligned: {key}")
            self._experts[key] = record
        expected = self.num_layers * self.num_experts
        if len(self._experts) != expected:
            raise ValueError(
                f"expected {expected} experts, found {len(self._experts)}"
            )
        total = sum(item.total_bytes for item in self._experts.values())
        if total != self.expert_weight_bytes:
            raise ValueError(
                f"expert bytes {total} differ from manifest "
                f"{self.expert_weight_bytes}"
            )
        sizes = {item.total_bytes for item in self._experts.values()}
        if len(sizes) != 1:
            raise ValueError("replay currently requires equal-sized experts")
        self.expert_bytes = sizes.pop()

    @staticmethod
    def _tensor(raw: dict[str, Any], kind: str) -> TensorLocation:
        result = TensorLocation(
            tensor=str(raw["tensor"]),
            tensor_kind=kind,
            source_shard=str(raw["source_shard"]),
            source_shard_sha256=str(raw["source_shard_sha256"]),
            file_offset_begin=int(raw["file_offset_begin"]),
            file_offset_end=int(raw["file_offset_end"]),
            bytes=int(raw["bytes"]),
            dtype=str(raw["dtype"]),
            shape=tuple(int(value) for value in raw["shape"]),
        )
        if result.file_offset_end - result.file_offset_begin != result.bytes:
            raise ValueError(f"invalid tensor offsets: {result.tensor}")
        return result

    def expert(self, layer_id: int, expert_id: int) -> ExpertRecord:
        try:
            return self._experts[(layer_id, expert_id)]
        except KeyError as error:
            raise ValueError(
                f"unknown layer/expert: {layer_id}/{expert_id}"
            ) from error

    def compact_tensor_accesses(
        self, layer_id: int, expert_ids: list[int]
    ) -> list[dict[str, Any]]:
        accesses: list[dict[str, Any]] = []
        for expert_id in expert_ids:
            record = self.expert(layer_id, expert_id)
            for tensor in record.tensors:
                accesses.append(
                    {
                        "expert_id": expert_id,
                        "tensor": tensor.tensor,
                        "tensor_kind": tensor.tensor_kind,
                        "source_shard": tensor.source_shard,
                        "source_offset_begin": tensor.file_offset_begin,
                        "source_offset_end": tensor.file_offset_end,
                        "tensor_bytes": tensor.bytes,
                        "dtype": tensor.dtype,
                        "shape": list(tensor.shape),
                    }
                )
        return accesses

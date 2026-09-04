"""Local-only Qwen3-MoE loader that never materializes expert tensors.

Expert identity and byte ranges come from the frozen model manifest.  Every
expert record is checked against the safetensors header, while non-expert
payloads are read one tensor at a time from their exact file ranges.  Avoiding
a second whole-shard mmap is required on hosts with strict memory overcommit.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import re
import struct
import sys
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterator


MODEL_FINGERPRINT = (
    "af52de6efe45aa0e0fe9fe393985a25daa16306c440effe493a12ba10e03dda9"
)
EXPECTED_LAYERS = 48
EXPECTED_EXPERTS_PER_LAYER = 128
EXPECTED_EXPERT_TENSORS = EXPECTED_LAYERS * EXPECTED_EXPERTS_PER_LAYER * 3
EXPERT_PATTERN = re.compile(
    r"^model\.layers\.(\d+)\.mlp\.experts\.(\d+)\."
    r"(gate_proj|up_proj|down_proj)\.weight$"
)

_SAFETENSORS_DTYPES: dict[str, tuple[str, int]] = {
    "BOOL": ("bool", 1),
    "U8": ("uint8", 1),
    "I8": ("int8", 1),
    "I16": ("int16", 2),
    "I32": ("int32", 4),
    "I64": ("int64", 8),
    "F16": ("float16", 2),
    "BF16": ("bfloat16", 2),
    "F32": ("float32", 4),
    "F64": ("float64", 8),
}


def _safetensors_geometry(
    name: str, metadata: dict[str, Any]
) -> tuple[str, tuple[int, ...], int, int, int]:
    dtype_name = str(metadata.get("dtype"))
    dtype_spec = _SAFETENSORS_DTYPES.get(dtype_name)
    if dtype_spec is None:
        raise RuntimeError(f"unsupported safetensors dtype for {name}: {dtype_name}")
    shape_raw = metadata.get("shape")
    offsets_raw = metadata.get("data_offsets")
    if not isinstance(shape_raw, list) or not isinstance(offsets_raw, list):
        raise RuntimeError(f"invalid safetensors metadata for {name}")
    if len(offsets_raw) != 2:
        raise RuntimeError(f"invalid safetensors offsets for {name}: {offsets_raw!r}")
    shape = tuple(int(value) for value in shape_raw)
    if not shape or any(value <= 0 for value in shape):
        raise RuntimeError(f"invalid safetensors shape for {name}: {shape!r}")
    begin, end = (int(value) for value in offsets_raw)
    if begin < 0 or end <= begin:
        raise RuntimeError(f"invalid safetensors range for {name}: {begin}:{end}")
    expected_bytes = math.prod(shape) * dtype_spec[1]
    if end - begin != expected_bytes:
        raise RuntimeError(
            f"safetensors byte geometry mismatch for {name}: "
            f"{end - begin} != {expected_bytes}"
        )
    return dtype_name, shape, begin, end, expected_bytes


@dataclass(frozen=True)
class TensorSlice:
    tensor: str
    layer: int
    expert_id: int
    projection: str
    shard: str
    shard_sha256: str
    file_offset_begin: int
    file_offset_end: int
    bytes: int
    dtype: str
    shape: tuple[int, ...]


@dataclass(frozen=True)
class ExpertSlices:
    layer: int
    expert_id: int
    gate: TensorSlice
    up: TensorSlice
    down: TensorSlice

    @property
    def total_bytes(self) -> int:
        return self.gate.bytes + self.up.bytes + self.down.bytes


class CapacityInventory:
    def __init__(self, manifest_path: pathlib.Path, model_root: pathlib.Path) -> None:
        self.manifest_path = manifest_path.expanduser().resolve(strict=True)
        self.model_root = model_root.expanduser().resolve(strict=True)
        self.payload = json.loads(self.manifest_path.read_text())
        self._validate_identity()
        self.files = [
            record
            for record in self.payload["files"]
            if str(record["path"]).endswith(".safetensors")
        ]
        self.shard_records = {
            pathlib.Path(str(record["path"])).name: record for record in self.files
        }
        self.tensors = self._collect_expert_tensors()
        self.experts = self._group_experts()

    @staticmethod
    def sha256(path: pathlib.Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(4 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _validate_identity(self) -> None:
        if self.payload.get("schema_version") != 1:
            raise RuntimeError("unsupported model manifest schema")
        if self.payload.get("ModelFingerprint") != MODEL_FINGERPRINT:
            raise RuntimeError("frozen model fingerprint mismatch")
        expected_manifest_sha = os.environ.get("HBFSIM_CAPACITY_MANIFEST_SHA256")
        if expected_manifest_sha:
            actual = self.sha256(self.manifest_path)
            if actual != expected_manifest_sha:
                raise RuntimeError(
                    f"model manifest SHA256 mismatch: {actual} != {expected_manifest_sha}"
                )
        configuration = self.payload.get("configuration", {})
        required = {
            "num_hidden_layers": EXPECTED_LAYERS,
            "num_experts": EXPECTED_EXPERTS_PER_LAYER,
            "num_experts_per_tok": 8,
            "hidden_size": 2048,
            "moe_intermediate_size": 768,
        }
        for key, expected in required.items():
            if int(configuration.get(key, -1)) != expected:
                raise RuntimeError(
                    f"manifest configuration mismatch for {key}: "
                    f"{configuration.get(key)!r} != {expected}"
                )
        if self.payload.get("has_shared_expert") is not False:
            raise RuntimeError("E6 capacity loader requires no shared expert")
        if int(self.payload.get("expert_count_total", -1)) != 6144:
            raise RuntimeError("manifest expert count mismatch")

    @staticmethod
    def _slice(record: dict[str, Any]) -> TensorSlice:
        match = EXPERT_PATTERN.fullmatch(str(record["tensor"]))
        if match is None:
            raise RuntimeError(f"invalid expert tensor name: {record['tensor']}")
        layer, expert_id, projection = match.groups()
        begin = int(record["file_offset_begin"])
        end = int(record["file_offset_end"])
        size = int(record["bytes"])
        layer_id = int(layer)
        expert_index = int(expert_id)
        dtype = str(record["dtype"])
        shape = tuple(int(value) for value in record["shape"])
        expected_shape = (
            (2048, 768) if projection == "down_proj" else (768, 2048)
        )
        expected_bytes = expected_shape[0] * expected_shape[1] * 2
        shard_sha256 = str(record["source_shard_sha256"])
        if not 0 <= layer_id < EXPECTED_LAYERS:
            raise RuntimeError(f"expert layer out of range: {record['tensor']}")
        if not 0 <= expert_index < EXPECTED_EXPERTS_PER_LAYER:
            raise RuntimeError(f"expert id out of range: {record['tensor']}")
        if dtype != "BF16" or shape != expected_shape or size != expected_bytes:
            raise RuntimeError(
                "unexpected expert tensor geometry: "
                f"{record['tensor']} dtype={dtype} shape={shape} bytes={size}"
            )
        if begin < 8 or end <= begin or end - begin != size:
            raise RuntimeError(f"invalid byte range for {record['tensor']}")
        if not re.fullmatch(r"[0-9a-f]{64}", shard_sha256):
            raise RuntimeError(f"invalid shard SHA256 for {record['tensor']}")
        return TensorSlice(
            tensor=str(record["tensor"]),
            layer=layer_id,
            expert_id=expert_index,
            projection=projection,
            shard=pathlib.Path(str(record["source_shard"])).name,
            shard_sha256=shard_sha256,
            file_offset_begin=begin,
            file_offset_end=end,
            bytes=size,
            dtype=dtype,
            shape=shape,
        )

    def _collect_expert_tensors(self) -> dict[str, TensorSlice]:
        tensors: dict[str, TensorSlice] = {}
        for expert in self.payload["expert_weights"]:
            records = [*expert["w13"]["tensors"], expert["w2"]["tensor"]]
            for record in records:
                tensor = self._slice(record)
                if tensor.tensor in tensors:
                    raise RuntimeError(f"duplicate expert tensor: {tensor.tensor}")
                tensors[tensor.tensor] = tensor
        if len(tensors) != EXPECTED_EXPERT_TENSORS:
            raise RuntimeError(
                f"expert tensor completeness failure: {len(tensors)} != "
                f"{EXPECTED_EXPERT_TENSORS}"
            )
        return tensors

    def _group_experts(self) -> dict[tuple[int, int], ExpertSlices]:
        grouped: dict[tuple[int, int], dict[str, TensorSlice]] = defaultdict(dict)
        for tensor in self.tensors.values():
            grouped[(tensor.layer, tensor.expert_id)][tensor.projection] = tensor
        expected_keys = {
            (layer, expert)
            for layer in range(EXPECTED_LAYERS)
            for expert in range(EXPECTED_EXPERTS_PER_LAYER)
        }
        if set(grouped) != expected_keys:
            missing = sorted(expected_keys - set(grouped))[:8]
            extra = sorted(set(grouped) - expected_keys)[:8]
            raise RuntimeError(f"expert grid mismatch missing={missing} extra={extra}")
        result: dict[tuple[int, int], ExpertSlices] = {}
        for key, projections in grouped.items():
            if set(projections) != {"gate_proj", "up_proj", "down_proj"}:
                raise RuntimeError(f"projection completeness failure for {key}")
            result[key] = ExpertSlices(
                layer=key[0],
                expert_id=key[1],
                gate=projections["gate_proj"],
                up=projections["up_proj"],
                down=projections["down_proj"],
            )
            if result[key].total_bytes != 3 * 2048 * 768 * 2:
                raise RuntimeError(f"expert byte contract mismatch for {key}")
        return result

    @staticmethod
    def read_safetensors_header(path: pathlib.Path) -> tuple[int, dict[str, Any]]:
        with path.open("rb") as handle:
            prefix = handle.read(8)
            if len(prefix) != 8:
                raise RuntimeError(f"truncated safetensors prefix: {path}")
            header_bytes = struct.unpack("<Q", prefix)[0]
            if header_bytes <= 0 or header_bytes > path.stat().st_size - 8:
                raise RuntimeError(f"invalid safetensors header length: {path}")
            raw = handle.read(header_bytes)
            if len(raw) != header_bytes:
                raise RuntimeError(f"truncated safetensors header: {path}")
        return 8 + header_bytes, json.loads(raw)

    def validate_headers(self) -> dict[str, Any]:
        validated = 0
        nonexpert_validated = 0
        nonexpert_bytes = 0
        nonexpert_dtypes: set[str] = set()
        shards_with_experts: set[str] = set()
        nonexpert_by_shard: dict[str, list[str]] = defaultdict(list)
        for name, shard in self.nonexpert_weight_map().items():
            nonexpert_by_shard[shard].append(name)
        for shard, record in sorted(self.shard_records.items()):
            path = (self.model_root / shard).resolve(strict=True)
            size = path.stat().st_size
            expected_size = int(record["size_bytes"])
            if size != expected_size:
                raise RuntimeError(f"shard size mismatch {shard}: {size} != {expected_size}")
            payload_base, header = self.read_safetensors_header(path)
            shard_tensors = [item for item in self.tensors.values() if item.shard == shard]
            if shard_tensors:
                shards_with_experts.add(shard)
            for tensor in shard_tensors:
                metadata = header.get(tensor.tensor)
                if metadata is None:
                    raise RuntimeError(f"missing header tensor: {tensor.tensor}")
                begin, end = (int(value) for value in metadata["data_offsets"])
                checks = (
                    metadata["dtype"] == tensor.dtype,
                    tuple(metadata["shape"]) == tensor.shape,
                    payload_base + begin == tensor.file_offset_begin,
                    payload_base + end == tensor.file_offset_end,
                    str(record["sha256"]) == tensor.shard_sha256,
                )
                if not all(checks):
                    raise RuntimeError(f"header/manifest mismatch: {tensor.tensor}")
                validated += 1
            for name in sorted(nonexpert_by_shard.get(shard, [])):
                metadata = header.get(name)
                if not isinstance(metadata, dict):
                    raise RuntimeError(f"missing non-expert header tensor: {name}")
                dtype, _, begin, end, tensor_bytes = _safetensors_geometry(
                    name, metadata
                )
                if payload_base + begin < payload_base or payload_base + end > size:
                    raise RuntimeError(f"non-expert tensor outside shard: {name}")
                nonexpert_validated += 1
                nonexpert_bytes += tensor_bytes
                nonexpert_dtypes.add(dtype)
        tensor_shards = {item.shard for item in self.tensors.values()}
        if shards_with_experts != tensor_shards:
            raise RuntimeError(
                "expert tensors reference unknown shards: "
                f"{sorted(tensor_shards - shards_with_experts)}"
            )
        if validated != EXPECTED_EXPERT_TENSORS:
            raise RuntimeError(
                f"validated expert tensor count {validated} != {EXPECTED_EXPERT_TENSORS}"
            )
        expected_nonexpert_bytes = int(self.payload["non_expert_tensor_bytes"])
        if nonexpert_bytes != expected_nonexpert_bytes:
            raise RuntimeError(
                "validated non-expert byte count mismatch: "
                f"{nonexpert_bytes} != {expected_nonexpert_bytes}"
            )
        return {
            "schema_version": 1,
            "status": "PASS",
            "expert_tensors_validated_without_get_tensor": validated,
            "nonexpert_tensors_validated_without_mmap": nonexpert_validated,
            "nonexpert_bytes_validated_without_mmap": nonexpert_bytes,
            "nonexpert_dtypes": sorted(nonexpert_dtypes),
            "shards_validated": len(self.shard_records),
            "model_fingerprint": self.payload["ModelFingerprint"],
        }

    def expert(self, layer: int, expert_id: int) -> ExpertSlices:
        try:
            return self.experts[(layer, expert_id)]
        except KeyError as exc:
            raise RuntimeError(f"unknown expert ({layer}, {expert_id})") from exc

    def nonexpert_weight_map(self) -> dict[str, str]:
        index_path = (self.model_root / "model.safetensors.index.json").resolve(
            strict=True
        )
        index = json.loads(index_path.read_text())
        weight_map = {str(k): str(v) for k, v in index["weight_map"].items()}
        expert_names = {name for name in weight_map if EXPERT_PATTERN.fullmatch(name)}
        if expert_names != set(self.tensors):
            missing = sorted(set(self.tensors) - expert_names)[:8]
            extra = sorted(expert_names - set(self.tensors))[:8]
            raise RuntimeError(
                f"index/manifest expert mismatch missing={missing} extra={extra}"
            )
        return {
            name: pathlib.Path(shard).name
            for name, shard in weight_map.items()
            if name not in expert_names
        }

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "manifest": str(self.manifest_path),
            "model_root": str(self.model_root),
            "model_fingerprint": self.payload["ModelFingerprint"],
            "expert_tensors": len(self.tensors),
            "experts": len(self.experts),
            "shards": len(self.shard_records),
            "expert_weight_bytes": int(self.payload["expert_weight_bytes"]),
            "non_expert_tensor_bytes": int(self.payload["non_expert_tensor_bytes"]),
        }


_INVENTORY_LOCK = threading.Lock()
_INVENTORY: CapacityInventory | None = None


def install_inventory(inventory: CapacityInventory) -> None:
    global _INVENTORY
    with _INVENTORY_LOCK:
        if _INVENTORY is not None and (
            _INVENTORY.manifest_path != inventory.manifest_path
            or _INVENTORY.model_root != inventory.model_root
        ):
            raise RuntimeError("attempted to replace the worker capacity inventory")
        _INVENTORY = inventory


def get_inventory() -> CapacityInventory:
    if _INVENTORY is None:
        raise RuntimeError("capacity inventory has not been installed by the loader")
    return _INVENTORY


def _read_exact_into(handle: Any, payload: bytearray, *, label: str) -> None:
    view = memoryview(payload)
    consumed = 0
    while consumed != len(view):
        count = handle.readinto(view[consumed:])
        if count is None or count <= 0:
            raise RuntimeError(
                f"short safetensors payload read for {label}: "
                f"{consumed} != {len(view)}"
            )
        consumed += count


def _read_tensor_range(
    handle: Any,
    *,
    path: pathlib.Path,
    payload_base: int,
    name: str,
    metadata: dict[str, Any],
) -> Any:
    if sys.byteorder != "little":
        raise RuntimeError("safetensors exact-range loader requires little endian")
    dtype_name, shape, begin, end, expected_bytes = _safetensors_geometry(
        name, metadata
    )
    torch_name = _SAFETENSORS_DTYPES[dtype_name][0]
    absolute_begin = payload_base + begin
    absolute_end = payload_base + end
    path_size = path.stat().st_size
    if absolute_end > path_size:
        raise RuntimeError(
            f"safetensors payload range outside shard for {name}: "
            f"{absolute_end} > {path_size}"
        )

    import torch

    dtype = getattr(torch, torch_name, None)
    if dtype is None:
        raise RuntimeError(f"torch does not provide dtype {torch_name} for {name}")
    payload = bytearray(expected_bytes)
    handle.seek(absolute_begin)
    _read_exact_into(handle, payload, label=name)
    return torch.frombuffer(payload, dtype=dtype).reshape(shape)


def iter_nonexpert_tensors(
    inventory: CapacityInventory,
) -> Iterator[tuple[str, Any]]:
    """Yield exact-range non-expert tensors without whole-shard mmap."""
    by_shard: dict[str, list[str]] = defaultdict(list)
    for name, shard in inventory.nonexpert_weight_map().items():
        by_shard[shard].append(name)
    for shard in sorted(by_shard):
        path = inventory.model_root / shard
        payload_base, header = inventory.read_safetensors_header(path)
        keys = set(header) - {"__metadata__"}
        with path.open("rb", buffering=0) as handle:
            for name in sorted(by_shard[shard]):
                if EXPERT_PATTERN.fullmatch(name):
                    raise AssertionError("expert exact-range read failed closed")
                if name not in keys:
                    raise RuntimeError(f"index key absent from shard header: {name}")
                metadata = header[name]
                if not isinstance(metadata, dict):
                    raise RuntimeError(f"invalid safetensors tensor header: {name}")
                yield name, _read_tensor_range(
                    handle,
                    path=path,
                    payload_base=payload_base,
                    name=name,
                    metadata=metadata,
                )


def _write_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _model_root_from_config(model_config: Any) -> pathlib.Path:
    requested = pathlib.Path(str(model_config.model)).expanduser().resolve(strict=True)
    frozen = pathlib.Path(os.environ["HBFSIM_CAPACITY_MODEL_ROOT"]).expanduser().resolve(
        strict=True
    )
    if requested != frozen:
        raise RuntimeError(f"model path is not frozen local root: {requested} != {frozen}")
    if not requested.is_dir():
        raise RuntimeError(f"model root is not a directory: {requested}")
    return requested


def _load_paths(model_config: Any) -> tuple[CapacityInventory, pathlib.Path]:
    root = _model_root_from_config(model_config)
    manifest = pathlib.Path(os.environ["HBFSIM_CAPACITY_MANIFEST"]).expanduser().resolve(
        strict=True
    )
    inventory = CapacityInventory(manifest, root)
    install_inventory(inventory)
    evidence = pathlib.Path(os.environ["HBFSIM_CAPACITY_REPORT_DIR"]).resolve()
    return inventory, evidence


def _expected_parameter_names(model: Any) -> set[str]:
    return {name for name, _ in model.named_parameters()}


def _load_nonexperts(model: Any, inventory: CapacityInventory) -> tuple[set[str], set[str]]:
    expected = _expected_parameter_names(model)
    loaded = set(model.load_weights(iter_nonexpert_tensors(inventory)))
    missing = expected - loaded
    unexpected = loaded - expected
    if missing or unexpected:
        raise RuntimeError(
            "strict non-expert parameter equality failed: "
            f"missing={sorted(missing)[:16]} unexpected={sorted(unexpected)[:16]}"
        )
    return expected, loaded


# vLLM is intentionally imported only in a worker where the plugin is active.
try:
    from vllm.model_executor.model_loader.base_loader import BaseModelLoader
except ImportError:  # CPU-only static tests can still import inventory helpers.
    BaseModelLoader = object  # type: ignore[misc,assignment]


class CapacityModelLoader(BaseModelLoader):  # type: ignore[misc]
    """vLLM loader for the exact frozen local Qwen3-30B-A3B checkpoint."""

    def __init__(self, load_config: Any) -> None:
        if BaseModelLoader is object:
            raise RuntimeError("vLLM is required for CapacityModelLoader")
        super().__init__(load_config)

    def download_model(self, model_config: Any) -> None:
        # This is a validation hook only.  No network call is permitted.
        _load_paths(model_config)

    def load_weights(self, model: Any, model_config: Any) -> None:
        inventory, evidence = _load_paths(model_config)
        header_report = inventory.validate_headers()
        expected, loaded = _load_nonexperts(model, inventory)

        # Create the single capacity context and allocate/map the full-resident
        # expert cache during model load, before any prompt is admitted.
        from adapters.vllm_capacity.capacity_runtime import get_capacity_runtime

        runtime = get_capacity_runtime()
        runtime.map_shards(inventory.model_root, inventory.files)
        runtime.write_runtime_manifest(evidence / "e6-runtime-mappings.json")
        report = {
            **inventory.report(),
            **header_report,
            "loader": "hbfsim_capacity",
            "expert_get_tensor_calls": 0,
            "nonexpert_payload_reader": "exact-range-readinto-no-whole-shard-mmap",
            "nonexpert_parameters_expected": len(expected),
            "nonexpert_parameters_loaded": len(loaded),
            "strict_loaded_named_parameter_equality": expected == loaded,
            "full_resident_context_created_during_model_load": True,
        }
        _write_json(evidence / "e6-loader-validation.json", report)


__all__ = [
    "CapacityInventory",
    "CapacityModelLoader",
    "ExpertSlices",
    "TensorSlice",
    "get_inventory",
    "install_inventory",
    "iter_nonexpert_tensors",
]

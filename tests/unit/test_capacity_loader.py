from __future__ import annotations

import importlib
import json
import struct
from types import SimpleNamespace


def _module():
    try:
        return importlib.import_module("adapters.vllm_capacity.capacity_loader")
    except ModuleNotFoundError:
        return importlib.import_module("capacity_loader")


def test_expert_name_contract_and_slice():
    loader = _module()
    name = "model.layers.47.mlp.experts.127.down_proj.weight"
    assert loader.EXPERT_PATTERN.fullmatch(name)
    record = {
        "tensor": name,
        "source_shard": "model-00016-of-00016.safetensors",
        "source_shard_sha256": "a" * 64,
        "file_offset_begin": 128,
        "file_offset_end": 128 + 2048 * 768 * 2,
        "bytes": 2048 * 768 * 2,
        "dtype": "BF16",
        "shape": [2048, 768],
    }
    tensor = loader.CapacityInventory._slice(record)
    assert (tensor.layer, tensor.expert_id, tensor.projection) == (
        47,
        127,
        "down_proj",
    )


def test_safetensors_header_offsets_are_absolute(tmp_path):
    loader = _module()
    metadata = {
        "x": {"dtype": "BF16", "shape": [2], "data_offsets": [0, 4]}
    }
    raw = json.dumps(metadata, separators=(",", ":")).encode()
    path = tmp_path / "tiny.safetensors"
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\0" * 4)
    base, header = loader.CapacityInventory.read_safetensors_header(path)
    assert base == 8 + len(raw)
    assert header == metadata


def test_slice_rejects_inconsistent_byte_range():
    loader = _module()
    record = {
        "tensor": "model.layers.0.mlp.experts.0.gate_proj.weight",
        "source_shard": "x.safetensors",
        "source_shard_sha256": "a" * 64,
        "file_offset_begin": 10,
        "file_offset_end": 20,
        "bytes": 768 * 2048 * 2,
        "dtype": "BF16",
        "shape": [768, 2048],
    }
    try:
        loader.CapacityInventory._slice(record)
    except RuntimeError as exc:
        assert "byte range" in str(exc)
    else:
        raise AssertionError("invalid manifest range was accepted")


def _write_tiny_shard(path, metadata, payload):
    raw = json.dumps(metadata, separators=(",", ":")).encode()
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + payload)


def test_nonexpert_reader_uses_exact_ranges_without_safetensors_mmap(tmp_path):
    loader = _module()
    torch = importlib.import_module("torch")
    path = tmp_path / "tiny.safetensors"
    metadata = {
        "a": {"dtype": "BF16", "shape": [2], "data_offsets": [0, 4]},
        "b": {"dtype": "F32", "shape": [2], "data_offsets": [4, 12]},
    }
    payload = b"\x01\x00\x02\x00" + struct.pack("<ff", 1.5, -2.0)
    _write_tiny_shard(path, metadata, payload)

    inventory = SimpleNamespace(
        model_root=tmp_path,
        nonexpert_weight_map=lambda: {"b": path.name, "a": path.name},
        read_safetensors_header=loader.CapacityInventory.read_safetensors_header,
    )
    tensors = dict(loader.iter_nonexpert_tensors(inventory))
    assert list(tensors) == ["a", "b"]
    assert tensors["a"].dtype == torch.bfloat16
    assert tensors["a"].view(torch.uint16).tolist() == [1, 2]
    assert tensors["b"].dtype == torch.float32
    assert tensors["b"].tolist() == [1.5, -2.0]
    assert "safe_open" not in loader.iter_nonexpert_tensors.__code__.co_names


def test_nonexpert_reader_rejects_header_byte_geometry(tmp_path):
    loader = _module()
    path = tmp_path / "bad.safetensors"
    metadata = {
        "x": {"dtype": "BF16", "shape": [2], "data_offsets": [0, 2]},
    }
    _write_tiny_shard(path, metadata, b"\0\0")
    inventory = SimpleNamespace(
        model_root=tmp_path,
        nonexpert_weight_map=lambda: {"x": path.name},
        read_safetensors_header=loader.CapacityInventory.read_safetensors_header,
    )
    try:
        list(loader.iter_nonexpert_tensors(inventory))
    except RuntimeError as exc:
        assert "byte geometry mismatch" in str(exc)
    else:
        raise AssertionError("invalid non-expert byte geometry was accepted")


def test_exact_reader_rejects_short_read():
    loader = _module()

    class ShortReader:
        def readinto(self, view):
            return 0

    try:
        loader._read_exact_into(ShortReader(), bytearray(4), label="x")
    except RuntimeError as exc:
        assert "short safetensors payload read" in str(exc)
    else:
        raise AssertionError("short payload read was accepted")

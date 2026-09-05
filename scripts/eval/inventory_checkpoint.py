#!/usr/bin/env python3
"""Read actual GGUF metadata without hashing/loading weight payloads.

The initial supported contract is qwen3moe with packed F16/F32 expert tensors.
Other architectures/layouts fail closed instead of inheriting Qwen defaults.
This inventory describes source extents; it does not materialize a backing file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("x") as output:
        json.dump(value, output, indent=2, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def tensor_category(name):
    if re.fullmatch(r"blk\.(\d+)\.ffn_(up|gate|down)_exps\.weight", name):
        return "eligible_expert"
    if "_exps" in name:
        raise ValueError(f"unsupported expert projection: {name}")
    if re.fullmatch(r"blk\.\d+\.ffn_.*shexp.*", name):
        return "shared_expert"
    if re.fullmatch(r"blk\.\d+\.attn_.*", name):
        return "attention"
    return "other_resident"


def inventory_checkpoint(path, page_bytes=16384):
    from gguf import GGUFReader, GGMLQuantizationType

    positive(page_bytes, "page_bytes")
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("checkpoint must be a regular GGUF file")
    before = path.stat()
    reader = GGUFReader(str(path), mode="r")
    architecture = reader.fields["general.architecture"].contents()
    if architecture != "qwen3moe":
        raise ValueError(f"unsupported checkpoint architecture: {architecture}")
    metadata = {key: field.contents() for key, field in reader.fields.items()
                if key.startswith(("general.", architecture + ".", "GGUF."))}
    def config(name):
        key = architecture + "." + name
        if key not in metadata:
            raise ValueError(f"missing checkpoint metadata: {key}")
        return positive(metadata[key], key)
    layers, experts, top_k = config("block_count"), config("expert_count"), config("expert_used_count")
    if top_k > experts:
        raise ValueError("checkpoint k exceeds expert count")
    kv_shape = {"layers": layers, "heads_kv": config("attention.head_count_kv"),
                "key_length": config("attention.key_length"),
                "value_length": config("attention.value_length"),
                "maximum_context_tokens": config("context_length")}
    tensors, groups = [], {}
    for tensor in reader.tensors:
        shape = [int(value) for value in tensor.shape]
        extent = {"name": tensor.name, "shape": shape,
                  "dtype": tensor.tensor_type.name, "bytes": int(tensor.n_bytes),
                  "source_offset": int(tensor.data_offset),
                  "category": tensor_category(tensor.name)}
        match = re.fullmatch(r"blk\.(\d+)\.ffn_(up|gate|down)_exps\.weight", tensor.name)
        if match:
            layer, projection = int(match[1]), match[2]
            if len(shape) != 3 or shape[-1] != experts or not 0 <= layer < layers:
                raise ValueError("expert tensor dimension disagrees with checkpoint metadata")
            if tensor.tensor_type not in (GGMLQuantizationType.F16, GGMLQuantizationType.F32):
                raise ValueError("unsupported expert dtype/layout; no guessed byte slicing")
            if extent["bytes"] % experts:
                raise ValueError("expert slices are not equal whole byte extents")
            if (layer, projection) in groups:
                raise ValueError("duplicate expert projection")
            groups[layer, projection] = extent
        tensors.append(extent)
    expert_rows = []
    for layer in range(layers):
        if any((layer, projection) not in groups for projection in ("up", "gate", "down")):
            raise ValueError(f"missing expert projection in layer {layer}")
        for expert in range(experts):
            segments = []
            for projection in ("up", "gate", "down"):
                tensor = groups[layer, projection]
                nbytes = tensor["bytes"] // experts
                segments.append({"tensor": tensor["name"], "projection": projection,
                                 "source_offset": tensor["source_offset"] + expert * nbytes,
                                 "bytes": nbytes})
            nbytes = sum(segment["bytes"] for segment in segments)
            expert_rows.append({"layer": layer, "expert": expert, "bytes": nbytes,
                                "packed_logical_pages": (nbytes + page_bytes - 1) // page_bytes,
                                "segments": segments})
    categories = {name: sum(t["bytes"] for t in tensors if t["category"] == name)
                  for name in ("eligible_expert", "shared_expert", "attention", "other_resident")}
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError("checkpoint changed during metadata inventory")
    result = {"schema_version": 1, "source_kind": "CHECKPOINT_METADATA", "format": "GGUF",
              "checkpoint_path": str(path), "architecture": architecture,
              "file_identity": {"bytes": before.st_size, "mtime_ns": before.st_mtime_ns,
                                "inode": before.st_ino, "device": before.st_dev},
              "model_revision": metadata.get("general.revision"),
              "config": metadata, "config_sha256": identity(metadata),
              "tensor_manifest_sha256": identity(tensors), "weight_payload_hash": None,
              "E": experts, "k": top_k, "layers": layers, "kv_shape": kv_shape,
              "tensor_count": len(tensors), "tensor_bytes": sum(t["bytes"] for t in tensors),
              "eligible_expert_bytes": categories["eligible_expert"],
              "shared_expert_bytes": categories["shared_expert"],
              "attention_bytes": categories["attention"],
              "resident_non_offloaded_bytes": sum(v for k, v in categories.items() if k != "eligible_expert"),
              "page_bytes": page_bytes,
              "packed_logical_pages": sum(e["packed_logical_pages"] for e in expert_rows),
              "backing_materialized": False, "tensors": tensors, "experts": expert_rows}
    validate_inventory(result)
    return result


def validate_inventory(inv):
    if inv.get("schema_version") != 1 or inv.get("source_kind") != "CHECKPOINT_METADATA":
        raise ValueError("unsupported inventory schema/source")
    layers, experts = positive(inv["layers"], "layers"), positive(inv["E"], "E")
    if not 1 <= positive(inv["k"], "k") <= experts:
        raise ValueError("invalid top-k")
    page = positive(inv["page_bytes"], "page_bytes")
    config = inv["config"]
    architecture = inv["architecture"]
    if architecture != "qwen3moe" or config.get("general.architecture") != architecture:
        raise ValueError("unsupported architecture identity")
    for field, key in (("layers", "block_count"), ("E", "expert_count"), ("k", "expert_used_count")):
        if inv[field] != config[architecture + "." + key]:
            raise ValueError("inventory dimensions disagree with embedded config")
    expected_kv = {"layers": layers}
    for field, key in (("heads_kv", "attention.head_count_kv"),
                       ("key_length", "attention.key_length"),
                       ("value_length", "attention.value_length"),
                       ("maximum_context_tokens", "context_length")):
        expected_kv[field] = positive(config[architecture + "." + key], key)
    if inv["kv_shape"] != expected_kv:
        raise ValueError("KV shape disagrees with embedded config")
    tensors = inv["tensors"]
    if len(tensors) != inv["tensor_count"] or len({t["name"] for t in tensors}) != len(tensors):
        raise ValueError("tensor identity/count mismatch")
    if identity(tensors) != inv["tensor_manifest_sha256"] or identity(inv["config"]) != inv["config_sha256"]:
        raise ValueError("inventory metadata identity mismatch")
    end = 0
    for tensor in sorted(tensors, key=lambda t: t["source_offset"]):
        if tensor["category"] != tensor_category(tensor["name"]):
            raise ValueError("tensor category disagrees with supported naming contract")
        start, nbytes = tensor["source_offset"], positive(tensor["bytes"], "tensor bytes")
        if start < end or start + nbytes > inv["file_identity"]["bytes"]:
            raise ValueError("overlapping or truncated tensor extent")
        end = start + nbytes
    if sum(t["bytes"] for t in tensors) != inv["tensor_bytes"]:
        raise ValueError("tensor byte conservation failure")
    rows = inv["experts"]
    if len(rows) != layers * experts or {(r["layer"], r["expert"]) for r in rows} != {
            (layer, expert) for layer in range(layers) for expert in range(experts)}:
        raise ValueError("expert identity completeness failure")
    by_name = {tensor["name"]: tensor for tensor in tensors}
    for row in rows:
        expected_segments = []
        for projection in ("up", "gate", "down"):
            name = f"blk.{row['layer']}.ffn_{projection}_exps.weight"
            tensor = by_name.get(name)
            if tensor is None or tensor["category"] != "eligible_expert":
                raise ValueError("missing or misclassified expert projection")
            if len(tensor["shape"]) != 3 or tensor["shape"][-1] != experts:
                raise ValueError("expert dimension mismatch")
            if tensor["dtype"] not in ("F16", "F32"):
                raise ValueError("unsupported expert dtype/layout")
            elements = math.prod(positive(dimension, "tensor dimension") for dimension in tensor["shape"])
            if elements * {"F16": 2, "F32": 4}[tensor["dtype"]] != tensor["bytes"]:
                raise ValueError("expert shape/dtype bytes disagree")
            nbytes = tensor["bytes"] // experts
            if nbytes * experts != tensor["bytes"]:
                raise ValueError("unequal expert slices")
            expected_segments.append({"tensor": name, "projection": projection,
                                      "source_offset": tensor["source_offset"] + row["expert"] * nbytes,
                                      "bytes": nbytes})
        if row["segments"] != expected_segments:
            raise ValueError("expert segment identity/offset mismatch")
    if any(sum(s["bytes"] for s in r["segments"]) != r["bytes"] or
           r["packed_logical_pages"] != (r["bytes"] + page - 1) // page for r in rows):
        raise ValueError("expert segment/page conservation failure")
    eligible = sum(t["bytes"] for t in tensors if t["category"] == "eligible_expert")
    if sum(r["bytes"] for r in rows) != eligible or eligible != inv["eligible_expert_bytes"]:
        raise ValueError("expert byte conservation failure")
    if eligible + inv["resident_non_offloaded_bytes"] != inv["tensor_bytes"]:
        raise ValueError("resident byte conservation failure")
    for category, field in (("shared_expert", "shared_expert_bytes"), ("attention", "attention_bytes")):
        if sum(t["bytes"] for t in tensors if t["category"] == category) != inv[field]:
            raise ValueError("resident category byte conservation failure")
    if sum(r["packed_logical_pages"] for r in rows) != inv["packed_logical_pages"]:
        raise ValueError("page conservation failure")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--page-bytes", type=int, default=16384)
    args = parser.parse_args()
    inv = inventory_checkpoint(args.checkpoint, args.page_bytes)
    write_json(args.output, inv)
    print(json.dumps({key: inv[key] for key in ("E", "k", "layers", "tensor_bytes", "eligible_expert_bytes")}))


if __name__ == "__main__":
    main()

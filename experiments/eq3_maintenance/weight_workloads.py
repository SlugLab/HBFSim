"""Read-only LLM weight page extents and deterministic HBF read requests.

The model catalog contains metadata only.  Generated addresses always remain
in the complete model's global page space; a finite request window is never
renumbered into a smaller synthetic working set.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).with_name("sources") / "qwen2_5_weight_models.json"


def _positive_integer(value: Any, name: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value < (0 if allow_zero else 1):
        raise ValueError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return value


def _catalog() -> dict[str, Any]:
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return data["models"]


def model_metadata(model_id: str) -> dict[str, Any]:
    try:
        return json.loads(json.dumps(_catalog()[model_id]))
    except KeyError as error:
        raise ValueError(f"unknown model_id {model_id!r}") from error


def _logical_regions(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive exact BF16 tensor bytes in a documented logical packing order."""
    arch = metadata["architecture"]
    scalar = metadata["bytes_per_tensor_element"]
    hidden = arch["hidden_size"]
    intermediate = arch["intermediate_size"]
    layers = arch["num_hidden_layers"]
    heads = arch["num_attention_heads"]
    kv_heads = arch["num_key_value_heads"]
    vocab = arch["vocab_size"]
    if hidden % heads:
        raise ValueError("hidden size must be divisible by attention heads")
    head_dim = hidden // heads

    definitions: list[tuple[str, int, str]] = [
        ("model.embed_tokens", vocab * hidden * scalar, "embedding"),
    ]
    for layer in range(layers):
        prefix = f"model.layers.{layer}"
        definitions.extend((
            (prefix + ".input_layernorm", hidden * scalar, "layer_norm"),
            (prefix + ".self_attn",
             (hidden * hidden + hidden
              + 2 * (hidden * kv_heads * head_dim + kv_heads * head_dim)
              + hidden * hidden) * scalar,
             "attention"),
            (prefix + ".post_attention_layernorm", hidden * scalar, "layer_norm"),
            (prefix + ".mlp", 3 * hidden * intermediate * scalar, "mlp"),
        ))
    definitions.extend((
        ("model.norm", hidden * scalar, "final_norm"),
        ("lm_head", vocab * hidden * scalar, "output_embedding"),
    ))

    regions = []
    cursor = 0
    for identity, size, kind in definitions:
        regions.append({
            "id": identity,
            "kind": kind,
            "start_byte": cursor,
            "end_byte": cursor + size,
            "size_bytes": size,
        })
        cursor += size
    if cursor != metadata["tensor_payload_bytes"]:
        raise ValueError(
            f"architecture-derived tensor bytes {cursor} do not match official "
            f"metadata.total_size {metadata['tensor_payload_bytes']}"
        )
    return regions


def _stack_page_counts(page_count: int, stacks: int) -> list[int]:
    quotient, remainder = divmod(page_count, stacks)
    return [quotient + (index < remainder) for index in range(stacks)]


def weight_extent(model_id: str, page_bytes: int = 16384, stacks: int = 4) -> dict[str, Any]:
    """Return full-model page extent, logical regions, and static stripe layout."""
    page_bytes = _positive_integer(page_bytes, "page_bytes")
    stacks = _positive_integer(stacks, "stacks")
    if stacks not in {4, 8}:
        raise ValueError("stacks must be 4 or 8 for the current EQ3 topologies")
    metadata = model_metadata(model_id)
    payload = _positive_integer(metadata["tensor_payload_bytes"], "tensor_payload_bytes")
    pages = math.ceil(payload / page_bytes)
    regions = _logical_regions(metadata)
    for region in regions:
        region["first_global_page"] = region["start_byte"] // page_bytes
        region["last_global_page_inclusive"] = (region["end_byte"] - 1) // page_bytes
        region["overlapping_page_count"] = (
            region["last_global_page_inclusive"] - region["first_global_page"] + 1
        )
    return {
        "schema_version": "eq3-weight-extent-v1",
        "model_id": model_id,
        "revision": metadata["revision"],
        "resolved_commit": metadata["resolved_commit"],
        "dtype": metadata["dtype"],
        "tensor_payload_bytes": payload,
        "page_bytes": page_bytes,
        "global_page_count": pages,
        "allocated_page_bytes": pages * page_bytes,
        "last_page_padding_bytes": pages * page_bytes - payload,
        "stacks": stacks,
        "placement": "global_page_modulo_stack_static_stripe",
        "stack_page_counts": {
            f"hbf{index}": count
            for index, count in enumerate(_stack_page_counts(pages, stacks))
        },
        "mapping": {
            "stack_index": "global_page % stacks",
            "local_page": "global_page // stacks",
        },
        "logical_region_packing": "DERIVED_FROM_DOCUMENTED_QWEN2_ARCHITECTURE",
        "regions": regions,
        "sources": metadata["sources"],
        "limitations": [
            "logical regions are reconstructable derived packing, not safetensors file offsets",
            "token/s and inference-stage causality are UNAVAILABLE",
        ],
    }


def _selected_page_range(extent: dict[str, Any], region: str | None) -> tuple[int, int]:
    if region is None:
        return 0, extent["global_page_count"]
    matches = [
        item for item in extent["regions"]
        if item["id"] == region or item["id"].startswith(region + ".")
    ]
    if not matches:
        raise ValueError(f"unknown logical region {region!r}")
    first = min(item["first_global_page"] for item in matches)
    end = max(item["last_global_page_inclusive"] for item in matches) + 1
    return first, end


def generate_weight_requests(
    model_id: str,
    request_count: int,
    start_page: int,
    period_ns: int,
    *,
    page_bytes: int = 16384,
    stacks: int = 4,
    start_time_ns: int = 0,
    region: str | None = None,
    request_id_start: int = 0,
) -> dict[str, Any]:
    """Generate deterministic read requests in the complete global page space.

    ``region`` can create a layer/region hotspot.  ``start_page`` is still a
    global model page and must lie in that region; returned addresses are never
    rebased to zero.  Requests wrap only at the selected full-model/region
    boundary. ``scan_index`` records the canonical range cycle containing the
    request; aggregate equivalent scans are reported separately.
    """
    request_count = _positive_integer(request_count, "request_count", allow_zero=True)
    start_page = _positive_integer(start_page, "start_page", allow_zero=True)
    period_ns = _positive_integer(period_ns, "period_ns")
    start_time_ns = _positive_integer(start_time_ns, "start_time_ns", allow_zero=True)
    request_id_start = _positive_integer(request_id_start, "request_id_start", allow_zero=True)
    extent = weight_extent(model_id, page_bytes, stacks)
    range_start, range_end = _selected_page_range(extent, region)
    if not range_start <= start_page < range_end:
        raise ValueError(
            f"start_page {start_page} outside selected global range [{range_start}, {range_end})"
        )
    span = range_end - range_start
    regions = extent["regions"]
    requests = []
    unique_pages: set[int] = set()
    unique_valid_bytes = 0
    for index in range(request_count):
        sequence_offset = start_page - range_start + index
        global_page = range_start + sequence_offset % span
        scan_index = sequence_offset // span
        stack_index = global_page % stacks
        byte_address = global_page * page_bytes
        valid_bytes = min(page_bytes, extent["tensor_payload_bytes"] - byte_address)
        overlap = [
            item["id"] for item in regions
            if item["start_byte"] < byte_address + valid_bytes
            and byte_address < item["end_byte"]
        ]
        requests.append({
            "request_id": request_id_start + index,
            "arrival_ns": start_time_ns + index * period_ns,
            "operation": "read",
            "bytes": page_bytes,
            "valid_weight_bytes": valid_bytes,
            "stack": f"hbf{stack_index}",
            "global_page": global_page,
            "global_byte_address": byte_address,
            "local_page": global_page // stacks,
            "scan_index": scan_index,
            "logical_regions": overlap,
        })
        if global_page not in unique_pages:
            unique_pages.add(global_page)
            unique_valid_bytes += valid_bytes
    return {
        "schema_version": "eq3-weight-read-requests-v1",
        "model_id": model_id,
        "operation_mix": {"read_fraction": 1.0, "write_fraction": 0.0},
        "address_space": "FULL_MODEL_GLOBAL_PAGES",
        "selected_region": region,
        "selected_global_page_range": [range_start, range_end],
        "request_count": request_count,
        "period_ns": period_ns,
        "requests": requests,
        "coverage": {
            "unique_global_pages": len(unique_pages),
            "unique_model_payload_bytes": unique_valid_bytes,
            "model_payload_fraction": unique_valid_bytes / extent["tensor_payload_bytes"],
            "equivalent_full_page_scans": request_count / extent["global_page_count"],
            "equivalent_selected_range_scans": request_count / span,
        },
        "extent_summary": {
            key: extent[key] for key in (
                "tensor_payload_bytes", "page_bytes", "global_page_count", "stacks",
                "placement", "stack_page_counts", "revision", "resolved_commit"
            )
        },
        "limitations": [
            "read-only weight traffic; cache hits, activations, KV cache, and writes excluded",
            "request period is an explicit scenario input, not a token-rate derivation",
            "token/s is UNAVAILABLE",
        ],
    }


__all__ = ["generate_weight_requests", "model_metadata", "weight_extent"]

#!/usr/bin/env python3
"""Validate real routed-expert traces and deterministic token equivalence."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
from typing import Any

from model_inventory import ModelInventory


def request_tokens(summary: dict[str, Any]) -> list[tuple[list[int], list[int]]]:
    return [
        (list(item["prompt_token_ids"]), list(item["output_token_ids"]))
        for item in summary["requests"]
    ]


def route_identity(path: pathlib.Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            stable = {
                key: event[key]
                for key in (
                    "prompt_id",
                    "phase",
                    "token_step",
                    "route_token_index",
                    "layer_id",
                    "topk_expert_ids",
                    "expert_access_bytes",
                )
            }
            digest.update(
                json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
            )
            count += 1
    return digest.hexdigest(), count


def validate_trace(
    trace_path: pathlib.Path,
    summary_path: pathlib.Path,
    inventory: ModelInventory,
) -> dict[str, Any]:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    protocol = summary["protocol"]
    expected_per_layer = int(protocol["num_prompts"]) * (
        int(protocol["input_len"]) + int(protocol["output_len"]) - 1
    )
    expected_events = expected_per_layer * inventory.num_layers
    per_layer: collections.Counter[int] = collections.Counter()
    per_phase: collections.Counter[str] = collections.Counter()
    event_count = 0
    expert_access_count = 0
    tensor_access_count = 0
    recomputed_bytes = 0
    access_sequences: list[int] = []
    with trace_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if event["model_fingerprint"] != inventory.model_fingerprint:
                raise ValueError("model fingerprint mismatch")
            layer = int(event["layer_id"])
            ids = [int(value) for value in event["topk_expert_ids"]]
            if len(ids) != inventory.top_k or len(set(ids)) != len(ids):
                raise ValueError("invalid top-k cardinality")
            expected_tensors: dict[str, tuple[int, tuple[int, ...], str]] = {}
            for expert in ids:
                record = inventory.expert(layer, expert)
                recomputed_bytes += record.total_bytes
                for tensor in record.tensors:
                    expected_tensors[tensor.tensor] = (
                        tensor.bytes,
                        tensor.shape,
                        tensor.tensor_kind,
                    )
            observed = event["expert_tensors"]
            if len(observed) != len(expected_tensors):
                raise ValueError("tensor access cardinality mismatch")
            for item in observed:
                key = item["tensor"]
                expected = expected_tensors.get(key)
                if expected is None:
                    raise ValueError(f"unexpected tensor: {key}")
                if (
                    int(item["tensor_bytes"]),
                    tuple(int(value) for value in item["shape"]),
                    item["tensor_kind"],
                ) != expected:
                    raise ValueError(f"tensor metadata mismatch: {key}")
                access_sequences.append(int(item["access_order_sequence"]))
            per_layer[layer] += 1
            per_phase[str(event["phase"])] += 1
            event_count += 1
            expert_access_count += len(ids)
            tensor_access_count += len(observed)
    checks = {
        "event_count": event_count == expected_events,
        "per_layer_forward_count": all(
            per_layer[layer] == expected_per_layer
            for layer in range(inventory.num_layers)
        ),
        "expert_ids_in_range": True,
        "topk_cardinality": True,
        "tensor_shape_dtype_bytes_match_manifest": True,
        "total_access_bytes_recomputed": recomputed_bytes
        == expert_access_count * inventory.expert_bytes,
        "access_order_contiguous": access_sequences
        == list(range(len(access_sequences))),
        "trace_summary_event_count": event_count
        == int(summary["trace"]["event_count"]),
    }
    if not all(checks.values()):
        raise AssertionError(checks)
    return {
        "summary": summary,
        "validation": {
            "expected_events": expected_events,
            "event_count": event_count,
            "expert_access_count": expert_access_count,
            "tensor_access_count": tensor_access_count,
            "recomputed_expert_access_bytes": recomputed_bytes,
            "per_layer_event_count": {
                str(key): value for key, value in sorted(per_layer.items())
            },
            "per_phase_event_count": dict(sorted(per_phase.items())),
            "checks": checks,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True, type=pathlib.Path)
    parser.add_argument("--summary", required=True, type=pathlib.Path)
    parser.add_argument("--model-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--native-baseline", type=pathlib.Path)
    parser.add_argument("--repeat-trace", type=pathlib.Path)
    parser.add_argument("--repeat-summary", type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    inventory = ModelInventory(args.model_manifest)
    result = validate_trace(args.trace, args.summary, inventory)
    summary = result.pop("summary")
    checks = result["validation"]["checks"]
    checks["native_token_ids_bit_exact"] = None
    checks["repeat_token_ids_bit_exact"] = None
    checks["repeat_routes_bit_exact"] = None
    if args.native_baseline is not None:
        native = json.loads(args.native_baseline.read_text(encoding="utf-8"))
        checks["native_token_ids_bit_exact"] = (
            request_tokens(summary) == request_tokens(native)
        )
    if args.repeat_trace is not None or args.repeat_summary is not None:
        if args.repeat_trace is None or args.repeat_summary is None:
            raise ValueError("repeat trace and summary must be supplied together")
        repeat = validate_trace(args.repeat_trace, args.repeat_summary, inventory)
        checks["repeat_token_ids_bit_exact"] = request_tokens(summary) == request_tokens(
            repeat["summary"]
        )
        checks["repeat_routes_bit_exact"] = route_identity(args.trace) == route_identity(
            args.repeat_trace
        )
    result.update(
        {
            "schema_version": 1,
            "status": (
                "PASS"
                if all(value is not False for value in checks.values())
                else "FAIL"
            ),
            "evidence_class": "REAL_QWEN_TRACE",
            "model_fingerprint": inventory.model_fingerprint,
            "trace_identity": route_identity(args.trace),
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if result["status"] != "PASS":
        raise AssertionError(checks)
    print(json.dumps({"status": "PASS", "checks": checks}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

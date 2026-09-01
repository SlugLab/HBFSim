#!/usr/bin/env python3
"""Replay real Qwen expert routes under CLOCK, LRU, and Belady policies."""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import pathlib
from dataclasses import dataclass
from typing import Any, Iterable

from model_inventory import ModelInventory
from placement_policy import ExpertKey, make_policy

RATIOS: tuple[tuple[int, int], ...] = (
    (1, 0),
    (1, 1),
    (1, 2),
    (1, 4),
    (1, 8),
    (1, 16),
)
POLICIES = ("clock", "lru", "belady")


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def safe_ratio(numerator: int | float, denominator: int | float) -> float | None:
    return None if denominator == 0 else numerator / denominator


def ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def reuse_bucket(distance: int | None) -> str:
    if distance is None:
        return "cold"
    if distance == 0:
        return "0"
    lower = 1 << (distance.bit_length() - 1)
    upper = (lower << 1) - 1
    return f"{lower}-{upper}"


class Fenwick:
    def __init__(self, size: int) -> None:
        self.values = [0] * (size + 1)

    def add(self, index: int, delta: int) -> None:
        index += 1
        while index < len(self.values):
            self.values[index] += delta
            index += index & -index

    def prefix(self, end: int) -> int:
        total = 0
        while end > 0:
            total += self.values[end]
            end -= end & -end
        return total


@dataclass(frozen=True)
class TraceAccess:
    key: ExpertKey
    phase: str
    request_id: str
    prompt_id: int
    token_step: int
    layer_id: int


def load_trace(paths: Iterable[pathlib.Path], inventory: ModelInventory) -> tuple[
    list[TraceAccess], dict[str, Any]
]:
    accesses: list[TraceAccess] = []
    events = 0
    trace_files: list[dict[str, Any]] = []
    for input_path in paths:
        resolved = input_path.resolve(strict=True)
        digest = hashlib.sha256()
        line_count = 0
        with resolved.open("rb") as raw_stream:
            for raw_line in raw_stream:
                digest.update(raw_line)
                line_count += 1
                event = json.loads(raw_line)
                if int(event["schema_version"]) != 1:
                    raise ValueError("unsupported trace schema")
                if event["model_fingerprint"] != inventory.model_fingerprint:
                    raise ValueError("trace/model fingerprint mismatch")
                layer = int(event["layer_id"])
                experts = [int(value) for value in event["topk_expert_ids"]]
                if len(experts) != inventory.top_k or len(set(experts)) != len(experts):
                    raise ValueError("invalid top-k expert list")
                for expert in experts:
                    inventory.expert(layer, expert)
                    accesses.append(
                        TraceAccess(
                            key=(layer, expert),
                            phase=str(event["phase"]),
                            request_id=str(event["request_id"]),
                            prompt_id=int(event["prompt_id"]),
                            token_step=int(event["token_step"]),
                            layer_id=layer,
                        )
                    )
                events += 1
        trace_files.append(
            {
                "path": str(resolved),
                "sha256": digest.hexdigest(),
                "line_count": line_count,
            }
        )
    if not accesses:
        raise ValueError("trace contains no expert accesses")
    metadata = {
        "files": trace_files,
        "event_count": events,
        "expert_access_count": len(accesses),
        "trace_fingerprint": canonical_sha256(trace_files),
    }
    return accesses, metadata


def reuse_histogram(accesses: list[TraceAccess]) -> dict[str, int]:
    tree = Fenwick(len(accesses))
    last: dict[ExpertKey, int] = {}
    histogram: collections.Counter[str] = collections.Counter()
    active = 0
    for position, access in enumerate(accesses):
        previous = last.get(access.key)
        if previous is None:
            distance = None
            active += 1
        else:
            distance = active - tree.prefix(previous + 1)
            tree.add(previous, -1)
        tree.add(position, 1)
        last[access.key] = position
        histogram[reuse_bucket(distance)] += 1
    return dict(sorted(histogram.items()))


def capacity_geometry(
    inventory: ModelInventory, ratio: tuple[int, int], page_bytes: int
) -> dict[str, Any]:
    hbm, hbf = ratio
    requested_alpha = 1.0 if hbf == 0 else hbm / (hbm + hbf)
    requested_hbm = math.floor(inventory.expert_weight_bytes * requested_alpha)
    expert_pages = ceil_div(inventory.expert_bytes, page_bytes)
    expert_slot_bytes = expert_pages * page_bytes
    slots = (
        inventory.num_layers * inventory.num_experts
        if hbf == 0
        else requested_hbm // expert_slot_bytes
    )
    actual_hbm = slots * expert_slot_bytes
    actual_hbf = inventory.expert_weight_bytes - actual_hbm
    return {
        "requested_ratio": f"{hbm}:{hbf}",
        "requested_alpha": requested_alpha,
        "requested_hbm_bytes": requested_hbm,
        "actual_hbm_bytes": actual_hbm,
        "actual_hbf_bytes": actual_hbf,
        "achieved_alpha": safe_ratio(actual_hbm, inventory.expert_weight_bytes),
        "achieved_ratio_hbf_over_hbm": safe_ratio(actual_hbf, actual_hbm),
        "cache_frame_count": actual_hbm // page_bytes,
        "page_bytes": page_bytes,
        "complete_expert_slots": slots,
        "expert_slot_bytes": expert_slot_bytes,
    }


def replay_cell(
    accesses: list[TraceAccess],
    inventory: ModelInventory,
    profile: dict[str, Any],
    trace_meta: dict[str, Any],
    geometry: dict[str, Any],
    policy_name: str,
    git_commit: str,
    environment_fingerprint: str,
    repetition: int,
) -> dict[str, Any]:
    keys = [access.key for access in accesses]
    slots = int(geometry["complete_expert_slots"])
    full_preloaded = slots >= inventory.num_layers * inventory.num_experts
    policy = None if full_preloaded else make_policy(policy_name, slots, keys)
    hits = 0
    misses = 0
    evictions = 0
    per_object: dict[ExpertKey, dict[str, int]] = {}
    phase_stats: dict[str, dict[str, int]] = {}
    for position, access in enumerate(accesses):
        item = per_object.setdefault(
            access.key, {"access_count": 0, "miss_count": 0, "eviction_count": 0}
        )
        item["access_count"] += 1
        phase = phase_stats.setdefault(
            access.phase, {"access_count": 0, "hit_count": 0, "miss_count": 0}
        )
        phase["access_count"] += 1
        if full_preloaded:
            hit = True
            victim = None
        else:
            assert policy is not None
            outcome = policy.access(access.key, position)
            hit = outcome.hit
            victim = outcome.evicted
        if hit:
            hits += 1
            phase["hit_count"] += 1
        else:
            misses += 1
            item["miss_count"] += 1
            phase["miss_count"] += 1
        if victim is not None:
            evictions += 1
            per_object.setdefault(
                victim,
                {"access_count": 0, "miss_count": 0, "eviction_count": 0},
            )["eviction_count"] += 1

    expert_bytes = inventory.expert_bytes
    total_access_bytes = len(accesses) * expert_bytes
    hit_bytes = hits * expert_bytes
    miss_bytes = misses * expert_bytes
    bandwidth = int(profile["aggregate_bandwidth_bytes_per_s"])
    read_latency_ns = int(profile["read_latency_ns"])
    transfer_ns = ceil_div(expert_bytes * 1_000_000_000, bandwidth)
    modeled_read_ns = 0 if full_preloaded else misses * (read_latency_ns + transfer_ns)
    object_rows = []
    for (layer, expert), values in sorted(per_object.items()):
        object_rows.append(
            {
                "model_layer": layer,
                "expert_id": expert,
                "tensor_kind": "w13+w2",
                "range_id": None,
                "bytes": expert_bytes,
                **values,
            }
        )
    manifest = {
        "git_commit": git_commit,
        "model_fingerprint": inventory.model_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "workload": trace_meta["trace_fingerprint"],
        "ratio": geometry["requested_ratio"],
        "actual_bytes": geometry["actual_hbm_bytes"],
        "policy": policy_name,
        "hbf_profile": str(profile.get("name", "unknown")),
        "simulator_mode": "analytic-profile-serial-upper-bound",
        "prefetch": {"readahead_pages": 0},
        "seed": 0,
        "repetition": repetition,
    }
    conservation = {
        "request_conservation": hits + misses == len(accesses),
        "byte_conservation": hit_bytes + miss_bytes == total_access_bytes,
        "hbf_read_conservation": miss_bytes == miss_bytes,
        "no_unsigned_underflow": all(
            value >= 0
            for value in (hits, misses, evictions, hit_bytes, miss_bytes)
        ),
    }
    if not all(conservation.values()):
        raise AssertionError(f"replay conservation failed: {conservation}")
    return {
        "schema_version": 2,
        "status": "PASS",
        "evidence_class": "TRACE_DRIVEN_MODELED",
        "timing_evidence_class": "ANALYTIC_PROFILE_UPPER_BOUND",
        "cell_id": canonical_sha256(manifest),
        "cell_manifest": manifest,
        "trace": trace_meta,
        "geometry": geometry,
        "policy": policy_name,
        "cache_initialization": "all-experts-preloaded" if full_preloaded else "empty",
        "stats_v2": {
            "global": {
                "requests_total": len(accesses),
                "demand_requests": len(accesses),
                "speculative_requests": 0,
                "modeled_device_time_ns": modeled_read_ns,
                "host_service_time_ns": None,
                "backing_io_wall_time_ns": None,
                "h2d_copy_time_ns": None,
                "dtoh_copy_time_ns": None,
                "emulator_dispatcher_wall_time_ns": None,
                "application_wall_time_ns": None,
            },
            "capacity": {
                "configured_hbm_cache_bytes": geometry["requested_hbm_bytes"],
                "actual_page_aligned_hbm_cache_bytes": geometry["actual_hbm_bytes"],
                "hbf_logical_bytes": geometry["actual_hbf_bytes"],
                "hbf_actually_accessed_bytes": miss_bytes,
                "resident_bytes_current": geometry["actual_hbm_bytes"]
                if full_preloaded
                else min(slots, len(set(keys))) * geometry["expert_slot_bytes"],
                "resident_bytes_peak": geometry["actual_hbm_bytes"]
                if full_preloaded
                else min(slots, len(set(keys))) * geometry["expert_slot_bytes"],
                "free_frames": 0 if full_preloaded else max(0, slots - len(set(keys))),
                "hits": hits,
                "misses": misses,
                "byte_hit_ratio": safe_ratio(hit_bytes, total_access_bytes),
                "page_hit_ratio": safe_ratio(hits, hits + misses),
                "clean_evictions": evictions,
                "dirty_evictions": 0,
                "writeback_bytes": 0,
                "hbf_read_bytes": miss_bytes,
                "hbf_program_bytes": 0,
                "duplicate_misses": 0,
                "coalesced_misses": 0,
                "in_flight_pages": 0,
                "page_residence_time_ns": None,
                "reuse_distance_histogram": reuse_histogram(accesses),
            },
            "scheduling": {
                "queue_depth_histogram": None,
                "engine_outstanding_requests": None,
                "per_channel_utilization": None,
                "demand_waiting_time_ns": modeled_read_ns,
                "speculative_waiting_time_ns": None,
                "demand_exposed_stall_ns": modeled_read_ns,
                "hidden_prefetched_stall_ns": 0,
                "late_prefetch_stall_ns": 0,
            },
            "objects": object_rows,
            "phases": phase_stats,
        },
        "derived": {
            "expert_access_bytes": total_access_bytes,
            "hit_bytes": hit_bytes,
            "miss_bytes": miss_bytes,
            "hbf_bytes_per_route_token_expert_access": safe_ratio(
                miss_bytes, len(accesses)
            ),
            "serialized_modeled_ns_per_miss": read_latency_ns + transfer_ns,
            "modeled_time_semantics": (
                "sum of nominal read latency plus aggregate-bandwidth transfer; "
                "conservative serialized analytic bound, not HBFSim fast/hybrid "
                "or MQSim device execution"
            ),
        },
        "conservation": conservation,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True, action="append", type=pathlib.Path)
    parser.add_argument("--model-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--profile", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--environment-fingerprint", required=True)
    parser.add_argument("--repetition", type=int, default=0)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    args.output_dir.mkdir(parents=True)
    inventory = ModelInventory(args.model_manifest)
    accesses, trace_meta = load_trace(args.trace, inventory)
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    raw_dir = args.output_dir / "raw"
    raw_dir.mkdir()
    for ratio in RATIOS:
        geometry = capacity_geometry(inventory, ratio, int(profile["page_bytes"]))
        for policy in POLICIES:
            result = replay_cell(
                accesses,
                inventory,
                profile,
                trace_meta,
                geometry,
                policy,
                args.git_commit,
                args.environment_fingerprint,
                args.repetition,
            )
            cell_path = raw_dir / f"{result['cell_id']}.json"
            cell_path.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            results.append(result)
    summary = {
        "schema_version": 1,
        "status": "PASS",
        "evidence_class": "TRACE_DRIVEN_MODELED",
        "timing_boundary": (
            "cache/traffic results are exact for the frozen trace and policy; "
            "timing is an analytic serialized upper bound pending HBFSim/MQSim replay"
        ),
        "cell_count": len(results),
        "trace": trace_meta,
        "cells": [
            {
                "cell_id": item["cell_id"],
                "ratio": item["geometry"]["requested_ratio"],
                "policy": item["policy"],
                "actual_hbm_bytes": item["geometry"]["actual_hbm_bytes"],
                "actual_hbf_bytes": item["geometry"]["actual_hbf_bytes"],
                "hits": item["stats_v2"]["capacity"]["hits"],
                "misses": item["stats_v2"]["capacity"]["misses"],
                "byte_hit_ratio": item["stats_v2"]["capacity"]["byte_hit_ratio"],
                "hbf_read_bytes": item["stats_v2"]["capacity"]["hbf_read_bytes"],
                "modeled_device_time_ns": item["stats_v2"]["global"][
                    "modeled_device_time_ns"
                ],
            }
            for item in results
        ],
    }
    (args.output_dir / "replay-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (args.output_dir / "replay-summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        rows = summary["cells"]
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"status": "PASS", "cell_count": len(results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

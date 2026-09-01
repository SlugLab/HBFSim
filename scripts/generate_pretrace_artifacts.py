#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import subprocess
from datetime import datetime, timezone
from typing import Any


RATIOS = ((1, 0), (1, 1), (1, 2), (1, 4), (1, 8), (1, 16))


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: pathlib.Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def capacity_geometry(
    expert_weight_bytes: int,
    expert_bytes: int,
    total_experts: int,
    page_bytes: int,
    ratio: tuple[int, int],
) -> dict[str, object]:
    hbm, hbf = ratio
    requested_alpha = 1.0 if hbf == 0 else hbm / (hbm + hbf)
    requested_hbm = math.floor(expert_weight_bytes * requested_alpha)
    expert_slot_bytes = math.ceil(expert_bytes / page_bytes) * page_bytes
    slots = (
        total_experts
        if hbf == 0
        else min(total_experts, requested_hbm // expert_slot_bytes)
    )
    actual_hbm = slots * expert_slot_bytes
    resident_expert_weight_bytes = slots * expert_bytes
    actual_hbf = max(0, expert_weight_bytes - resident_expert_weight_bytes)
    return {
        "requested_ratio": f"{hbm}:{hbf}",
        "requested_alpha": requested_alpha,
        "requested_hbm_bytes": requested_hbm,
        "actual_hbm_bytes": actual_hbm,
        "actual_hbf_bytes": actual_hbf,
        "resident_expert_weight_bytes": resident_expert_weight_bytes,
        "page_alignment_padding_bytes": actual_hbm
        - resident_expert_weight_bytes,
        "achieved_alpha": resident_expert_weight_bytes / expert_weight_bytes,
        "achieved_ratio_hbf_over_hbm": (
            0.0
            if resident_expert_weight_bytes == 0
            else actual_hbf / resident_expert_weight_bytes
        ),
        "cache_frame_count": actual_hbm // page_bytes,
        "page_bytes": page_bytes,
        "complete_expert_slots": slots,
        "expert_slot_bytes": expert_slot_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-root", required=True, type=pathlib.Path)
    parser.add_argument("--repository", required=True, type=pathlib.Path)
    args = parser.parse_args()

    root = args.result_root.resolve(strict=True)
    repository = args.repository.resolve(strict=True)
    boundary = pathlib.Path("/root/hbfsim-exp").resolve(strict=True)
    if boundary not in root.parents:
        raise ValueError(f"result root escapes write boundary: {root}")

    model = read_json(root / "05-model-manifest.json")
    environment = read_json(root / "01-environment-consistency.json")
    profile_path = repository / "configs" / "profiles" / "nominal.json"
    profile = read_json(profile_path)
    schema_path = repository / "adapters" / "vllm_capacity" / "trace_schema.json"
    trace_schema = read_json(schema_path)
    git_commit_for_replay = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        text=True,
    ).strip()

    configuration = model["configuration"]
    total_experts = int(model["expert_count_total"])
    expert_weight_bytes = int(model["expert_weight_bytes"])
    expert_bytes_values = model["per_expert_bytes_unique"]
    if len(expert_bytes_values) != 1:
        raise ValueError("capacity matrix requires uniform complete expert size")
    expert_bytes = int(expert_bytes_values[0])
    page_bytes = int(profile["page_bytes"])
    geometry = [
        capacity_geometry(
            expert_weight_bytes,
            expert_bytes,
            total_experts,
            page_bytes,
            ratio,
        )
        for ratio in RATIOS
    ]

    schema_artifact = {
        "schema_version": 1,
        "status": "E4_SCHEMA_MATERIALIZED_AFTER_VALIDATION",
        "protocol_timing": (
            "schema materialized after E4 trace validation and before E5 replay; "
            "not represented as a pre-E4 preregistration"
        ),
        "source_path": str(schema_path),
        "source_sha256": sha256(schema_path),
        "model_fingerprint": model["ModelFingerprint"],
        "environment_fingerprint": environment["environment_fingerprint"],
        "event_schema": trace_schema,
    }
    write_json(root / "07-trace-schema.json", schema_artifact)

    matrix = {
        "schema_version": 1,
        "status": "E5_PROTOCOL_FROZEN_BEFORE_REPLAY",
        "materialized_at_utc": datetime.now(timezone.utc).isoformat(),
        "branch": "exp/hbm-hbf-capacity-qwen3-30b-a3b",
        "protocol_source_commit": (
            "d270003f6d0566921f48ddc1408d8dd29b3af0a8"
        ),
        "git_commit_for_replay": git_commit_for_replay,
        "protocol_timing": (
            "E5 matrix frozen after E4 trace validation and before any E5 "
            "measured replay; not represented as a pre-E4 preregistration"
        ),
        "model_fingerprint": model["ModelFingerprint"],
        "environment_fingerprint": environment["environment_fingerprint"],
        "model_geometry": {
            "num_layers": int(configuration["num_hidden_layers"]),
            "num_experts_per_layer": int(configuration["num_experts"]),
            "top_k": int(configuration["num_experts_per_tok"]),
            "total_complete_experts": total_experts,
            "expert_bytes": expert_bytes,
            "tierable_expert_weight_bytes": expert_weight_bytes,
            "non_expert_tensor_bytes": int(model["non_expert_tensor_bytes"]),
        },
        "frozen_profile": {
            "path": str(profile_path),
            "sha256": sha256(profile_path),
            "page_bytes": page_bytes,
            "thermal": "off",
            "readahead_pages": 0,
        },
        "capacity_points": geometry,
        "trace_replay": {
            "input": "REAL_QWEN_TRACE_ONLY",
            "synthetic_qwen_sequence_prohibited": True,
            "policies": ["CLOCK", "LRU", "offline Belady"],
            "full_resident_semantics": "preloaded ideal near-memory",
            "coarse_sweep_modes": ["fast", "hybrid"],
            "warmup_repetition": -1,
            "measured_repetitions": [0, 1, 2, 3, 4],
            "coarse_sweep_repetitions": [0, 1, 2, 3, 4],
            "mqsim_reference_repetitions": [0, 1, 2, 3, 4],
            "configuration_order": "randomized independently per repetition",
            "warmup_order_seed": 7919,
            "measured_order_seeds": [1009, 2027, 3037, 4051, 5059],
            "mqsim_required_points": ["1:0", "1:1", "1:4", "1:16"],
            "mqsim_adaptive_points": [
                "nearest valid point left of detected knee",
                "detected knee",
                "nearest valid point right of detected knee",
            ],
            "scheduling_semantics": (
                "ordered blocking demand misses with no compute gaps"
            ),
            "reported_time_domains": [
                "modeled_device_service_ns",
                "demand_exposed_stall_ns",
                "emulator_dispatcher_wall_time_ns",
            ],
            "run_gate": {
                "gpu_exclusive_required_before_each_run": True,
                "gpu_temperature_power_memory_snapshot_required": True,
                "gpu_clock_or_power_limit_changes_prohibited": True,
            },
        },
        "knee_preregistration": {
            "performance_loss_thresholds": [0.05, 0.10],
            "selection_direction": (
                "smallest achieved HBM bytes meeting each threshold"
            ),
            "slope_change": (
                "largest absolute adjacent second difference after ordering "
                "by achieved alpha; ties select smaller HBM"
            ),
            "online_slo": {
                "ttft": "<= 1.10x native at identical workload/load",
                "tpot": "<= 1.10x native at identical workload/load",
                "absolute_thresholds": (
                    "record native-derived seconds before any capacity cell"
                ),
                "post_hoc_changes_prohibited": True,
            },
        },
        "workloads": [
            {
                "name": "deterministic-smoke",
                "input_tokens": 32,
                "output_tokens": 32,
                "concurrency": [1],
                "prompts": 4,
                "seed": 0,
                "ignore_eos": True,
            },
            {
                "name": "short-interactive",
                "input_tokens": 512,
                "output_tokens": 256,
                "concurrency": [1, 8, 32],
                "seed": 0,
                "ignore_eos": True,
            },
            {
                "name": "prefill-heavy",
                "input_tokens": 8192,
                "output_tokens": 64,
                "concurrency": [1, 8],
                "optional_concurrency": [32],
                "seed": 0,
                "ignore_eos": True,
            },
            {
                "name": "decode-heavy",
                "input_tokens": 256,
                "pilot_output_tokens": 512,
                "formal_output_tokens": 2048,
                "concurrency": [1, 8, 32],
                "seed": 0,
                "ignore_eos": True,
            },
        ],
        "formal_arrival_rates": [
            "0.25x native sustainable saturation",
            "0.50x native sustainable saturation",
            "0.75x native sustainable saturation",
            "0.90x native sustainable saturation",
        ],
        "execution_gates": {
            "E4": "two bit-exact validated real traces plus overhead",
            "E5": "all coarse cells plus selected MQSim references",
            "E6": "full-resident adapter bit-exact and overhead measured",
            "E7": "constrained eviction point bit-exact and metrics complete",
            "E8": "authorized only after E6 and E7 pass",
            "E9": "prohibited because PR #5 did not pass",
        },
        "prefetch": {
            "status": "PROHIBITED_PR5_REJECTED",
            "readahead_pages": 0,
        },
    }
    matrix["matrix_fingerprint_excludes"] = [
        "materialized_at_utc",
        "matrix_fingerprint",
    ]
    matrix["matrix_fingerprint"] = canonical_sha256(
        {
            key: value
            for key, value in matrix.items()
            if key not in matrix["matrix_fingerprint_excludes"]
        }
    )
    write_json(root / "08-experiment-matrix.json", matrix)

    design = f"""# Experts-only capacity adapter design

Status: **DESIGN FROZEN AFTER E4; IMPLEMENTATION GATED ON E5**.

This document materializes the adapter contract after E4 trace validation and
before E5 measured replay. It is not represented as a pre-E4 preregistration,
and it is not evidence that end-to-end capacity staging has been implemented.

## Scope and environment

- Tierable objects are only Qwen3-30B-A3B routed-expert `w13` and `w2` tensors.
- Dense weights, attention, embedding, norms, shared expert and LM head stay on
  the unchanged original vLLM path.
- The original native environment remains Python 3.13, PyTorch 2.9.1/cu128,
  vLLM 0.15.1, Triton 3.5.1, FlashInfer 0.6.1 and GCC/G++ 13 extensions.
- HBF timing/build code remains CUDA 13.0 with GCC/G++ 14 and explicit CUDA
  host compiler G++ 14.  The two component contracts must not be merged.
- Model fingerprint: `{model['ModelFingerprint']}`.
- Environment fingerprint: `{environment['environment_fingerprint']}`.

## Immutable backing

Build a read-only expert pack from the already present safetensors shards.  One
metadata record identifies `(layer, expert_id)`, the exact source shard and
offsets for both `w13` and `w2`, their shapes/dtype/byte counts, and independent
SHA-256 values.  A complete expert is publishable only when both tensors match
their recorded hashes.  No new model or transformed checkpoint is downloaded.

## Bounded HBM cache

Allocate only `complete_expert_slots` compact slots from the frozen capacity
geometry in `08-experiment-matrix.json`; never allocate a hidden full
`[num_experts, ...]` GPU tensor at a constrained point.  Each slot has a
generation, state, owner key, in-flight ticket and CLOCK/LRU metadata.  A router
step first merges unique `(layer, expert_id)` keys and pins all active entries.
Misses submit one modeled read per complete expert.  Backing read and H2D copies
for both `w13` and `w2` may start only under that ticket; the slot is published
resident only after modeled completion, copy completion and checksums succeed.
Failure rolls the slot back transactionally and cannot evict a pinned demand
resident entry.

## Fused-MoE boundary

The call boundary accepts compact, ordinary CUDA HBM tensors and remapped slot
IDs only.  It never passes an HBFSim capacity pointer, unbacked range, stale
generation or opaque pointer to Triton/FlashInfer.  Original top-k IDs and
weights are retained for audit; remapping changes only which compact tensor row
the fused kernel reads.  Both `w13` and `w2` use the same slot generation.

If the installed fused-MoE API cannot consume a compact first dimension plus
remapped IDs without changing numerical order, E6 stops as NOT_COMPLETED.  A
full physical expert tensor is not an acceptable workaround.

## Modes and gates

1. `adapter=off`: unchanged native vLLM.
2. `adapter=on, full-resident`: adapter boundary exercised with all {total_experts}
   complete experts resident; token IDs, router IDs and micro-tensor hashes must
   match native exactly; adapter-only overhead is measured.
3. `adapter=on, constrained`: first run 1:4 if it causes eviction, otherwise use
   the E5 trace-discovered eviction point.  It must remain token-bit-exact and
   pass no-stale-slot, no-duplicate-load, no-use-before-completion and leak
   checks before any formal matrix is authorized.

E6 implementation starts only after two deterministic real traces validate and
the CLOCK/LRU/Belady E5 replay completes.  PR #5 was rejected, so prefetch stays
disabled and no prefetch performance cell is authorized.

## Required accounting

Report requested and achieved capacity, raw expert bytes, alignment padding,
slot/frame counts, HBM peak memory, hits/misses/evictions, demand HBF bytes,
modeled service/stall, backing I/O, copy time, host service, and dispatcher wall
time separately.  `demand_media_reads`, `speculative_media_reads` and
`total_media_reads` remain distinct; speculative values are zero while PR #5 is
blocked.
"""
    (root / "06-capacity-adapter-design.md").write_text(design, encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "PASS",
                "reports": [
                    "06-capacity-adapter-design.md",
                    "07-trace-schema.json",
                    "08-experiment-matrix.json",
                ],
                "capacity_points": len(geometry),
                "matrix_fingerprint": matrix["matrix_fingerprint"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

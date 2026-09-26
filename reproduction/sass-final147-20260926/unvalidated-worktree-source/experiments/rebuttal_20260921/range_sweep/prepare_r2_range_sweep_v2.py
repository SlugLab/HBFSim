#!/usr/bin/env python3
"""Prepare, but never launch, the final-bundle R2 range sweep."""

import argparse
import hashlib
import json
from pathlib import Path

SIZES = (16 << 10, 1 << 20, 16 << 20, 64 << 20, 256 << 20)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--r1-validation", type=Path, required=True)
    parser.add_argument("--final-nominal-dir", type=Path, required=True)
    parser.add_argument("--joint-bundle-manifest", type=Path, required=True)
    parser.add_argument("--nominal-launcher", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    blockers: list[str] = []
    r1 = json.loads(args.r1_validation.read_text()) if args.r1_validation.is_file() else {}
    if r1.get("status") != "PASS":
        blockers.append("final_r1_validation_not_pass")
    nominal_result_path = args.final_nominal_dir / "result.json"
    registration_path = args.final_nominal_dir / "registration.json"
    nominal = json.loads(nominal_result_path.read_text()) if nominal_result_path.is_file() else {}
    registration = json.loads(registration_path.read_text()) if registration_path.is_file() else {}
    if nominal.get("scientific_status") != "COMPLETE" or nominal.get("request_terminal_status") != "success":
        blockers.append("final_nominal_not_complete")
    access = (nominal.get("access_accounting") or {}).get("access") or {}
    if access.get("status") != "COMPLETE":
        blockers.append("final_nominal_accounting_not_complete")
    if "eval_delay" in (nominal.get("access_accounting") or {}):
        blockers.append("final_nominal_eval_must_be_inactive")
    storages = registration.get("storages") or []
    if len(storages) != 1 or int(storages[0].get("storage_bytes", 0)) <= 0:
        blockers.append("single_positive_storage_binding_missing")
        full = 0
    else:
        full = int(storages[0]["storage_bytes"])
    if registration.get("registered_bytes") != (16 << 10):
        blockers.append("final_nominal_registered_bytes_not_16KiB")
    validated_nominal = ((r1.get("arms") or {}).get("nominal") or {}).get("result_sha256")
    if nominal_result_path.is_file() and validated_nominal != sha(nominal_result_path):
        blockers.append("final_nominal_not_bound_to_r1_validation")
    for required in (args.joint_bundle_manifest, args.nominal_launcher):
        if not required.is_file():
            blockers.append(f"missing:{required}")

    cells = []
    seen = set()
    for requested in (*SIZES, full):
        effective = min(requested, full) if full else requested
        if effective in seen:
            continue
        seen.add(effective)
        reused = effective == (16 << 10)
        cell = {
            "requested_bytes": requested,
            "effective_bytes": effective,
            "is_full_storage": bool(full and effective == full),
            "status": "REUSED_FINAL_R1_NOMINAL" if reused else "NOT_RUN",
            "attempt_limit": 0 if reused else 1,
            "timeout_seconds": 0 if reused else 900,
            "kill_after_seconds": 10,
        }
        if reused:
            cell["reuse"] = {
                "result_path": str(nominal_result_path.resolve()),
                "result_sha256": sha(nominal_result_path),
                "registration_path": str(registration_path.resolve()),
                "registration_sha256": sha(registration_path),
            }
        cells.append(cell)

    observed = nominal.get("generation_seconds")
    ready = not blockers
    plan = {
        "schema_version": 2,
        "status": "READY_NOT_RUN" if ready else "BLOCKED_BY_R1",
        "gpu_execution_requested": False,
        "cells": cells,
        "object_full_bytes": full,
        "only_per_cell_override": "hbf_range_bytes=effective_bytes",
        "fixed_inputs": {
            "r1_validation_path": str(args.r1_validation.resolve()),
            "r1_validation_sha256": sha(args.r1_validation) if args.r1_validation.is_file() else None,
            "joint_bundle_manifest_path": str(args.joint_bundle_manifest.resolve()),
            "joint_bundle_manifest_sha256": sha(args.joint_bundle_manifest) if args.joint_bundle_manifest.is_file() else None,
            "nominal_launcher_path": str(args.nominal_launcher.resolve()),
            "nominal_launcher_sha256": sha(args.nominal_launcher) if args.nominal_launcher.is_file() else None,
            "profile_path": nominal.get("profile"),
            "profile_sha256": sha(Path(nominal["profile"])) if nominal.get("profile") else None,
            "output_token_ids_sha256": nominal.get("output_token_ids_sha256"),
            "model": nominal.get("model"),
            "prompt_token_ids": nominal.get("prompt_token_ids"),
            "input_len": nominal.get("input_len"),
            "output_len": nominal.get("output_len"),
            "max_model_len": nominal.get("max_model_len"),
            "max_num_batched_tokens": nominal.get("max_num_batched_tokens"),
            "num_prompts": nominal.get("num_prompts"),
            "seed": nominal.get("seed"),
            "warmup_requests": nominal.get("warmup_requests"),
            "attention_backend": nominal.get("attention_backend"),
            "hbf_timing_model": nominal.get("hbf_timing_model"),
        },
        "resource_bounds": {
            "per_new_cell_timeout_seconds": 900,
            "new_cell_count": sum(c["attempt_limit"] for c in cells),
            "maximum_new_gpu_active_seconds": 900 * sum(c["attempt_limit"] for c in cells),
            "maximum_termination_grace_seconds": 10 * sum(c["attempt_limit"] for c in cells),
            "wall_time_note": "worker caps plus termination grace; setup and verified cleanup are measured separately",
            "retries": 0,
            "basis": {
                "final_r1_nominal_generation_seconds": observed,
                "policy": "fixed finite cap; not a linear extrapolation from range bytes",
            },
        },
        "continuation_policy": {
            "timeout": "preserve partial raw; do not extend that cell; continue only after cleanup and resource gates",
            "hard_stop": [
                "CUDA fault or OOM", "output-token mismatch", "counter closure failure",
                "snapshot incomplete", "monitoring unavailable", "cleanup failure",
            ],
            "no_workload_change": True,
        },
        "claim_gate": {
            "minimum": "16KiB reused final R1 nominal + one larger intermediate + full storage must pass",
            "full_matrix": "all planned unique ranges pass",
            "failure_scope": "failed or timed-out cells remain failures; no smaller workload is relabeled as that cell",
        },
        "blockers": blockers,
        "zero_new_gpu_runs_performed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())

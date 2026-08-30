#!/usr/bin/env python3
"""Freeze APP-2 baseline and fail-closed compatibility evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import time


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_evidence(source: pathlib.Path, evidence_dir: pathlib.Path, name: str) -> dict:
    destination = evidence_dir / name
    shutil.copy2(source, destination)
    return {
        "source_path": str(source.resolve()),
        "evidence_path": f"evidence/{name}",
        "bytes": destination.stat().st_size,
        "sha256": sha256(destination),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-result", required=True, type=pathlib.Path)
    parser.add_argument("--v1-stderr", required=True, type=pathlib.Path)
    parser.add_argument("--v2-registration", required=True, type=pathlib.Path)
    parser.add_argument("--v2-bindings", required=True, type=pathlib.Path)
    parser.add_argument("--v2-stderr", required=True, type=pathlib.Path)
    parser.add_argument("--compatibility", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    evidence_dir = args.output / "evidence"
    evidence_dir.mkdir()

    sources = {
        "baseline_result": (args.baseline_result, "baseline-result.json"),
        "v1_stderr": (args.v1_stderr, "v1-package-off-stderr.txt"),
        "v2_registration": (args.v2_registration, "v2-registration.json"),
        "v2_bindings": (args.v2_bindings, "v2-triton-bindings.jsonl"),
        "v2_stderr": (args.v2_stderr, "v2-package-off-stderr.txt"),
        "compatibility": (args.compatibility, "validated-compatibility.json"),
    }
    artifacts = {
        key: copy_evidence(source, evidence_dir, name)
        for key, (source, name) in sources.items()
    }
    baseline = json.loads(args.baseline_result.read_text())
    compatibility = json.loads(args.compatibility.read_text())
    registration = json.loads(args.v2_registration.read_text())
    bindings = [
        json.loads(line)
        for line in args.v2_bindings.read_text().splitlines()
        if line.strip()
    ]
    binding_counts: dict[str, int] = {}
    for item in bindings:
        result = str(item.get("result", "unknown"))
        binding_counts[result] = binding_counts.get(result, 0) + 1
    v1_error = args.v1_stderr.read_text(errors="replace")
    v2_error = args.v2_stderr.read_text(errors="replace")
    payload = {
        "schema_version": 1,
        "campaign": "APP-2 vLLM/Qwen package-thermal readiness",
        "created_unix_ns": time.time_ns(),
        "verdict": "NO_GO_CURRENT_HOST_STACK",
        "validated_stack": compatibility["validated_stack"],
        "observed_stack": baseline["versions"],
        "baseline": {
            "succeeded": True,
            "load_seconds": baseline["load_seconds"],
            "generation_seconds": baseline["generation_seconds"],
            "output_token_ids": baseline["output_token_ids"],
            "model": baseline["model"],
            "seed": baseline["seed"],
        },
        "attempts": [
            {
                "id": "v1-pinned-selector-on-current-vllm",
                "package_thermal_stage": "off",
                "reached_generation": False,
                "failure": "pinned vLLM 0.15.1 parameter selector did not match vLLM 0.26.0 names",
                "evidence_detected": "parameter_regex matched no CUDA storages" in v1_error,
            },
            {
                "id": "v2-current-selector-with-transformed-fused-moe",
                "package_thermal_stage": "off",
                "registration": {
                    "unique_storage_count": registration["unique_storage_count"],
                    "registered_bytes": registration["registered_bytes"],
                    "selection": registration["selection"],
                },
                "binding_result_counts": binding_counts,
                "reached_generation": True,
                "failure": "Triton fused_moe launch failed with CUDA invalid argument after transformed function binding",
                "evidence_detected": "Triton Error [CUDA]: invalid argument" in v2_error,
            },
        ],
        "claim_boundary": {
            "native_baseline": "SUPPORTED_ON_CURRENT_HOST",
            "adapter_registration": "SUPPORTED_ON_CURRENT_HOST",
            "package_thermal_off_generation": "FAILED_CLOSED",
            "read_only_shadow_active_matrix": "NOT_EXECUTED",
            "thermal_report": "NOT_PRODUCED",
            "token_equivalence_with_hbfsim": "NOT_ESTABLISHED",
            "performance_or_thermal_claim": "PROHIBITED",
        },
        "root_cause_scope": (
            "The checked compatibility contract pins vLLM 0.15.1, torch 2.9.1+cu128, "
            "Triton 3.5.1, and FlashInfer 0.6.1. The host now exposes vLLM 0.26.0, "
            "torch 2.11.0, Triton 3.6.0, and FlashInfer 0.6.14. The old transformed "
            "kernel launch contract is therefore not treated as portable evidence."
        ),
        "artifacts": artifacts,
    }
    result = args.output / "app2-readiness.json"
    result.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({
        "verdict": payload["verdict"],
        "baseline_tokens": payload["baseline"]["output_token_ids"],
        "binding_result_counts": binding_counts,
        "result_sha256": sha256(result),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate that a bpftime-wrapped target produced activation evidence.

This is deliberately an activation check, not the strict scientific validator.
Strict runs may contain expected negative fixture records; the auto workflow
performs the complete strict-policy validation separately.
"""

import argparse
import json
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            records.append(value)
    return records


def validate_activation(manifest_path: Path, coverage_path: Path, policy: str) -> None:
    manifests = _read_jsonl(manifest_path)
    decisions = _read_jsonl(coverage_path)
    if not manifests or not all(m.get("module_id") and m.get("kernel") for m in manifests):
        raise ValueError("missing a valid instrumentation manifest")
    if not decisions or not all(isinstance(d.get("allowed"), bool) for d in decisions):
        raise ValueError("missing coverage decisions with boolean allowed fields")

    strict_records = any(
        d.get("requires_instrumented_execution") is True for d in decisions
    )
    if policy == "strict" or (policy == "auto" and strict_records):
        # A strict no-direct-hit launch is legitimate activation evidence when
        # the known module was forced down the transformed execution path.
        # Denied fixture records may coexist; the strict workflow validates
        # those denials and the bridge selection log independently.
        activated = any(
            d["allowed"]
            and (d.get("modeled") is True
                 or d.get("requires_instrumented_execution") is True)
            for d in decisions
        )
        if not activated:
            raise ValueError("strict run has no allowed transformed activation decision")
        return

    # Preserve the historical wrapper contract when strict policy is unset.
    if not all(d.get("reason") for d in decisions):
        raise ValueError("legacy coverage decision is missing a reason")
    if not any(d.get("modeled") is True for d in decisions):
        raise ValueError("legacy run has no modeled decision")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("coverage", type=Path)
    parser.add_argument(
        "--policy", choices=("auto", "legacy", "partial", "strict"), default="auto"
    )
    args = parser.parse_args()
    validate_activation(args.manifest, args.coverage, args.policy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

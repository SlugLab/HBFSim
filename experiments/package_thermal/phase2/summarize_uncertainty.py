#!/usr/bin/env python3
"""Summarize a completed Phase-II uncertainty campaign as tornado data."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any


def load(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    plan_path = root / "campaign-plan.json"
    summary_path = root / "summary.json"
    plan = load(plan_path)
    summary = load(summary_path)
    cases = {item["case_id"]: item for item in summary["cases"]}
    records = []
    for relative in plan["cases"]:
        case_path = root / relative
        case = load(case_path)
        result = cases[case["case_id"]]
        if result.get("status") is not None:
            raise ValueError(f"incomplete uncertainty case: {case['case_id']}")
        records.append({
            "height": int(case["geometry"]["stack_height"]),
            "arm": case["uncertainty"]["arm"],
            "parameter": case["uncertainty"]["parameter"],
            "multiplier": float(case["uncertainty"]["multiplier"]),
            "hbf_hotspot_c": float(result["final_hbf_hotspot_c"]),
            "thermal_resistance_k_per_w": float(
                result["thermal_resistance_k_per_w"]),
            "case_sha256": digest(case_path),
        })
    tornado = []
    for height in (8, 16):
        height_rows = [row for row in records if row["height"] == height]
        baseline = next(row for row in height_rows if row["arm"] == "baseline")
        baseline_rise = baseline["hbf_hotspot_c"] - 30.0
        for row in height_rows:
            row["hotspot_delta_vs_baseline_c"] = (
                row["hbf_hotspot_c"] - baseline["hbf_hotspot_c"])
            row["hotspot_rise_relative_change"] = (
                (row["hbf_hotspot_c"] - 30.0) / baseline_rise - 1.0
                if baseline_rise else None)
        tornado.append({
            "height": height,
            "baseline": baseline,
            "arms_by_absolute_hotspot_effect": sorted(
                [row for row in height_rows if row["arm"] != "baseline"],
                key=lambda row: abs(row["hotspot_delta_vs_baseline_c"]),
                reverse=True),
        })
    output = {
        "schema_version": 1,
        "campaign_plan_sha256": digest(plan_path),
        "campaign_summary_sha256": digest(summary_path),
        "method": "paired one-factor-at-a-time steady 3D-ICE sensitivity",
        "evidence_class": "C",
        "tornado": tornado,
        "claim_limit": "one-factor-at-a-time sensitivity is not a joint uncertainty distribution",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

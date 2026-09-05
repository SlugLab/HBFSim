#!/usr/bin/env python3
"""Account for usable fast-tier bytes from a validated checkpoint inventory.

rho describes capacity, not observed cache residency/hit rate. KV allocation
uses explicit sequence/context/dtype inputs; no device capacity is guessed.
The legacy ratio remains a separate historical input and never overrides rho.
"""
import argparse
import json
import math
from pathlib import Path

from inventory_checkpoint import identity, positive, validate_inventory, write_json


def budget_fast_tier(inv, *, fast_bytes, active_sequences, context_tokens,
                     kv_element_bytes, workspace_bytes, safety_bytes, legacy_ratio=None):
    validate_inventory(inv)
    for name, value in (("fast_bytes", fast_bytes), ("active_sequences", active_sequences),
                        ("context_tokens", context_tokens), ("kv_element_bytes", kv_element_bytes)):
        positive(value, name)
    for name, value in (("workspace_bytes", workspace_bytes), ("safety_bytes", safety_bytes)):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative byte count")
    if legacy_ratio is not None and (not math.isfinite(legacy_ratio) or not 0 <= legacy_ratio <= 1):
        raise ValueError("legacy_ratio must lie in [0,1]")
    shape = inv["kv_shape"]
    if context_tokens > shape["maximum_context_tokens"]:
        raise ValueError("requested context exceeds checkpoint metadata")
    kv_bytes = (active_sequences * context_tokens * shape["layers"] * shape["heads_kv"] *
                (shape["key_length"] + shape["value_length"]) * kv_element_bytes)
    resident = inv["resident_non_offloaded_bytes"]
    effective = fast_bytes - resident - kv_bytes - workspace_bytes - safety_bytes
    if effective < 0:
        raise ValueError("fast-tier budget cannot cover resident/KV/workspace/safety allocations")
    pages = effective // inv["page_bytes"]
    page_effective = pages * inv["page_bytes"]
    eligible = positive(inv["eligible_expert_bytes"], "eligible bytes")
    remaining_pages, covered, selected = pages, 0, []
    # A deterministic logical packing budget, not observed cache residency.
    # The last page of one expert cannot silently hold a different expert.
    for expert in sorted(inv["experts"], key=lambda row: (row["layer"], row["expert"])):
        required = expert["packed_logical_pages"]
        if required <= remaining_pages:
            covered += expert["bytes"]
            selected.append([expert["layer"], expert["expert"]])
            remaining_pages -= required
    allocated = (pages - remaining_pages) * inv["page_bytes"]
    return {"schema_version": 1, "inventory_sha256": identity(inv),
            "fast_bytes": fast_bytes, "resident_non_offloaded_bytes": resident,
            "active_sequences": active_sequences, "context_tokens": context_tokens,
            "kv_element_bytes": kv_element_bytes, "kv_bytes": kv_bytes,
            "workspace_bytes": workspace_bytes, "safety_bytes": safety_bytes,
            "C_fast_effective": effective, "page_aligned_effective_bytes": page_effective,
            "fast_pages": pages, "page_bytes": inv["page_bytes"],
            "W_HBF_eligible": eligible, "rho": effective / eligible,
            "rho_requested": effective / eligible,
            "achieved_rho": covered / eligible,
            "rho_achieved": covered / eligible,
            "budget_covered_bytes": covered,
            "budget_fully_covered_experts": len(selected),
            "selected_experts": selected,
            "placement_status": ("BUDGET_FEASIBLE" if selected else
                                 "INFEASIBLE_NO_WHOLE_EXPERT_FITS"),
            "unused_bytes": effective - allocated,
            "packing_padding_bytes": allocated - covered,
            "budget_order": "whole_experts_greedy_layer_then_expert; no partial expert",
            "rho_interpretation": "CAPACITY_BUDGET_NOT_OBSERVED_RESIDENCY",
            "legacy_ratio": legacy_ratio, "cache_validation": "NOT_EXECUTED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    for name in ("fast-bytes", "active-sequences", "context-tokens", "kv-element-bytes",
                 "workspace-bytes", "safety-bytes"):
        parser.add_argument("--" + name, required=True, type=int)
    parser.add_argument("--legacy-ratio", type=float)
    args = parser.parse_args()
    result = budget_fast_tier(json.loads(args.inventory.read_text()), **{
        key: value for key, value in vars(args).items() if key not in ("inventory", "output")})
    write_json(args.output, result)
    print(json.dumps({key: result[key] for key in ("C_fast_effective", "rho", "achieved_rho")}))


if __name__ == "__main__":
    main()

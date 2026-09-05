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

from inventory_checkpoint import identity, positive, write_json
from freeze_storage_split import regular_bytes
from evaluation_inventory import (load_hf_snapshot, validate_evaluation_inventory,
                                  publish_hf_document)
from verify_hf_metadata import canonical, digest, strict_object


def budget_fast_tier(inv, *, fast_bytes, active_sequences, context_tokens,
                     kv_element_bytes, workspace_bytes, safety_bytes, legacy_ratio=None,
                     hf_snapshot=None, inventory_file_bytes=None):
    validate_evaluation_inventory(inv, hf_snapshot=hf_snapshot)
    hf = inv['format'] == 'HF_SAFETENSORS'
    if hf and (type(inventory_file_bytes) is not bytes or
               canonical(strict_object(inventory_file_bytes)) != canonical(inv)):
        raise ValueError('HF budget requires the exact matching inventory file bytes')
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
    result = {"schema_version": 1, "inventory_sha256": identity(inv),
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
    if hf:
        result.update(model_binding=inv['model_binding'],
                      inventory_file_sha256=digest(inventory_file_bytes),
                      source_kind=inv['source_kind'], provenance=inv['provenance'],
                      scientific_validation_passed=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    for name in ("fast-bytes", "active-sequences", "context-tokens", "kv-element-bytes",
                 "workspace-bytes", "safety-bytes"):
        parser.add_argument("--" + name, required=True, type=int)
    parser.add_argument("--legacy-ratio", type=float)
    parser.add_argument("--hf-metadata-refresh", type=Path)
    args = parser.parse_args()
    snapshot = load_hf_snapshot(args.hf_metadata_refresh) if args.hf_metadata_refresh else None
    raw = regular_bytes(args.inventory.absolute())
    inv = strict_object(raw)
    result = budget_fast_tier(inv, hf_snapshot=snapshot, inventory_file_bytes=raw, **{
        key: value for key, value in vars(args).items()
        if key not in ("inventory", "output", "hf_metadata_refresh")})
    if inv['format'] == 'HF_SAFETENSORS':
        if args.output.resolve() == args.inventory.resolve():
            raise ValueError('HF budget cannot replace its inventory input')
        publish_hf_document(args.output, result, hf_snapshot=snapshot)
    else:
        write_json(args.output, result)
    print(json.dumps({key: result[key] for key in ("C_fast_effective", "rho", "achieved_rho")}))


if __name__ == "__main__":
    main()

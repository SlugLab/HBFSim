"""Offline, ledger-derived host manifests for four weight-coverage scopes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

COUNTS = (4, 8, 12, 16)
GLOBAL = ("model.embed_tokens.weight", "model.norm.weight", "lm_head.weight")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def layer_aliases(layer: int) -> tuple[str, ...]:
    root = f"model.layers.{layer}"
    return (
        f"{root}.mlp.experts.w13_weight", f"{root}.mlp.experts.w2_weight",
        f"{root}.input_layernorm.weight", f"{root}.post_attention_layernorm.weight",
        f"{root}.self_attn.q_norm.weight", f"{root}.self_attn.k_norm.weight",
        f"{root}.self_attn.qkv_proj.weight", f"{root}.self_attn.o_proj.weight",
        f"{root}.mlp.gate.weight",
    )


def parse_sources(ledger_bytes: bytes, baseline_bytes: bytes) -> tuple[dict, dict, dict]:
    ledger = json.loads(ledger_bytes)
    baseline = json.loads(baseline_bytes)
    if ledger.get("total_storage_count") != 147 or len(ledger.get("entries", [])) != 147:
        raise ValueError("frozen ledger is not 147 storages")
    entries: dict[str, dict] = {}
    for entry in ledger["entries"]:
        names = entry.get("aliases")
        if not isinstance(names, list) or len(names) != 1 or names[0] in entries:
            raise ValueError("ambiguous or duplicate ledger alias")
        if type(entry.get("storage_bytes")) is not int or entry["storage_bytes"] <= 0:
            raise ValueError("invalid ledger bytes")
        entries[names[0]] = entry
    expected = set(GLOBAL)
    for layer in range(16):
        expected.update(layer_aliases(layer))
    if set(entries) != expected or sum(e["storage_bytes"] for e in entries.values()) != ledger.get("total_storage_bytes"):
        raise ValueError("ledger aliases or byte total changed")
    if baseline.get("schema") != "hbfsim.combined_selected_scope.v1" or baseline.get("scope") != "added49":
        raise ValueError("unexpected frozen selected scope")
    rows = baseline.get("targets")
    if not isinstance(rows, list) or len(rows) != 49:
        raise ValueError("frozen selected scope is not 49 rows")
    row_by_alias = {}
    for row in rows:
        alias = row.get("alias")
        if alias in row_by_alias or alias not in entries or row.get("storage_bytes") != entries[alias]["storage_bytes"]:
            raise ValueError("selected row duplicate or ledger mismatch")
        row_by_alias[alias] = row
    target_names = {"lm_head.weight"}
    for layer in range(16):
        target_names.update((f"model.layers.{layer}.self_attn.qkv_proj.weight",
                             f"model.layers.{layer}.self_attn.o_proj.weight",
                             f"model.layers.{layer}.mlp.gate.weight"))
    if set(row_by_alias) != target_names:
        raise ValueError("frozen target aliases changed")
    return ledger, entries, row_by_alias


def make_scopes(ledger_bytes: bytes, baseline_bytes: bytes) -> list[dict]:
    ledger, entries, row_by_alias = parse_sources(ledger_bytes, baseline_bytes)
    result = []
    for n in COUNTS:
        registered = sorted(set(GLOBAL).union(*(set(layer_aliases(i)) for i in range(n))))
        selected = {"lm_head.weight"}
        for i in range(n):
            selected.update((f"model.layers.{i}.self_attn.qkv_proj.weight",
                             f"model.layers.{i}.self_attn.o_proj.weight",
                             f"model.layers.{i}.mlp.gate.weight"))
        if len(registered) != 9*n+3 or len(selected) != 3*n+1 or not selected.issubset(registered):
            raise ValueError("derived scope count mismatch")
        targets = [row_by_alias[alias] for alias in sorted(selected)]
        patterns = ["^" + re.escape(alias) + "$" for alias in registered]
        if {alias for alias in entries if any(re.fullmatch(p, alias) for p in patterns)} != set(registered):
            raise ValueError("selection regex changed scope")
        result.append({
            "schema": "hbfsim.coverage_sweep_measurement_scope.v1",
            "scope": f"layers-{n}", "layer_count": n,
            "global_aliases": list(GLOBAL), "registration_aliases": registered,
            "registered_count": len(registered),
            "registered_bytes": sum(entries[alias]["storage_bytes"] for alias in registered),
            "model_total_storage_bytes": ledger["total_storage_bytes"],
            "selected_count": len(selected), "targets": targets,
            "include_patterns": patterns, "hbf_weight_selection": "include",
            "source_ledger_sha256": sha(ledger_bytes),
            "source_frozen_scope_sha256": sha(baseline_bytes),
            "evidence_class": "CPU_HOST_ONLY_NOT_GPU_APPROVED",
        })
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--frozen-scope", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    if a.output_dir.exists():
        raise SystemExit("output directory already exists; original receipts must not be overwritten")
    scopes = make_scopes(a.ledger.read_bytes(), a.frozen_scope.read_bytes())
    a.output_dir.mkdir(parents=True)
    receipt = {"schema": "hbfsim.coverage_sweep_host_generation.v1", "scopes": []}
    for scope in scopes:
        path = a.output_dir / f"{scope['scope']}.json"
        raw = (json.dumps(scope, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        path.write_bytes(raw)
        receipt["scopes"].append({"name": scope["scope"], "sha256": sha(raw),
                                  "registered_count": scope["registered_count"],
                                  "registered_bytes": scope["registered_bytes"],
                                  "model_total_storage_bytes": scope["model_total_storage_bytes"],
                                  "selected_count": scope["selected_count"]})
    (a.output_dir / "SCOPES.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()

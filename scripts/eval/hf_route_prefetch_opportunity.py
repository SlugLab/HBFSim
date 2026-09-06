#!/usr/bin/env python3
"""Build a causal route-only prefetch opportunity ledger for frozen HF010.

This CPU-only control contains no service clock, media model, GPU operation, or
prefetch execution. It reports only which whole-expert candidates were knowable
from prior routes and their overlap with a later captured demand.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess

from budget_fast_tier import budget_fast_tier
from evaluation_inventory import (load_hf_snapshot, publish_hf_document,
                                  validate_evaluation_inventory)
from freeze_storage_split import regular_bytes
from verify_hf_metadata import canonical, strict_object


ROOT = Path(__file__).resolve().parents[2]
METADATA_BUNDLE = (
    ROOT / "results/manifests/hf-qwen3-30b-a3b-metadata-20260906-resume001"
).resolve()
RULE = "LATEST_OBSERVED_SAME_LAYER_CURRENT_MEMBERS"
INPUT_NAMES = (
    "inventory", "budget", "consistency", "routing_manifest", "real",
    "shuffled",
)
LIMITS = {
    "inventory": 16 << 20,
    "budget": 1 << 20,
    "consistency": 1 << 20,
    "routing_manifest": 1 << 20,
    "real": 2 << 20,
    "shuffled": 2 << 20,
}


@dataclass(frozen=True)
class ExpectedBindings:
    consistency_sha256: str
    routing_manifest_sha256: str
    legacy_inventory_sha256: str
    capture_route_sha256: str
    capture_route_array_sha256: str
    real_sha256: str
    shuffled_sha256: str
    member_id: str
    decode_steps: int
    prediction_nodes: int
    matched_prediction_nodes: int


HF010 = ExpectedBindings(
    consistency_sha256=(
        "c195d7833eb9ab810256f88cef06fb44bfe498d80c5bf64f7181d3a7f3847b8f"
    ),
    routing_manifest_sha256=(
        "23102e4252daab9162e40ea98eafc39af4cbb28bef2e5912bf5fa6392ba22b9b"
    ),
    legacy_inventory_sha256=(
        "ed64d4c0bd11ee75cb4cd1de4e82ed1b0071fbc4389b8b974e9275382a1e912a"
    ),
    capture_route_sha256=(
        "32e8f4c3cb59a854373de9e478cf695582674ed435eb46883c246a9f591ef0f8"
    ),
    capture_route_array_sha256=(
        "5b4595b95d0adc90eeef2772c7ba83d1c3d5eaec2289482e71d88fccdcd70dd1"
    ),
    real_sha256=(
        "96a29e62d373d8b07f19a8ecbcd4cff8ee5c729a0208f05c4b51204025a3039b"
    ),
    shuffled_sha256=(
        "872308d9e42c2bb978661d78c06e5e427b4b8bde44c96a271b4186b76118a7c2"
    ),
    member_id="hf010-request0",
    decode_steps=7,
    prediction_nodes=289,
    matched_prediction_nodes=288,
)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def require(condition, message: str) -> None:
    if not condition:
        raise ValueError(message)


def source_gate(expected_head: str) -> None:
    require(
        len(expected_head) == 40
        and all(character in "0123456789abcdef" for character in expected_head),
        "expected HEAD must be a full lowercase commit hash",
    )
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C"}

    def git(*arguments):
        return subprocess.run(
            ["git", "-C", str(ROOT), *arguments], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=environment,
        ).stdout

    require(git("rev-parse", "HEAD").strip() == expected_head,
            "HEAD differs from route-only authorization")
    require(git("branch", "--show-current").strip()
            == "eval/eq1-eq4-implementation", "branch differs")
    require(not git("status", "--porcelain=v1", "--untracked-files=no"),
            "tracked worktree or index is dirty")
    require(not git("diff", "--cached", "--name-only"), "index is dirty")


def parse_inputs(snapshots: dict[str, bytes]) -> dict[str, dict]:
    require(set(snapshots) == set(INPUT_NAMES), "unexpected route-only input")
    documents = {}
    for name in INPUT_NAMES:
        payload = snapshots[name]
        require(type(payload) is bytes and len(payload) <= LIMITS[name],
                "route-only input exceeds bound: " + name)
        document = strict_object(payload)
        require("compute_ns" not in _keys(document),
                "route-only control rejects compute timing")
        documents[name] = document
    return documents


def _keys(value):
    if type(value) is dict:
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif type(value) is list:
        for child in value:
            yield from _keys(child)


def validate_consistency(document: dict, inventory: dict,
                         expected: ExpectedBindings) -> None:
    binding = inventory["model_binding"]
    require(
        document.get("schema_version") == 1
        and document.get("status") == "ROUTE_CONSISTENCY_ONLY"
        and document.get("evidence_class") == "UNVALIDATED_ROUTING_CAPTURE"
        and document.get("effective_test_only") is False
        and document.get("scientific_validation_passed") is False,
        "consistency status/attribution mismatch",
    )
    checks = document.get("checks")
    require(type(checks) is dict and checks
            and all(value is True for value in checks.values()),
            "consistency checks are not exact true")
    metadata = document.get("metadata")
    require(
        type(metadata) is dict
        and metadata.get("receipt_sha256") == binding["receipt_sha256"]
        and metadata.get("complete_sha256") == binding["complete_sha256"]
        and metadata.get("donor_sha256") == expected.legacy_inventory_sha256
        and metadata.get("metadata_identity_sha256")
            == binding["metadata_identity_sha256"]
        and metadata.get("observation_identity_sha256")
            == binding["observation_identity_sha256"],
        "consistency metadata/donor join mismatch",
    )
    arms = document.get("arms")
    require(type(arms) is list and len(arms) == 3, "consistency arms mismatch")
    capture = [arm for arm in arms if arm.get("arm") == "capture"]
    require(len(capture) == 1 and type(capture[0].get("artifacts")) is dict,
            "consistency capture arm missing")
    artifacts = capture[0]["artifacts"]
    require(
        artifacts.get("frozen-donor.json") == expected.legacy_inventory_sha256
        and artifacts.get("raw-routes.npy")
            == expected.capture_route_array_sha256
        and artifacts.get("routing.jsonl") == expected.capture_route_sha256,
        "consistency capture artifact join mismatch",
    )


def validate_routing_manifest(document: dict, inventory: dict,
                              expected: ExpectedBindings) -> None:
    require(
        document.get("schema_version") == 1
        and document.get("provenance") == "PROJECTED"
        and document.get("input_source_kind") == "CAPTURED_ROUTE"
        and document.get("capture_validation")
            == "NOT_CERTIFIED_BY_POSTPROCESSOR"
        and document.get("concurrency_kind") == "TRACE_COMPOSED"
        and document.get("composition_seed") == 0
        and document.get("composition_rule")
            == "ALIGN_DECODE_STEP_DROP_FINISHED_SEQUENCES"
        and document.get("inventory_sha256")
            == expected.legacy_inventory_sha256
        and (document.get("E"), document.get("k"), document.get("layers"))
            == (inventory["E"], inventory["k"], inventory["layers"]),
        "routing manifest attribution/dimensions mismatch",
    )
    members = document.get("input_members")
    require(
        type(members) is list and len(members) == 1
        and members[0].get("member_id") == expected.member_id
        and members[0].get("sha256") == expected.capture_route_sha256
        and document.get("member_traces") == [expected.member_id],
        "routing manifest member/capture join mismatch",
    )
    outputs = document.get("outputs")
    require(
        type(outputs) is dict
        and outputs.get("real", {}).get("sha256") == expected.real_sha256
        and outputs.get("shuffled", {}).get("sha256")
            == expected.shuffled_sha256,
        "routing manifest output join mismatch",
    )


def route_nodes(document: dict, series: str, inventory: dict,
                expected: ExpectedBindings) -> dict[tuple[int, int], dict]:
    require(
        type(document) is dict and set(document) == {
            "layers", "provenance", "reuse", "routes", "series", "steps"
        }
        and document["series"] == series
        and document["provenance"] == "PROJECTED",
        "routing output schema/series mismatch",
    )
    rows = document["routes"]
    require(type(rows) is list, "routing rows must be a list")
    nodes = {}
    sources = {layer: set() for layer in range(inventory["layers"])}
    required_keys = {
        "layer_id", "member", "source_token_step", "token_step",
        "topk_expert_ids",
    }
    for row in rows:
        require(type(row) is dict and set(row) == required_keys,
                "routing row schema mismatch")
        step, layer = row["token_step"], row["layer_id"]
        source_step, member = row["source_token_step"], row["member"]
        experts = row["topk_expert_ids"]
        require(
            type(step) is int and 0 <= step < expected.decode_steps
            and type(layer) is int and 0 <= layer < inventory["layers"]
            and type(source_step) is int
            and 0 <= source_step < expected.decode_steps
            and member == expected.member_id
            and type(experts) is list and len(experts) == inventory["k"]
            and len(set(experts)) == len(experts)
            and all(type(expert) is int and 0 <= expert < inventory["E"]
                    for expert in experts)
            and (step, layer) not in nodes,
            "routing row identity/bounds mismatch",
        )
        if series == "real":
            require(source_step == step, "real route source step mismatch")
        nodes[step, layer] = row
        sources[layer].add(source_step)
    wanted = {
        (step, layer) for step in range(expected.decode_steps)
        for layer in range(inventory["layers"])
    }
    require(set(nodes) == wanted, "routing output lacks complete decode grid")
    full_steps = set(range(expected.decode_steps))
    require(all(value == full_steps for value in sources.values()),
            "routing source steps are not a per-layer permutation")
    return nodes


def object_index(inventory: dict) -> dict[tuple[int, int], dict]:
    page = inventory["page_bytes"]
    objects = {}
    for row in inventory["experts"]:
        key = (row["layer"], row["expert"])
        require(key not in objects, "duplicate inventory expert")
        objects[key] = {
            "layer": key[0], "expert": key[1],
            "payload_bytes": row["bytes"],
            "packed_bytes": row["packed_logical_pages"] * page,
        }
    wanted = {
        (layer, expert) for layer in range(inventory["layers"])
        for expert in range(inventory["E"])
    }
    require(set(objects) == wanted, "inventory expert grid incomplete")
    return objects


def total_bytes(keys, objects, field):
    return sum(objects[key][field] for key in keys)


def series_ledger(document: dict, series: str, inventory: dict, budget: dict,
                  expected: ExpectedBindings) -> dict:
    nodes = route_nodes(document, series, inventory, expected)
    objects = object_index(inventory)
    history = {}
    events = []
    overlap_histogram = {}
    overlap_total = 0
    prediction_nodes = matched_nodes = terminal_nodes = 0
    fingerprint = inventory["model_binding"]["historical_model_fingerprint"]
    capacity = budget["page_aligned_effective_bytes"]

    for step, layer in sorted(nodes):
        row = nodes[step, layer]
        member = row["member"]
        history[member, layer] = row
        target_layer = (layer + 1) % inventory["layers"]
        target_step = step if layer + 1 < inventory["layers"] else step + 1
        observed = history.get((member, target_layer))
        current = {(layer, expert) for expert in row["topk_expert_ids"]}
        event = {
            "observation_step": step,
            "observation_layer": layer,
            "target_step": target_step,
            "target_layer": target_layer,
            "historical_model_fingerprint": fingerprint,
            "rule": RULE,
            "current_demand_experts": [objects[key] for key in sorted(current)],
            "prediction_available": observed is not None,
            "prediction_basis": [],
            "candidate_occurrences": 0,
            "candidate_experts": [],
            "candidate_duplicates_suppressed": 0,
            "candidate_payload_bytes": 0,
            "candidate_packed_bytes": 0,
            "captured_target_demand_available": (target_step, target_layer) in nodes,
            "captured_target_demand_experts": [],
            "captured_overlap_experts": [],
            "captured_overlap_payload_bytes": 0,
            "captured_overlap_packed_bytes": 0,
            "capacity_check": {
                "semantics": (
                    "CURRENT_DEMAND_PLUS_CANDIDATE_WHOLE_EXPERT_WORKING_SET_"
                    "ONLY_NO_RESIDENCY_EVICTION_OR_SERVICE_MODEL"
                ),
                "page_aligned_effective_bytes": capacity,
                "working_set_packed_bytes": total_bytes(current, objects,
                                                        "packed_bytes"),
                "fits": total_bytes(current, objects, "packed_bytes")
                        <= capacity,
            },
            "status": "NO_PRIOR_TARGET_LAYER_OBSERVATION",
        }
        if observed is not None:
            prediction_nodes += 1
            occurrences = [
                (target_layer, expert)
                for expert in observed["topk_expert_ids"]
            ]
            candidates = set(occurrences)
            candidate_packed = total_bytes(candidates, objects, "packed_bytes")
            combined = current | candidates
            event.update(
                prediction_basis=[{
                    "member": member,
                    "observed_token_step": observed["token_step"],
                    "observed_source_token_step": observed["source_token_step"],
                    "observed_layer": observed["layer_id"],
                }],
                candidate_occurrences=len(occurrences),
                candidate_experts=[objects[key] for key in sorted(candidates)],
                candidate_duplicates_suppressed=(
                    len(occurrences) - len(candidates)
                ),
                candidate_payload_bytes=total_bytes(candidates, objects,
                                                    "payload_bytes"),
                candidate_packed_bytes=candidate_packed,
            )
            event["capacity_check"].update(
                working_set_packed_bytes=total_bytes(combined, objects,
                                                    "packed_bytes"),
                fits=total_bytes(combined, objects, "packed_bytes") <= capacity,
            )
            target = nodes.get((target_step, target_layer))
            if target is None:
                terminal_nodes += 1
                event["status"] = "END_OF_CAPTURE_HORIZON"
            else:
                matched_nodes += 1
                demand = {
                    (target_layer, expert)
                    for expert in target["topk_expert_ids"]
                }
                overlap = candidates & demand
                overlap_total += len(overlap)
                overlap_histogram[str(len(overlap))] = (
                    overlap_histogram.get(str(len(overlap)), 0) + 1
                )
                event.update(
                    captured_target_demand_experts=[
                        objects[key] for key in sorted(demand)
                    ],
                    captured_overlap_experts=[
                        objects[key] for key in sorted(overlap)
                    ],
                    captured_overlap_payload_bytes=total_bytes(
                        overlap, objects, "payload_bytes"
                    ),
                    captured_overlap_packed_bytes=total_bytes(
                        overlap, objects, "packed_bytes"
                    ),
                    status="MATCHED_CAPTURED_DEMAND",
                )
        events.append(event)

    require(prediction_nodes == expected.prediction_nodes
            and matched_nodes == expected.matched_prediction_nodes
            and terminal_nodes == 1,
            "causal prediction horizon count mismatch")
    return {
        "series": series,
        "demand_nodes": len(nodes),
        "nodes_without_prediction": len(nodes) - prediction_nodes,
        "prediction_bearing_nodes": prediction_nodes,
        "matched_prediction_nodes": matched_nodes,
        "end_of_horizon_candidate_nodes": terminal_nodes,
        "captured_overlap_expert_occurrences": overlap_total,
        "captured_overlap_experts_per_matched_node": overlap_histogram,
        "events": events,
    }


def build_ledger(snapshots: dict[str, bytes], *, hf_snapshot,
                 expected: ExpectedBindings = HF010) -> dict:
    documents = parse_inputs(snapshots)
    inventory = documents["inventory"]
    require(inventory.get("schema_version") == 2
            and inventory.get("format") == "HF_SAFETENSORS"
            and inventory.get("scientific_validation_passed") is False,
            "schema-2 HF inventory required")
    validate_evaluation_inventory(inventory, hf_snapshot=hf_snapshot)
    binding = inventory["model_binding"]
    require(binding.get("legacy_inventory_sha256")
            == expected.legacy_inventory_sha256,
            "HF inventory does not bind the HF010 donor")

    budget = documents["budget"]
    arguments = {
        key: budget[key] for key in (
            "fast_bytes", "active_sequences", "context_tokens",
            "kv_element_bytes", "workspace_bytes", "safety_bytes",
            "legacy_ratio",
        )
    }
    recomputed = budget_fast_tier(
        inventory, hf_snapshot=hf_snapshot,
        inventory_file_bytes=snapshots["inventory"], **arguments,
    )
    require(canonical(recomputed) == canonical(budget),
            "capacity budget differs from schema-2 inventory derivation")
    require(budget["model_binding"] == binding
            and budget["inventory_file_sha256"]
                == digest(snapshots["inventory"]),
            "capacity budget inventory/model binding mismatch")
    require(budget["C_fast_effective"] * 16
            == inventory["eligible_expert_bytes"]
            and budget["rho_interpretation"]
                == "CAPACITY_BUDGET_NOT_OBSERVED_RESIDENCY",
            "first route-only pilot requires exact rho=1/16 capacity")

    require(digest(snapshots["consistency"])
            == expected.consistency_sha256,
            "unexpected consistency report bytes")
    require(digest(snapshots["routing_manifest"])
            == expected.routing_manifest_sha256,
            "unexpected routing manifest bytes")
    require(digest(snapshots["real"]) == expected.real_sha256
            and digest(snapshots["shuffled"]) == expected.shuffled_sha256,
            "unexpected routing output bytes")
    validate_consistency(documents["consistency"], inventory, expected)
    validate_routing_manifest(documents["routing_manifest"], inventory,
                              expected)

    series = {
        name: series_ledger(documents[name], name, inventory, budget, expected)
        for name in ("real", "shuffled")
    }
    return {
        "schema_version": 1,
        "status": "PROJECTED_ROUTE_ONLY_CAUSAL_CONTROL",
        "resource_class": "CPU_ONLY",
        "provenance": "PROJECTED",
        "concurrency_kind": "TRACE_COMPOSED",
        "capture_validation": "NOT_CERTIFIED_BY_POSTPROCESSOR",
        "scientific_validation_passed": False,
        "composition_seed": 0,
        "prediction_rule": RULE,
        "capacity_ratio": "1/16",
        "timing": "ABSENT_NO_COMPUTE_OR_MEDIA_CLOCK",
        "model_binding": binding,
        "inputs": {
            name: {"sha256": digest(snapshots[name]),
                   "bytes": len(snapshots[name])}
            for name in INPUT_NAMES
        },
        "budget": {
            key: budget[key] for key in (
                "inventory_file_sha256", "C_fast_effective",
                "page_aligned_effective_bytes", "W_HBF_eligible",
                "rho_requested", "achieved_rho", "placement_status",
                "budget_fully_covered_experts",
            )
        },
        "series": series,
        "limitations": [
            "Candidate overlap is a route-set observation, not a cache hit.",
            (
                "Capacity checks contain no residency, eviction, service, "
                "or allocator model."
            ),
            "No compute interval, media timing, queue timing, or speedup is measured.",
            (
                "The final candidate has no later captured demand and is "
                "not classified useful."
            ),
        ],
    }


def run(args) -> dict:
    source_gate(args.expected_head)
    require(args.metadata_refresh.resolve() == METADATA_BUNDLE,
            "route-only pilot requires the published HF010 metadata snapshot")
    paths = {name: getattr(args, name).absolute() for name in INPUT_NAMES}
    snapshots = {name: regular_bytes(path) for name, path in paths.items()}
    hf_snapshot = load_hf_snapshot(args.metadata_refresh)
    result = build_ledger(snapshots, hf_snapshot=hf_snapshot)
    for name, path in paths.items():
        require(regular_bytes(path) == snapshots[name],
                "route-only input changed during derivation: " + name)
    source_gate(args.expected_head)
    output = args.output.absolute()
    require(output.resolve().is_relative_to(
        (ROOT / "results/gold/hf-routing-runner").resolve()),
        "route-only output must remain under HF routing gold root",
    )
    publish_hf_document(output, result, hf_snapshot=hf_snapshot)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-refresh", type=Path, required=True)
    for name in INPUT_NAMES:
        parser.add_argument("--" + name.replace("_", "-"), type=Path,
                            required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()
    try:
        result = run(args)
    except (OSError, ValueError, KeyError, TypeError,
            subprocess.SubprocessError) as error:
        parser.exit(2, "hf_route_prefetch_opportunity: " + str(error) + "\n")
    print(json.dumps({
        "status": result["status"],
        "output": str(args.output),
        "prediction_nodes": result["series"]["real"][
            "prediction_bearing_nodes"
        ],
        "matched_prediction_nodes": result["series"]["real"][
            "matched_prediction_nodes"
        ],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Join immutable native-PTX collection provenance to a completed PTX stage."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
from typing import Any


SCHEMA = "hbfsim.native_ptx_provenance_scoped.v1"
SNAPSHOT_FIELDS = ("path", "size_bytes", "sha256", "device", "inode", "mtime_ns")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_object(path: pathlib.Path) -> tuple[dict[str, Any], bytes]:
    payload = path.read_bytes()
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value, payload


def read_jsonl(path: pathlib.Path) -> tuple[list[dict[str, Any]], bytes]:
    payload = path.read_bytes()
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(payload.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"pass manifest line {number} is not an object")
        rows.append(value)
    return rows, payload


def equal_snapshot(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(left.get(field) == right.get(field) for field in SNAPSHOT_FIELDS)


def verify_completion(staging_dir: pathlib.Path, pass_manifest: pathlib.Path) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes, bytes]:
    staging_dir = staging_dir.resolve(strict=True)
    pass_manifest = pass_manifest.resolve(strict=True)
    marker_path = staging_dir / "COMPLETE.json"
    manifest_path = staging_dir / "ptx-staging-manifest.json"
    marker, marker_payload = load_object(marker_path)
    manifest, manifest_payload = load_object(manifest_path)
    pass_payload = pass_manifest.read_bytes()
    if marker.get("manifest_sha256") != digest(manifest_payload):
        raise ValueError("staging manifest hash mismatch")
    if pathlib.Path(str(marker.get("pass_manifest_path", ""))).resolve() != pass_manifest:
        raise ValueError("pass manifest path mismatch")
    if marker.get("pass_manifest_sha256") != digest(pass_payload):
        raise ValueError("pass manifest hash mismatch")
    if marker.get("status") != manifest.get("status"):
        raise ValueError("completion status mismatch")
    artifact_hashes = marker.get("artifact_sha256")
    if not isinstance(artifact_hashes, dict):
        raise ValueError("completion marker has no artifact hashes")
    for raw_path, expected in artifact_hashes.items():
        path = pathlib.Path(raw_path)
        if not path.is_file() or digest(path.read_bytes()) != expected:
            raise ValueError(f"staged artifact hash mismatch: {path}")
    artifact_by_resolved_path: dict[pathlib.Path, str] = {}
    for raw_path, expected in artifact_hashes.items():
        resolved = pathlib.Path(raw_path).resolve()
        if resolved in artifact_by_resolved_path:
            raise ValueError(f"duplicate resolved artifact path: {resolved}")
        artifact_by_resolved_path[resolved] = expected
    for variant in manifest.get("variants", []):
        if not variant.get("staged_path"):
            continue
        staged_path = pathlib.Path(variant["staged_path"]).resolve()
        marker_sha = artifact_by_resolved_path.get(staged_path)
        variant_sha = variant.get("staged_sha256")
        if marker_sha is None:
            raise ValueError(f"variant staged path absent from COMPLETE artifacts: {staged_path}")
        actual_sha = digest(staged_path.read_bytes())
        if not isinstance(variant_sha, str) or variant_sha != marker_sha or variant_sha != actual_sha:
            raise ValueError(f"variant staged SHA mismatch: {staged_path}")
    expected_ptx = {
        pathlib.Path(variant["staged_path"]).resolve()
        for variant in manifest.get("variants", []) if variant.get("staged_path")
    }
    actual_ptx = {path.resolve() for path in staging_dir.glob("*.ptx")}
    if actual_ptx != expected_ptx:
        raise ValueError("staged PTX file set mismatch")
    return manifest, marker, manifest_payload, marker_payload, pass_payload


def verify_collector(manifest: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    if manifest.get("schema") != "hbfsim.native_ptx_collection.v2":
        raise ValueError("collector manifest is not v2")
    if manifest.get("collector_status") != "READY":
        raise ValueError("collector manifest is not READY")
    before = manifest.get("container")
    after_list = manifest.get("container_after_listing")
    after_extract = manifest.get("container_after_extraction")
    if not all(isinstance(value, dict) for value in (before, after_list, after_extract)):
        raise ValueError("collector manifest lacks complete container snapshots")
    if not equal_snapshot(before, after_list) or not equal_snapshot(before, after_extract):
        raise ValueError("collector container snapshots are unstable")
    members_by_id: dict[str, dict[str, Any]] = {}
    for member in manifest.get("members", []):
        member_id = str(member.get("member_id", ""))
        if not member_id or member_id in members_by_id:
            raise ValueError("missing or duplicate collector member_id")
        path = pathlib.Path(str(member.get("saved_path", "")))
        payload = path.read_bytes()
        if digest(payload) != member.get("member_sha256") or len(payload) != member.get("size_bytes"):
            raise ValueError(f"collector member bytes mismatch: {member_id}")
        if member.get("status") not in {"READY", "READY_NO_ENTRIES"}:
            raise ValueError(f"collector member is not parse-ready: {member_id}")
        members_by_id[member_id] = member
    if not members_by_id:
        raise ValueError("READY collector has no members")

    derived: dict[str, list[dict[str, Any]]] = {}
    container_sha = before.get("sha256")
    for member_id, member in members_by_id.items():
        for entry in member.get("entries", []):
            derived.setdefault(entry, []).append({
                "member_id": member_id,
                "member_sha256": member["member_sha256"],
                "container_sha256": container_sha,
                "ptx_version": member.get("ptx_version"),
                "target": member.get("target"),
                "target_directive": member.get("target_directive"),
            })
    recorded = manifest.get("entry_memberships")
    if not isinstance(recorded, dict) or recorded != derived:
        raise ValueError("collector entry_memberships do not exactly match members")
    return members_by_id, derived


def ptx_entries(payload: bytes) -> list[str]:
    text = payload.decode("utf-8")
    return re.findall(r"(?m)^\s*(?:\.visible\s+)?\.entry\s+([^\s(]+)\s*\(", text)


def build_join(collector_path: pathlib.Path, staging_dir: pathlib.Path,
               pass_manifest: pathlib.Path,
               selected_entries: list[str]) -> dict[str, Any]:
    if not selected_entries or any(not isinstance(value, str) or not value
                                   for value in selected_entries):
        raise ValueError("selected entry set is empty or invalid")
    if len(selected_entries) != len(set(selected_entries)):
        raise ValueError("duplicate selected entry")
    selected_set = set(selected_entries)

    collector_path = collector_path.resolve(strict=True)
    collector, collector_payload = load_object(collector_path)
    members_by_id, entry_memberships = verify_collector(collector)
    stage, marker, stage_payload, marker_payload, pass_payload = verify_completion(
        staging_dir, pass_manifest
    )
    if stage.get("policy") not in {"strict", "partial"}:
        raise ValueError("unknown staging policy")
    if stage.get("status") not in {"READY", "PARTIAL_READY"}:
        raise ValueError("staging is not complete enough for scoped join")
    variants = stage.get("variants")
    if not isinstance(variants, list):
        raise ValueError("staging variants is not a list")

    # Entry names alone never choose among members.  Each explicit selection
    # must already have exactly one collector membership.
    selected_memberships: dict[str, dict[str, Any]] = {}
    selected_member_ids: set[str] = set()
    for entry in selected_entries:
        memberships = entry_memberships.get(entry, [])
        if len(memberships) != 1:
            raise ValueError(
                f"selected entry does not have unique collector membership: {entry}"
            )
        selected_memberships[entry] = memberships[0]
        selected_member_ids.add(str(memberships[0]["member_id"]))

    selected_members = {member_id: members_by_id[member_id]
                        for member_id in selected_member_ids}
    selected_sha_to_member: dict[str, dict[str, Any]] = {}
    for member in selected_members.values():
        raw_sha = str(member["member_sha256"])
        previous = selected_sha_to_member.get(raw_sha)
        if previous is not None and previous["member_id"] != member["member_id"]:
            raise ValueError("selected raw SHA maps to multiple collector members")
        selected_sha_to_member[raw_sha] = member

    stage_by_sha: dict[str, dict[str, Any]] = {}
    for variant in variants:
        raw_sha = str(variant.get("raw_sha256", ""))
        if not raw_sha or raw_sha in stage_by_sha:
            raise ValueError("missing or duplicate staging raw_sha256")
        if variant.get("module_id") != f"ptx:sha256:{raw_sha}":
            raise ValueError("staging module_id/raw_sha mismatch")
        if variant.get("status") not in {"READY", "PARTIAL_READY"}:
            raise ValueError("selected staging variant is not ready")
        stage_by_sha[raw_sha] = variant
    if set(stage_by_sha) != set(selected_sha_to_member):
        raise ValueError("staging raw SHA set is not exactly the explicitly selected members")

    expected_pairs: set[tuple[str, str]] = set()
    module_joins: list[dict[str, Any]] = []
    for raw_sha in sorted(stage_by_sha):
        variant = stage_by_sha[raw_sha]
        member = selected_sha_to_member[raw_sha]
        member_entries = member.get("entries", [])
        if len(member_entries) != len(set(member_entries)):
            raise ValueError("collector member has duplicate entry inventory")
        if variant.get("kernel_entries") != member_entries:
            raise ValueError("collector/full staging entry inventory differs")
        selected_for_member = [entry for entry in selected_entries
                               if selected_memberships[entry]["member_id"] == member["member_id"]]
        if variant.get("selected_entries") != selected_for_member:
            raise ValueError("staging selected entries differ from explicit selection")
        if variant.get("raw_bytes") != member.get("size_bytes"):
            raise ValueError("collector/staging raw byte size differs")
        sources = variant.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("staging variant has no raw source")
        for source_value in sources:
            source = pathlib.Path(str(source_value)).resolve(strict=True)
            source_payload = source.read_bytes()
            if digest(source_payload) != raw_sha or len(source_payload) != member["size_bytes"]:
                raise ValueError(f"staging source is not the complete raw member: {source}")
        staged_path = pathlib.Path(str(variant.get("staged_path", ""))).resolve(strict=True)
        staged_inventory = ptx_entries(staged_path.read_bytes())
        if staged_inventory != member_entries:
            raise ValueError("staged PTX does not preserve the full member entry inventory")
        results = variant.get("entry_results")
        if not isinstance(results, list):
            raise ValueError("staging entry_results is not a list")
        result_entries = [row.get("kernel") for row in results]
        if result_entries != selected_for_member or any(
            row.get("status") != "SUPPORTED_TRANSFORMED" for row in results
        ):
            raise ValueError("selected staging results are not exactly transformed")
        module_id = variant["module_id"]
        expected_pairs.update((module_id, entry) for entry in selected_for_member)
        module_joins.append({
            "module_id": module_id,
            "raw_sha256": raw_sha,
            "staged_path": str(staged_path),
            "staged_sha256": variant.get("staged_sha256"),
            "member": {
                "member_id": member["member_id"],
                "listed_index": member.get("listed_index"),
                "listed_name": member.get("listed_name"),
                "container_sha256": collector["container"]["sha256"],
                "member_sha256": member["member_sha256"],
                "size_bytes": member["size_bytes"],
                "ptx_version": member.get("ptx_version"),
                "target": member.get("target"),
                "target_directive": member.get("target_directive"),
                "full_entry_inventory": member_entries,
            },
            "selected_entries": selected_for_member,
            "unselected_entries": [entry for entry in member_entries
                                   if entry not in selected_set],
        })

    pass_rows, _ = read_jsonl(pass_manifest)
    pass_pairs = [(row.get("module_id"), row.get("kernel")) for row in pass_rows]
    if len(pass_pairs) != len(set(pass_pairs)):
        raise ValueError("duplicate module/kernel pair in pass manifest")
    if set(pass_pairs) != expected_pairs:
        raise ValueError("pass manifest does not exactly cover the explicit entry subset")
    for row in pass_rows:
        if not row.get("instrumented") or int(row.get("rewritten_instructions", 0)) <= 0:
            raise ValueError("selected pass row is not instrumented")
        if row.get("unsupported_parameters") or int(row.get("unsupported_instructions", 0)):
            raise ValueError("selected pass row has unsupported coverage")

    scoped_entries = {
        entry: [{
            **selected_memberships[entry],
            "module_id": f"ptx:sha256:{selected_memberships[entry]['member_sha256']}",
        }]
        for entry in selected_entries
    }
    return {
        "schema": SCHEMA,
        "status": "SCOPED_READY",
        "scope": "explicit_entry_subset",
        "whole_module_ready": False,
        "selection_contract": {
            "explicit_selected_entries": selected_entries,
            "automatic_member_selection": False,
            "unique_collector_membership_required": True,
            "unselected_entry_policy": "STRICT_REJECT",
            "dynamic_binding_proven": False,
        },
        "collector": {
            "manifest_path": str(collector_path),
            "manifest_sha256": digest(collector_payload),
            "collector_source": collector.get("collector"),
            "container": collector["container"],
        },
        "staging": {
            "directory": str(staging_dir.resolve()),
            "manifest_sha256": digest(stage_payload),
            "complete_sha256": digest(marker_payload),
            "pass_manifest_path": str(pass_manifest.resolve()),
            "pass_manifest_sha256": digest(pass_payload),
            "status": stage["status"],
            "policy": stage["policy"],
        },
        "module_joins": module_joins,
        "selected_entry_memberships": scoped_entries,
    }

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collector-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--staging-dir", required=True, type=pathlib.Path)
    parser.add_argument("--pass-manifest", required=True, type=pathlib.Path)
    parser.add_argument("--selected-entry", action="append", default=[])
    parser.add_argument("--selected-entries-json", type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    if args.output.exists():
        print(json.dumps({"status": "OUTPUT_EXISTS", "output": str(args.output.resolve())}))
        return 2
    try:
        selected_entries = list(args.selected_entry)
        if args.selected_entries_json is not None:
            if selected_entries:
                raise ValueError("use either --selected-entry or --selected-entries-json")
            value = json.loads(args.selected_entries_json.read_text(encoding="utf-8"))
            if not isinstance(value, list):
                raise ValueError("selected entries JSON root is not a list")
            selected_entries = value
        joined = build_join(args.collector_manifest, args.staging_dir,
                            args.pass_manifest, selected_entries)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(joined, indent=2, sort_keys=True) + "\n")
    except Exception as exc:
        print(json.dumps({"status": "REJECTED", "reason": f"{type(exc).__name__}:{exc}"}))
        return 1
    print(json.dumps({"status": "SCOPED_READY", "output": str(args.output.resolve()),
                      "sha256": digest(args.output.read_bytes())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

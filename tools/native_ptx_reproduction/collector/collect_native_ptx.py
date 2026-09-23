#!/usr/bin/env python3
"""Extract original embedded PTX members from a native container.

This collector deliberately uses cuobjdump's extraction interface.  The human
oriented ``--dump-ptx`` output is never accepted as PTX identity evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import tempfile
from typing import Any


LIST_LINE = re.compile(r"^PTX file\s+([0-9]+):\s+(.+?)\s*$")
LINE_COMMENT = re.compile(rb"//[^\r\n]*")
BLOCK_COMMENT = re.compile(rb"/\*.*?\*/", re.DOTALL)
ENTRY = re.compile(rb"(?:\.visible\s+)?\.entry\s+([.$A-Za-z_][.$A-Za-z0-9_]*)\s*\(")
VERSION = re.compile(rb"(?m)^\s*\.version\s+([0-9]+(?:\.[0-9]+)?)\s*$")
TARGET = re.compile(rb"(?m)^\s*\.target\s+([^\r\n]+?)\s*$")
SCHEMA = "hbfsim.native_ptx_collection.v2"
NO_PTX_DIAGNOSTIC = re.compile(
    r"(?:does not contain device code|No PTX file found(?: to extract)?)",
    re.IGNORECASE,
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_record(path: pathlib.Path) -> dict[str, Any]:
    data = path.read_bytes()
    stat = path.stat()
    return {"path": str(path.resolve()), "size_bytes": len(data), "sha256": sha256(data),
            "device": stat.st_dev, "inode": stat.st_ino, "mtime_ns": stat.st_mtime_ns}


def same_snapshot(left: dict[str, Any], right: dict[str, Any]) -> bool:
    fields = ("path", "size_bytes", "sha256", "device", "inode", "mtime_ns")
    return all(left.get(field) == right.get(field) for field in fields)


def run(argv: list[str], cwd: pathlib.Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, check=False)
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "stdout": completed.stdout.decode("utf-8", errors="replace"),
        "stderr": completed.stderr.decode("utf-8", errors="replace"),
        "stdout_sha256": sha256(completed.stdout),
        "stderr_sha256": sha256(completed.stderr),
    }


def parse_listing(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    members: list[dict[str, Any]] = []
    unparsed: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = LIST_LINE.match(line)
        if match:
            members.append({"listed_index": int(match.group(1)), "listed_name": match.group(2)})
        elif not line.startswith("cuobjdump info"):
            unparsed.append(line)
    return members, unparsed


def is_explicit_no_ptx(listing: dict[str, Any], listed: list[dict[str, Any]],
                       unparsed: list[str]) -> bool:
    """Recognize cuobjdump's documented semantic no-device-code response.

    CUDA 13 returns 255 for a valid host ELF with no device code, so return code
    alone cannot distinguish NO_PTX from an invocation/tool failure.
    """
    diagnostic = str(listing.get("stdout", "")) + "\n" + str(listing.get("stderr", ""))
    return not listed and not unparsed and bool(NO_PTX_DIAGNOSTIC.search(diagnostic))


def parse_member(data: bytes) -> dict[str, Any]:
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return {"status": "PARSE_FAILURE", "reason": f"non_utf8_ptx:{exc}"}
    clean = LINE_COMMENT.sub(b"", BLOCK_COMMENT.sub(b"", data))
    versions = [m.group(1).decode("ascii") for m in VERSION.finditer(clean)]
    targets = [m.group(1).decode("ascii").strip() for m in TARGET.finditer(clean)]
    entries = [m.group(1).decode("utf-8") for m in ENTRY.finditer(clean)]
    if len(versions) != 1:
        return {"status": "PARSE_FAILURE", "reason": "expected_exactly_one_version",
                "versions_found": versions, "targets_found": targets, "entries": entries}
    if len(targets) != 1:
        return {"status": "PARSE_FAILURE", "reason": "expected_exactly_one_target",
                "ptx_version": versions[0], "targets_found": targets, "entries": entries}
    if len(entries) != len(set(entries)):
        duplicates = sorted({name for name in entries if entries.count(name) > 1})
        return {"status": "PARSE_FAILURE", "reason": "duplicate_entry_in_member",
                "ptx_version": versions[0], "target_directive": targets[0],
                "entries": entries, "duplicate_entries": duplicates}
    target_terms = [part.strip() for part in targets[0].split(",") if part.strip()]
    return {
        "status": "READY" if entries else "READY_NO_ENTRIES",
        "ptx_version": versions[0],
        "target": target_terms[0] if target_terms else None,
        "target_features": target_terms[1:],
        "target_directive": targets[0],
        "entries": entries,
    }


def collect(container: pathlib.Path, tool: pathlib.Path, output: pathlib.Path) -> dict[str, Any]:
    container = container.resolve(strict=True)
    tool = tool.resolve(strict=True)
    output = output.resolve()
    # Evidence directories are immutable attempts.  Never replace a prior
    # manifest, failure, or raw extracted member.
    output.mkdir(parents=True, exist_ok=False)
    container_before = file_record(container)
    tool_info = run([str(tool), "--version"])
    listing = run([str(tool), "-lptx", str(container)])
    listed, unparsed = parse_listing(listing["stdout"] + "\n" + listing["stderr"])
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "collector_status": "TOOL_FAILURE",
        "identity_contract": {
            "rule": "dynamic binding must join actual loaded container and extracted member identity; entry names are not identities",
            "automatic_entry_selection": False,
        },
        "collector": file_record(pathlib.Path(__file__).resolve()),
        "container": container_before,
        "tool": {**file_record(tool), "version_command": tool_info},
        "list_command": listing,
        "listed_members": listed,
        "unparsed_listing_lines": unparsed,
        "members": [],
        "entry_memberships": {},
    }
    indexes = [item["listed_index"] for item in listed]
    names = [item["listed_name"] for item in listed]
    if len(indexes) != len(set(indexes)) or len(names) != len(set(names)):
        result["collector_status"] = "PARSE_FAILURE"
        result["failure_reason"] = "duplicate_listing_index_or_name"
        return result
    container_after_listing = file_record(container)
    result["container_after_listing"] = container_after_listing
    if not same_snapshot(container_before, container_after_listing):
        result["collector_status"] = "CONTAINER_CHANGED"
        result["failure_reason"] = "container_changed_during_listing"
        return result
    if tool_info["returncode"] != 0:
        result["failure_reason"] = "cuobjdump_version_or_list_failed"
        return result
    if is_explicit_no_ptx(listing, listed, unparsed):
        result["collector_status"] = "NO_PTX"
        result["failure_reason"] = None
        result["no_ptx_evidence"] = "cuobjdump_explicit_no_device_code_diagnostic"
        return result
    if listing["returncode"] != 0:
        result["failure_reason"] = "cuobjdump_list_failed"
        return result
    if unparsed:
        result["collector_status"] = "PARSE_FAILURE"
        result["failure_reason"] = "unparsed_cuobjdump_listing"
        return result
    if not listed:
        result["collector_status"] = "NO_PTX"
        result["failure_reason"] = None
        return result

    members_dir = output / "members"
    members_dir.mkdir()
    with tempfile.TemporaryDirectory(prefix="native-ptx-extract-") as tmp_name:
        tmp = pathlib.Path(tmp_name)
        extraction = run([str(tool), "-xptx", "all", str(container)], cwd=tmp)
        result["extract_command"] = extraction
        container_after_extraction = file_record(container)
        result["container_after_extraction"] = container_after_extraction
        if not same_snapshot(container_before, container_after_extraction):
            result["collector_status"] = "CONTAINER_CHANGED"
            result["failure_reason"] = "container_changed_during_extraction"
            return result
        if extraction["returncode"] != 0:
            result["failure_reason"] = "cuobjdump_extract_failed"
            return result
        extracted = sorted(path for path in tmp.iterdir() if path.is_file())
        by_name = {path.name: path for path in extracted}
        listed_names = [str(item["listed_name"]) for item in listed]
        if len(by_name) != len(extracted) or set(by_name) != set(listed_names):
            result["collector_status"] = "PARSE_FAILURE"
            result["failure_reason"] = "listed_and_extracted_member_sets_differ"
            result["extracted_names"] = sorted(by_name)
            return result
        first_content: dict[str, str] = {}
        parse_failed = False
        for ordinal, listed_member in enumerate(listed, 1):
            name = str(listed_member["listed_name"])
            raw = by_name[name].read_bytes()
            raw_sha = sha256(raw)
            member_id = f"member-{ordinal:04d}"
            safe_name = f"{member_id}-{raw_sha}.ptx"
            destination = members_dir / safe_name
            destination.write_bytes(raw)
            parsed = parse_member(raw)
            duplicate_of = first_content.get(raw_sha)
            if duplicate_of is None:
                first_content[raw_sha] = member_id
            member = {
                "member_id": member_id,
                "listed_index": listed_member["listed_index"],
                "listed_name": name,
                "member_sha256": raw_sha,
                "size_bytes": len(raw),
                "saved_path": str(destination),
                "duplicate_of_member_id": duplicate_of,
                **parsed,
            }
            result["members"].append(member)
            if parsed["status"] == "PARSE_FAILURE":
                parse_failed = True
            for entry in parsed.get("entries", []):
                result["entry_memberships"].setdefault(entry, []).append({
                    "member_id": member_id,
                    "member_sha256": raw_sha,
                    "container_sha256": result["container"]["sha256"],
                    "ptx_version": parsed.get("ptx_version"),
                    "target": parsed.get("target"),
                    "target_directive": parsed.get("target_directive"),
                })
        result["unique_member_content_count"] = len(first_content)
        result["collector_status"] = "PARSE_FAILURE" if parse_failed else "READY"
        result["failure_reason"] = "one_or_more_members_failed_parse" if parse_failed else None
    return result


def execute(args: argparse.Namespace) -> int:
    manifest = args.output.resolve() / "native-ptx-manifest.json"
    if args.output.resolve().exists():
        print(json.dumps({"status": "OUTPUT_EXISTS", "output": str(args.output.resolve())}))
        return 2
    try:
        result = collect(args.container, args.cuobjdump, args.output)
    except FileExistsError:
        # Another process won the atomic attempt-directory mkdir.  It owns all
        # bytes there; do not add or replace even an error manifest.
        print(json.dumps({"status": "OUTPUT_EXISTS", "output": str(args.output.resolve())}))
        return 2
    except Exception as exc:  # preserve a machine-readable collector failure
        result = {"schema": SCHEMA, "collector_status": "TOOL_FAILURE",
                  "failure_reason": f"collector_exception:{type(exc).__name__}:{exc}"}
    manifest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with manifest.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    except FileExistsError:
        print(json.dumps({"status": "OUTPUT_EVIDENCE_EXISTS", "manifest": str(manifest)}))
        return 3
    print(json.dumps({"status": result["collector_status"], "manifest": str(manifest)}))
    return 0 if result["collector_status"] in {"READY", "NO_PTX"} else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True, type=pathlib.Path)
    parser.add_argument("--cuobjdump", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return execute(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

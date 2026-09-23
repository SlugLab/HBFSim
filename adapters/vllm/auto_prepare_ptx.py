#!/usr/bin/env python3
"""Discover and stage every supported PTX entry in a module.

Each original PTX module is transformed in one isolated child process.  The
child loads the pass once and feeds each successive output back into the same
CDLL, preserving the pass' trusted-module registry across all entries.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from typing import Any


ENTRY_PATTERN = re.compile(
    rb"(?:\.visible\s+)?\.entry\s+([.$A-Za-z_][.$A-Za-z0-9_]*)\s*\("
)
FUNC_PATTERN = re.compile(
    rb"(?:\.visible\s+)?\.func(?:\s+\([^)]*\))?\s+"
    rb"([.$A-Za-z_][.$A-Za-z0-9_]*)[^\{]*\{"
)
LINE_COMMENT = re.compile(rb"//[^\r\n]*")
BLOCK_COMMENT = re.compile(rb"/\*.*?\*/", re.DOTALL)
INSTRUCTION_PATTERN = re.compile(
    rb"(?m)^\s*(?:@[!A-Za-z0-9_.$%-]+\s+)?([A-Za-z][A-Za-z0-9_.]*)"
)
CALL_PATTERN = re.compile(rb"\bcall(?:\.uni)?\b")
IDENTITY_PATTERN = re.compile(
    rb"\.visible\s+\.const\s+\.align\s+8\s+\.b8\s+"
    rb"__hbfsim_module_identity\s*\[32\]"
)
ZERO_IDENTITY = "ptx:sha256:" + "0" * 64
OUTPUT_SIZES = (1 << 20, 4 << 20, 16 << 20, 64 << 20)
ENTRY_STATUSES = {"SUPPORTED_TRANSFORMED", "UNSUPPORTED", "UNRESOLVED"}
NON_HBF_UNSUPPORTED = re.compile(r"^(?:ld|st)\.(?:param|local|shared|const)(?:\.|$)")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strip_comments(payload: bytes) -> bytes:
    return LINE_COMMENT.sub(b"", BLOCK_COMMENT.sub(b"", payload))


def _entry_inventory(payload: bytes) -> tuple[tuple[str, ...], tuple[str, ...]]:
    seen: set[str] = set()
    ordered: list[str] = []
    duplicates: set[str] = set()
    all_names = [
        match.group(1).decode("utf-8", errors="strict")
        for match in ENTRY_PATTERN.finditer(_strip_comments(payload))
    ]
    for name in all_names:
        if name in seen:
            duplicates.add(name)
        else:
            seen.add(name)
            ordered.append(name)
    return tuple(ordered), tuple(sorted(duplicates))


def _entry_names(payload: bytes) -> tuple[str, ...]:
    entries, duplicates = _entry_inventory(payload)
    if duplicates:
        raise ValueError(f"duplicate PTX entries: {', '.join(duplicates)}")
    return entries


def _matching_entries(entries: Sequence[str], patterns: Sequence[str]) -> tuple[str, ...]:
    if not patterns:
        return tuple(entries)
    return tuple(
        entry for entry in entries
        if any(fnmatch.fnmatchcase(entry, pattern) for pattern in patterns)
    )


def _function_bodies(payload: bytes) -> Iterable[tuple[str, bytes]]:
    """Yield approximate PTX .func bodies; fail closed on malformed braces."""
    payload = _strip_comments(payload)
    for match in FUNC_PATTERN.finditer(payload):
        depth = 1
        cursor = match.end()
        while cursor < len(payload) and depth:
            byte = payload[cursor]
            if byte == ord("{"):
                depth += 1
            elif byte == ord("}"):
                depth -= 1
            cursor += 1
        name = match.group(1).decode("utf-8", errors="strict")
        if depth:
            yield name, payload[match.end():]
        else:
            yield name, payload[match.end():cursor - 1]


def _may_access_hbf(opcode: str) -> bool:
    parts = opcode.lower().split(".")
    base = parts[0]
    if base == "cp" and "async" in parts and "global" in parts:
        return True
    if base not in {"ld", "st", "atom", "red", "suld", "sust", "tex", "tld4"}:
        return False
    if "global" in parts or "generic" in parts:
        return True
    if any(space in parts for space in ("param", "local", "shared", "const")):
        return False
    # An ld/st without an explicit state space uses generic addressing.
    return base in {"ld", "st", "atom", "red"}


def _call_graph_status(payload: bytes) -> tuple[str, list[str]]:
    uncommented = _strip_comments(payload)
    if CALL_PATTERN.search(uncommented):
        return "UNRESOLVED_CALL_DEPENDENCY", ["call"]
    unresolved = []
    for name, body in _function_bodies(uncommented):
        opcodes = [
            match.group(1).decode("ascii", errors="strict")
            for match in INSTRUCTION_PATTERN.finditer(body)
        ]
        if any(_may_access_hbf(opcode) for opcode in opcodes):
            unresolved.append(name)
    if unresolved:
        return "UNRESOLVED_NONINLINE_MEMORY_FUNCTION", sorted(set(unresolved))
    return "NO_UNRESOLVED_MEMORY_FUNCTION", []


def _manifest_key(record: dict[str, Any]) -> tuple[str, str]:
    return str(record.get("module_id", "")), str(record.get("kernel", ""))


def _deduplicate_manifest_records(
    records: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse status-66 retries, rejecting identity-zero and conflicts."""
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("pass manifest record is not an object")
        module_id, kernel = _manifest_key(record)
        if not module_id.startswith("ptx:sha256:") or len(module_id) != 75:
            raise ValueError(f"invalid pass manifest module_id: {module_id!r}")
        if module_id == ZERO_IDENTITY:
            raise ValueError("zero PTX module identity is forbidden")
        if not kernel:
            raise ValueError("pass manifest record has no kernel")
        key = module_id, kernel
        previous = unique.get(key)
        if previous is None:
            unique[key] = record
        elif previous != record:
            raise ValueError(f"conflicting pass manifest records for {key}")
    return [unique[key] for key in sorted(unique)]


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"manifest line {number} is not an object")
        rows.append(value)
    return rows


def _process_input(plugin: ctypes.CDLL, payload: bytes, kernel: str) -> dict[str, Any]:
    request = json.dumps({
        "input": {
            "full_ptx": payload.decode("utf-8"),
            "to_patch_kernel": kernel,
            "global_ebpf_map_info_symbol": "map_info",
            "ebpf_communication_data_symbol": "constData",
        },
        "ebpf_instructions": [],
    }).encode("utf-8")
    for size in OUTPUT_SIZES:
        output = ctypes.create_string_buffer(size)
        status = int(plugin.process_input(request, size, output))
        if status == 66:
            continue
        response: dict[str, Any] = {}
        if output.value:
            parsed = json.loads(output.value)
            if isinstance(parsed, dict):
                response = parsed
        if status != 0:
            return {"status_code": status, "response": response}
        return {"status_code": 0, "response": response}
    return {"status_code": 66, "response": {"error": "pass output exceeded 64 MiB"}}


def _transform_module_child(
    source: pathlib.Path,
    pass_library: pathlib.Path,
    manifest_path: pathlib.Path,
    entries: Sequence[str],
    call_graph_status: str,
    unresolved_functions: Sequence[str],
) -> dict[str, Any]:
    original = source.read_bytes()
    raw_sha = _sha256(original)
    payload = original
    manifest_path.unlink(missing_ok=True)
    os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(manifest_path)
    plugin = ctypes.CDLL(str(pass_library.resolve()))
    plugin.process_input.argtypes = (ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
    plugin.process_input.restype = ctypes.c_int
    entry_results: list[dict[str, Any]] = []

    if call_graph_status != "NO_UNRESOLVED_MEMORY_FUNCTION":
        entry_results.extend({
            "kernel": entry,
            "status": "UNRESOLVED",
            "reason": call_graph_status,
            "unresolved_functions": list(unresolved_functions),
        } for entry in entries)
    else:
        for entry in entries:
            outcome = _process_input(plugin, payload, entry)
            response = outcome["response"]
            status_code = outcome["status_code"]
            identity = response.get("transform_identity")
            coverage = response.get("coverage") or {}
            unsupported = [
                str(opcode) for opcode in (coverage.get("unsupported_opcodes") or [])
                if not NON_HBF_UNSUPPORTED.search(str(opcode))
            ]
            modified = bool(response.get("modified"))
            rewritten = int(coverage.get("rewritten_instructions") or 0)
            if status_code != 0:
                entry_results.append({
                    "kernel": entry, "status": "UNSUPPORTED",
                    "reason": response.get("error", f"process_input_status_{status_code}"),
                    "status_code": status_code,
                })
                continue
            if identity != raw_sha:
                raise ValueError(
                    f"transform identity mismatch for {entry}: {identity!r} != {raw_sha}"
                )
            if unsupported or not modified or rewritten <= 0:
                entry_results.append({
                    "kernel": entry, "status": "UNSUPPORTED",
                    "reason": "unsupported_or_unmodified",
                    "unsupported_opcodes": unsupported,
                    "rewritten_instructions": rewritten,
                })
                continue
            candidate = response.get("output_ptx")
            if not isinstance(candidate, str):
                raise ValueError(f"missing output_ptx for {entry}")
            next_payload = candidate.encode("utf-8")
            if not IDENTITY_PATTERN.search(next_payload):
                raise ValueError(f"transformed PTX for {entry} lacks module identity")
            payload = next_payload
            entry_results.append({
                "kernel": entry, "status": "SUPPORTED_TRANSFORMED",
                "rewritten_instructions": rewritten,
            })

    manifest_records = _deduplicate_manifest_records(_read_jsonl(manifest_path))
    by_kernel = {row["kernel"]: row for row in manifest_records}
    for result in entry_results:
        if result["status"] != "SUPPORTED_TRANSFORMED":
            continue
        record = by_kernel.get(result["kernel"])
        if record is None:
            raise ValueError(f"missing pass manifest for {result['kernel']}")
        if record["module_id"] != f"ptx:sha256:{raw_sha}":
            raise ValueError(f"pass manifest identity mismatch for {result['kernel']}")
        if not record.get("instrumented") or int(record.get("rewritten_instructions", 0)) <= 0:
            raise ValueError(f"pass manifest is not instrumented for {result['kernel']}")
        if record.get("unsupported_parameters") or int(record.get("unsupported_instructions", 0)):
            raise ValueError(f"pass manifest contains unsupported coverage for {result['kernel']}")

    return {
        "raw_sha256": raw_sha,
        "raw_bytes": len(original),
        "staged_bytes": len(payload),
        "staged_sha256": _sha256(payload),
        "entry_results": entry_results,
        "manifest_records": manifest_records,
        "output_ptx_base64": base64.b64encode(payload).decode("ascii"),
    }


def _run_module_child(
    source: pathlib.Path,
    pass_library: pathlib.Path,
    entries: Sequence[str],
    call_graph_status: str,
    unresolved_functions: Sequence[str],
    timeout_seconds: float,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hbfsim-auto-ptx-") as temporary:
        manifest_path = pathlib.Path(temporary) / "pass-manifest.jsonl"
        argv = [
            sys.executable, str(pathlib.Path(__file__).resolve()),
            "--transform-module-child", "--source", str(source.resolve()),
            "--pass-library", str(pass_library.resolve()),
            "--pass-manifest", str(manifest_path),
            "--entries-json", json.dumps(list(entries)),
            "--call-graph-status", call_graph_status,
            "--unresolved-functions-json", json.dumps(list(unresolved_functions)),
        ]
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_seconds
            )
        except subprocess.TimeoutExpired as error:
            return {
                "child_status": "TIMEOUT",
                "timeout_seconds": timeout_seconds,
                "stdout": (error.stdout or "") if isinstance(error.stdout, str)
                else (error.stdout or b"").decode(errors="replace"),
                "stderr": (error.stderr or "") if isinstance(error.stderr, str)
                else (error.stderr or b"").decode(errors="replace"),
            }
        if completed.returncode != 0:
            raise RuntimeError(
                f"isolated PTX transform failed for {source}: {completed.stderr.strip()}"
            )
        result = json.loads(completed.stdout)
        if not isinstance(result, dict):
            raise ValueError("child result is not an object")
        return result


def _publish_exact(path: pathlib.Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"refusing to overwrite differing artifact: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _preflight_exact(path: pathlib.Path, payload: bytes) -> None:
    if path.exists() and path.read_bytes() != payload:
        raise FileExistsError(f"refusing to overwrite differing artifact: {path}")


def _check_generation_directory(staging_dir: pathlib.Path) -> None:
    if not staging_dir.exists():
        return
    entries = list(staging_dir.iterdir())
    if entries and not (staging_dir / "COMPLETE.json").is_file():
        raise FileExistsError(
            f"incomplete staging generation must not be reused: {staging_dir}"
        )


def verify_stage_completion(
    staging_dir: pathlib.Path, pass_manifest: pathlib.Path
) -> dict[str, Any]:
    """Verify the final marker, both manifests, and the exact PTX file set."""
    staging_dir = pathlib.Path(staging_dir).resolve()
    pass_manifest = pathlib.Path(pass_manifest).resolve()
    marker_path = staging_dir / "COMPLETE.json"
    manifest_path = staging_dir / "ptx-staging-manifest.json"
    if not marker_path.is_file():
        raise ValueError(f"staging generation has no COMPLETE marker: {staging_dir}")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    manifest_payload = manifest_path.read_bytes()
    pass_payload = pass_manifest.read_bytes()
    if marker.get("manifest_sha256") != _sha256(manifest_payload):
        raise ValueError("staging manifest hash mismatch")
    if pathlib.Path(marker.get("pass_manifest_path", "")).resolve() != pass_manifest:
        raise ValueError("pass manifest path mismatch")
    if marker.get("pass_manifest_sha256") != _sha256(pass_payload):
        raise ValueError("pass manifest hash mismatch")
    manifest = json.loads(manifest_payload)
    if marker.get("status") != manifest.get("status"):
        raise ValueError("completion status mismatch")
    artifact_hashes = marker.get("artifact_sha256")
    if not isinstance(artifact_hashes, dict):
        raise ValueError("completion marker has no artifact hashes")
    for raw_path, expected in artifact_hashes.items():
        path = pathlib.Path(raw_path)
        if not path.is_file() or _sha256(path.read_bytes()) != expected:
            raise ValueError(f"staged artifact hash mismatch: {path}")
    expected_ptx = {
        pathlib.Path(variant["staged_path"]).resolve()
        for variant in manifest.get("variants", [])
        if variant.get("staged_path")
    }
    actual_ptx = {path.resolve() for path in staging_dir.glob("*.ptx")}
    if actual_ptx != expected_ptx:
        raise ValueError(
            f"staged PTX file set mismatch: expected={sorted(map(str, expected_ptx))}, "
            f"actual={sorted(map(str, actual_ptx))}"
        )
    return manifest


def stage_all_ptx(
    cache_root: pathlib.Path,
    staging_dir: pathlib.Path,
    pass_library: pathlib.Path,
    pass_manifest: pathlib.Path,
    *,
    policy: str = "strict",
    kernel_patterns: tuple[str, ...] = (),
    module_timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    if policy not in {"strict", "partial"}:
        raise ValueError("policy must be 'strict' or 'partial'")
    if module_timeout_seconds <= 0:
        raise ValueError("module_timeout_seconds must be positive")
    cache_root = pathlib.Path(cache_root).resolve()
    staging_dir = pathlib.Path(staging_dir).resolve()
    pass_library = pathlib.Path(pass_library).resolve()
    pass_manifest = pathlib.Path(pass_manifest).resolve()
    if not cache_root.is_dir():
        raise ValueError(f"PTX cache is not a directory: {cache_root}")
    if not pass_library.is_file():
        raise ValueError(f"PTX pass library does not exist: {pass_library}")
    _check_generation_directory(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    variants_by_sha: dict[str, dict[str, Any]] = {}
    all_manifest_records: list[dict[str, Any]] = []
    staged_payloads: dict[pathlib.Path, bytes] = {}
    for source in sorted(cache_root.rglob("*.ptx")):
        if staging_dir == source.parent or staging_dir in source.parents:
            continue
        original = source.read_bytes()
        raw_sha = _sha256(original)
        all_entries, duplicate_entries = _entry_inventory(original)
        selected = _matching_entries(all_entries, kernel_patterns)
        existing = variants_by_sha.get(raw_sha)
        if existing is not None:
            existing["sources"].append(str(source.resolve()))
            continue
        call_status, unresolved = _call_graph_status(original)
        if duplicate_entries or not all_entries or not selected:
            reason = (
                "DUPLICATE_ENTRY" if duplicate_entries else
                ("NO_ENTRY" if not all_entries else "SKIPPED_PATTERN")
            )
            variants_by_sha[raw_sha] = {
                "raw_sha256": raw_sha,
                "raw_bytes": len(original),
                "staged_path": None, "staged_sha256": None,
                "staged_bytes": None,
                "kernel_entries": list(all_entries),
                "selected_entries": list(selected),
                "entry_results": [
                    {"kernel": entry, "status": "UNRESOLVED", "reason": reason}
                    for entry in (selected or all_entries)
                ],
                "module_id": f"ptx:sha256:{raw_sha}",
                "call_graph_status": reason,
                "unresolved_functions": [],
                "duplicate_entries": list(duplicate_entries),
                "sources": [str(source.resolve())],
                "status": "NOT_READY",
            }
            continue
        child = _run_module_child(
            source, pass_library, selected, call_status, unresolved,
            module_timeout_seconds,
        )
        if child.get("child_status") == "TIMEOUT":
            variants_by_sha[raw_sha] = {
                "raw_sha256": raw_sha, "raw_bytes": len(original),
                "staged_path": None, "staged_sha256": None,
                "staged_bytes": None,
                "kernel_entries": list(all_entries),
                "selected_entries": list(selected),
                "entry_results": [
                    {"kernel": entry, "status": "UNRESOLVED", "reason": "CHILD_TIMEOUT"}
                    for entry in selected
                ],
                "module_id": f"ptx:sha256:{raw_sha}",
                "call_graph_status": call_status,
                "unresolved_functions": unresolved,
                "sources": [str(source.resolve())],
                "status": "NOT_READY",
                "child_diagnostic": child,
            }
            continue
        if child["raw_sha256"] != raw_sha:
            raise ValueError(f"child raw digest mismatch for {source}")
        results = child["entry_results"]
        if any(row.get("status") not in ENTRY_STATUSES for row in results):
            raise ValueError(f"invalid entry status for {source}")
        transformed = [r["kernel"] for r in results if r["status"] == "SUPPORTED_TRANSFORMED"]
        rejected = [r["kernel"] for r in results if r["status"] != "SUPPORTED_TRANSFORMED"]
        selected_is_all = set(selected) == set(all_entries)
        fully_ready = selected_is_all and not rejected and len(transformed) == len(all_entries)
        partial_ready = policy == "partial" and bool(transformed)
        publish = fully_ready or partial_ready
        destination = staging_dir / f"{raw_sha}.ptx"
        staged_payload = base64.b64decode(child.pop("output_ptx_base64"))
        if publish:
            staged_payloads[destination] = staged_payload
        child_records = child.pop("manifest_records")
        committed = set(transformed) if publish else set()
        all_manifest_records.extend(
            record for record in child_records if record.get("kernel") in committed
        )
        variants_by_sha[raw_sha] = {
            "raw_sha256": raw_sha,
            "raw_bytes": len(original),
            "staged_path": str(destination) if publish else None,
            "staged_sha256": child["staged_sha256"] if publish else None,
            "staged_bytes": child["staged_bytes"] if publish else None,
            "kernel_entries": list(all_entries),
            "selected_entries": list(selected),
            "entry_results": results,
            "raw_attempt_records": child_records,
            "module_id": f"ptx:sha256:{raw_sha}",
            "call_graph_status": call_status,
            "unresolved_functions": unresolved,
            "sources": [str(source.resolve())],
            "status": "READY" if fully_ready else ("PARTIAL_READY" if partial_ready else "NOT_READY"),
        }

    variants = [variants_by_sha[key] for key in sorted(variants_by_sha)]
    if not variants:
        overall = "NOT_READY"
    elif all(v["status"] == "READY" for v in variants):
        overall = "READY"
    elif policy == "partial" and any(v["status"] in {"READY", "PARTIAL_READY"} for v in variants):
        overall = "PARTIAL_READY"
    else:
        overall = "NOT_READY"
    final_records = _deduplicate_manifest_records(all_manifest_records)
    manifest = {
        "schema_version": 1,
        "status": overall,
        "policy": policy,
        "cache_root": str(cache_root),
        "staging_dir": str(staging_dir),
        "kernel_patterns": list(kernel_patterns),
        "completion_marker": str(staging_dir / "COMPLETE.json"),
        "variants": variants,
    }
    manifest_payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    pass_payload = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for row in final_records
    )
    manifest_path = staging_dir / "ptx-staging-manifest.json"
    artifact_hashes = {
        str(path): _sha256(payload) for path, payload in sorted(
            {**staged_payloads, pass_manifest: pass_payload,
             manifest_path: manifest_payload}.items(), key=lambda item: str(item[0])
        )
    }
    complete_payload = (json.dumps({
        "schema_version": 1,
        "status": overall,
        "manifest_sha256": _sha256(manifest_payload),
        "pass_manifest_path": str(pass_manifest),
        "pass_manifest_sha256": _sha256(pass_payload),
        "artifact_sha256": artifact_hashes,
    }, indent=2, sort_keys=True) + "\n").encode()
    complete_path = staging_dir / "COMPLETE.json"
    planned = {
        **staged_payloads,
        pass_manifest: pass_payload,
        manifest_path: manifest_payload,
        complete_path: complete_payload,
    }
    for path, payload in planned.items():
        _preflight_exact(path, payload)
    # COMPLETE is the final atomic publication point.  Consumers must reject a
    # generation without it and verify both manifest hashes before use.
    for path, payload in planned.items():
        if path != complete_path:
            _publish_exact(path, payload)
    _publish_exact(complete_path, complete_payload)
    return verify_stage_completion(staging_dir, pass_manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transform-module-child", action="store_true")
    parser.add_argument("--source", type=pathlib.Path)
    parser.add_argument("--entries-json")
    parser.add_argument("--call-graph-status", default="NO_UNRESOLVED_MEMORY_FUNCTION")
    parser.add_argument("--unresolved-functions-json", default="[]")
    parser.add_argument("--cache-root", type=pathlib.Path)
    parser.add_argument("--staging-dir", type=pathlib.Path)
    parser.add_argument("--pass-library", type=pathlib.Path, required=True)
    parser.add_argument("--pass-manifest", type=pathlib.Path, required=True)
    parser.add_argument("--policy", choices=("strict", "partial"), default="strict")
    parser.add_argument("--kernel-pattern", action="append", default=[])
    parser.add_argument("--module-timeout-seconds", type=float, default=120.0)
    args = parser.parse_args()
    if args.transform_module_child:
        if args.source is None or args.entries_json is None:
            parser.error("child mode requires --source and --entries-json")
        result = _transform_module_child(
            args.source, args.pass_library, args.pass_manifest,
            json.loads(args.entries_json), args.call_graph_status,
            json.loads(args.unresolved_functions_json),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.cache_root is None or args.staging_dir is None:
        parser.error("--cache-root and --staging-dir are required")
    manifest = stage_all_ptx(
        args.cache_root, args.staging_dir, args.pass_library,
        args.pass_manifest, policy=args.policy,
        kernel_patterns=tuple(args.kernel_pattern),
        module_timeout_seconds=args.module_timeout_seconds,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["status"] in {"READY", "PARTIAL_READY"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

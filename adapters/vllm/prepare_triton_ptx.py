#!/usr/bin/env python3
"""Stage unique Triton PTX variants for bpftime's late-load bootstrap."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Any


ENTRY_PATTERN = re.compile(
    rb"(?:\.visible\s+)?\.entry\s+([.$A-Za-z_][.$A-Za-z0-9_]*)\s*\("
)
Transformer = Callable[[bytes, str], bytes]


def is_staged_source(source: pathlib.Path,
                     cache_root: pathlib.Path) -> bool:
    for parent in source.parents:
        if parent == cache_root:
            return False
        if (parent / "ptx-staging-manifest.json").is_file():
            return True
    return False


def kernel_names(payload: bytes) -> tuple[str, ...]:
    return tuple(sorted({
        match.group(1).decode("utf-8", errors="strict")
        for match in ENTRY_PATTERN.finditer(payload)
    }))


def stage_ptx(cache_root: pathlib.Path, staging_dir: pathlib.Path, *,
              kernel: str | None = None,
              transformer: Transformer | None = None,
              host_launch_only: bool = False) -> dict[str, Any]:
    cache_root = cache_root.resolve()
    staging_dir = staging_dir.resolve()
    if not cache_root.is_dir():
        raise ValueError(f"Triton cache is not a directory: {cache_root}")
    if host_launch_only and transformer is None:
        raise ValueError("host-launch-only staging requires a transformer")
    staging_dir.mkdir(parents=True, exist_ok=True)

    variants: dict[str, dict[str, Any]] = {}
    for source in sorted(cache_root.rglob("*.ptx")):
        if staging_dir == source.parent or staging_dir in source.parents:
            continue
        if is_staged_source(source, cache_root):
            continue
        payload = source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        names = kernel_names(payload)
        if not names or (kernel is not None and kernel not in names):
            continue
        record = variants.get(digest)
        if record is not None:
            record["sources"].append(str(source.resolve()))
            continue
        destination = staging_dir / f"{digest}.ptx"
        selected_kernel = kernel or names[0]
        staged_payload = (
            transformer(payload, selected_kernel) if transformer else payload
        )
        if (not destination.exists() or
                destination.read_bytes() != staged_payload):
            destination.write_bytes(staged_payload)
        variants[digest] = {
            "sha256": digest,
            "kernel_names": names,
            "staged_path": str(destination),
            "sources": [str(source.resolve())],
            "bytes": len(payload),
            "staged_bytes": len(staged_payload),
            "prepatched": transformer is not None,
            "host_launch_only": host_launch_only,
        }

    ordered = [variants[key] for key in sorted(variants)]
    expected = {f"{record['sha256']}.ptx" for record in ordered}
    for stale in staging_dir.glob("*.ptx"):
        if stale.name not in expected:
            stale.unlink()
    manifest = {
        "schema_version": 3,
        "cache_root": str(cache_root),
        "staging_dir": str(staging_dir),
        "host_launch_only": host_launch_only,
        "variants": ordered,
    }
    temporary = staging_dir / ".ptx-staging-manifest.json.tmp"
    output = staging_dir / "ptx-staging-manifest.json"
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    return manifest


def transform_one(plugin_path: pathlib.Path, payload: bytes,
                  kernel: str, host_launch_only: bool = False) -> bytes:
    plugin = ctypes.CDLL(str(plugin_path.resolve()))
    plugin.process_input.argtypes = (
        ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p
    )
    plugin.process_input.restype = ctypes.c_int
    request = json.dumps({
        "input": {
            "full_ptx": payload.decode("utf-8"),
            "to_patch_kernel": kernel,
            "global_ebpf_map_info_symbol": "map_info",
            "ebpf_communication_data_symbol": "constData",
            "host_launch_only": host_launch_only,
        },
        "ebpf_instructions": [],
    }).encode("utf-8")
    for size in (1 << 20, 4 << 20, 16 << 20, 64 << 20):
        output = ctypes.create_string_buffer(size)
        status = plugin.process_input(request, size, output)
        if status == 66:
            continue
        if status != 0:
            raise RuntimeError(
                f"HBF PTX pass failed for {kernel} with status {status}"
            )
        response = json.loads(output.value)
        if not response.get("modified"):
            raise RuntimeError(f"HBF PTX pass did not prepare {kernel}")
        return response["output_ptx"].encode("utf-8")
    raise RuntimeError(f"HBF PTX pass output exceeded 64 MiB for {kernel}")


def subprocess_transformer(
    plugin_path: pathlib.Path, manifest_path: pathlib.Path,
    host_launch_only: bool = False,
) -> Transformer:
    def invoke(payload: bytes, kernel: str) -> bytes:
        environment = os.environ.copy()
        environment["HBFSIM_PASS_MANIFEST_PATH"] = str(
            manifest_path.resolve()
        )
        command = [
            sys.executable, str(pathlib.Path(__file__).resolve()),
            "--transform-one", "--pass-library", str(plugin_path.resolve()),
            "--kernel", kernel,
        ]
        if host_launch_only:
            command.append("--host-launch-only")
        completed = subprocess.run(
            command, input=payload, capture_output=True, env=environment,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"isolated HBF PTX pass failed for {kernel}: "
                f"{completed.stderr.decode(errors='replace')}"
            )
        return completed.stdout

    return invoke


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transform-one", action="store_true")
    parser.add_argument("--cache-root", type=pathlib.Path)
    parser.add_argument("--staging-dir", type=pathlib.Path)
    parser.add_argument("--pass-library", type=pathlib.Path)
    parser.add_argument("--pass-manifest", type=pathlib.Path)
    parser.add_argument("--kernel", default="fused_moe_kernel")
    parser.add_argument("--host-launch-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if args.transform_one:
        if args.pass_library is None:
            parser.error("--transform-one requires --pass-library")
        sys.stdout.buffer.write(
            transform_one(
                args.pass_library, sys.stdin.buffer.read(), args.kernel,
                args.host_launch_only,
            )
        )
        return 0
    if args.cache_root is None or args.staging_dir is None:
        parser.error("--cache-root and --staging-dir are required")
    if args.host_launch_only and args.pass_library is None:
        parser.error("--host-launch-only requires --pass-library")
    transformer = None
    if args.pass_library is not None:
        if args.pass_manifest is None:
            parser.error("--pass-library requires --pass-manifest")
        args.pass_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.pass_manifest.unlink(missing_ok=True)
        transformer = subprocess_transformer(
            args.pass_library, args.pass_manifest, args.host_launch_only
        )
    manifest = stage_ptx(
        args.cache_root, args.staging_dir, kernel=args.kernel,
        transformer=transformer,
        host_launch_only=args.host_launch_only,
    )
    if not manifest["variants"]:
        raise SystemExit("no Triton PTX variants found")
    if not args.quiet:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

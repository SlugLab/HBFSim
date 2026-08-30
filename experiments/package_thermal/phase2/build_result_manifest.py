#!/usr/bin/env python3
"""Create a portable SHA-256 manifest for selected Phase-II result roots."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=pathlib.Path)
    parser.add_argument("--result", action="append", required=True)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    base = args.base.resolve()
    roots = []
    total_files = 0
    total_bytes = 0
    for relative in args.result:
        root = (base / relative).resolve()
        if not root.is_dir() or base not in root.parents:
            raise RuntimeError(f"invalid result root: {relative}")
        files = []
        aggregate = hashlib.sha256()
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            rel = path.relative_to(root).as_posix()
            size = path.stat().st_size
            digest = file_sha256(path)
            aggregate.update(f"{digest}  {rel}\n".encode())
            files.append({"path": rel, "bytes": size, "sha256": digest})
            total_files += 1
            total_bytes += size
        roots.append({
            "path": relative,
            "file_count": len(files),
            "bytes": sum(item["bytes"] for item in files),
            "aggregate_sha256": aggregate.hexdigest(),
            "files": files,
        })
    payload = {
        "schema_version": 1,
        "created_unix_ns": time.time_ns(),
        "hash_contract": "sha256(<file_sha256><two spaces><root-relative-path><newline>) in lexical path order",
        "base": str(base),
        "root_count": len(roots),
        "file_count": total_files,
        "bytes": total_bytes,
        "results": roots,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({key: payload[key] for key in ("root_count", "file_count", "bytes")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Future gated launcher for R3 and page-size capacity fixtures.

The default command is preflight. GPU execution requires --execute and must be
invoked only by the single giga GPU coordinator after R1 and R2 pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys


PAGE_BYTES = [4096, 8192, 16384, 32768, 65536]


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=pathlib.Path)
    parser.add_argument("--build-dir", required=True, type=pathlib.Path)
    parser.add_argument("--result-root", required=True, type=pathlib.Path)
    parser.add_argument("--backing-dir", required=True, type=pathlib.Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--r1-r2-gate", choices=["PASS", "NOT_PASS"],
                        default="NOT_PASS")
    args = parser.parse_args()

    root = args.source_root.resolve()
    build = args.build_dir.resolve()
    results = args.result_root.resolve()
    binary = build / "capacity_payload_fixture"
    legacy_binary = build / "benchmarks/cuda/hbf_microbench"
    plan = {
        "schema_version": 1,
        "status": "READY_NOT_RUN" if binary.is_file() else "BUILD_REQUIRED",
        "gpu_execution_requested": args.execute,
        "r1_r2_gate": args.r1_r2_gate,
        "logical_bytes": 110 * 1024**3,
        "cache_bytes": 2 * 1024**3,
        "page_bytes": PAGE_BYTES,
        "frame_counts": {str(page): 2 * 1024**3 // page for page in PAGE_BYTES},
        "payload_fixture": str(binary),
        "payload_fixture_sha256": sha256(binary) if binary.is_file() else None,
        "original_r3_entry": str(root / "scripts/run_microbench.py"),
        "original_r3_binary": str(legacy_binary),
        "original_r3_scope": "public hbfsim_map_file random sparse capacity path",
        "mqsim_timing_claim_for_page_fixture": False,
        "concurrent_eviction_tested": False,
    }
    print(json.dumps(plan, indent=2))
    if not args.execute:
        return 0
    if args.r1_r2_gate != "PASS":
        raise SystemExit("R1/R2 gate is not PASS; refusing GPU execution")
    if not binary.is_file() or not legacy_binary.is_file():
        raise SystemExit("required binaries are missing")

    if results.exists() and any(results.iterdir()):
        raise SystemExit(f"result root is not empty; refusing overwrite: {results}")
    results.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for page in PAGE_BYTES:
        page_dir = results / f"page_{page}"
        page_dir.mkdir(parents=True, exist_ok=True)
        command = [str(binary), "--page-bytes", str(page),
                   "--output", str(page_dir / "result.json"),
                   "--backing-dir", str(args.backing_dir)]
        completed = subprocess.run(command, cwd=root, env=env, text=True,
                                   capture_output=True, timeout=900)
        (page_dir / "stdout.log").write_text(completed.stdout)
        (page_dir / "stderr.log").write_text(completed.stderr)
        if completed.returncode != 0:
            raise SystemExit(f"page fixture failed for {page} bytes")
        subprocess.run([
            sys.executable,
            str(root / "experiments/rebuttal_20260921/capacity/validate_fixture.py"),
            str(page_dir / "result.json"),
        ], cwd=root, env=env, check=True,
           stdout=(page_dir / "validation.log").open("w"),
           stderr=subprocess.STDOUT)

    original = results / "original_r3"
    original.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run([
        sys.executable, str(root / "scripts/run_microbench.py"),
        "--build-dir", str(build),
        "--profile", str(root / "configs/profiles/nominal.json"),
        "--over-vram", "--logical-bytes", "110G", "--cache-bytes", "2G",
        "--iterations", "128", "--backing-dir", str(args.backing_dir),
        "--output", str(original / "summary.json"),
    ], cwd=root, env=env, text=True, capture_output=True, timeout=900)
    (original / "stdout.log").write_text(completed.stdout)
    (original / "stderr.log").write_text(completed.stderr)
    if completed.returncode != 0:
        raise SystemExit("original public hbfsim_map_file R3 path failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Stage only the one-shot exact probe from a calibrated runtime bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib


class SidebandArtifactError(RuntimeError):
    pass


def _regular_file(path: pathlib.Path, label: str) -> pathlib.Path:
    if path.is_symlink() or not path.is_file():
        raise SidebandArtifactError(f"{label} is not a regular file: {path}")
    return path.resolve()


def _atomic_write(path: pathlib.Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def prepare_exact_sideband(
    profile_path: pathlib.Path,
    probe_ptx_path: pathlib.Path,
    report_dir: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    profile_path = _regular_file(profile_path, "exact profile")
    probe_ptx_path = _regular_file(probe_ptx_path, "exact probe PTX")
    try:
        profile = json.loads(profile_path.read_text())
        artifacts = profile["runtime_artifacts"]
        source_prepatched = pathlib.Path(artifacts["prepatched_ptx_dir"])
        source_manifest = pathlib.Path(artifacts["pass_manifest"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise SidebandArtifactError(
            "exact profile runtime artifacts are incomplete"
        ) from error
    source_manifest = _regular_file(source_manifest, "pass manifest")
    if source_prepatched.is_symlink() or not source_prepatched.is_dir():
        raise SidebandArtifactError(
            f"prepatched PTX root is not a directory: {source_prepatched}"
        )

    probe_payload = probe_ptx_path.read_bytes()
    original_sha = hashlib.sha256(probe_payload).hexdigest()
    transformed_path = _regular_file(
        source_prepatched / f"{original_sha}.ptx",
        "prepatched exact probe",
    )
    transformed_payload = transformed_path.read_bytes()
    transformed_sha = hashlib.sha256(transformed_payload).hexdigest()
    matches = []
    try:
        for line in source_manifest.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("original_ptx_sha256") == original_sha:
                matches.append(record)
    except json.JSONDecodeError as error:
        raise SidebandArtifactError("pass manifest is invalid JSONL") from error
    if len(matches) != 1:
        raise SidebandArtifactError(
            "exact probe requires exactly one pass-manifest record"
        )
    record = matches[0]
    if record.get("kernel") != "hbfsim_llama_probe_kernel" or \
            record.get("instrumented") is not True or \
            record.get("transformed_ptx_sha256") != transformed_sha or \
            record.get("unsupported_instructions") or \
            record.get("unsupported_opcodes") or \
            record.get("unsupported_parameters"):
        raise SidebandArtifactError(
            "exact probe pass-manifest record is not admissible"
        )

    root = report_dir.resolve() / "exact-sideband-artifacts" / original_sha
    prepatched = root / "prepatched-ptx"
    staged_ptx = prepatched / f"{original_sha}.ptx"
    manifest = root / "pass-manifest.jsonl"
    _atomic_write(staged_ptx, transformed_payload)
    _atomic_write(
        manifest,
        (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode(),
    )
    return prepatched.resolve(), manifest.resolve()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--probe-ptx", type=pathlib.Path, required=True)
    parser.add_argument("--report-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    prepatched, manifest = prepare_exact_sideband(
        args.profile, args.probe_ptx, args.report_dir
    )
    print(prepatched)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

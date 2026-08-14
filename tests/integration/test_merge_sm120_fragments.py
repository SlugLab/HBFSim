#!/usr/bin/env python3
"""Verify immutable merging of module-bound SM120 Stage 1 fragments."""

from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
MERGER = ROOT / "scripts/calibration/merge_sm120_fragments.py"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_fragment(root: pathlib.Path, bundle_root: pathlib.Path,
                  marker: bytes, toolchain: dict) -> pathlib.Path:
    original_hash = sha(b"original-" + marker)
    transformed = b"// transformed " + marker + b"\n"
    transformed_hash = sha(transformed)
    directory = root / original_hash
    directory.mkdir()
    prepatched = directory / "prepatched-ptx"
    prepatched.mkdir()
    (prepatched / f"{original_hash}.ptx").write_bytes(transformed)
    module_id = f"ptx:sha256:{original_hash}"
    manifest = {
        "manifest_schema_version": 3,
        "module_id": module_id,
        "kernel": "fused_moe_kernel",
        "aot_required_for_exact": True,
        "unsupported_instructions": 0,
    }
    pass_bytes = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (directory / "pass-manifest.jsonl").write_bytes(pass_bytes)
    bundle = bundle_root / original_hash / "sm_120a"
    bundle.mkdir(parents=True)
    module = {
        "module_id": module_id,
        "ptx_target": "sm_120a",
        "original_ptx_sha256": original_hash,
        "transformed_ptx_sha256": transformed_hash,
        "cubin_sha256": sha(b"cubin-" + marker),
        "sass_sha256": sha(b"sass-" + marker),
        "kernels": [{"name": "fused_moe_kernel", "block_threads": 128,
                     "registers": 64, "spill_load_bytes": 0,
                     "spill_store_bytes": 0, "static_shared_bytes": 0,
                     "max_dynamic_shared_bytes": 0,
                     "occupancy_blocks_per_sm": 1}],
    }
    fragment = {
        "fragment_schema_version": 1,
        "toolchain": toolchain,
        "modules": [module],
        "validation": {"status": "pending"},
        "provenance": {
            "pass_manifest_sha256": sha(pass_bytes),
            "bundle": str(bundle.resolve()),
        },
    }
    path = directory / "profile-fragment.json"
    path.write_text(json.dumps(fragment, sort_keys=True))
    return path


def run(fragments: list[pathlib.Path], output: pathlib.Path,
        expected: int) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(MERGER)]
    for fragment in fragments:
        command.extend(("--fragment", str(fragment)))
    command.extend(("--output-dir", str(output)))
    completed = subprocess.run(command, text=True, capture_output=True,
                               check=False, timeout=30)
    require(completed.returncode == expected,
            f"expected {expected}, got {completed.returncode}: "
            f"{completed.stdout}\n{completed.stderr}")
    return completed


def main() -> int:
    require(MERGER.is_file(), f"fragment merger is missing: {MERGER}")
    with tempfile.TemporaryDirectory(prefix="hbfsim-merge-stage1-") as temporary:
        root = pathlib.Path(temporary)
        bundle_root = root / "bundles"
        toolchain = {"cuda_version": "13.0", "ptxas_version": "13.0.88",
                     "nvdisasm_version": "13.0.85",
                     "cuobjdump_version": "13.0.85",
                     "ncu_version": "2025.4.1.0"}
        first = make_fragment(root, bundle_root, b"a", toolchain)
        second = make_fragment(root, bundle_root, b"b", toolchain)
        output = root / "merged"
        summary = json.loads(run([second, first], output, 0).stdout)
        fragment_path = output / "profile-fragment.json"
        merged = json.loads(fragment_path.read_text())
        modules = merged["modules"]
        hashes = [module["original_ptx_sha256"] for module in modules]
        require(hashes == sorted(hashes) and len(hashes) == 2,
                "merged modules are not unique and canonical")
        require(summary["module_count"] == 2 and
                summary["validation_status"] == "pending",
                "merge summary invented or omitted evidence")
        pass_bytes = (output / "pass-manifest.jsonl").read_bytes()
        require(sha(pass_bytes) ==
                merged["provenance"]["pass_manifest_sha256"],
                "merged pass manifest hash is not bound")
        manifests = [json.loads(line) for line in pass_bytes.splitlines()]
        require({item["module_id"] for item in manifests} ==
                {module["module_id"] for module in modules},
                "merged pass evidence does not cover every module")
        for module in modules:
            member = (output / "prepatched-ptx" /
                      f"{module['original_ptx_sha256']}.ptx")
            require(member.is_file() and
                    sha(member.read_bytes()) ==
                    module["transformed_ptx_sha256"],
                    "prepatched member is not content-bound")
        require(pathlib.Path(merged["provenance"]["bundle"]).parents[1] ==
                bundle_root.resolve(), "bundle roots were not preserved")

        duplicate = run([first, second], output, 66)
        require("output" in duplicate.stderr,
                "existing output did not fail closed")

        mismatch_root = root / "mismatch"
        mismatch_root.mkdir()
        mismatch = make_fragment(
            mismatch_root, bundle_root, b"c",
            {**toolchain, "cuda_version": "13.1"})
        rejected = root / "rejected"
        run([first, mismatch], rejected, 66)
        require(not rejected.exists(), "incompatible merge published output")
    print(json.dumps({"status": "passed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

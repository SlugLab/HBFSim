"""Create an isolated, prepatched PTX stage for the exact frozen QKV variant.

CPU/filesystem only.  The bpftime filename is the *unmodified* source PTX
SHA-256; its contents are the existing HBF pass output for that source.
"""
import argparse
import hashlib
import json
from pathlib import Path

SOURCE_SHA = "6db074711d19c3e31cbcc98c6e170f0b4330259f220c2d518ce8aee42b9c9eab"
PATCHED_SHA = "7219c1e8f58fdca32bc520a3a8ccdb1055aaccc33d12ff7935d7ef7baf786d67"
MANIFEST_SHA = "7e6efbdbde59aaa9bdeed87f7307758d7d8b5bbaa70d997545e2886da4aa80b6"
KERNEL = "_ZN8internal5gemvx6kernelIii13__nv_bfloat16S2_S2_fLb0ELb1ELb1ELb0ELi6ELb0E18cublasGemvParamsExIi30cublasGemvTensorStridedBatchedIKS2_ES6_S4_IS2_EfEEENSt9enable_ifIXntT5_EvE4typeET11_"


def checked(path: Path, digest: str) -> bytes:
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != digest:
        raise ValueError(f"{path}: SHA-256 {actual} differs from {digest}")
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-ptx", type=Path, required=True)
    ap.add_argument("--instrumented-ptx", type=Path, required=True)
    ap.add_argument("--pass-manifest", type=Path, required=True)
    ap.add_argument("--stage", type=Path, required=True)
    args = ap.parse_args()
    source = checked(args.source_ptx, SOURCE_SHA)
    patched = checked(args.instrumented_ptx, PATCHED_SHA)
    manifest_bytes = checked(args.pass_manifest, MANIFEST_SHA)
    rows = [json.loads(line) for line in manifest_bytes.splitlines() if line.strip()]
    if len(rows) != 1 or rows[0].get("module_id") != f"ptx:sha256:{SOURCE_SHA}" or rows[0].get("kernel") != KERNEL:
        raise ValueError("pass manifest identity is not the exact QKV variant")
    if rows[0].get("instrumented") is not True or rows[0].get("rewritten_instructions", 0) <= 0 or rows[0].get("unsupported_instructions") != 0:
        raise ValueError("pass manifest does not establish a rewritten, supported variant")
    if source == patched:
        raise ValueError("staged PTX is identical to the unmodified source")
    args.stage.mkdir(parents=True, exist_ok=False)
    (args.stage / f"{SOURCE_SHA}.ptx").write_bytes(patched)
    (args.stage / "pass-manifests.jsonl").write_bytes(manifest_bytes)
    receipt = {
        "schema": "hbfsim.qkv_exact_prepatched_stage.v1",
        "source_ptx_sha256": SOURCE_SHA,
        "instrumented_ptx_sha256": PATCHED_SHA,
        "pass_manifest_sha256": MANIFEST_SHA,
        "kernel": KERNEL,
        "staged_ptx": str((args.stage / f"{SOURCE_SHA}.ptx").resolve()),
        "status": "CPU_PREPARED_NOT_RUNTIME_BOUND",
    }
    (args.stage / "STAGE_RECEIPT.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()

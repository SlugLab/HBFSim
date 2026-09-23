#!/usr/bin/env python3
"""Create a local CUDA 13.0 view that fixes two glibc/CUDA declarations.

The repository does not ship NVIDIA headers or binaries. This command reads
an installed, hash-checked CUDA 13.0 toolkit and creates a separate view.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil


ORIGINAL_HEADER_SHA256 = "decdc28efcfaf0aaf806abc96d7bba9cb84b37c6e83cb82cda59b6aa59916ff8"
ORIGINAL_NVCC_SHA256 = "b8e2347d9d7fcadf8812a27324d515de99d033d3e48611811ece7e66d7895f8a"
PATCHED_HEADER_SHA256 = "3fb882181f0519f32b999dbc047fe6dba5c029b271170140b08fbc743ac39c1a"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def link_children(source: Path, target: Path, excluded: set[str]) -> None:
    target.mkdir(parents=True, exist_ok=False)
    for child in source.iterdir():
        if child.name not in excluded:
            (target / child.name).symlink_to(child)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cuda-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.cuda_root.resolve(strict=True)
    output = args.output.absolute()
    if output.exists():
        parser.error("output already exists; use a fresh versioned directory")
    original_header = source / "targets/x86_64-linux/include/crt/math_functions.h"
    original_nvcc = source / "bin/nvcc"
    original_bytes = original_header.read_bytes()
    if sha(original_bytes) != ORIGINAL_HEADER_SHA256:
        parser.error("CUDA 13.0 math header SHA mismatch")
    if sha(original_nvcc.read_bytes()) != ORIGINAL_NVCC_SHA256:
        parser.error("CUDA 13.0 nvcc SHA mismatch")
    before_double = b"rsqrt(double x);"
    before_float = b"rsqrtf(float x);"
    if original_bytes.count(before_double) != 1 or original_bytes.count(before_float) != 1:
        parser.error("expected exactly one declaration of each rsqrt overload")
    patched = original_bytes.replace(before_double, b"rsqrt(double x) noexcept(true);")
    patched = patched.replace(before_float, b"rsqrtf(float x) noexcept(true);")
    if sha(patched) != PATCHED_HEADER_SHA256:
        parser.error("patched CUDA 13.0 math header SHA mismatch")
    link_children(source, output, {"bin", "targets"})
    link_children(source / "bin", output / "bin", {"nvcc"})
    shutil.copy2(original_nvcc, output / "bin/nvcc")
    link_children(source / "targets", output / "targets", {"x86_64-linux"})
    link_children(source / "targets/x86_64-linux", output / "targets/x86_64-linux", {"include"})
    link_children(source / "targets/x86_64-linux/include",
                  output / "targets/x86_64-linux/include", {"crt"})
    link_children(source / "targets/x86_64-linux/include/crt",
                  output / "targets/x86_64-linux/include/crt", {"math_functions.h"})
    (output / "targets/x86_64-linux/include/crt/math_functions.h").write_bytes(patched)
    receipt = {"schema": "hbfsim.cuda13_glibc_rsqrt_view.v1",
               "original_toolkit": str(source), "view": str(output),
               "nvcc_sha256": sha((output / "bin/nvcc").read_bytes()),
               "original_header_sha256": ORIGINAL_HEADER_SHA256,
               "view_header_sha256": PATCHED_HEADER_SHA256,
               "declaration_changes": ["rsqrt(double): add noexcept(true)",
                                       "rsqrtf(float): add noexcept(true)"],
               "other_files": "symlinks to original CUDA 13.0 toolkit"}
    (output.parent / (output.name + "-receipt.json")).write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()

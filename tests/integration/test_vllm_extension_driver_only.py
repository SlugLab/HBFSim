#!/usr/bin/env python3
"""Fail closed if the vLLM extension imports a CUDA runtime provider."""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def run(*argv: str) -> str:
    completed = subprocess.run(
        argv,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    require(
        completed.returncode == 0,
        f"command failed: {' '.join(argv)}\n{completed.stdout}",
    )
    return completed.stdout


def main() -> None:
    require(
        len(sys.argv) == 7,
        "expected extension, readelf, nm, CMake, and two source paths",
    )
    extension, readelf, nm, cmake_path, context_path, capacity_path = sys.argv[1:]

    dynamic = run(readelf, "-d", extension)
    undefined = run(nm, "-D", "--undefined-only", extension)
    sources = "\n".join(
        pathlib.Path(path).read_text(encoding="utf-8")
        for path in (context_path, capacity_path)
    )
    cmake = pathlib.Path(cmake_path).read_text(encoding="utf-8")

    needed = re.findall(r"\(NEEDED\).*?\[([^]]+)\]", dynamic)
    require(
        "libcuda.so.1" in needed,
        f"system driver dependency is missing: {needed}",
    )
    require(
        not any(name.startswith("libcudart.so") for name in needed),
        f"CUDA runtime provider leaked into extension: {needed}",
    )
    require(
        not re.search(r"\b(?:RPATH|RUNPATH)\b.*?/cuda-", dynamic),
        "toolkit runtime directory leaked into extension RPATH/RUNPATH",
    )
    require(
        not re.search(r"\bU\s+cuda[A-Z]\w*", undefined),
        f"CUDA runtime symbol leaked into extension:\n{undefined}",
    )

    required_driver_symbols = (
        "cuCtxSynchronize",
        "cuMemFreeHost",
        "cuMemHostAlloc",
        "cuMemHostGetDevicePointer_v2",
        "cuMemHostRegister_v2",
        "cuMemHostUnregister",
    )
    for symbol in required_driver_symbols:
        require(symbol in undefined, f"required driver symbol is missing: {symbol}")

    require(
        "cuda_runtime_api.h" not in sources,
        "runtime API header remains in core sources",
    )
    require(
        not re.search(r"::cuda[A-Z]", sources),
        "direct CUDA runtime call remains in core sources",
    )
    require(
        not re.search(
            r"target_link_libraries\s*\(\s*hbfsim_core\b[^)]*\bCUDA::cudart\b",
            cmake,
            re.DOTALL,
        ),
        "hbfsim_core still links the CUDA runtime provider",
    )

    print("PASS: vLLM extension is CUDA-driver-only")


if __name__ == "__main__":
    main()

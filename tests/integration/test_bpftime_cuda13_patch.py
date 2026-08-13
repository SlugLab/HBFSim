#!/usr/bin/env python3

import pathlib
import shutil
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def require_guarded_legacy_calls(path: pathlib.Path) -> None:
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if "cuCtxCreate(&context, 0," not in line:
            continue
        prefix = "\n".join(lines[max(0, index - 5):index])
        require("#if CUDA_VERSION >= 13000" in prefix and "#else" in prefix,
                f"unguarded pre-CUDA-13 cuCtxCreate call in {path}:{index + 1}")


def main() -> int:
    source = pathlib.Path(sys.argv[1]).resolve()
    patches = [pathlib.Path(value).resolve() for value in sys.argv[2:]]
    require(len(patches) == 5, "expected the complete five-patch series")
    for patch in patches:
        require(patch.is_file(), f"missing bpftime patch: {patch}")

    with tempfile.TemporaryDirectory(prefix="hbfsim-bpftime-cuda13-") as root:
        copied = pathlib.Path(root) / "bpftime"
        shutil.copytree(source, copied, ignore=shutil.ignore_patterns(".git"))
        for patch in patches:
            checked = subprocess.run(
                ["git", "apply", "--check", str(patch)], cwd=copied,
                text=True, capture_output=True,
            )
            require(checked.returncode == 0,
                    f"patch does not apply after its predecessors: "
                    f"{patch.name}: {checked.stderr}")
            subprocess.run(["git", "apply", str(patch)], cwd=copied,
                           check=True)

        nv_attach = copied / "attach/nv_attach_impl/nv_attach_impl.cpp"
        map_handler = copied / "runtime/src/handler/map_handler.cpp"
        require_guarded_legacy_calls(nv_attach)
        require_guarded_legacy_calls(map_handler)
        require(nv_attach.read_text().count("cuCtxCreate(&context, nullptr, 0,")
                >= 1, "CUDA 13 nv-attach context path is missing")
        require(map_handler.read_text().count(
                    "cuCtxCreate(&context, nullptr, 0,") >= 5,
                "not all CUDA map context paths use the CUDA 13 signature")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

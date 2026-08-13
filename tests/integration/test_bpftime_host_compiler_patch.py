#!/usr/bin/env python3

import pathlib
import shutil
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    source = pathlib.Path(sys.argv[1]).resolve()
    patches = [pathlib.Path(value).resolve() for value in sys.argv[2:]]
    require(len(patches) == 4, "expected the complete four-patch series")
    for patch in patches:
        require(patch.is_file(), f"missing bpftime patch: {patch}")

    with tempfile.TemporaryDirectory(prefix="hbfsim-bpftime-host-compiler-") as root:
        copied = pathlib.Path(root) / "bpftime"
        shutil.copytree(source, copied, ignore=shutil.ignore_patterns(".git"))
        for patch in patches:
            applied = subprocess.run(
                ["git", "apply", "--check", str(patch)],
                cwd=copied,
                text=True,
                capture_output=True,
            )
            require(applied.returncode == 0,
                    f"patch does not apply after its predecessors: "
                    f"{patch.name}: {applied.stderr}")
            subprocess.run(["git", "apply", str(patch)], cwd=copied,
                           check=True)

        libbpf = copied / "third_party/bpftool/libbpf/src/libbpf.c"
        text = libbpf.read_text()
        require("const char *res;" in text,
                "kallsyms suffix pointer is not const-correct")
        require("*psym_trim = sym_trim, *sym_sfx;" not in text,
                "available-kallsyms suffix pointer remains mutable")
        require("char sym_trim[256], *psym_trim = sym_trim;" in text,
                "mutable trim buffer contract was not preserved")
        require("const char *sym_sfx;" in text,
                "available-kallsyms suffix pointer is not const-correct")
        require("const char *next_path;" in text,
                "search-path separator pointer is not const-correct")

        llvm_jit_cmake = copied / "vm/llvm-jit/CMakeLists.txt"
        cmake_text = llvm_jit_cmake.read_text()
        require("if(${BPFTIME_LLVM_JIT})\n    add_subdirectory(cli)" not in
                cmake_text,
                "embedded LLVM JIT still builds its optional CLI unconditionally")
        require("if(${BUILD_LLVM_AOT_CLI})\n    add_subdirectory(cli)" in
                cmake_text,
                "LLVM AOT CLI option no longer controls its subdirectory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

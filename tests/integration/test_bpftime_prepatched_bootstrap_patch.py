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
    require(len(patches) == 6, "expected the complete six-patch series")
    with tempfile.TemporaryDirectory(prefix="hbfsim-bpftime-bootstrap-") as root:
        copied = pathlib.Path(root) / "bpftime"
        shutil.copytree(source, copied, ignore=shutil.ignore_patterns(".git"))
        for patch in patches:
            checked = subprocess.run(
                ["git", "apply", "--check", str(patch)], cwd=copied,
                text=True, capture_output=True,
            )
            require(checked.returncode == 0,
                    f"patch does not apply after predecessors: "
                    f"{patch.name}: {checked.stderr}")
            subprocess.run(["git", "apply", str(patch)], cwd=copied,
                           check=True)

        implementation = (copied / "attach/nv_attach_impl/nv_attach_impl.cpp").read_text()
        function = implementation.index(
            "void nv_attach_impl::bootstrap_existing_fatbins()")
        prepatched = implementation.index(
            "BPFTIME_CUDA_LATE_PTX_PREPATCHED", function)
        module_scan = implementation.index(
            "elf_introspect::list_loaded_modules()", function)
        require(prepatched < module_scan,
                "prepatched exact bootstrap still scans every loaded fatbin")
        branch = implementation[prepatched:module_scan]
        require("extract_ptxs({})" in branch and
                "prefill_patched_kernel_functions_from_loaded_fatbins();" in branch and
                "return;" in branch,
                "prepatched exact bootstrap is not ingested exactly once")
        require("thousands of times in llama.cpp/vLLM" in branch,
                "large-workload regression rationale is missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

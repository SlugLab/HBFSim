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
    patch1 = pathlib.Path(sys.argv[2]).resolve()
    patch2 = pathlib.Path(sys.argv[3]).resolve()
    require(patch2.is_file(), "AOT bpftime patch is missing")
    with tempfile.TemporaryDirectory(prefix="hbfsim-bpftime-aot-") as temp:
        copied = pathlib.Path(temp) / "bpftime"
        shutil.copytree(source, copied, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "build"))
        for patch in (patch1, patch2):
            check = subprocess.run(["git", "apply", "--check", str(patch)],
                                   cwd=copied, text=True, capture_output=True)
            require(check.returncode == 0,
                    f"patch does not apply after predecessor: {check.stderr}")
            subprocess.run(["git", "apply", str(patch)], cwd=copied, check=True)

        attach = copied / "attach" / "nv_attach_impl"
        fatbin = (attach / "nv_attach_fatbin_record.cpp").read_text()
        bundle = (attach / "nv_attach_aot_bundle.cpp").read_text()
        bridge = (attach / "nv_attach_aot_load_provenance.cpp").read_text()
        cmake = (attach / "CMakeLists.txt").read_text()

        require("HBFSIM_EXACT_BUNDLE_DIR" in bundle,
                "AOT loader has no explicit bundle-root control")
        for token in ("canonical", "symlink_status", "is_regular_file",
                      "kMaxCubinBytes", "kMaxArtifactBytes", "sha256",
                      "original_ptx_sha256", "transformed_ptx_sha256",
                      "cubin_sha256", "ptx_target"):
            require(token in bundle, f"AOT bundle validation misses {token}")
        require("candidates.size() != 1" in bundle,
                "AOT loader does not reject missing/duplicate target bundles")
        require("sm_120" in bundle and "sm_120a" in bundle and
                "sm_120f" in bundle,
                "AOT loader does not bound target variants")

        compile_guard = fatbin.find("if (!exact_aot_enabled())")
        compile_call = fatbin.find("compile_ptxs(impl, patched_ptx)")
        require(compile_guard >= 0 and compile_call > compile_guard,
                "exact mode still unconditionally JIT-compiles PTX")
        require("load_exact_aot_bundle(original_ptx_sha256)" in fatbin,
                "fatbin loader does not select by original PTX digest")
        require("sha256(ptx.data(), ptx.size())" in fatbin and
                "aot_bundle->transformed_ptx_sha256" in fatbin,
                "exact loader does not bind staged PTX bytes to the artifact")
        require("aot_module_load_scope" in fatbin and
                "compiled_elf.data()" in fatbin,
                "AOT provenance does not bracket the loaded cubin buffer")
        require("aot_bundle->artifact_json" in fatbin,
                "AOT provenance does not pass the artifact manifest")
        require("sha256(compiled_elf.data(), compiled_elf.size())" in fatbin,
                "module cache is not keyed by loaded cubin bytes")

        for token in ("hbfsim_begin_module_load_from_aot",
                      "hbfsim_end_module_load", "permits_load"):
            require(token in bridge, f"AOT provenance bridge misses {token}")
        require("nv_attach_aot_bundle.cpp" in cmake and
                "nv_attach_aot_load_provenance.cpp" in cmake,
                "AOT sources are not compiled into bpftime")

        tests = (attach / "test" / "test_aot_load_provenance.cpp").read_text()
        for phrase in ("missing hooks reject", "partial hooks reject",
                       "zero begin token rejects", "successful begin ends once"):
            require(phrase in tests, f"upstream AOT bridge test misses {phrase}")
        bundle_tests_path = attach / "test" / "test_aot_bundle.cpp"
        require(bundle_tests_path.is_file(),
                "upstream patch has no executable AOT bundle tests")
        bundle_tests = bundle_tests_path.read_text()
        for phrase in ("valid exact AOT bundle loads",
                       "missing and duplicate targets reject",
                       "symlink bundle members reject",
                       "oversized bundle members reject",
                       "malformed and mismatched artifacts reject"):
            require(phrase in bundle_tests,
                    f"upstream AOT bundle test misses {phrase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

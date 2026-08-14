#!/usr/bin/env python3

import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def write_tool(path: pathlib.Path, body: str) -> None:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_builder(builder: pathlib.Path, root: pathlib.Path,
                environment: dict[str, str] | None = None,
                target: str = "sm_120") -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable, str(builder),
        "--original-ptx", str(root / "original.ptx"),
        "--transformed-ptx", str(root / "transformed.ptx"),
        "--pass-manifest", str(root / "pass.jsonl"),
        "--kernel-contract", str(root / "kernel-contract.json"),
        "--target", target,
        "--bundle-root", str(root / "bundles"),
        "--ptxas", str(root / "tools" / "ptxas"),
        "--nvdisasm", str(root / "tools" / "nvdisasm"),
        "--cuobjdump", str(root / "tools" / "cuobjdump"),
        "--expected-cuda-release", "13.0",
    ]
    return subprocess.run(command, text=True, capture_output=True,
                          env=environment, check=False)


def prepare(root: pathlib.Path) -> tuple[str, pathlib.Path]:
    tools = root / "tools"
    tools.mkdir()
    original = (
        ".version 9.0\n.target sm_120\n.address_size 64\n"
        ".visible .entry kernel() { ret; }\n"
    ).encode()
    identity = hashlib.sha256(original).hexdigest()
    marker = ", ".join(f"0x{int(identity[i:i+2], 16):02x}"
                       for i in range(0, 64, 2))
    transformed = (
        original.decode()
        + ".visible .const .align 8 .b8 __hbfsim_module_identity[32] = {"
        + marker + "};\n"
    ).encode()
    (root / "original.ptx").write_bytes(original)
    (root / "transformed.ptx").write_bytes(transformed)
    (root / "pass.jsonl").write_text(json.dumps({
        "module_id": "ptx:sha256:" + identity,
        "kernel": "kernel",
        "ptx_target": "sm_120",
        "instrumented": True,
    }) + "\n")
    (root / "kernel-contract.json").write_text(json.dumps({
        "kernel": {
            "block_threads": 256,
            "max_dynamic_shared_bytes": 49152,
            "occupancy_blocks_per_sm": 2,
        }
    }))

    write_tool(tools / "ptxas", r'''
import os, pathlib, sys
if "--version" in sys.argv:
    print("ptxas: release " + os.environ.get("FAKE_CUDA_RELEASE", "13.0") + ", V13.0.88")
    raise SystemExit(0)
if os.environ.get("FAKE_PTXAS_FAIL") == "1":
    print("synthetic ptxas failure", file=sys.stderr)
    raise SystemExit(1)
out = pathlib.Path(sys.argv[sys.argv.index("--output-file") + 1])
source = pathlib.Path(sys.argv[-1]).read_bytes()
out.write_bytes(b"\x7fELF-HBFSIM-" + source)
print("ptxas info    : Function properties for kernel", file=sys.stderr)
print("    0 bytes stack frame, 16 bytes spill stores, 8 bytes spill loads", file=sys.stderr)
print("ptxas info    : Used 48 registers", file=sys.stderr)
print("ptxas info    : Function properties for __hbfsim_future_fault", file=sys.stderr)
''')
    # Real CUDA ptxas is larger than the 16 MiB manifest/PTX input bound.
    # Preserve that distinction in the fake-tool integration test.
    with (tools / "ptxas").open("ab") as output:
        output.write(b"\n#" + b"x" * (17 * 1024 * 1024))
    write_tool(tools / "nvdisasm", r'''
import os, sys
if "--version" in sys.argv:
    print("nvdisasm: release " + os.environ.get("FAKE_CUDA_RELEASE", "13.0") + ", V13.0.85")
    raise SystemExit(0)
if os.environ.get("FAKE_EMPTY_SASS") != "1":
    print("// fake sm_120 sass\n/*0000*/ EXIT;")
''')
    write_tool(tools / "cuobjdump", r'''
import os, sys
if "--version" in sys.argv:
    print("cuobjdump: release " + os.environ.get("FAKE_CUDA_RELEASE", "13.0") + ", V13.0.85")
    raise SystemExit(0)
if os.environ.get("FAKE_BAD_RESOURCE") == "1":
    print("malformed resources")
else:
    print("Resource usage:\n Function kernel:\n  REG:48 STACK:0 SHARED:1024 LOCAL:0"
          "\n Function __hbfsim_future_fault:\n  STACK:0 LOCAL:0")
''')
    return identity, root / "bundles" / hashlib.sha256(original).hexdigest() / "sm_120"


def main() -> int:
    builder = pathlib.Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="hbfsim-artifact-test-") as temp:
        root = pathlib.Path(temp)
        identity, bundle = prepare(root)
        result = run_builder(builder, root)
        require(result.returncode == 0,
                f"builder failed: {result.stdout}\n{result.stderr}")
        require(bundle.is_dir(), "content-addressed bundle was not created")
        require({item.name for item in bundle.iterdir()} == {
            "original.ptx", "transformed.ptx", "module.cubin",
            "module.sass", "artifact.json"}, "unexpected bundle layout")
        artifact = json.loads((bundle / "artifact.json").read_text())
        require(artifact["schema_version"] == 1, "wrong artifact schema")
        require(artifact["module_id"] == "ptx:sha256:" + identity,
                "wrong module identity")
        require(artifact["ptx_target"] == "sm_120", "wrong target")
        for name, filename in (
            ("original_ptx_sha256", "original.ptx"),
            ("transformed_ptx_sha256", "transformed.ptx"),
            ("cubin_sha256", "module.cubin"),
            ("sass_sha256", "module.sass"),
        ):
            digest = hashlib.sha256((bundle / filename).read_bytes()).hexdigest()
            require(artifact["hashes"][name] == digest, f"wrong {name}")
        kernel = artifact["kernels"][0]
        require(kernel == {
            "name": "kernel", "registers": 48,
            "spill_store_bytes": 16, "spill_load_bytes": 8,
            "static_shared_bytes": 1024,
            "max_dynamic_shared_bytes": 49152,
            "block_threads": 256, "occupancy_blocks_per_sm": 2,
        }, "wrong resource record")
        require("13.0" in artifact["toolchain"]["ptxas_version"],
                "tool versions were not recorded")

        duplicate = run_builder(builder, root)
        require(duplicate.returncode == 66 and "already exists" in duplicate.stderr,
                "builder overwrote an existing bundle")
        bad_target = run_builder(builder, root, target="sm_121")
        require(bad_target.returncode == 64 and "unsupported target" in bad_target.stderr,
                "unsupported target was accepted")

        verify = subprocess.run(
            [sys.executable, str(builder), "--verify-bundle", str(bundle)],
            text=True, capture_output=True, check=False)
        require(verify.returncode == 0, "fresh bundle did not verify")
        cubin = bundle / "module.cubin"
        cubin.write_bytes(cubin.read_bytes() + b"tampered")
        verify = subprocess.run(
            [sys.executable, str(builder), "--verify-bundle", str(bundle)],
            text=True, capture_output=True, check=False)
        require(verify.returncode == 65 and "cubin_sha256" in verify.stderr,
                "tampered cubin verified")

    for variable, expected in (
        ("FAKE_PTXAS_FAIL", "ptxas failed"),
        ("FAKE_BAD_RESOURCE", "resource"),
        ("FAKE_EMPTY_SASS", "SASS"),
        ("FAKE_CUDA_RELEASE", "CUDA release"),
    ):
        with tempfile.TemporaryDirectory(prefix="hbfsim-artifact-fail-") as temp:
            root = pathlib.Path(temp)
            prepare(root)
            environment = os.environ.copy()
            environment[variable] = "12.8" if variable == "FAKE_CUDA_RELEASE" else "1"
            result = run_builder(builder, root, environment)
            require(result.returncode != 0 and expected in result.stderr,
                    f"{variable} did not fail closed: {result.stderr}")

    with tempfile.TemporaryDirectory(prefix="hbfsim-artifact-marker-") as temp:
        root = pathlib.Path(temp)
        prepare(root)
        transformed = root / "transformed.ptx"
        transformed.write_text(transformed.read_text().replace("0x", "0X", 1))
        result = run_builder(builder, root)
        require(result.returncode == 65 and "module identity" in result.stderr,
                "malformed embedded identity was accepted")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

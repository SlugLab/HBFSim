#!/usr/bin/env python3

import hashlib
import json
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


def main() -> int:
    repository = pathlib.Path(__file__).resolve().parents[2]
    prepare = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else (
        repository / "scripts/calibration/prepare_sm120_exact.py"
    )
    plugin = pathlib.Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else (
        repository / "build-sm120-exact/libptxpass_hbf.so"
    )
    builder = pathlib.Path(sys.argv[3]).resolve() if len(sys.argv) > 3 else (
        repository / "scripts/calibration/build_sm120_artifact.py"
    )
    require(prepare.is_file(), f"preparation command is missing: {prepare}")
    require(plugin.is_file(), f"PTX pass plugin is missing: {plugin}")

    source = prepare.read_text()
    for forbidden in ("nvidia-smi", "nvmlDeviceSet", "set_clock", "validation.status = 'passed'"):
        require(forbidden not in source,
                f"preparation command contains a mutating/false-proof path: {forbidden}")

    with tempfile.TemporaryDirectory(prefix="hbfsim-prepare-exact-") as temp:
        root = pathlib.Path(temp)
        tools = root / "tools"
        tools.mkdir()
        original = repository / "tests/fixtures/ptx/supported.ptx"
        contract = root / "kernel-contract.json"
        contract.write_text(json.dumps({
            "kernel": {
                "block_threads": 256,
                "max_dynamic_shared_bytes": 49152,
                "occupancy_blocks_per_sm": 2,
            }
        }))
        write_tool(tools / "ptxas", r'''
import pathlib, sys
if "--version" in sys.argv:
    print("ptxas: release 13.0, V13.0.88")
    raise SystemExit(0)
out = pathlib.Path(sys.argv[sys.argv.index("--output-file") + 1])
out.write_bytes(b"\x7fELF-HBFSIM-EXACT")
print("ptxas info    : Function properties for kernel", file=sys.stderr)
print("    0 bytes stack frame, 16 bytes spill stores, 8 bytes spill loads", file=sys.stderr)
print("ptxas info    : Used 48 registers", file=sys.stderr)
''')
        write_tool(tools / "nvdisasm", r'''
import sys
if "--version" in sys.argv:
    print("nvdisasm: release 13.0, V13.0.85")
else:
    print("// fake sm_120 sass\n/*0000*/ EXIT;")
''')
        write_tool(tools / "cuobjdump", r'''
import sys
if "--version" in sys.argv:
    print("cuobjdump: release 13.0, V13.0.85")
else:
    print("Resource usage:\n Function kernel:\n  REG:48 STACK:0 SHARED:1024 LOCAL:0")
''')
        write_tool(tools / "ncu", r'''
import sys
if "--version" not in sys.argv:
    raise SystemExit(64)
print("NVIDIA Nsight Compute CLI version 2025.4.1.0")
''')

        output = root / "prepared"
        bundles = root / "bundles"
        command = [
            sys.executable, str(prepare),
            "--original-ptx", str(original),
            "--kernel", "kernel",
            "--kernel-contract", str(contract),
            "--target", "sm_120",
            "--output-dir", str(output),
            "--bundle-root", str(bundles),
            "--ptxpass-plugin", str(plugin),
            "--artifact-builder", str(builder),
            "--ptxas", str(tools / "ptxas"),
            "--nvdisasm", str(tools / "nvdisasm"),
            "--cuobjdump", str(tools / "cuobjdump"),
            "--ncu", str(tools / "ncu"),
            "--expected-cuda-release", "13.0",
        ]
        result = subprocess.run(command, text=True, capture_output=True,
                                check=False)
        require(result.returncode == 0,
                f"preparation failed: {result.stdout}\n{result.stderr}")
        summary = json.loads(result.stdout)
        require(summary["validation_status"] == "pending",
                "preparation claimed completed validation")

        transformed = output / "transformed.ptx"
        manifest_path = output / "pass-manifest.jsonl"
        fragment_path = output / "profile-fragment.json"
        require(transformed.is_file() and manifest_path.is_file() and
                fragment_path.is_file(), "preparation outputs are incomplete")
        manifests = [json.loads(line) for line in manifest_path.read_text().splitlines()]
        require(len(manifests) == 1 and manifests[0]["manifest_schema_version"] == 3,
                "preparation did not preserve schema-v3 pass evidence")
        require(manifests[0]["async_transform_version"] == "sm120-future-v1" and
                manifests[0]["ambiguities"] == [],
                "preparation lost async-future pass evidence")
        require(manifests[0]["aot_required_for_exact"] is True,
                "pass evidence did not require AOT")

        original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
        prepatched = output / "prepatched-ptx" / f"{original_hash}.ptx"
        require(prepatched.read_bytes() == transformed.read_bytes(),
                "prepatched PTX was not content-bound to transformed PTX")
        bundle = pathlib.Path(summary["bundle"])
        require(bundle == bundles / original_hash / "sm_120" and bundle.is_dir(),
                "preparation returned the wrong content-addressed bundle")
        artifact = json.loads((bundle / "artifact.json").read_text())
        fragment = json.loads(fragment_path.read_text())
        require(fragment["fragment_schema_version"] == 1,
                "wrong profile fragment schema")
        require(fragment["validation"] == {"status": "pending"},
                "profile fragment invented validation evidence")
        require(fragment["modules"] == [{
            "module_id": artifact["module_id"],
            "ptx_target": artifact["ptx_target"],
            **artifact["hashes"],
            "kernels": artifact["kernels"],
        }], "profile fragment does not match AOT artifact")
        require(fragment["toolchain"]["cuda_version"] == "13.0",
                "profile fragment lost the CUDA release")
        require("2025.4.1.0" in fragment["toolchain"]["ncu_version"],
                "profile fragment lost Nsight Compute provenance")

        duplicate = subprocess.run(command, text=True, capture_output=True,
                                   check=False)
        require(duplicate.returncode == 66 and "output" in duplicate.stderr,
                "preparation overwrote an existing result")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

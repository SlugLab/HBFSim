#!/usr/bin/env python3

"""Prepare one content-bound SM120 PTX/AOT input for exact calibration."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from typing import NoReturn


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_PASS_OUTPUT_BYTES = 256 * 1024 * 1024
TARGETS = frozenset(("sm_120", "sm_120a", "sm_120f"))
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PrepareError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise PrepareError(64, message)


def fail(code: int, message: str) -> NoReturn:
    raise PrepareError(code, message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_file(value: str, label: str,
                 maximum: int = MAX_INPUT_BYTES) -> pathlib.Path:
    path = pathlib.Path(value)
    try:
        if path.is_symlink():
            fail(66, f"{label} must not be a symlink")
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.R_OK):
            fail(66, f"{label} must be a readable regular file")
        if resolved.stat().st_size > maximum:
            fail(66, f"{label} exceeds the size limit")
        return resolved
    except OSError as error:
        fail(66, f"unable to resolve {label}: {error}")


def executable(value: str, label: str) -> pathlib.Path:
    path = regular_file(value, label)
    if not os.access(path, os.X_OK):
        fail(66, f"{label} is not executable")
    return path


def fresh_output(value: str) -> tuple[pathlib.Path, pathlib.Path]:
    output = pathlib.Path(value)
    if not output.is_absolute():
        fail(64, "output directory must be absolute")
    if output.exists() or output.is_symlink():
        fail(66, f"output directory already exists: {output}")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        parent = output.parent.resolve(strict=True)
        if not parent.is_dir():
            fail(66, "output parent is not a directory")
    except OSError as error:
        fail(66, f"unable to prepare output parent: {error}")
    return output, parent


def safe_directory(value: str, label: str) -> pathlib.Path:
    path = pathlib.Path(value)
    if not path.is_absolute():
        fail(64, f"{label} must be absolute")
    try:
        if path.is_symlink():
            fail(66, f"{label} must not be a symlink")
        path.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            fail(66, f"{label} is not a directory")
        return resolved
    except OSError as error:
        fail(66, f"unable to prepare {label}: {error}")


def read_json(path: pathlib.Path, label: str):
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(70, f"invalid {label}: {error}")


def tool_version(tool: pathlib.Path, label: str) -> str:
    try:
        result = subprocess.run([str(tool), "--version"], text=True,
                                capture_output=True, check=False)
    except OSError as error:
        fail(70, f"unable to execute {label}: {error}")
    version = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or not version:
        fail(70, f"{label} version query failed")
    return version


def invoke_pass(plugin_path: pathlib.Path, original: str, kernel: str,
                manifest_path: pathlib.Path) -> str:
    try:
        plugin = ctypes.CDLL(str(plugin_path), mode=ctypes.RTLD_LOCAL)
        process = plugin.process_input
    except (OSError, AttributeError) as error:
        fail(70, f"unable to load PTX pass plugin: {error}")
    process.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
    process.restype = ctypes.c_int
    request = json.dumps({
        "input": {
            "full_ptx": original,
            "to_patch_kernel": kernel,
            "global_ebpf_map_info_symbol": "map_info",
            "ebpf_communication_data_symbol": "constData",
        },
        "ebpf_instructions": [],
    }, separators=(",", ":")).encode()
    output = ctypes.create_string_buffer(MAX_PASS_OUTPUT_BYTES)
    previous = os.environ.get("HBFSIM_PASS_MANIFEST_PATH")
    os.environ["HBFSIM_PASS_MANIFEST_PATH"] = str(manifest_path)
    try:
        status = process(request, len(output), output)
    finally:
        if previous is None:
            os.environ.pop("HBFSIM_PASS_MANIFEST_PATH", None)
        else:
            os.environ["HBFSIM_PASS_MANIFEST_PATH"] = previous
    if status != 0:
        fail(70, f"PTX pass failed with status {status}")
    try:
        response = json.loads(output.value)
        transformed = response["output_ptx"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        fail(70, f"PTX pass returned an invalid response: {error}")
    if not isinstance(transformed, str) or not transformed:
        fail(70, "PTX pass returned empty transformed PTX")
    return transformed


def validate_manifest(path: pathlib.Path, original_hash: str,
                      transformed_hash: str, kernel: str,
                      target: str) -> None:
    try:
        records = [json.loads(line) for line in path.read_text().splitlines()
                   if line.strip()]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(70, f"invalid PTX pass manifest: {error}")
    if len(records) != 1:
        fail(70, "PTX pass must emit exactly one manifest record")
    record = records[0]
    expected = {
        "manifest_schema_version": 2,
        "module_id": "ptx:sha256:" + original_hash,
        "original_ptx_sha256": original_hash,
        "transformed_ptx_sha256": transformed_hash,
        "aot_required_for_exact": True,
        "kernel": kernel,
        "ptx_target": target,
        "instrumented": True,
        "cubin_only": False,
    }
    for key, value in expected.items():
        if record.get(key) != value:
            fail(70, f"PTX pass manifest has invalid {key}")


def invoke_builder(args: argparse.Namespace, original: pathlib.Path,
                   transformed: pathlib.Path, manifest: pathlib.Path,
                   bundle_root: pathlib.Path) -> pathlib.Path:
    command = [
        sys.executable, str(args.artifact_builder),
        "--original-ptx", str(original),
        "--transformed-ptx", str(transformed),
        "--pass-manifest", str(manifest),
        "--kernel-contract", str(args.kernel_contract),
        "--target", args.target,
        "--bundle-root", str(bundle_root),
        "--ptxas", str(args.ptxas),
        "--nvdisasm", str(args.nvdisasm),
        "--cuobjdump", str(args.cuobjdump),
        "--expected-cuda-release", args.expected_cuda_release,
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True,
                                check=False)
    except OSError as error:
        fail(70, f"unable to invoke artifact builder: {error}")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        code = 66 if result.returncode == 66 else 70
        fail(code, f"artifact builder failed: {detail}")
    try:
        response = json.loads(result.stdout)
        bundle = pathlib.Path(response["bundle"]).resolve(strict=True)
    except (json.JSONDecodeError, KeyError, OSError) as error:
        fail(70, f"artifact builder returned an invalid result: {error}")
    return bundle


def write_json(path: pathlib.Path, value: object) -> None:
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    try:
        with path.open("xb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        fail(70, f"unable to write {path.name}: {error}")


def parser() -> argparse.ArgumentParser:
    result = Parser(description=__doc__)
    result.add_argument("--original-ptx", required=True)
    result.add_argument("--kernel", required=True)
    result.add_argument("--kernel-contract", required=True)
    result.add_argument("--target", required=True, choices=sorted(TARGETS))
    result.add_argument("--output-dir", required=True)
    result.add_argument("--bundle-root", required=True)
    result.add_argument("--ptxpass-plugin", required=True)
    result.add_argument("--artifact-builder", required=True)
    result.add_argument("--ptxas", required=True)
    result.add_argument("--nvdisasm", required=True)
    result.add_argument("--cuobjdump", required=True)
    result.add_argument("--ncu", required=True)
    result.add_argument("--expected-cuda-release", default="13.0")
    return result


def prepare(args: argparse.Namespace) -> dict[str, object]:
    if not args.kernel or not re.fullmatch(r"[A-Za-z0-9_.$]+", args.kernel):
        fail(64, "invalid kernel name")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", args.expected_cuda_release):
        fail(64, "invalid expected CUDA release")
    original = regular_file(args.original_ptx, "original PTX")
    args.kernel_contract = regular_file(args.kernel_contract, "kernel contract")
    args.ptxpass_plugin = regular_file(args.ptxpass_plugin, "PTX pass plugin")
    args.artifact_builder = regular_file(args.artifact_builder, "artifact builder")
    args.ptxas = executable(args.ptxas, "ptxas")
    args.nvdisasm = executable(args.nvdisasm, "nvdisasm")
    args.cuobjdump = executable(args.cuobjdump, "cuobjdump")
    args.ncu = executable(args.ncu, "ncu")
    output, output_parent = fresh_output(args.output_dir)
    bundle_root = safe_directory(args.bundle_root, "bundle root")
    try:
        original_bytes = original.read_bytes()
        original_text = original_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(64, f"invalid original PTX: {error}")
    if not original_bytes:
        fail(64, "original PTX must not be empty")
    original_hash = sha256_bytes(original_bytes)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".hbfsim-exact-",
                                              dir=output_parent))
    try:
        original_copy = temporary / "original.ptx"
        transformed_path = temporary / "transformed.ptx"
        manifest_path = temporary / "pass-manifest.jsonl"
        original_copy.write_bytes(original_bytes)
        transformed = invoke_pass(args.ptxpass_plugin, original_text,
                                  args.kernel, manifest_path)
        transformed_path.write_text(transformed)
        transformed_hash = sha256_bytes(transformed.encode())
        validate_manifest(manifest_path, original_hash, transformed_hash,
                          args.kernel, args.target)
        ncu_version = tool_version(args.ncu, "ncu")
        bundle = invoke_builder(args, original_copy, transformed_path,
                                manifest_path, bundle_root)
        artifact = read_json(bundle / "artifact.json", "artifact manifest")
        hashes = artifact.get("hashes", {})
        if (artifact.get("module_id") != "ptx:sha256:" + original_hash or
                hashes.get("transformed_ptx_sha256") != transformed_hash):
            fail(70, "artifact identity does not match prepared PTX")
        module = {
            "module_id": artifact["module_id"],
            "ptx_target": artifact["ptx_target"],
            **hashes,
            "kernels": artifact["kernels"],
        }
        fragment = {
            "fragment_schema_version": 1,
            "toolchain": {
                "cuda_version": artifact["toolchain"]["cuda_release"],
                "ptxas_version": artifact["toolchain"]["ptxas_version"],
                "nvdisasm_version": artifact["toolchain"]["nvdisasm_version"],
                "cuobjdump_version": artifact["toolchain"]["cuobjdump_version"],
                "ncu_version": ncu_version,
            },
            "modules": [module],
            "validation": {"status": "pending"},
            "provenance": {
                "pass_manifest_sha256": sha256_bytes(manifest_path.read_bytes()),
                "bundle": str(bundle),
            },
        }
        prepatched = temporary / "prepatched-ptx"
        prepatched.mkdir()
        (prepatched / f"{original_hash}.ptx").write_bytes(
            transformed_path.read_bytes())
        write_json(temporary / "profile-fragment.json", fragment)
        os.rename(temporary, output)
        return {
            "bundle": str(bundle),
            "output_dir": str(output),
            "pass_manifest": str(output / "pass-manifest.jsonl"),
            "prepatched_ptx_dir": str(output / "prepatched-ptx"),
            "profile_fragment": str(output / "profile-fragment.json"),
            "validation_status": "pending",
        }
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def main() -> int:
    try:
        args = parser().parse_args()
        print(json.dumps(prepare(args), sort_keys=True))
        return 0
    except PrepareError as error:
        print(f"prepare_sm120_exact: {error}", file=sys.stderr)
        return error.code
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"prepare_sm120_exact: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())

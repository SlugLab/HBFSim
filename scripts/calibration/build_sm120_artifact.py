#!/usr/bin/env python3

"""Build and verify content-addressed HBFSim SM120 AOT artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import NoReturn


TARGETS = frozenset(("sm_120", "sm_120a", "sm_120f"))
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_CUBIN_BYTES = 1024 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MODULE_ID_RE = re.compile(r"^ptx:sha256:([0-9a-f]{64})$")
IDENTITY_RE = re.compile(
    r"\.visible \.const \.align 8 \.b8 "
    r"__hbfsim_module_identity\[32\] = \{"
    r"(0x[0-9a-f]{2}(?:, 0x[0-9a-f]{2}){31})\};"
)


class BuildError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def fail(code: int, message: str) -> NoReturn:
    raise BuildError(code, message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_file(path_text: str, label: str, maximum: int = MAX_MANIFEST_BYTES) -> pathlib.Path:
    path = pathlib.Path(path_text)
    try:
        if path.is_symlink():
            fail(66, f"{label} must not be a symlink")
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            fail(66, f"{label} is not a regular file")
        if resolved.stat().st_size > maximum:
            fail(66, f"{label} exceeds the size limit")
        return resolved
    except OSError as error:
        fail(66, f"unable to resolve {label}: {error}")


def executable(path_text: str, label: str) -> pathlib.Path:
    path = regular_file(path_text, label)
    if not os.access(path, os.X_OK):
        fail(66, f"{label} is not executable")
    return path


def parse_json_file(path: pathlib.Path, label: str):
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(65, f"invalid {label}: {error}")


def tool_version(tool: pathlib.Path, label: str, expected_release: str) -> str:
    try:
        result = subprocess.run([str(tool), "--version"], text=True,
                                capture_output=True, check=False)
    except OSError as error:
        fail(70, f"unable to execute {label}: {error}")
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or not output:
        fail(70, f"{label} version query failed")
    release = re.search(r"release\s+([0-9]+\.[0-9]+)", output)
    if release is None or release.group(1) != expected_release:
        fail(64, f"{label} CUDA release does not match {expected_release}")
    return output


def embedded_identity(transformed: str) -> str:
    matches = list(IDENTITY_RE.finditer(transformed))
    if len(matches) != 1 or transformed.count("__hbfsim_module_identity") != 1:
        fail(65, "transformed PTX has an invalid module identity declaration")
    return "".join(byte[2:] for byte in matches[0].group(1).split(", "))


def pass_manifest(path: pathlib.Path, module_id: str, target: str) -> list[str]:
    records = []
    try:
        for line in path.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(65, f"invalid pass manifest: {error}")
    matches = [record for record in records if record.get("module_id") == module_id]
    if not matches:
        fail(65, "pass manifest has no record for the module identity")
    kernels: list[str] = []
    for record in matches:
        if record.get("ptx_target") != target:
            fail(65, "pass manifest target does not match requested target")
        if record.get("instrumented") is not True:
            fail(65, "pass manifest module is not fully instrumented")
        kernel = record.get("kernel")
        if not isinstance(kernel, str) or not kernel:
            fail(65, "pass manifest has an invalid kernel name")
        if kernel in kernels:
            fail(65, "pass manifest contains a duplicate kernel record")
        kernels.append(kernel)
    return kernels


@dataclass(frozen=True)
class KernelContract:
    block_threads: int
    max_dynamic_shared_bytes: int
    occupancy_blocks_per_sm: int


def kernel_contracts(path: pathlib.Path, kernels: list[str]) -> dict[str, KernelContract]:
    root = parse_json_file(path, "kernel contract")
    if not isinstance(root, dict) or set(root) != set(kernels):
        fail(65, "kernel contract names do not match pass manifest")
    result = {}
    for kernel in kernels:
        value = root[kernel]
        required = {"block_threads", "max_dynamic_shared_bytes",
                    "occupancy_blocks_per_sm"}
        if not isinstance(value, dict) or set(value) != required:
            fail(65, f"kernel contract for {kernel} has invalid fields")
        block = value["block_threads"]
        dynamic = value["max_dynamic_shared_bytes"]
        occupancy = value["occupancy_blocks_per_sm"]
        if (not isinstance(block, int) or isinstance(block, bool) or block <= 0 or
                block > 1024 or not isinstance(dynamic, int) or
                isinstance(dynamic, bool) or dynamic < 0 or
                not isinstance(occupancy, int) or isinstance(occupancy, bool) or
                occupancy <= 0):
            fail(65, f"kernel contract for {kernel} has invalid values")
        result[kernel] = KernelContract(block, dynamic, occupancy)
    return result


def run_tool(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, text=True, capture_output=True,
                                check=False)
    except OSError as error:
        fail(70, f"unable to execute {label}: {error}")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        fail(70, f"{label} failed: {detail}")
    return result


def ptxas_resources(log: str, kernels: list[str]) -> dict[str, dict[str, int]]:
    starts = list(re.finditer(r"Function properties for\s+([^\s]+)", log))
    result: dict[str, dict[str, int]] = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(log)
        section = log[match.end():end]
        spills = re.search(
            r"([0-9]+) bytes spill stores,\s*([0-9]+) bytes spill loads",
            section)
        registers = re.search(r"Used\s+([0-9]+) registers", section)
        if spills is None or registers is None:
            fail(70, f"ptxas resource output is incomplete for {match.group(1)}")
        result[match.group(1)] = {
            "registers": int(registers.group(1)),
            "spill_store_bytes": int(spills.group(1)),
            "spill_load_bytes": int(spills.group(2)),
        }
    if set(result) != set(kernels):
        fail(70, "ptxas resource records do not match pass manifest")
    return result


def cuobjdump_resources(output: str, kernels: list[str]) -> dict[str, int]:
    starts = list(re.finditer(r"Function\s+([^:\s]+):", output))
    result: dict[str, int] = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(output)
        section = output[match.end():end]
        shared = re.search(r"\bSHARED:([0-9]+)\b", section)
        registers = re.search(r"\bREG:([0-9]+)\b", section)
        if shared is None or registers is None:
            fail(70, f"cuobjdump resource output is incomplete for {match.group(1)}")
        result[match.group(1)] = int(shared.group(1))
    if set(result) != set(kernels):
        fail(70, "cuobjdump resource records do not match pass manifest")
    return result


def durable_write(path: pathlib.Path, data: bytes) -> None:
    with path.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def verify_bundle(path_text: str) -> None:
    bundle = pathlib.Path(path_text)
    try:
        if bundle.is_symlink():
            fail(66, "bundle must not be a symlink")
        bundle = bundle.resolve(strict=True)
    except OSError as error:
        fail(66, f"unable to resolve bundle: {error}")
    expected_files = {"original.ptx", "transformed.ptx", "module.cubin",
                      "module.sass", "artifact.json"}
    if not bundle.is_dir() or {item.name for item in bundle.iterdir()} != expected_files:
        fail(65, "bundle layout is invalid")
    artifact = parse_json_file(bundle / "artifact.json", "artifact manifest")
    hashes = artifact.get("hashes")
    if not isinstance(hashes, dict):
        fail(65, "artifact hashes are missing")
    mapping = {
        "original_ptx_sha256": "original.ptx",
        "transformed_ptx_sha256": "transformed.ptx",
        "cubin_sha256": "module.cubin",
        "sass_sha256": "module.sass",
    }
    for field, filename in mapping.items():
        expected = hashes.get(field)
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            fail(65, f"artifact {field} is invalid")
        if sha256_bytes((bundle / filename).read_bytes()) != expected:
            fail(65, f"artifact {field} mismatch")
    original_digest = hashes["original_ptx_sha256"]
    target = artifact.get("ptx_target")
    if bundle.parent.name != original_digest or bundle.name != target:
        fail(65, "bundle path does not match artifact identity")


def build(args: argparse.Namespace) -> pathlib.Path:
    if args.target not in TARGETS:
        fail(64, f"unsupported target: {args.target}")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", args.expected_cuda_release):
        fail(64, "invalid expected CUDA release")
    original_path = regular_file(args.original_ptx, "original PTX")
    transformed_path = regular_file(args.transformed_ptx, "transformed PTX")
    manifest_path = regular_file(args.pass_manifest, "pass manifest")
    contract_path = regular_file(args.kernel_contract, "kernel contract")
    ptxas = executable(args.ptxas, "ptxas")
    nvdisasm = executable(args.nvdisasm, "nvdisasm")
    cuobjdump = executable(args.cuobjdump, "cuobjdump")
    versions = {
        "ptxas_version": tool_version(ptxas, "ptxas", args.expected_cuda_release),
        "nvdisasm_version": tool_version(nvdisasm, "nvdisasm", args.expected_cuda_release),
        "cuobjdump_version": tool_version(cuobjdump, "cuobjdump", args.expected_cuda_release),
        "cuda_release": args.expected_cuda_release,
    }
    original = original_path.read_bytes()
    transformed = transformed_path.read_bytes()
    if not original or not transformed:
        fail(65, "PTX inputs must not be empty")
    try:
        transformed_text = transformed.decode("utf-8")
    except UnicodeDecodeError as error:
        fail(65, f"transformed PTX is not UTF-8: {error}")
    original_digest = sha256_bytes(original)
    identity = embedded_identity(transformed_text)
    if identity != original_digest:
        fail(65, "embedded module identity does not match original PTX")
    module_id = "ptx:sha256:" + identity
    kernels = pass_manifest(manifest_path, module_id, args.target)
    contracts = kernel_contracts(contract_path, kernels)

    bundle_root = pathlib.Path(args.bundle_root).resolve(strict=False)
    final_parent = bundle_root / original_digest
    final = final_parent / args.target
    if final.exists() or final.is_symlink():
        fail(66, f"bundle already exists: {final}")
    final_parent.mkdir(parents=True, exist_ok=True)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=f".{args.target}.",
                                              dir=final_parent))
    try:
        transformed_copy = temporary / "transformed.ptx"
        original_copy = temporary / "original.ptx"
        cubin = temporary / "module.cubin"
        sass = temporary / "module.sass"
        durable_write(original_copy, original)
        durable_write(transformed_copy, transformed)
        compile_result = run_tool(
            [str(ptxas), f"--gpu-name={args.target}", "--verbose",
             "--output-file", str(cubin), str(transformed_copy)], "ptxas")
        if not cubin.is_file() or cubin.stat().st_size == 0 or cubin.stat().st_size > MAX_CUBIN_BYTES:
            fail(70, "ptxas failed to produce a bounded cubin")
        with cubin.open("rb") as input_file:
            os.fsync(input_file.fileno())
        ptx_resources = ptxas_resources(
            compile_result.stdout + compile_result.stderr, kernels)
        disassembly = run_tool(
            [str(nvdisasm), "--print-code", "--print-raw", str(cubin)],
            "nvdisasm").stdout.encode()
        if not disassembly.strip():
            fail(70, "nvdisasm produced empty SASS")
        durable_write(sass, disassembly)
        object_resources = cuobjdump_resources(
            run_tool([str(cuobjdump), "--dump-resource-usage", str(cubin)],
                     "cuobjdump").stdout,
            kernels)
        resource_records = []
        for kernel in sorted(kernels):
            contract = contracts[kernel]
            record = {
                "name": kernel,
                **ptx_resources[kernel],
                "static_shared_bytes": object_resources[kernel],
                "max_dynamic_shared_bytes": contract.max_dynamic_shared_bytes,
                "block_threads": contract.block_threads,
                "occupancy_blocks_per_sm": contract.occupancy_blocks_per_sm,
            }
            resource_records.append(record)
        artifact = {
            "schema_version": 1,
            "module_id": module_id,
            "ptx_target": args.target,
            "toolchain": versions,
            "hashes": {
                "original_ptx_sha256": original_digest,
                "transformed_ptx_sha256": sha256_bytes(transformed),
                "cubin_sha256": sha256_bytes(cubin.read_bytes()),
                "sass_sha256": sha256_bytes(disassembly),
            },
            "kernels": resource_records,
        }
        durable_write(temporary / "artifact.json",
                      (json.dumps(artifact, sort_keys=True, indent=2) + "\n").encode())
        os.rename(temporary, final)
        parent_fd = os.open(final_parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        verify_bundle(str(final))
        return final
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--verify-bundle")
    result.add_argument("--original-ptx")
    result.add_argument("--transformed-ptx")
    result.add_argument("--pass-manifest")
    result.add_argument("--kernel-contract")
    result.add_argument("--target")
    result.add_argument("--bundle-root")
    result.add_argument("--ptxas")
    result.add_argument("--nvdisasm")
    result.add_argument("--cuobjdump")
    result.add_argument("--expected-cuda-release", default="13.0")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.verify_bundle:
            build_fields = (args.original_ptx, args.transformed_ptx,
                            args.pass_manifest, args.kernel_contract,
                            args.target, args.bundle_root, args.ptxas,
                            args.nvdisasm, args.cuobjdump)
            if any(build_fields):
                fail(64, "--verify-bundle cannot be combined with build options")
            verify_bundle(args.verify_bundle)
            return 0
        required = ("original_ptx", "transformed_ptx", "pass_manifest",
                    "kernel_contract", "target", "bundle_root", "ptxas",
                    "nvdisasm", "cuobjdump")
        missing = [name for name in required if not getattr(args, name)]
        if missing:
            fail(64, "missing build options: " + ", ".join(missing))
        final = build(args)
        print(json.dumps({"bundle": str(final)}, sort_keys=True))
        return 0
    except BuildError as error:
        print(f"build_sm120_artifact: {error}", file=sys.stderr)
        return error.code
    except (OSError, ValueError) as error:
        print(f"build_sm120_artifact: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Merge immutable module-bound SM120 Stage 1 fragments for one workload."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import sys
import uuid


USAGE = (
    "merge_sm120_fragments.py --fragment FILE [--fragment FILE ...] "
    "--output-dir DIR"
)


class InputError(Exception):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def parse(arguments: list[str]) -> tuple[list[str], str]:
    fragments = []
    output = ""
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if index + 1 >= len(arguments):
            raise ValueError(USAGE)
        value = arguments[index + 1]
        if option == "--fragment" and value:
            fragments.append(value)
        elif option == "--output-dir" and value and not output:
            output = value
        else:
            raise ValueError(USAGE)
        index += 2
    if not fragments or not output:
        raise ValueError(USAGE)
    return fragments, output


def regular_file(value: str, label: str) -> pathlib.Path:
    path = pathlib.Path(value)
    if path.is_symlink() or not path.is_file():
        raise InputError(f"{label} must be a regular non-symlink: {path}")
    return path.resolve()


def regular_directory(value: str, label: str) -> pathlib.Path:
    path = pathlib.Path(value)
    if path.is_symlink() or not path.is_dir():
        raise InputError(f"{label} must be a directory non-symlink: {path}")
    return path.resolve()


def output_path(value: str) -> pathlib.Path:
    path = pathlib.Path(value).absolute()
    if path.exists() or path.is_symlink():
        raise InputError(f"output already exists: {path}")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise InputError(f"output parent is unsafe: {path.parent}")
    return path


def module_identity(module: object) -> tuple[str, str, str]:
    if not isinstance(module, dict):
        raise InputError("fragment module is malformed")
    original = module.get("original_ptx_sha256")
    transformed = module.get("transformed_ptx_sha256")
    target = module.get("ptx_target")
    if not isinstance(original, str) or len(original) != 64 or \
            not isinstance(transformed, str) or len(transformed) != 64 or \
            not isinstance(target, str) or target not in {
                "sm_120", "sm_120a", "sm_120f"} or \
            module.get("module_id") != f"ptx:sha256:{original}":
        raise InputError("fragment module identity is invalid")
    for name in ("cubin_sha256", "sass_sha256"):
        value = module.get(name)
        if not isinstance(value, str) or len(value) != 64:
            raise InputError(f"fragment module {name} is invalid")
    kernels = module.get("kernels")
    if not isinstance(kernels, list) or not kernels:
        raise InputError("fragment module kernel evidence is missing")
    return original, transformed, target


def load_fragment(path: pathlib.Path) -> dict:
    try:
        document = json.loads(path.read_bytes())
    except (json.JSONDecodeError, OSError) as error:
        raise InputError(f"fragment is not valid JSON: {path}") from error
    if not isinstance(document, dict) or \
            document.get("fragment_schema_version") != 1 or \
            document.get("validation") != {"status": "pending"}:
        raise InputError(f"fragment schema/status is invalid: {path}")
    toolchain = document.get("toolchain")
    modules = document.get("modules")
    provenance = document.get("provenance")
    if not isinstance(toolchain, dict) or not toolchain or \
            not isinstance(modules, list) or not modules or \
            not isinstance(provenance, dict) or \
            set(provenance) != {"pass_manifest_sha256", "bundle"}:
        raise InputError(f"fragment evidence is incomplete: {path}")
    pass_path = regular_file(str(path.parent / "pass-manifest.jsonl"),
                             "pass manifest")
    pass_bytes = pass_path.read_bytes()
    if sha(pass_bytes) != provenance.get("pass_manifest_sha256"):
        raise InputError(f"pass manifest hash mismatch: {path}")
    try:
        manifests = [json.loads(line) for line in pass_bytes.splitlines()
                     if line.strip()]
    except json.JSONDecodeError as error:
        raise InputError(f"pass manifest is malformed: {pass_path}") from error
    if not manifests:
        raise InputError(f"pass manifest is empty: {pass_path}")
    module_ids = set()
    identities = []
    for module in modules:
        original, transformed, target = module_identity(module)
        identities.append((original, transformed, target))
        module_ids.add(module["module_id"])
    covered = set()
    for manifest in manifests:
        if not isinstance(manifest, dict) or \
                manifest.get("manifest_schema_version", 0) < 3 or \
                manifest.get("aot_required_for_exact") is not True or \
                manifest.get("unsupported_instructions") != 0 or \
                manifest.get("module_id") not in module_ids:
            raise InputError(f"pass manifest lacks exact evidence: {pass_path}")
        covered.add(manifest["module_id"])
    if covered != module_ids:
        raise InputError(f"pass manifest module coverage mismatch: {pass_path}")
    prepatched = regular_directory(str(path.parent / "prepatched-ptx"),
                                   "prepatched PTX directory")
    bundle = regular_directory(str(provenance["bundle"]), "bundle")
    if len(bundle.parents) < 2:
        raise InputError(f"bundle layout is invalid: {bundle}")
    bundle_root = bundle.parents[1]
    for original, transformed, target in identities:
        member = regular_file(str(prepatched / f"{original}.ptx"),
                              "prepatched PTX")
        if sha(member.read_bytes()) != transformed:
            raise InputError(f"prepatched PTX hash mismatch: {member}")
        expected_bundle = bundle_root / original / target
        regular_directory(str(expected_bundle), "module bundle")
    return {"document": document, "manifests": manifests,
            "prepatched": prepatched, "bundle_root": bundle_root}


def merge(fragment_paths: list[pathlib.Path], output: pathlib.Path) -> dict:
    loaded = [load_fragment(path) for path in fragment_paths]
    toolchain = loaded[0]["document"]["toolchain"]
    bundle_root = loaded[0]["bundle_root"]
    if any(item["document"]["toolchain"] != toolchain for item in loaded):
        raise InputError("fragment toolchains differ")
    if any(item["bundle_root"] != bundle_root for item in loaded):
        raise InputError("fragment bundle roots differ")
    modules: dict[str, dict] = {}
    manifests: dict[bytes, dict] = {}
    sources: dict[str, pathlib.Path] = {}
    for item in loaded:
        for module in item["document"]["modules"]:
            original, _, _ = module_identity(module)
            prior = modules.get(original)
            if prior is not None and prior != module:
                raise InputError(f"conflicting duplicate module: {original}")
            modules[original] = module
            sources[original] = item["prepatched"] / f"{original}.ptx"
        for manifest in item["manifests"]:
            manifests[canonical(manifest)] = manifest
    ordered_modules = [modules[key] for key in sorted(modules)]
    ordered_manifests = sorted(
        manifests.values(),
        key=lambda item: (str(item.get("module_id", "")),
                          str(item.get("kernel", "")), canonical(item)))
    manifest_bytes = b"".join(canonical(item) + b"\n"
                              for item in ordered_manifests)
    first = ordered_modules[0]
    first_bundle = (bundle_root / first["original_ptx_sha256"] /
                    first["ptx_target"])
    document = {
        "fragment_schema_version": 1,
        "toolchain": toolchain,
        "modules": ordered_modules,
        "validation": {"status": "pending"},
        "provenance": {
            "pass_manifest_sha256": sha(manifest_bytes),
            "bundle": str(first_bundle.resolve()),
        },
    }
    partial = output.parent / f".{output.name}.partial-{uuid.uuid4().hex}"
    try:
        partial.mkdir(mode=0o700)
        prepatched = partial / "prepatched-ptx"
        prepatched.mkdir()
        for original in sorted(sources):
            source = regular_file(str(sources[original]), "prepatched PTX")
            shutil.copyfile(source, prepatched / f"{original}.ptx")
        (partial / "pass-manifest.jsonl").write_bytes(manifest_bytes)
        (partial / "profile-fragment.json").write_bytes(
            json.dumps(document, indent=2, sort_keys=True).encode() + b"\n")
        os.replace(partial, output)
    except Exception:
        if partial.exists() and partial.name.startswith(f".{output.name}.partial-"):
            shutil.rmtree(partial)
        raise
    return {"status": "passed", "output": str(output),
            "module_count": len(ordered_modules),
            "pass_manifest_sha256": sha(manifest_bytes),
            "validation_status": "pending"}


def main(arguments: list[str]) -> int:
    try:
        fragment_values, output_value = parse(arguments)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 64
    try:
        paths = [regular_file(value, "fragment") for value in fragment_values]
        if len(paths) != len(set(paths)):
            raise InputError("duplicate fragment input")
        output = output_path(output_value)
        result = merge(paths, output)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (InputError, json.JSONDecodeError, KeyError, OSError,
            TypeError, ValueError) as error:
        print(f"merge_sm120_fragments: {error}", file=sys.stderr)
        return 66
    except Exception as error:
        print(f"merge_sm120_fragments: internal error: {error}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

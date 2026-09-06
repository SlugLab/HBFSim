#!/usr/bin/env python3
"""Closed, isolated entry point for one parent-owned provisional HF arm.

Importing this module uses only the standard library and has no filesystem,
package, accelerator, or inference side effects.  The CLI accepts only the
parent-created wire and ownership record; it exposes no runtime injection.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys


ROOT = Path(__file__).resolve().parents[2]
PINNED_PYTHON = "/opt/miniconda3/bin/python3.13"
PINNED_SITE = Path("/opt/miniconda3/lib/python3.13/site-packages")
ARMS = ("native", "capture", "repeat")
MAX_ENVELOPE = 1 << 20
MAX_BLOBS = 512
MAX_TRANSPORT = 320 << 20
MAX_COMPONENT = 16 << 20
MAX_WIRE_COMPONENT = 64 << 20
PROJECT_MAX_FILE = 2 << 20
PROJECT_MAX_TOTAL = 16 << 20
PLATFORM_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ")
FIXED_ENV = {
    "CUDA_VISIBLE_DEVICES": "0",
    "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
    "PYTORCH_NVML_BASED_CUDA_CHECK": "1",
    "TORCHINDUCTOR_COMPILE_THREADS": "1",
    "VLLM_PLUGINS": "",
    "TVM_FFI_DISABLE_TORCH_C_DLPACK": "1",
    "VLLM_ENABLE_V1_MULTIPROCESSING": "0",
    "VLLM_USE_FLASHINFER_MOE_FP16": "0",
    "VLLM_USE_FLASHINFER_SAMPLER": "0",
    "VLLM_NO_USAGE_STATS": "1",
    "VLLM_DO_NOT_TRACK": "1",
    "DO_NOT_TRACK": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "TOKENIZERS_PARALLELISM": "false",
    "FLASHINFER_DISABLE_JIT": "1",
    "FLASHINFER_LOGLEVEL": "0",
    "FLASHINFER_LOGDEST": "stderr",
    "FLASHINFER_AUTOTUNER_LOAD_FROM_FILE": "0",
}
CACHE_ENV = {
    "XDG_CACHE_HOME": "cache",
    "VLLM_CACHE_ROOT": "cache/vllm",
    "VLLM_CONFIG_ROOT": "config/vllm",
    "VLLM_ASSETS_CACHE": "cache/vllm/assets",
    "HF_HOME": "cache/hf",
    "HF_HUB_CACHE": "cache/hf/hub",
    "HF_XET_CACHE": "cache/hf/xet",
    "HF_MODULES_CACHE": "cache/hf/modules",
    "TORCH_HOME": "cache/torch",
    "TORCH_EXTENSIONS_DIR": "cache/torch-extensions",
    "TORCHINDUCTOR_CACHE_DIR": "cache/inductor",
    "TVM_FFI_CACHE_DIR": "cache/tvm-ffi",
    "TRITON_HOME": "cache/triton-home",
    "TRITON_CACHE_DIR": "cache/triton",
    "TRITON_DUMP_DIR": "cache/triton-dump",
    "TRITON_OVERRIDE_DIR": "cache/triton-override",
    "FLASHINFER_WORKSPACE_BASE": "cache/flashinfer-workspace",
    "CUDA_CACHE_PATH": "cache/cuda",
    "TMPDIR": "tmp",
}
PROJECT_FILES = tuple(
    """
scripts/eval/hf_owned_worker.py
scripts/eval/hf_routing_runner.py
scripts/eval/hf_loaded_arm.py
scripts/eval/hf_runtime_sources.py
scripts/eval/hf_startup_sources.py
scripts/eval/hf_moe_tuning.py
scripts/eval/hf_moe_tuning_runtime.py
scripts/eval/hf_runtime_imports.py
scripts/eval/evaluation_inventory.py
scripts/eval/inventory_checkpoint.py
scripts/eval/verify_hf_metadata.py
scripts/eval/run_manifest.py
scripts/eval/run_matrix.py
scripts/eval/resource_guard.py
scripts/eval/export_results.py
scripts/eval/validate_results.py
adapters/vllm_capacity/hf_routing_worker.py
adapters/vllm_capacity/hf_owned_routes.py
adapters/vllm_capacity/hf_runtime_contract.py
adapters/vllm_capacity/routed_capture_compat.py
adapters/vllm_capacity/trace_collector.py
adapters/vllm_capacity/model_inventory.py
""".split()
)
RUNTIME_CONTRACT_MODULES = (
    "vllm.entrypoints.llm",
    "vllm.v1.engine.llm_engine",
    "vllm.v1.engine.core_client",
    "vllm.v1.engine.core",
    "vllm.v1.executor.uniproc_executor",
    "vllm.v1.worker.gpu_worker",
    "vllm.v1.worker.gpu_model_runner",
    "vllm.model_executor.models.qwen3_moe",
    "vllm.v1.sample.ops.topk_topp_sampler",
    "vllm.v1.attention.backends.triton_attn",
    "vllm.v1.attention.backends.registry",
    "vllm.attention.layer",
    "vllm.model_executor.layers.fused_moe.shared_fused_moe",
    "vllm.model_executor.layers.fused_moe.unquantized_fused_moe_method",
    "vllm.model_executor.layers.fused_moe.oracle.unquantized",
    "vllm.model_executor.layers.fused_moe.modular_kernel",
    "vllm.model_executor.layers.fused_moe.prepare_finalize",
    "vllm.model_executor.layers.fused_moe.fused_moe",
    "vllm.config.compilation",
    "vllm.config.vllm",
    "vllm.v1.kv_cache_interface",
    "torch",
)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if type(key) is not str or key in result:
                raise ValueError("wire JSON contains a duplicate/non-string key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("wire JSON contains a non-finite constant")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("wire JSON is invalid") from error


def _identity(info):
    return [
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    ]


def _directory_identity(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.resolve() != path:
        absolute = False
    else:
        absolute = path.is_absolute()
    if not absolute:
        raise ValueError("directory must be canonical, absolute, and non-symlink")
    return [info.st_dev, info.st_ino]


def _path_binding(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("path must be absolute")
    rows = []
    for component in reversed(path.parents):
        if component == Path("/"):
            continue
        info = component.lstat()
        if not stat.S_ISDIR(info.st_mode) or component.resolve() != component:
            raise ValueError("path ancestor is not a canonical directory")
        rows.append([str(component), info.st_dev, info.st_ino])
    return rows


def _add_blob(rows, blobs, logical, raw, limit=MAX_COMPONENT):
    if (
        type(logical) is not str
        or logical in {r["logical_name"] for r in rows}
        or type(raw) is not bytes
    ):
        raise ValueError("invalid/duplicate wire blob")
    if len(raw) > limit:
        raise ValueError("wire component exceeds fixed bound")
    ordinal = f"payload-{len(rows):04d}.bin"
    rows.append(
        dict(
            logical_name=logical,
            ordinal_name=ordinal,
            length=len(raw),
            sha256=_digest(raw),
        )
    )
    blobs[ordinal] = raw
    return logical


def _artifact_refs(prefix, artifacts, rows, blobs):
    if type(artifacts) is not tuple:
        raise ValueError("snapshot artifacts must be an exact tuple")
    result = []
    seen = set()
    for entry in artifacts:
        if (
            type(entry) is not tuple
            or len(entry) != 2
            or type(entry[0]) is not str
            or entry[0] in seen
        ):
            raise ValueError("invalid snapshot artifact tuple")
        seen.add(entry[0])
        logical = f"{prefix}.artifact.{len(result):04d}"
        limit = (
            (32 << 20)
            if prefix == "metadata" and entry[0] == "donor.json"
            else MAX_COMPONENT
        )
        _add_blob(rows, blobs, logical, entry[1], limit)
        result.append([entry[0], logical])
    return result


def _encode_wire(metadata, runtime, startup, tuning, request):
    """Encode exact snapshot bytes.  Snapshot paths are labels, never blob paths."""
    rows = []
    blobs = {}
    mr = _add_blob(rows, blobs, "metadata.receipt", metadata.receipt_bytes)
    mc = _add_blob(rows, blobs, "metadata.complete", metadata.complete_bytes, 4096)
    ma = _artifact_refs("metadata", metadata.artifacts, rows, blobs)
    rr = _add_blob(rows, blobs, "runtime.manifest", runtime.manifest_bytes, 8 << 20)
    ra = _artifact_refs("runtime", runtime.artifacts, rows, blobs)
    sr = _add_blob(rows, blobs, "startup.manifest", startup.manifest_bytes, 8 << 20)
    sa = _artifact_refs("startup", startup.artifacts, rows, blobs)
    tr = _add_blob(rows, blobs, "tuning.manifest", tuning.manifest_bytes, 1 << 20)
    tc = (
        None
        if tuning.config_bytes is None
        else _add_blob(rows, blobs, "tuning.config", tuning.config_bytes, 1 << 20)
    )
    if len(rows) > MAX_BLOBS or sum(r["length"] for r in rows) > MAX_TRANSPORT:
        raise ValueError("wire exceeds fixed item/byte bounds")
    envelope = dict(
        schema_version=1,
        request=request,
        blobs=rows,
        snapshots=dict(
            metadata=dict(
                bundle=str(metadata.bundle), receipt=mr, complete=mc, artifacts=ma
            ),
            runtime=dict(manifest=rr, artifacts=ra),
            startup=dict(manifest=sr, artifacts=sa),
            tuning=dict(manifest=tr, config=tc),
        ),
    )
    if len(_canonical(envelope)) > MAX_ENVELOPE:
        raise ValueError("wire envelope exceeds fixed bound")
    return envelope, blobs


def _write_wire(directory, envelope, blobs):
    directory = Path(directory)
    if directory.exists() or directory.is_symlink():
        raise FileExistsError(directory)
    directory.mkdir(mode=0o700)
    expected = {row["ordinal_name"] for row in envelope["blobs"]}
    if set(blobs) != expected:
        raise ValueError("wire blob set differs from envelope")
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in sorted(blobs):
            handle = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=fd,
            )
            with os.fdopen(handle, "wb") as stream:
                stream.write(blobs[name])
                stream.flush()
                os.fsync(stream.fileno())
        raw = _canonical(envelope)
        handle = os.open(
            "envelope.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=fd,
        )
        with os.fdopen(handle, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
    finally:
        os.close(fd)
    return _digest(raw)


def _read_fd_exact(fd, length, limit):
    if length > limit:
        raise ValueError("wire file exceeds bound")
    chunks = []
    remaining = length
    while remaining:
        chunk = os.read(fd, min(1 << 20, remaining))
        if not chunk:
            raise ValueError("wire file ended early")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(fd, 1):
        raise ValueError("wire file length changed")
    return b"".join(chunks)


def _read_flat(directory, name, length, limit):
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size != length:
            raise ValueError("wire member is not expected regular file")
        raw = _read_fd_exact(fd, length, limit)
        if _identity(os.fstat(fd)) != _identity(before):
            raise ValueError("wire member changed during read")
        return raw
    finally:
        os.close(fd)


def _validate_envelope(document):
    if (
        type(document) is not dict
        or set(document) != {"schema_version", "request", "blobs", "snapshots"}
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
    ):
        raise ValueError("wire envelope fields differ from fixed contract")
    rows = document["blobs"]
    if type(rows) is not list or not 1 <= len(rows) <= MAX_BLOBS:
        raise ValueError("wire blob count is invalid")
    logical = set()
    total = 0
    for index, row in enumerate(rows):
        if type(row) is not dict or set(row) != {
            "logical_name",
            "ordinal_name",
            "length",
            "sha256",
        }:
            raise ValueError("wire blob descriptor is invalid")
        if (
            type(row["logical_name"]) is not str
            or not row["logical_name"]
            or row["logical_name"] in logical
        ):
            raise ValueError("wire logical name is invalid/duplicate")
        if row["ordinal_name"] != f"payload-{index:04d}.bin":
            raise ValueError("wire ordinal name is invalid")
        if (
            type(row["length"]) is not int
            or isinstance(row["length"], bool)
            or not 0 <= row["length"] <= MAX_WIRE_COMPONENT
        ):
            raise ValueError("wire length is invalid")
        if (
            type(row["sha256"]) is not str
            or re.fullmatch("[0-9a-f]{64}", row["sha256"]) is None
        ):
            raise ValueError("wire digest is invalid")
        logical.add(row["logical_name"])
        total += row["length"]
    if total > MAX_TRANSPORT:
        raise ValueError("wire byte sum exceeds fixed bound")
    if type(document["request"]) is not dict or type(document["snapshots"]) is not dict:
        raise ValueError("wire request/snapshot fields are invalid")
    return rows


def _read_wire(envelope_path, envelope_sha256):
    path = Path(envelope_path)
    if (
        type(envelope_sha256) is not str
        or re.fullmatch("[0-9a-f]{64}", envelope_sha256) is None
    ):
        raise ValueError("independent envelope digest is invalid")
    if not path.is_absolute() or path.name != "envelope.json" or path.resolve() != path:
        raise ValueError("wire envelope path is not canonical")
    binding = _path_binding(path)
    parent = path.parent
    parent_identity = _directory_identity(parent)
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        raw = _read_flat(
            directory,
            "envelope.json",
            os.stat("envelope.json", dir_fd=directory, follow_symlinks=False).st_size,
            MAX_ENVELOPE,
        )
        if _digest(raw) != envelope_sha256:
            raise ValueError("wire envelope differs from parent-supplied digest")
        document = _strict_json(raw)
        rows = _validate_envelope(document)
        names = set(os.listdir(directory))
        expected = {"envelope.json", *(r["ordinal_name"] for r in rows)}
        if names != expected:
            raise ValueError("wire directory has missing or extra members")
        blobs = {}
        for row in rows:
            value = _read_flat(
                directory, row["ordinal_name"], row["length"], MAX_WIRE_COMPONENT
            )
            if _digest(value) != row["sha256"]:
                raise ValueError("wire member digest mismatch")
            blobs[row["logical_name"]] = value
        if (
            _directory_identity(parent) != parent_identity
            or _path_binding(path) != binding
        ):
            raise ValueError("wire directory/ancestor changed during read")
        return document, blobs
    except OSError as error:
        raise ValueError("wire namespace changed during bounded read") from error
    finally:
        os.close(directory)


def _wire_namespace_binding(envelope_path, envelope=None):
    path = Path(envelope_path)
    parent = path.parent
    directory_identity = _directory_identity(parent)
    ancestors = _path_binding(path)
    names = ["envelope.json"]
    if envelope is not None:
        rows = _validate_envelope(envelope)
        names.extend(row["ordinal_name"] for row in rows)
    files = {}
    for name in names:
        info = (parent / name).lstat()
        if not stat.S_ISREG(info.st_mode) or (parent / name).is_symlink():
            raise ValueError("wire member identity is not a regular file")
        files[name] = _identity(info)
    return {
        "directory_identity": directory_identity,
        "ancestors": ancestors,
        "files": files,
    }


def _read_wire_retained(envelope_path, envelope_sha256, retained=None):
    if retained is None:
        before = _wire_namespace_binding(envelope_path)
    else:
        before = _wire_namespace_binding_from_names(
            envelope_path, tuple(retained["files"])
        )
        if before != retained:
            raise ValueError("wire file, directory, or ancestor identity changed")
    envelope, blobs = _read_wire(envelope_path, envelope_sha256)
    after = _wire_namespace_binding(envelope_path, envelope)
    if retained is None:
        if (
            before["directory_identity"] != after["directory_identity"]
            or before["ancestors"] != after["ancestors"]
            or before["files"]["envelope.json"]
            != after["files"]["envelope.json"]
        ):
            raise ValueError("wire identity changed around initial retained read")
    elif after != retained:
        raise ValueError("wire identity changed during retained reread")
    return envelope, blobs, after


def _wire_namespace_binding_from_names(envelope_path, names):
    path = Path(envelope_path)
    parent = path.parent
    if (
        type(names) is not tuple
        or not names
        or names[0] != "envelope.json"
        or len(set(names)) != len(names)
        or any(
            type(name) is not str
            or re.fullmatch(r"payload-[0-9]{4}\.bin", name) is None
            for name in names[1:]
        )
    ):
        raise ValueError("retained wire member names are invalid")
    files = {}
    for name in names:
        info = (parent / name).lstat()
        if not stat.S_ISREG(info.st_mode) or (parent / name).is_symlink():
            raise ValueError("retained wire member is not a regular file")
        files[name] = _identity(info)
    return {
        "directory_identity": _directory_identity(parent),
        "ancestors": _path_binding(path),
        "files": files,
    }


def _refs(rows, blobs):
    if type(rows) is not list:
        raise ValueError("wire artifact references are invalid")
    result = []
    names = set()
    for row in rows:
        if (
            type(row) is not list
            or len(row) != 2
            or type(row[0]) is not str
            or not row[0]
            or len(row[0]) > 4096
            or Path(row[0]).is_absolute()
            or ".." in Path(row[0]).parts
            or type(row[1]) is not str
            or row[0] in names
            or row[1] not in blobs
        ):
            raise ValueError("wire artifact reference is invalid")
        names.add(row[0])
        result.append((row[0], blobs[row[1]]))
    return tuple(result)


def _decode_snapshots(envelope, blobs, classes):
    if type(classes) is not tuple or len(classes) != 4:
        raise ValueError("snapshot class set is invalid")
    snapshots = envelope["snapshots"]
    if type(snapshots) is not dict or set(snapshots) != {
        "metadata",
        "runtime",
        "startup",
        "tuning",
    }:
        raise ValueError("wire snapshot set differs")
    m, r, s, t = (snapshots[k] for k in ("metadata", "runtime", "startup", "tuning"))
    if any(type(value) is not dict for value in (m, r, s, t)) or (
        set(m) != {"bundle", "receipt", "complete", "artifacts"}
        or set(r) != {"manifest", "artifacts"}
        or set(s) != {"manifest", "artifacts"}
        or set(t) != {"manifest", "config"}
    ):
        raise ValueError("wire snapshot descriptors differ")
    scalar_refs = (
        m["receipt"],
        m["complete"],
        r["manifest"],
        s["manifest"],
        t["manifest"],
    )
    if (
        type(m["bundle"]) is not str
        or not m["bundle"]
        or any(type(value) is not str for value in scalar_refs)
        or (t["config"] is not None and type(t["config"]) is not str)
    ):
        raise ValueError("wire snapshot scalar reference is invalid")
    used = {m["receipt"], m["complete"], r["manifest"], s["manifest"], t["manifest"]}
    for rows in (m["artifacts"], r["artifacts"], s["artifacts"]):
        used.update(row[1] for row in rows if type(row) is list and len(row) == 2)
    if t["config"] is not None:
        used.add(t["config"])
    try:
        complete = used == set(blobs)
    except TypeError as error:
        raise ValueError("wire logical references are invalid") from error
    if not complete:
        raise ValueError("wire contains unreferenced or missing logical blobs")
    return (
        classes[0](
            Path(m["bundle"]),
            blobs[m["receipt"]],
            blobs[m["complete"]],
            _refs(m["artifacts"], blobs),
        ),
        classes[1](blobs[r["manifest"]], _refs(r["artifacts"], blobs)),
        classes[2](blobs[s["manifest"]], _refs(s["artifacts"], blobs)),
        classes[3](
            blobs[t["manifest"]], None if t["config"] is None else blobs[t["config"]]
        ),
    )


def _actual_bootstrap_facts():
    flags = sys.flags
    return dict(
        executable=str(Path(sys.executable).resolve()),
        isolated=flags.isolated,
        no_site=flags.no_site,
        no_user_site=flags.no_user_site,
        ignore_environment=flags.ignore_environment,
        dont_write_bytecode=flags.dont_write_bytecode,
        pycache_prefix=sys.pycache_prefix,
        xoptions=dict(sys._xoptions),
    )


def _check_empty_prefix(prefix):
    prefix = Path(prefix)
    identity = _directory_identity(prefix)
    if os.listdir(prefix):
        raise ValueError("private bytecode prefix is not empty")
    return identity


def _worker_prefix_check(prefix, identity):
    prefix = Path(prefix)
    facts = _actual_bootstrap_facts()
    expected = str(prefix)
    if (
        facts["pycache_prefix"] != expected
        or facts["xoptions"].get("pycache_prefix") != expected
        or type(facts["dont_write_bytecode"]) is not int
        or facts["dont_write_bytecode"] != 1
        or _check_empty_prefix(prefix) != identity
    ):
        raise ValueError("private bytecode prefix property, identity, or contents changed")
    return identity


def _reject_preloaded_modules():
    forbidden = (
        "site",
        "sitecustomize",
        "usercustomize",
        "torch",
        "vllm",
        "flashinfer",
        "flashinfer_cubin",
        "numpy",
        "transformers",
        "triton",
        "safetensors",
        "tvm_ffi",
        "huggingface_hub",
    )
    loaded = sorted(
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    )
    if loaded:
        raise ValueError("site or inference modules were preloaded: " + loaded[0])


def _check_preloaded_bootstrap():
    return _reject_preloaded_modules()


def _check_bootstrap(prefix, facts=None):
    facts = _actual_bootstrap_facts() if facts is None else facts
    required = {
        "executable",
        "isolated",
        "no_site",
        "no_user_site",
        "ignore_environment",
        "dont_write_bytecode",
        "pycache_prefix",
        "xoptions",
    }
    if type(facts) is not dict or set(facts) != required:
        raise ValueError("interpreter facts are incomplete")
    expected = str(Path(prefix))
    if (
        str(Path(facts["executable"]).resolve()) != PINNED_PYTHON
        or any(
            type(facts[k]) is not int or facts[k] != 1
            for k in (
                "isolated",
                "no_site",
                "no_user_site",
                "ignore_environment",
                "dont_write_bytecode",
            )
        )
        or facts["pycache_prefix"] != expected
        or type(facts["xoptions"]) is not dict
        or facts["xoptions"].get("pycache_prefix") != expected
    ):
        raise ValueError(
            "owned worker requires pinned -I -S -B and explicit pycache prefix"
        )
    return _check_empty_prefix(prefix)


def _check_initial_sys_path():
    expected = [
        "/opt/miniconda3/lib/python313.zip",
        "/opt/miniconda3/lib/python3.13",
        "/opt/miniconda3/lib/python3.13/lib-dynload",
    ]
    if sys.path != expected:
        raise ValueError(
            "initial isolated sys.path differs from pinned standard library roots"
        )


def _validate_runtime_environment(runtime, work):
    if type(runtime) is not dict or any(
        type(key) is not str or type(value) is not str or len(value) > 16384
        for key, value in runtime.items()
    ):
        raise ValueError("runtime environment contains invalid fields")
    expected = {key: runtime[key] for key in PLATFORM_ENV if key in runtime}
    expected.update(FIXED_ENV)
    expected.update(
        {key: str(Path(work) / suffix) for key, suffix in CACHE_ENV.items()}
    )
    if runtime != expected:
        raise ValueError("runtime environment differs from fixed allowlist")
    return expected


def _check_environment(runtime, transport, actual, prefix):
    if (
        type(runtime) is not dict
        or type(transport) is not dict
        or transport != {"PYTHONPYCACHEPREFIX": str(prefix)}
    ):
        raise ValueError("invalid sole bootstrap transport field")
    if type(actual) is not dict or actual != {**runtime, **transport}:
        raise ValueError(
            "worker environment differs from frozen allowlist plus sole transport"
        )


def _read_fixed(root, relative):
    if relative not in PROJECT_FILES:
        raise ValueError("project source outside fixed closure")
    components = Path(relative).parts
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = [directory]
    try:
        for component in components[:-1]:
            opened.append(
                os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=opened[-1],
                )
            )
        fd = os.open(
            components[-1],
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=opened[-1],
        )
        opened.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > PROJECT_MAX_FILE:
            raise ValueError("project source is not bounded regular file")
        raw = _read_fd_exact(fd, info.st_size, PROJECT_MAX_FILE)
        if _identity(os.fstat(fd)) != _identity(info):
            raise ValueError("project source changed during read")
        chain = []
        for fd in opened[:-1]:
            observed = os.fstat(fd)
            chain.append([observed.st_dev, observed.st_ino])
        return raw, _identity(info), chain
    except OSError as error:
        raise ValueError("project source namespace changed") from error
    finally:
        for fd in reversed(opened):
            try:
                os.close(fd)
            except OSError:
                pass


def _namespace_absence(root):
    suffixes = tuple(
        dict.fromkeys((".py", ".pyc", *importlib.machinery.EXTENSION_SUFFIXES))
    )
    for stem in ("adapters/__init__", "adapters/vllm_capacity/__init__"):
        for suffix in suffixes:
            path = root / (stem + suffix)
            if path.exists() or path.is_symlink():
                raise ValueError(
                    "project adapter namespace initializer must remain absent"
                )
    return list(suffixes)


def capture_project_sources(root=ROOT):
    root = Path(root)
    root_identity = _directory_identity(root)
    _namespace_absence(root)
    files = {}
    total = 0
    for relative in PROJECT_FILES:
        raw, identity, ancestors = _read_fixed(root, relative)
        total += len(raw)
        if total > PROJECT_MAX_TOTAL:
            raise ValueError("project source closure exceeds total bound")
        files[relative] = dict(
            length=len(raw), sha256=_digest(raw), identity=identity, ancestors=ancestors
        )
    _namespace_absence(root)
    if _directory_identity(root) != root_identity:
        raise ValueError("project root changed during source capture")
    return dict(
        root=str(root), root_identity=root_identity, files=files, total_bytes=total
    )


def recheck_project_sources(frozen):
    if (
        type(frozen) is not dict
        or set(frozen) != {"root", "root_identity", "files", "total_bytes"}
        or frozen["root"] != str(ROOT)
        or frozen["root_identity"] != _directory_identity(ROOT)
        or set(frozen["files"]) != set(PROJECT_FILES)
    ):
        raise ValueError("project source closure binding is invalid")
    current = capture_project_sources(ROOT)
    if current != frozen:
        raise ValueError("selected project source bytes or identities changed")
    return dict(
        status="PROJECT_SOURCES_UNCHANGED",
        manifest_sha256=_digest(_canonical(frozen)),
        scientific_validation_passed=False,
    )


def _verify_namespace_paths():
    expected = {
        "adapters": str(ROOT / "adapters"),
        "adapters.vllm_capacity": str(ROOT / "adapters/vllm_capacity"),
    }
    for name, path in expected.items():
        module = importlib.import_module(name)
        if getattr(module, "__file__", None) is not None or list(module.__path__) != [
            path
        ]:
            raise ValueError(
                "adapter namespace path differs from fixed project directory"
            )


def _core_identity(value):
    keys = ("pid", "boot_id", "start_time", "ppid", "pgrp", "session")
    if type(value) is not dict or any(k not in value for k in keys):
        raise ValueError("process identity is incomplete")
    result = {k: value[k] for k in keys}
    if (
        type(result["boot_id"]) is not str
        or not result["boot_id"]
        or any(
            type(result[k]) is not int or isinstance(result[k], bool) or result[k] < 0
            for k in ("pid", "start_time", "ppid", "pgrp", "session")
        )
    ):
        raise ValueError("process identity fields are invalid")
    return result


def _verify_ownership(record, expected, child, parent):
    if (
        type(record) is not dict
        or type(expected) is not dict
        or set(record) != (set(expected) | {"parent", "child"})
    ):
        raise ValueError("ownership binding is invalid")
    for key, value in expected.items():
        if key not in ("parent", "child") and _canonical(record.get(key)) != _canonical(
            value
        ):
            raise ValueError("ownership record binding mismatch: " + key)
    actual_child = _core_identity(child)
    actual_parent = _core_identity(parent)
    if (
        _core_identity(record.get("child")) != actual_child
        or _core_identity(record.get("parent")) != actual_parent
    ):
        raise ValueError("ownership process identity mismatch")
    if (
        actual_child["ppid"] != actual_parent["pid"]
        or actual_child["boot_id"] != actual_parent["boot_id"]
        or actual_child["pgrp"] != actual_child["pid"]
        or actual_child["session"] != actual_child["pid"]
        or actual_child["start_time"] < actual_parent["start_time"]
    ):
        raise ValueError("worker is not the acknowledged fresh session child")
    return True


def _process_identity(pid):
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        return dict(
            pid=int(pid),
            boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            start_time=int(fields[19]),
            ppid=int(fields[1]),
            pgrp=int(fields[2]),
            session=int(fields[3]),
        )
    except (OSError, ValueError, IndexError):
        return None


def _read_json_file(path, limit):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("record path is not canonical")
    binding = _path_binding(path)
    parent_identity = _directory_identity(path.parent)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        raw = _read_flat(directory, path.name, info.st_size, limit)
        if (
            _directory_identity(path.parent) != parent_identity
            or _path_binding(path) != binding
        ):
            raise ValueError("record directory/ancestor changed during read")
        return _strict_json(raw)
    except OSError as error:
        raise ValueError("record namespace changed during anchored read") from error
    finally:
        os.close(directory)


def _torch_device_gate(torch, torch_cuda, gpu_uuid, device_name, capability):
    raw = torch._C._cuda_getDeviceCount()
    public = torch_cuda.device_count()
    current = torch_cuda.current_device()
    if (
        type(raw) is not int
        or isinstance(raw, bool)
        or raw != 1
        or type(public) is not int
        or isinstance(public, bool)
        or public != 1
        or type(current) is not int
        or isinstance(current, bool)
        or current != 0
    ):
        raise ValueError(
            "actual CUDA device count/current device differs from single-GPU control"
        )
    properties = torch_cuda.get_device_properties(0)
    observed = dict(
        raw_count=raw,
        public_count=public,
        current_device=current,
        uuid=str(properties.uuid),
        name=str(properties.name),
        capability=[properties.major, properties.minor],
    )
    if {
        key: observed[key] for key in ("uuid", "name", "capability")
    } != dict(uuid=gpu_uuid, name=device_name, capability=list(capability)):
        raise ValueError("Torch device identity differs from parent declaration")
    return observed


def _vllm_device_gate(platforms, cuda_platform, gpu_uuid, device_name, capability):
    current_platform = platforms.current_platform
    if type(current_platform) is not cuda_platform.NvmlCudaPlatform:
        raise ValueError("vLLM current platform is not the verified NVML CUDA platform")

    def value(names):
        for name in names:
            candidate = getattr(current_platform, name, None)
            if candidate is not None:
                return candidate() if callable(candidate) else candidate
        return None

    nvml_uuid = value(("get_device_uuid",))
    nvml_name = value(("get_device_name",))
    nvml_cap = value(("get_device_capability",))
    if nvml_uuid is None or str(nvml_uuid) != gpu_uuid:
        raise ValueError("vLLM NVML UUID differs from Torch/declaration")
    if nvml_name is None or str(nvml_name) != device_name:
        raise ValueError("vLLM NVML name differs from Torch/declaration")
    if nvml_cap is None or list(nvml_cap) != list(capability):
        raise ValueError("vLLM NVML capability differs from Torch/declaration")
    return dict(
        platform_class=(
            type(current_platform).__module__ + "." + type(current_platform).__qualname__
        ),
        uuid=str(nvml_uuid),
        name=str(nvml_name),
        capability=list(nvml_cap),
    )


def _device_gate(torch, platforms, cuda_platform, gpu_uuid, device_name, capability):
    """Compatibility helper retained for focused low-level tests."""
    observed = _torch_device_gate(
        torch, torch.cuda, gpu_uuid, device_name, capability
    )
    _vllm_device_gate(platforms, cuda_platform, gpu_uuid, device_name, capability)
    return observed


def _request(document, arm):
    fields = {
        "schema_version",
        "arm",
        "run_id",
        "git_commit",
        "environment_fingerprint",
        "gpu_uuid",
        "device_name",
        "device_capability",
        "work_dir",
        "output_dir",
        "process_dir",
        "attempt_dir",
        "metadata_bundle",
        "runtime_env",
        "transport_env",
        "prompt_token_ids",
        "project_sources",
    }
    if (
        type(document) is not dict
        or set(document) != fields
        or type(document["schema_version"]) is not int
        or document["schema_version"] != 1
        or type(document["arm"]) is not str
        or document["arm"] != arm
    ):
        raise ValueError("worker request differs from fixed contract")
    if (
        arm not in ARMS
        or type(document["prompt_token_ids"]) is not list
        or any(
            type(value) is not int or value <= 0
            for value in document["prompt_token_ids"]
        )
        or document["prompt_token_ids"] != list(range(1000, 1032))
    ):
        raise ValueError("worker arm/prompt control differs")
    if (
        type(document["run_id"]) is not str
        or not 0 < len(document["run_id"]) <= 128
        or type(document["git_commit"]) is not str
        or type(document["environment_fingerprint"]) is not str
        or re.fullmatch("[0-9a-f]{40}", document["git_commit"]) is None
        or re.fullmatch("[0-9a-f]{64}", document["environment_fingerprint"]) is None
    ):
        raise ValueError("worker revision/environment binding is invalid")
    if (
        type(document["device_capability"]) is not list
        or any(type(value) is not int for value in document["device_capability"])
        or document["device_capability"] != [12, 0]
        or document["device_name"] != "NVIDIA RTX PRO 6000 Blackwell Server Edition"
    ):
        raise ValueError("worker GPU declaration differs from fixed control")
    if (
        type(document["gpu_uuid"]) is not str
        or re.fullmatch(r"GPU-[A-Za-z0-9-]{1,128}", document["gpu_uuid"]) is None
        or type(document["runtime_env"]) is not dict
        or type(document["transport_env"]) is not dict
        or type(document["project_sources"]) is not dict
    ):
        raise ValueError("worker UUID/environment/source binding type is invalid")
    for key in (
        "work_dir",
        "output_dir",
        "process_dir",
        "attempt_dir",
        "metadata_bundle",
    ):
        if (
            type(document[key]) is not str
            or not document[key]
            or len(document[key]) > 4096
        ):
            raise ValueError("worker path binding type is invalid")
        path = Path(document[key])
        if (
            not path.is_absolute()
            or path.resolve() != path
            or not path.is_relative_to(ROOT)
        ):
            raise ValueError(
                "worker path binding is outside canonical project root: " + key
            )
    attempt = Path(document["attempt_dir"])
    process = Path(document["process_dir"])
    output = Path(document["output_dir"])
    work = Path(document["work_dir"])
    metadata = Path(document["metadata_bundle"])
    expected_transport = {
        "PYTHONPYCACHEPREFIX": str(work / "cache/python")
    }
    if (
        document["transport_env"] != expected_transport
        or type(document["transport_env"].get("PYTHONPYCACHEPREFIX")) is not str
        or Path(document["transport_env"]["PYTHONPYCACHEPREFIX"]).resolve()
        != work / "cache/python"
    ):
        raise ValueError("worker bytecode transport differs from fixed arm prefix")
    if (
        not attempt.is_relative_to(ROOT / "results/gold")
        or process != attempt / "process" / arm
        or output != attempt / "arms" / arm
        or not work.is_relative_to(ROOT / "results/tmp/hf-routing")
        or work.name != arm
    ):
        raise ValueError(
            "worker owned directory layout differs from fixed parent contract"
        )
    if any(
        left == right or left.is_relative_to(right) or right.is_relative_to(left)
        for left, right in ((work, attempt), (metadata, attempt), (metadata, work))
    ):
        raise ValueError("worker metadata, evidence, and cache paths overlap")
    return document


def _install_project_paths():
    additions = [
        str(ROOT / "scripts/eval"),
        str(ROOT / "adapters/vllm_capacity"),
        str(ROOT),
    ]
    if any(path in sys.path for path in additions):
        raise ValueError("project paths were present before controlled installation")
    sys.path[:0] = additions


def _install_site_path(site=PINNED_SITE):
    site = Path(site)
    if site.resolve() != site or not site.is_dir() or site.is_symlink():
        raise ValueError("pinned site-packages root is unavailable or noncanonical")
    if "site" in sys.modules or "sitecustomize" in sys.modules or "usercustomize" in sys.modules:
        raise ValueError("site processing module loaded before fixed site installation")
    expected_project = [
        str(ROOT / "scripts/eval"),
        str(ROOT / "adapters/vllm_capacity"),
        str(ROOT),
    ]
    expected_stdlib = [
        "/opt/miniconda3/lib/python313.zip",
        "/opt/miniconda3/lib/python3.13",
        "/opt/miniconda3/lib/python3.13/lib-dynload",
    ]
    if sys.path != expected_project + expected_stdlib:
        raise ValueError("sys.path changed before fixed site installation")
    sys.path.append(str(site))
    _namespace_absence(ROOT)
    _verify_namespace_paths()
    spec = importlib.util.find_spec("torch")
    if spec is None or spec.origin is None:
        raise ValueError("torch is unavailable from pinned site-packages")
    origin = Path(spec.origin).resolve()
    if not origin.is_relative_to(site):
        raise ValueError("torch resolved outside pinned site-packages")
    return str(site)


def _import_runtime_union(runtime_imports, tuning_runtime, contract):
    expected = (
        set(runtime_imports.REQUIRED_MODULES)
        | set(tuning_runtime.MODULES)
        | set(RUNTIME_CONTRACT_MODULES)
        | {"vllm.sampling_params", "numpy", "vllm.platforms", "vllm.platforms.cuda"}
    )
    modules = {name: importlib.import_module(name) for name in sorted(expected)}
    # Do not call _runtime_symbols for discovery.  Its fixed source list is
    # duplicated above and this equality catches drift before construction.
    return modules


def _write_exclusive_json(path, value):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(_canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _recheck_metadata_current(inventory, verify, snapshot):
    if inventory.load_hf_snapshot(snapshot.bundle) != snapshot:
        raise ValueError("current metadata bundle differs from frozen wire snapshot")
    verify.check_current_inputs(snapshot.bundle)
    if inventory.load_hf_snapshot(snapshot.bundle) != snapshot:
        raise ValueError("metadata bundle changed around current-input check")


def execute_owned(
    arm,
    envelope_path,
    envelope_sha256,
    ownership_record,
    *,
    _test_dependencies=None,
    _facts=None,
):
    """Run one arm after every closed-parent gate; private dependencies force MOCK."""
    if _facts is not None and _test_dependencies is None:
        raise ValueError("private interpreter facts require explicit MOCK dependencies")
    stage = "input"
    request = None
    try:
        envelope, blobs, wire_binding = _read_wire_retained(
            Path(envelope_path), envelope_sha256
        )
        request = _request(envelope["request"], arm)
        prefix = Path(request["transport_env"]["PYTHONPYCACHEPREFIX"])
        prefix_identity = _check_bootstrap(prefix, _facts)
        if _facts is None:
            _check_initial_sys_path()
            _check_preloaded_bootstrap()
        _validate_runtime_environment(request["runtime_env"], request["work_dir"])
        _check_environment(
            request["runtime_env"], request["transport_env"], dict(os.environ), prefix
        )
        if (
            Path(envelope_path) != Path(request["process_dir"]) / "wire/envelope.json"
            or Path(ownership_record)
            != Path(request["process_dir"]) / "ownership-record.json"
        ):
            raise ValueError("worker argv paths differ from request binding")

        stage = "bootstrap"
        _worker_prefix_check(prefix, prefix_identity)
        stage = "ownership"
        record = _read_json_file(Path(ownership_record), 1 << 20)
        expected = {
            key: request[key]
            for key in (
                "arm",
                "run_id",
                "attempt_dir",
                "process_dir",
                "work_dir",
                "output_dir",
                "gpu_uuid",
            )
        }
        expected.update(
            schema_version=1,
            resource_class="GPU_EXCLUSIVE",
            envelope_sha256=envelope_sha256,
            source_manifest_sha256=_digest(_canonical(request["project_sources"])),
            attempt_dir_identity=_directory_identity(Path(request["attempt_dir"])),
            process_dir_identity=_directory_identity(Path(request["process_dir"])),
        )
        child = _process_identity(os.getpid())
        parent = _process_identity(os.getppid())
        if not child or not parent:
            raise ValueError("live worker/parent identities unavailable")
        _verify_ownership(record, expected, child, parent)
        bootstrap = dict(
            schema_version=1,
            status="OWNED_BOOTSTRAP_VERIFIED",
            arm=arm,
            envelope_sha256=envelope_sha256,
            ownership_record_sha256=_digest(Path(ownership_record).read_bytes()),
            source_manifest_sha256=expected["source_manifest_sha256"],
            prefix=str(prefix),
            prefix_identity=prefix_identity,
            runtime_environment_sha256=_digest(_canonical(request["runtime_env"])),
            transport_environment=request["transport_env"],
            interpreter_facts=_actual_bootstrap_facts() if _facts is None else _facts,
            scientific_validation_passed=False,
        )
        _write_exclusive_json(Path(request["process_dir"]) / "bootstrap.json", bootstrap)
        del os.environ["PYTHONPYCACHEPREFIX"]
        _worker_prefix_check(prefix, prefix_identity)

        stage = "project"
        recheck_project_sources(request["project_sources"])
        _namespace_absence(ROOT)
        _install_project_paths()
        inventory = importlib.import_module("evaluation_inventory")
        sources = importlib.import_module("hf_runtime_sources")
        startup_api = importlib.import_module("hf_startup_sources")
        tuning_api = importlib.import_module("hf_moe_tuning")
        verify = importlib.import_module("verify_hf_metadata")

        stage = "metadata"
        snapshots = _decode_snapshots(
            envelope,
            blobs,
            (
                inventory.HFMetadataSnapshot,
                sources.RuntimeSourcesSnapshot,
                startup_api.StartupSourcesSnapshot,
                tuning_api.TuningInputsSnapshot,
            ),
        )
        metadata_snapshot, runtime_snapshot, startup_snapshot, tuning_snapshot = snapshots
        if Path(metadata_snapshot.bundle) != Path(request["metadata_bundle"]):
            raise ValueError("wire metadata bundle path differs")
        receipt, _, _, _ = inventory._unpack(metadata_snapshot)

        def check_wire():
            current_envelope, current_blobs, _ = _read_wire_retained(
                Path(envelope_path), envelope_sha256, wire_binding
            )
            if current_envelope != envelope or current_blobs != blobs:
                raise ValueError("wire bytes changed after initial validation")

        retained_checkers = (
            (
                "metadata-current",
                lambda: _recheck_metadata_current(
                    inventory, verify, metadata_snapshot
                ),
            ),
            ("runtime-sources", lambda: sources.recheck_runtime_sources(runtime_snapshot)),
            (
                "startup-sources",
                lambda: startup_api.recheck_startup_sources(
                    startup_snapshot, runtime_snapshot
                ),
            ),
            (
                "tuning",
                lambda: tuning_api.recheck_tuning_inputs(
                    tuning_snapshot,
                    metadata_snapshot,
                    runtime_snapshot,
                    request["device_name"],
                ),
            ),
            ("project", lambda: recheck_project_sources(request["project_sources"])),
            ("wire", check_wire),
            ("prefix", lambda: _worker_prefix_check(prefix, prefix_identity)),
        )

        def retained_checks():
            for _, check in retained_checkers:
                check()

        test_only = _test_dependencies is not None or _facts is not None
        primary_error = None
        primary_stage = None
        result = None
        device_path = None
        try:
            stage = "runtime-import"
            runtime_report = sources.validate_runtime_sources(runtime_snapshot)
            startup_report = startup_api.validate_startup_sources(
                startup_snapshot, runtime_snapshot
            )
            tuning_report = tuning_api.validate_tuning_inputs(
                tuning_snapshot,
                metadata_snapshot,
                runtime_snapshot,
                request["device_name"],
            )
            initial_postchecks = []
            for name, check in retained_checkers:
                try:
                    check()
                except BaseException as initial_error:
                    initial_postchecks.append(
                        {
                            "check": name,
                            "status": "failed",
                            "type": type(initial_error).__name__,
                            "message": str(initial_error),
                        }
                    )
                else:
                    initial_postchecks.append({"check": name, "status": "passed"})
            initial_failures = [
                row for row in initial_postchecks if row["status"] == "failed"
            ]
            if initial_failures:
                raise ValueError(
                    "initial retained input check failed: "
                    + initial_failures[0]["message"]
                )
            importlib.import_module("adapters")
            importlib.import_module("adapters.vllm_capacity")
            _verify_namespace_paths()
            _namespace_absence(ROOT)
            if test_only:
                if type(_test_dependencies) is not dict or set(_test_dependencies) != {
                    "runtime_callback"
                }:
                    raise ValueError("invalid private test dependencies")
                stage = "loaded-arm"
                result = _test_dependencies["runtime_callback"](request, snapshots)
                if type(result) is not dict:
                    raise ValueError("test runtime callback returned invalid result")
                device_path = None
            else:
                if (
                    receipt["evidence"] == "TEST_ONLY"
                    or runtime_report["test_only"]
                    or startup_report["test_only"]
                    or tuning_report["test_only"]
                ):
                    raise ValueError("real owned entrypoint rejects MOCK/TEST_ONLY inputs")
                if Path(runtime_report["source_root"]).resolve() != PINNED_SITE:
                    raise ValueError("frozen runtime source root differs from pinned site-packages")
                _install_site_path(PINNED_SITE)

                stage = "torch-device"
                torch = importlib.import_module("torch")
                torch_cuda = importlib.import_module("torch.cuda")
                first_torch = _torch_device_gate(
                    torch,
                    torch_cuda,
                    request["gpu_uuid"],
                    request["device_name"],
                    tuple(request["device_capability"]),
                )

                stage = "vllm-device"
                platforms = importlib.import_module("vllm.platforms")
                cuda_platform = importlib.import_module("vllm.platforms.cuda")
                first_nvml = _vllm_device_gate(
                    platforms,
                    cuda_platform,
                    request["gpu_uuid"],
                    request["device_name"],
                    tuple(request["device_capability"]),
                )
                runtime_imports = importlib.import_module("hf_runtime_imports")
                tuning_runtime = importlib.import_module("hf_moe_tuning_runtime")
                contract = importlib.import_module("hf_runtime_contract")
                _import_runtime_union(runtime_imports, tuning_runtime, contract)

                stage = "preconstruction"
                retained_checks()
                _verify_namespace_paths()
                _namespace_absence(ROOT)
                pre_torch = _torch_device_gate(
                    torch,
                    torch_cuda,
                    request["gpu_uuid"],
                    request["device_name"],
                    tuple(request["device_capability"]),
                )
                pre_nvml = _vllm_device_gate(
                    platforms,
                    cuda_platform,
                    request["gpu_uuid"],
                    request["device_name"],
                    tuple(request["device_capability"]),
                )
                observations = dict(
                    schema_version=1,
                    status="DEVICE_GATES_VERIFIED",
                    arm=arm,
                    envelope_sha256=envelope_sha256,
                    source_manifest_sha256=expected["source_manifest_sha256"],
                    process_identity=child,
                    declaration=dict(
                        uuid=request["gpu_uuid"],
                        name=request["device_name"],
                        capability=request["device_capability"],
                    ),
                    first=dict(torch=first_torch, nvml=first_nvml),
                    preconstruction=dict(torch=pre_torch, nvml=pre_nvml),
                    scientific_validation_passed=False,
                )
                device_path = Path(request["process_dir"]) / "device-observations.json"
                _write_exclusive_json(device_path, observations)
                plan = dict(
                    metadata_snapshot=metadata_snapshot,
                    runtime_snapshot=runtime_snapshot,
                    tuning_snapshot=tuning_snapshot,
                    device_name_declared=request["device_name"],
                    work_dir=request["work_dir"],
                    gpu_uuid=request["gpu_uuid"],
                    device_capability=tuple(request["device_capability"]),
                    prompt_token_ids=request["prompt_token_ids"],
                    run_id=request["run_id"],
                    git_commit=request["git_commit"],
                    environment_fingerprint=request["environment_fingerprint"],
                )
                stage = "loaded-arm"
                loaded = importlib.import_module("hf_loaded_arm")
                result = loaded.run_loaded_arm(plan, arm, request["output_dir"])

        except BaseException as execution_error:
            primary_error = execution_error
            primary_stage = stage

        stage = "postchecks"
        postchecks = []
        for name, check in (
            *retained_checkers,
            ("namespace-paths", _verify_namespace_paths),
            ("namespace-absence", lambda: _namespace_absence(ROOT)),
        ):
            try:
                check()
            except BaseException as post_error:
                postchecks.append(
                    {
                        "check": name,
                        "status": "failed",
                        "type": type(post_error).__name__,
                        "message": str(post_error),
                    }
                )
            else:
                postchecks.append({"check": name, "status": "passed"})
        failed_postchecks = [row for row in postchecks if row["status"] == "failed"]
        if primary_error is not None:
            primary_error._hf_stage = primary_stage
            primary_error._hf_postchecks = postchecks
            primary_error._hf_postcheck_errors = failed_postchecks
            raise primary_error
        if (
            type(result) is not dict
            or result.get("status") != "ARM_RETURNED_UNVALIDATED"
            or result.get("scientific_validation_passed") is not False
        ):
            error = ValueError("owned arm did not return the provisional success boundary")
            error._hf_stage = "loaded-arm"
            error._hf_postchecks = postchecks
            error._hf_postcheck_errors = failed_postchecks
            raise error
        if failed_postchecks:
            error = ValueError(
                "owned postchecks failed: " + failed_postchecks[0]["message"]
            )
            error._hf_postchecks = postchecks
            error._hf_postcheck_errors = failed_postchecks
            raise error

        output = Path(request["output_dir"])
        missing = [
            name
            for name in ("input-binding.json", "worker-status.json")
            if not (output / name).is_file()
        ]
        if missing:
            error = ValueError("owned arm output is missing: " + ",".join(missing))
            error._hf_missing_files = missing
            raise error
        _read_json_file(output / "input-binding.json", 1 << 20)
        _read_json_file(output / "worker-status.json", 1 << 20)
        wrapper = dict(
            schema_version=1,
            status=result["status"],
            arm=arm,
            provenance="MOCK" if test_only else result["provenance"],
            scientific_validation_passed=False,
            envelope_sha256=envelope_sha256,
            input_binding_sha256=_digest((output / "input-binding.json").read_bytes()),
            worker_status_sha256=_digest((output / "worker-status.json").read_bytes()),
            device_observations_sha256=(
                None if device_path is None else _digest(device_path.read_bytes())
            ),
            source_manifest_sha256=expected["source_manifest_sha256"],
        )
        _write_exclusive_json(
            Path(request["process_dir"]) / "owned-worker-status.json", wrapper
        )
        return wrapper
    except BaseException as error:
        if not hasattr(error, "_hf_postchecks"):
            error._hf_postchecks = (
                postchecks
                if "postchecks" in locals()
                else [
                    {
                        "check": "retained-inputs",
                        "status": "inapplicable",
                        "reason": "retained snapshots were not available",
                    }
                ]
            )
        if not hasattr(error, "_hf_stage"):
            error._hf_stage = stage
        if request is not None:
            error._hf_request = request
        raise


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--_owned-arm", dest="_owned_arm", choices=ARMS, required=True)
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--envelope-sha256", required=True)
    parser.add_argument("--ownership-record", type=Path, required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        execute_owned(
            args._owned_arm, args.envelope, args.envelope_sha256, args.ownership_record
        )
    except BaseException as error:
        try:
            failure = dict(
                schema_version=1,
                status="FAILED",
                arm=args._owned_arm,
                stage=getattr(error, "_hf_stage", "owned-entrypoint"),
                type=type(error).__name__,
                message=str(error),
                scientific_validation_passed=False,
            )
            if hasattr(error, "_hf_postcheck_errors"):
                failure["postcheck_errors"] = error._hf_postcheck_errors
            if hasattr(error, "_hf_postchecks"):
                failure["postchecks"] = error._hf_postchecks
            if hasattr(error, "_hf_missing_files"):
                failure["missing_files"] = error._hf_missing_files
            destination = None
            try:
                request = getattr(error, "_hf_request", None)
                if request is None:
                    envelope, _ = _read_wire(args.envelope, args.envelope_sha256)
                    request = envelope["request"]
                request = _request(request, args._owned_arm)
                process = Path(request["process_dir"])
                if (
                    args.envelope != process / "wire/envelope.json"
                    or args.ownership_record != process / "ownership-record.json"
                    or process.resolve() != process
                    or not process.is_relative_to(ROOT / "results/gold")
                    or not process.is_dir()
                    or process.is_symlink()
                ):
                    raise ValueError("failure evidence destination is not a bound process directory")
                destination = process / "owned-worker-failure.json"
                failure["envelope_sha256"] = args.envelope_sha256
                failure["source_manifest_sha256"] = _digest(
                    _canonical(request["project_sources"])
                )
                output = Path(request["output_dir"])
                input_path = output / "input-binding.json"
                status_path = output / "worker-status.json"
                if input_path.is_file():
                    failure["input_binding_sha256"] = _digest(input_path.read_bytes())
                if status_path.is_file():
                    status = _read_json_file(status_path, 1 << 20)
                    failure["worker_status_sha256"] = _digest(status_path.read_bytes())
                    failure["worker_primary_error"] = status.get("primary_error")
                    if (
                        type(status.get("primary_error")) is dict
                        and type(status["primary_error"].get("stage")) is str
                    ):
                        failure["stage"] = status["primary_error"]["stage"]
            except BaseException as diagnostic_error:
                failure["cross_binding_diagnostic"] = dict(
                    type=type(diagnostic_error).__name__, message=str(diagnostic_error)
                )
            if destination is not None:
                _write_exclusive_json(destination, failure)
            else:
                print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        except BaseException:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Owned parent for a native/capture/repeat provisional HF routing triplet.

The public CLI fixes every control except the verified metadata bundle, a fresh
private result directory, and the selected physical GPU UUID.  It never marks
the triplet scientifically valid; a separate outer validator is required.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, nullcontext
import csv
import hashlib
import importlib.machinery
import io
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
PINNED_PYTHON = "/opt/miniconda3/bin/python3.13"
DEVICE_NAME = "NVIDIA RTX PRO 6000 Blackwell Server Edition"
DEVICE_CAPABILITY = [12, 0]
ARMS = ("native", "capture", "repeat")
PROMPTS = list(range(1000, 1032))
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


class TripletFailure(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_exclusive(path, document):
    path = Path(path)
    raw = _canonical(document) + b"\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
        info = os.fstat(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return [
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    ]


def _replace_json(path, document):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_canonical(document) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _read_owned_status_binding(
    path, attempt, attempt_identity, expected_raw, retained_identity=None
):
    path = Path(path)
    attempt = Path(attempt)
    if (
        path != attempt / "triplet-status.json"
        or attempt.resolve() != attempt
        or not attempt.is_relative_to(ROOT / "results/gold")
        or path.resolve() != path
    ):
        raise ValueError("owned triplet status path is outside the canonical attempt")
    directory = os.open(attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory_info = os.fstat(directory)
        if [directory_info.st_dev, directory_info.st_ino] != attempt_identity:
            raise ValueError("owned triplet status attempt identity changed")
        fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=directory,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1 << 20:
                raise ValueError("owned triplet status is not a bounded regular file")
            raw = os.read(fd, info.st_size + 1)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        identity = [
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ]
        after_identity = [
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ]
        if (
            identity != after_identity
            or len(raw) != info.st_size
            or raw != expected_raw
            or (retained_identity is not None and identity != retained_identity)
        ):
            raise ValueError("owned triplet status bytes or identity changed")
        return identity
    except OSError as status_error:
        raise ValueError("owned triplet status namespace changed") from status_error
    finally:
        os.close(directory)


def _test_bootstrap_facts(prefix):
    return dict(
        executable=PINNED_PYTHON,
        isolated=1,
        no_site=1,
        no_user_site=1,
        ignore_environment=1,
        dont_write_bytecode=1,
        pycache_prefix=str(prefix),
        xoptions={"pycache_prefix": str(prefix)},
    )


def _full_topology(gpu_uuid):
    argv = [
        "nvidia-smi",
        "--query-gpu=index,uuid,name,compute_cap,mig.mode.current",
        "--format=csv,noheader,nounits",
    ]
    code, stdout, stderr = _bounded_command(argv, timeout=15, limit=64 << 10)
    if code:
        raise ValueError(stderr.decode(errors="replace").strip() or "nvidia-smi full topology failed")
    rows = list(
        csv.reader(io.StringIO(stdout.decode("utf-8", errors="strict")), skipinitialspace=True)
    )
    if len(rows) != 1 or len(rows[0]) != 5:
        raise ValueError("full GPU inventory is not exactly one physical GPU")
    index, observed_uuid, name, capability, mig = rows[0]
    try:
        cap = [int(piece) for piece in capability.split(".")]
    except ValueError as error:
        raise ValueError("GPU compute capability is invalid") from error
    return dict(
        index=int(index), uuid=observed_uuid, name=name, capability=cap, mig=mig
    )


def _bounded_command(argv, timeout, limit):
    if (
        type(argv) is not list
        or not argv
        or any(type(value) is not str or not value for value in argv)
        or type(timeout) not in (int, float)
        or timeout <= 0
        or type(limit) is not int
        or limit <= 0
    ):
        raise ValueError("bounded command parameters are invalid")
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    outputs = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in outputs:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("bounded command exceeded deadline")
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), min(8192, limit + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                outputs[key.fileobj].extend(chunk)
                if sum(len(value) for value in outputs.values()) > limit:
                    raise ValueError("bounded command output exceeded limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("bounded command exceeded deadline")
        code = process.wait(timeout=remaining)
        return code, bytes(outputs[process.stdout]), bytes(outputs[process.stderr])
    except BaseException:
        try:
            os.killpg(process.pid, 15)
            process.wait(timeout=1)
        except BaseException:
            try:
                os.killpg(process.pid, 9)
                process.wait(timeout=1)
            except BaseException:
                pass
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _validate_topology(report, gpu_uuid):
    expected = dict(
        index=0,
        uuid=gpu_uuid,
        name=DEVICE_NAME,
        capability=DEVICE_CAPABILITY,
        mig="Disabled",
    )
    if type(report) is not dict or report != expected:
        raise ValueError(
            "full physical GPU topology differs from fixed single-GPU control"
        )
    return report


def _core_identity(value):
    keys = ("pid", "boot_id", "start_time", "ppid", "pgrp", "session")
    if type(value) is not dict or any(key not in value for key in keys):
        raise ValueError("process identity is incomplete")
    return {key: value[key] for key in keys}


def _ownership_callback(process_dir, binding, parent):
    process_dir = Path(process_dir)
    record_path = process_dir / "ownership-record.json"
    progress_path = process_dir / "ownership-progress.json"
    fixed = None
    retained_raw = None
    retained_identity = None
    retained_directory = None
    latest_child = None

    def read_retained():
        directory = os.open(process_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(
                record_path.name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                dir_fd=directory,
            )
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1 << 20:
                    raise ValueError("ownership record is not a bounded regular file")
                raw = os.read(fd, info.st_size + 1)
                after = os.fstat(fd)
            finally:
                os.close(fd)
            identity = [
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ]
            after_identity = [
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ]
            current_directory = os.fstat(directory)
            directory_identity = [current_directory.st_dev, current_directory.st_ino]
            if identity != after_identity or len(raw) != info.st_size:
                raise ValueError("ownership record changed during anchored read")
            return raw, identity, directory_identity
        except OSError as error:
            raise ValueError("ownership record was deleted or replaced") from error
        finally:
            os.close(directory)

    def callback(child, phase):
        nonlocal fixed, retained_raw, retained_identity, retained_directory, latest_child
        core = _core_identity(child)
        latest_child = dict(child)
        if phase != "producer":
            raise ValueError("owned HF worker must be producer phase")
        if fixed is None:
            fixed = dict(
                schema_version=1, **binding, parent=_core_identity(parent), child=core
            )
            _write_exclusive(record_path, fixed)
            retained_raw, retained_identity, retained_directory = read_retained()
        elif core != fixed["child"]:
            raise ValueError("owned child identity changed after acknowledgement")
        else:
            raw, identity, directory_identity = read_retained()
            if (
                raw != retained_raw
                or identity != retained_identity
                or directory_identity != retained_directory
                or json.loads(raw) != fixed
            ):
                raise ValueError("immutable ownership record changed after acknowledgement")
        progress = dict(
            schema_version=1,
            child=core,
            owned_members=child.get("owned_members", []),
            phase=phase,
        )
        _replace_json(progress_path, progress)

    callback.fixed_record = lambda: fixed
    callback.latest_child = lambda: latest_child

    return callback


def _owned_exit_observation(callback, test_only):
    child = callback.latest_child()
    if child is None:
        return None, None, False
    if test_only:
        return [], False, True
    manifest = __import__("run_manifest")
    remaining = list(manifest.owned_processes(child).values())
    uncertain = manifest.uncertain_session(child)
    return remaining, uncertain, True


def _signal_number(signals):
    if type(signals) is not dict or "signal" not in signals:
        raise ValueError("shared signal state is invalid")
    number = signals["signal"]
    if number in (None, 0):
        return None
    if type(number) is not int or number <= 0:
        raise ValueError("shared signal number is invalid")
    return number


def _raise_for_signal(signals, stage):
    number = _signal_number(signals)
    if number is not None:
        raise TripletFailure(f"signal {number} observed during {stage}")


def _attempt_file_observations(worker, process, output):
    paths = {
        "input-binding": Path(output) / "input-binding.json",
        "worker-status": Path(output) / "worker-status.json",
        "bootstrap": Path(process) / "bootstrap.json",
        "owned-worker-status": Path(process) / "owned-worker-status.json",
        "owned-worker-failure": Path(process) / "owned-worker-failure.json",
        "device-observations": Path(process) / "device-observations.json",
    }
    observations = {}
    for name, path in paths.items():
        row = {"path": str(path), "present": False}
        try:
            info = path.lstat()
            row["present"] = True
            row["regular"] = stat.S_ISREG(info.st_mode) and not path.is_symlink()
            row["bytes"] = info.st_size
            if not row["regular"] or info.st_size > 1 << 20:
                raise ValueError("evidence is not a bounded regular file")
            document = worker._read_json_file(path, 1 << 20)
            row["valid_json_object"] = type(document) is dict
            row["sha256"] = _digest(path.read_bytes())
            if not row["valid_json_object"]:
                raise ValueError("evidence JSON is not an object")
        except FileNotFoundError:
            pass
        except BaseException as observation_error:
            row["valid_json_object"] = False
            row["error"] = {
                "type": type(observation_error).__name__,
                "message": str(observation_error),
            }
        observations[name] = row
    return observations


def _validate_owned_bootstrap(
    worker,
    path,
    arm,
    envelope_sha256,
    project,
    prefix,
    prefix_binding,
    runtime_environment,
    transport_environment,
    ownership_record,
):
    document = worker._read_json_file(path, 1 << 20)
    facts = document.get("interpreter_facts") if type(document) is dict else None
    observed_prefix = worker._check_bootstrap(prefix, facts)
    if observed_prefix != prefix_binding["directory_identity"]:
        raise ValueError("bootstrap prefix identity differs from retained arm prefix")
    expected = dict(
        schema_version=1,
        status="OWNED_BOOTSTRAP_VERIFIED",
        arm=arm,
        envelope_sha256=envelope_sha256,
        ownership_record_sha256=_digest(Path(ownership_record).read_bytes()),
        source_manifest_sha256=_digest(_canonical(project)),
        prefix=str(prefix),
        prefix_identity=prefix_binding["directory_identity"],
        runtime_environment_sha256=_digest(_canonical(runtime_environment)),
        transport_environment=transport_environment,
        interpreter_facts=facts,
        scientific_validation_passed=False,
    )
    if _canonical(document) != _canonical(expected):
        raise ValueError("bootstrap record differs from owned request and parent bindings")
    return document


def _ensure_fresh_directory(path, parent):
    path = Path(os.path.abspath(path))
    parent = Path(parent).resolve()
    if (
        not path.is_relative_to(parent)
        or path == parent
        or path.resolve() != path
        or path.exists()
        or path.is_symlink()
    ):
        raise ValueError(
            "requested directory must be a fresh canonical child of its fixed root"
        )
    path.mkdir(parents=False, mode=0o700)
    return path


def _prepare_work(work, environment):
    work.mkdir(parents=True, mode=0o700)
    roots = {
        Path(value)
        for value in environment.values()
        if type(value) is str and value.startswith(str(work) + os.sep)
    }
    for path in sorted(roots, key=lambda p: (len(p.parts), str(p))):
        if not path.is_relative_to(work):
            raise ValueError("runtime environment cache escaped private arm work")
        path.mkdir(parents=True, exist_ok=True)
    prefix = work / "cache/python"
    prefix.mkdir(parents=True)
    if os.listdir(prefix):
        raise ValueError("arm bytecode prefix was not created empty")
    return prefix


def _bootstrap_project_capture(root):
    root = Path(root)
    info = root.lstat()
    if root.resolve() != root or not stat.S_ISDIR(info.st_mode):
        raise ValueError("project root is not canonical")
    suffixes = tuple(
        dict.fromkeys((".py", ".pyc", *importlib.machinery.EXTENSION_SUFFIXES))
    )
    for stem in ("adapters/__init__", "adapters/vllm_capacity/__init__"):
        if any(
            (root / (stem + s)).exists() or (root / (stem + s)).is_symlink()
            for s in suffixes
        ):
            raise ValueError("adapter namespace initializer must remain absent")
    files = {}
    total = 0
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for relative in PROJECT_FILES:
            opened = [root_fd]
            try:
                for component in Path(relative).parts[:-1]:
                    opened.append(
                        os.open(
                            component,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=opened[-1],
                        )
                    )
                fd = os.open(
                    Path(relative).name,
                    os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
                    dir_fd=opened[-1],
                )
                opened.append(fd)
                leaf = os.fstat(fd)
                if not stat.S_ISREG(leaf.st_mode) or leaf.st_size > 2 << 20:
                    raise ValueError(
                        "project source is not canonical bounded regular file"
                    )
                chunks = []
                remaining = leaf.st_size
                while remaining:
                    chunk = os.read(fd, min(1 << 20, remaining))
                    if not chunk:
                        raise ValueError("project source ended early")
                    chunks.append(chunk)
                    remaining -= len(chunk)
                if os.read(fd, 1):
                    raise ValueError("project source length changed")
                raw = b"".join(chunks)
                after = os.fstat(fd)
                identity = [
                    leaf.st_dev,
                    leaf.st_ino,
                    leaf.st_mode,
                    leaf.st_size,
                    leaf.st_mtime_ns,
                    leaf.st_ctime_ns,
                ]
                if identity != [
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ]:
                    raise ValueError("project source changed during read")
                ancestors = []
                for ancestor in opened[:-1]:
                    observed = os.fstat(ancestor)
                    ancestors.append([observed.st_dev, observed.st_ino])
                total += len(raw)
                files[relative] = dict(
                    length=len(raw),
                    sha256=_digest(raw),
                    identity=identity,
                    ancestors=ancestors,
                )
            finally:
                for fd in reversed(opened[1:]):
                    os.close(fd)
    except OSError as error:
        raise ValueError(
            "project source namespace changed during anchored read"
        ) from error
    finally:
        os.close(root_fd)
    if total > 16 << 20:
        raise ValueError("project source closure exceeds total bound")
    current = root.lstat()
    if [current.st_dev, current.st_ino] != [info.st_dev, info.st_ino]:
        raise ValueError("project root changed during source capture")
    return dict(
        root=str(root),
        root_identity=[info.st_dev, info.st_ino],
        files=files,
        total_bytes=total,
    )


def _verify_revision_sources(project, revision):
    for relative, row in project["files"].items():
        result = subprocess.run(
            ["git", "show", revision + ":" + relative], cwd=ROOT, capture_output=True
        )
        if result.returncode:
            if relative not in {
                "scripts/eval/hf_owned_worker.py",
                "scripts/eval/hf_routing_runner.py",
            }:
                raise ValueError(
                    "fixed project source is absent from selected revision: " + relative
                )
        elif _digest(result.stdout) != row["sha256"]:
            raise ValueError(
                "tracked project source differs from selected revision: " + relative
            )


def _check_parent_bootstrap(prefix):
    flags = sys.flags
    if str(Path(sys.executable).resolve()) != PINNED_PYTHON or any(
        type(getattr(flags, k)) is not int or getattr(flags, k) != 1
        for k in (
            "isolated",
            "no_site",
            "no_user_site",
            "ignore_environment",
            "dont_write_bytecode",
        )
    ):
        raise ValueError("runner requires pinned -I -S -B interpreter")
    expected = [
        "/opt/miniconda3/lib/python313.zip",
        "/opt/miniconda3/lib/python3.13",
        "/opt/miniconda3/lib/python3.13/lib-dynload",
    ]
    if sys.path != expected:
        raise ValueError(
            "runner initial sys.path differs from pinned standard library roots"
        )
    if sys.pycache_prefix is not None:
        raise ValueError("runner bytecode prefix must be unset before private creation")
    sys.pycache_prefix = str(prefix)
    return _parent_prefix_check(prefix, None)


def _prefix_ancestor_binding(path):
    path = Path(path)
    rows = []
    for component in reversed(path.parents):
        if component == Path("/"):
            continue
        info = component.lstat()
        if not stat.S_ISDIR(info.st_mode) or component.resolve() != component:
            raise ValueError("bytecode prefix ancestor is not canonical")
        rows.append([str(component), info.st_dev, info.st_ino])
    return rows


def _parent_prefix_check(prefix, identity):
    prefix = Path(prefix)
    info = prefix.lstat()
    observed = {
        "directory_identity": [info.st_dev, info.st_ino],
        "ancestors": _prefix_ancestor_binding(prefix),
    }
    if (
        sys.pycache_prefix != str(prefix)
        or sys.flags.dont_write_bytecode != 1
        or prefix.is_symlink()
        or prefix.resolve() != prefix
        or not stat.S_ISDIR(info.st_mode)
        or os.listdir(prefix)
        or (identity is not None and observed != identity)
    ):
        raise ValueError("parent bytecode prefix property, identity, or contents changed")
    return observed


def _arm_prefix_check(worker, prefix, retained):
    current = {
        "directory_identity": worker._check_empty_prefix(prefix),
        "ancestors": worker._path_binding(prefix),
    }
    if current != retained:
        raise ValueError("arm bytecode prefix identity or ancestors changed")
    return current


def _reject_parent_preloads():
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
        raise ValueError("runner has preloaded site or inference module: " + loaded[0])


def _import_paths():
    additions = [
        str(ROOT / "scripts/eval"),
        str(ROOT / "adapters/vllm_capacity"),
        str(ROOT),
    ]
    for path in reversed(additions):
        if path not in sys.path:
            sys.path.insert(0, path)


def _freeze_real(metadata_bundle, device_name):
    inventory = __import__("evaluation_inventory")
    runtime = __import__("hf_runtime_sources")
    startup = __import__("hf_startup_sources")
    tuning = __import__("hf_moe_tuning")
    verify = __import__("verify_hf_metadata")
    metadata = inventory.load_hf_snapshot(metadata_bundle)
    inventory._unpack(metadata)
    verify.check_current_inputs(metadata_bundle)
    if inventory.load_hf_snapshot(metadata_bundle) != metadata:
        raise ValueError("metadata changed around initial current-input check")
    runtime_snapshot = runtime.collect_runtime_sources(include_tuning=True)
    startup_snapshot = startup.collect_startup_sources(runtime_snapshot)
    tuning_snapshot = tuning.collect_tuning_inputs(
        metadata, runtime_snapshot, device_name
    )
    return metadata, runtime_snapshot, startup_snapshot, tuning_snapshot


def _recheck_real(snapshots, project):
    inventory = __import__("evaluation_inventory")
    runtime = __import__("hf_runtime_sources")
    startup = __import__("hf_startup_sources")
    tuning = __import__("hf_moe_tuning")
    verify = __import__("verify_hf_metadata")
    metadata, primary, start, tuned = snapshots
    if inventory.load_hf_snapshot(metadata.bundle) != metadata:
        raise ValueError("current metadata bundle differs from retained snapshot")
    verify.check_current_inputs(metadata.bundle)
    if inventory.load_hf_snapshot(metadata.bundle) != metadata:
        raise ValueError("metadata changed around current-input check")
    runtime.recheck_runtime_sources(primary)
    startup.recheck_startup_sources(start, primary)
    tuning.recheck_tuning_inputs(tuned, metadata, primary, DEVICE_NAME)
    __import__("hf_owned_worker").recheck_project_sources(project)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata-bundle", type=Path, required=True)
    parser.add_argument("--fresh-out", type=Path, required=True)
    parser.add_argument("--selected-uuid", required=True)
    return parser


def run_triplet(metadata_bundle, fresh_out, selected_uuid, *, _test_dependencies=None):
    if (
        type(selected_uuid) is not str
        or not selected_uuid.startswith("GPU-")
        or "," in selected_uuid
    ):
        raise ValueError("selected physical GPU UUID is invalid")
    test_only = _test_dependencies is not None
    git_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if re.fullmatch("[0-9a-f]{40}", git_commit) is None:
        raise ValueError("git revision binding is invalid")
    depkeys = {
        "freeze_inputs",
        "topology_probe",
        "capture_project",
        "guard_factory",
        "run_child",
        "current_check",
        "source_recheck",
        "bootstrap_facts",
        "signal_state",
    }
    if test_only and (
        type(_test_dependencies) is not dict or set(_test_dependencies) != depkeys
    ):
        raise ValueError("invalid private runner dependencies")
    gold = ROOT / "results/gold"
    gold.mkdir(parents=True, exist_ok=True)
    out = Path(os.path.abspath(fresh_out))
    if (
        not out.is_relative_to(gold)
        or out == gold
        or out.exists()
        or out.is_symlink()
        or out.resolve() != out
    ):
        raise ValueError("fresh output must be a new canonical results/gold child")
    out.mkdir(parents=True, mode=0o700)
    attempt_name = out.name + "-" + uuid.uuid4().hex
    work_root = ROOT / "results/tmp/hf-routing" / attempt_name
    work_root.parent.mkdir(parents=True, exist_ok=True)
    if work_root.exists() or work_root.is_symlink():
        raise ValueError("private work root already exists")
    work_root.mkdir(mode=0o700)
    process_root = out / "process"
    arms_root = out / "arms"
    process_root.mkdir()
    arms_root.mkdir()
    parent_prefix = work_root / "parent/cache/python"
    parent_prefix.mkdir(parents=True)
    facts = _test_dependencies["bootstrap_facts"](parent_prefix) if test_only else None
    if not test_only:
        _reject_parent_preloads()
        parent_prefix_identity = _check_parent_bootstrap(parent_prefix)
    else:
        parent_prefix_identity = {
            "directory_identity": [
                parent_prefix.lstat().st_dev,
                parent_prefix.lstat().st_ino,
            ],
            "ancestors": _prefix_ancestor_binding(parent_prefix),
        }
    # Capture the fixed bytes before adding project paths or importing helpers.
    project = (
        _test_dependencies["capture_project"](ROOT)
        if test_only
        else _bootstrap_project_capture(ROOT)
    )
    if (
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        != git_commit
    ):
        raise ValueError("git revision changed during project source capture")
    if not test_only:
        _verify_revision_sources(project, git_commit)
        _parent_prefix_check(parent_prefix, parent_prefix_identity)
    _import_paths()
    worker = __import__("hf_owned_worker")
    attempt_identity = worker._directory_identity(out)
    if test_only:
        worker._check_bootstrap(parent_prefix, facts)
    else:
        worker.recheck_project_sources(project)
        _parent_prefix_check(parent_prefix, parent_prefix_identity)
    protocol = __import__("hf_routing_worker")
    platform_environment = {
        key: os.environ[key]
        for key in worker.PLATFORM_ENV
        if key in os.environ
    }
    environments = {}
    prefixes = {}
    prefix_identities = {}
    works = {}
    for arm in ARMS:
        work = work_root / arm
        works[arm] = work
        env = protocol.make_environment(work, selected_uuid, platform_environment)
        environments[arm] = env
        prefixes[arm] = _prepare_work(work, env)
        prefix_info = prefixes[arm].lstat()
        prefix_identities[arm] = {
            "directory_identity": [prefix_info.st_dev, prefix_info.st_ino],
            "ancestors": worker._path_binding(prefixes[arm]),
        }
    if test_only:
        run_child = _test_dependencies["run_child"]
        guard_factory = _test_dependencies["guard_factory"]
        signal_scope = nullcontext(_test_dependencies["signal_state"]())
    else:
        matrix = __import__("run_matrix")
        resource = __import__("resource_guard")
        run_child = matrix.run_child
        guard_factory = resource.ResourceGuard
        signal_scope = matrix.signal_state()
    run_id = "hf-owned-" + uuid.uuid4().hex
    env_fingerprint = _digest(
        _canonical(
            dict(
                selected_uuid=selected_uuid,
                device_name=DEVICE_NAME,
                capability=DEVICE_CAPABILITY,
                platform=platform_environment,
            )
        )
    )
    task = dict(resource_class="GPU_EXCLUSIVE", gpu_uuid=selected_uuid)
    guard = guard_factory(ROOT / "results", out, task, project_root=ROOT)
    result = dict(
        schema_version=1,
        status="FAILED",
        provenance="MOCK" if test_only else "UNVALIDATED_ROUTING_CAPTURE",
        test_only=test_only,
        scientific_validation_passed=False,
        run_id=run_id,
        gpu_uuid=selected_uuid,
        private_work_root=str(work_root),
        arms=[],
    )
    error = None
    snapshots = None
    retained_wires = []
    active_arm_record = None
    active_arm_stage = None
    signal_stack = ExitStack()
    signals = signal_stack.enter_context(signal_scope)
    try:
        with guard:
            topology = _validate_topology(
                (_test_dependencies["topology_probe"] if test_only else _full_topology)(
                    selected_uuid
                ),
                selected_uuid,
            )
            snapshots = (
                _test_dependencies["freeze_inputs"] if test_only else _freeze_real
            )(metadata_bundle, DEVICE_NAME)
            if type(snapshots) is not tuple or len(snapshots) != 4:
                raise ValueError("frozen input set is invalid")
            result["topology"] = topology
            result["input_bindings"] = dict(
                metadata_receipt_sha256=_digest(snapshots[0].receipt_bytes),
                metadata_complete_sha256=_digest(snapshots[0].complete_bytes),
                runtime_manifest_sha256=_digest(snapshots[1].manifest_bytes),
                startup_manifest_sha256=_digest(snapshots[2].manifest_bytes),
                tuning_manifest_sha256=_digest(snapshots[3].manifest_bytes),
                project_source_manifest_sha256=_digest(_canonical(project)),
            )

            def after_checks(arm, wire_dir, envelope_sha, wire_binding, phase):
                errors = []
                first_error = None
                checks = (
                    (
                        "current",
                        lambda: (
                            _test_dependencies["current_check"]
                            if test_only
                            else lambda *_: _recheck_real(snapshots, project)
                        )(snapshots),
                    ),
                    (
                        "source",
                        lambda: (
                            _test_dependencies["source_recheck"]
                            if test_only
                            else worker.recheck_project_sources
                        )(project),
                    ),
                    (
                        "wire",
                        lambda: worker._read_wire_retained(
                            wire_dir / "envelope.json", envelope_sha, wire_binding
                        ),
                    ),
                    (
                        "arm-prefix",
                        lambda: _arm_prefix_check(
                            worker, prefixes[arm], prefix_identities[arm]
                        ),
                    ),
                    (
                        "parent-prefix",
                        lambda: (
                            True
                            if test_only
                            else _parent_prefix_check(
                                parent_prefix, parent_prefix_identity
                            )
                        ),
                    ),
                    ("guard", lambda: guard.check(phase)),
                )
                for name, check in checks:
                    try:
                        check()
                    except BaseException as post_error:
                        if first_error is None:
                            first_error = post_error
                        errors.append(
                            dict(
                                check=name,
                                type=type(post_error).__name__,
                                message=str(post_error),
                            )
                        )
                return errors, first_error

            for arm in ARMS:
                active_arm_record = None
                active_arm_stage = "preflight"
                if not test_only:
                    _parent_prefix_check(parent_prefix, parent_prefix_identity)
                if (arms_root / arm).exists() or (arms_root / arm).is_symlink():
                    raise ValueError("arm output exists before owned worker")
                (
                    _test_dependencies["current_check"]
                    if test_only
                    else lambda *_: _recheck_real(snapshots, project)
                )(snapshots)
                (
                    _test_dependencies["source_recheck"]
                    if test_only
                    else worker.recheck_project_sources
                )(project)
                process = process_root / arm
                process.mkdir()
                wire_dir = process / "wire"
                request = dict(
                    schema_version=1,
                    arm=arm,
                    run_id=run_id,
                    git_commit=git_commit,
                    environment_fingerprint=env_fingerprint,
                    gpu_uuid=selected_uuid,
                    device_name=topology["name"],
                    device_capability=topology["capability"],
                    work_dir=str(works[arm]),
                    output_dir=str(arms_root / arm),
                    process_dir=str(process),
                    attempt_dir=str(out),
                    metadata_bundle=str(Path(metadata_bundle).absolute()),
                    runtime_env=environments[arm],
                    transport_env={"PYTHONPYCACHEPREFIX": str(prefixes[arm])},
                    prompt_token_ids=PROMPTS,
                    project_sources=project,
                )
                envelope, blobs = worker._encode_wire(*snapshots, request)
                envelope_sha = worker._write_wire(wire_dir, envelope, blobs)
                _, _, wire_binding = worker._read_wire_retained(
                    wire_dir / "envelope.json", envelope_sha
                )
                retained_wires.append(
                    (wire_dir / "envelope.json", envelope_sha, wire_binding)
                )
                binding = dict(
                    arm=arm,
                    run_id=run_id,
                    attempt_dir=str(out),
                    process_dir=str(process),
                    work_dir=str(works[arm]),
                    output_dir=str(arms_root / arm),
                    gpu_uuid=selected_uuid,
                    resource_class="GPU_EXCLUSIVE",
                    envelope_sha256=envelope_sha,
                    source_manifest_sha256=_digest(_canonical(project)),
                    attempt_dir_identity=worker._directory_identity(out),
                    process_dir_identity=worker._directory_identity(process),
                )
                if test_only:
                    parent = dict(
                        pid=1, boot_id="b", start_time=1, ppid=0, pgrp=1, session=1
                    )
                else:
                    parent = __import__("run_manifest").process_identity(
                        os.getpid(), include_zombies=True
                    )
                    if not parent:
                        raise TripletFailure("parent process identity unavailable")
                callback = _ownership_callback(process, binding, parent)
                argv = [
                    PINNED_PYTHON,
                    "-I",
                    "-S",
                    "-B",
                    "-X",
                    "pycache_prefix=" + str(prefixes[arm]),
                    str(ROOT / "scripts/eval/hf_owned_worker.py"),
                    "--_owned-arm",
                    arm,
                    "--envelope",
                    str(wire_dir / "envelope.json"),
                    "--envelope-sha256",
                    envelope_sha,
                    "--ownership-record",
                    str(process / "ownership-record.json"),
                ]
                launch_env = dict(
                    environments[arm], PYTHONPYCACHEPREFIX=str(prefixes[arm])
                )
                try:
                    active_arm_stage = "launch"
                    code = run_child(
                        argv,
                        launch_env,
                        process,
                        "producer",
                        guard,
                        callback,
                        signals,
                        900,
                        1,
                        bootstrap_no_site=True,
                    )
                except BaseException as launch_error:
                    fixed_record = callback.fixed_record()
                    failed_arm = dict(
                            arm=arm,
                            status="FAILED",
                            exit_code=None,
                            exit_code_observed=False,
                            primary_error=dict(
                                stage="launch",
                                type=type(launch_error).__name__,
                                message=str(launch_error),
                            ),
                            missing_files=sorted(
                                name
                                for name in (
                                    "worker-status.json",
                                    "owned-worker-status.json",
                                    "bootstrap.json",
                                )
                                if not (
                                    (arms_root / arm / name).is_file()
                                    or (process / name).is_file()
                                )
                            ),
                            work_dir=str(works[arm]),
                            process_identity=(
                                None if fixed_record is None else fixed_record["child"]
                            ),
                            process_identity_observed=fixed_record is not None,
                        )
                    failed_arm["files"] = _attempt_file_observations(
                        worker, process, arms_root / arm
                    )
                    exit_observation_error = None
                    try:
                        remaining, uncertain, exit_observed = _owned_exit_observation(
                            callback, test_only
                        )
                    except BaseException as observation_error:
                        remaining, uncertain, exit_observed = None, None, False
                        exit_observation_error = dict(
                            check="owned-exit-observation",
                            type=type(observation_error).__name__,
                            message=str(observation_error),
                        )
                    failed_arm["owned_remaining"] = remaining
                    failed_arm["uncertain_session"] = uncertain
                    failed_arm["owned_exit_observed"] = exit_observed
                    postcheck_errors, _ = after_checks(
                        arm,
                        wire_dir,
                        envelope_sha,
                        wire_binding,
                        "after-failed-" + arm,
                    )
                    if not exit_observed or remaining or uncertain:
                        postcheck_errors.append(
                            dict(
                                check="owned-exit",
                                type="TripletFailure",
                                message="owned child cleanup is incomplete or uncertain",
                            )
                        )
                    if exit_observation_error is not None:
                        postcheck_errors.append(exit_observation_error)
                    if postcheck_errors:
                        failed_arm["postcheck_errors"] = postcheck_errors
                    result["arms"].append(failed_arm)
                    active_arm_record = failed_arm
                    raise
                fixed_record = callback.fixed_record()
                active_arm_record = dict(
                    arm=arm,
                    exit_code=code,
                    exit_code_observed=True,
                    status="ATTEMPTED",
                    work_dir=str(works[arm]),
                    process_identity=(
                        None if fixed_record is None else fixed_record["child"]
                    ),
                    process_identity_observed=fixed_record is not None,
                    owned_remaining=None,
                    uncertain_session=None,
                    owned_exit_observed=False,
                    files=_attempt_file_observations(
                        worker, process, arms_root / arm
                    ),
                )
                result["arms"].append(active_arm_record)
                try:
                    active_arm_stage = "owned-exit"
                    remaining, uncertain, exit_observed = _owned_exit_observation(
                        callback, test_only
                    )
                    active_arm_record["owned_remaining"] = remaining
                    active_arm_record["uncertain_session"] = uncertain
                    active_arm_record["owned_exit_observed"] = exit_observed
                    if not exit_observed or remaining or uncertain:
                        raise TripletFailure(
                            arm + " owned child cleanup is incomplete or uncertain"
                        )
                    active_arm_stage = "worker-status"
                    status_path = arms_root / arm / "worker-status.json"
                    if test_only and not status_path.is_file():
                        status_path = process / "worker-status.json"
                    if not status_path.is_file():
                        active_arm_record["missing_files"] = ["worker-status.json"]
                        failure_path = process / "owned-worker-failure.json"
                        if failure_path.is_file():
                            failure = json.loads(failure_path.read_bytes())
                            raise TripletFailure(
                                arm
                                + " worker failed at "
                                + str(failure.get("stage"))
                                + ": "
                                + str(failure.get("message"))
                            )
                        raise TripletFailure(
                            arm
                            + " worker status is missing and no durable worker failure was recorded"
                        )
                    status_raw = status_path.read_bytes()
                    status = json.loads(status_raw)
                    if code == 0 and status.get("status") == "ARM_RETURNED_UNVALIDATED":
                        wrapper_path = process / "owned-worker-status.json"
                        bootstrap_path = process / "bootstrap.json"
                        if not wrapper_path.is_file() or not bootstrap_path.is_file():
                            active_arm_record["missing_files"] = [
                                path.name
                                for path in (wrapper_path, bootstrap_path)
                                if not path.is_file()
                            ]
                            raise TripletFailure(
                                arm + " success is missing bootstrap/cross-binding evidence"
                            )
                        _validate_owned_bootstrap(
                            worker,
                            bootstrap_path,
                            arm,
                            envelope_sha,
                            project,
                            prefixes[arm],
                            prefix_identities[arm],
                            environments[arm],
                            request["transport_env"],
                            process / "ownership-record.json",
                        )
                        wrapper = json.loads(wrapper_path.read_bytes())
                        input_path = arms_root / arm / "input-binding.json"
                        expected_wrapper = dict(
                            schema_version=1,
                            status="ARM_RETURNED_UNVALIDATED",
                            arm=arm,
                            provenance="MOCK" if test_only else status.get("provenance"),
                            scientific_validation_passed=False,
                            envelope_sha256=envelope_sha,
                            input_binding_sha256=_digest(input_path.read_bytes()),
                            worker_status_sha256=_digest(status_raw),
                            device_observations_sha256=(
                                None
                                if test_only
                                else _digest(
                                    (process / "device-observations.json").read_bytes()
                                )
                            ),
                            source_manifest_sha256=_digest(_canonical(project)),
                        )
                        if wrapper != expected_wrapper:
                            raise TripletFailure(
                                arm
                                + " worker cross-binding differs from owned inputs/status"
                            )
                    arm_result = active_arm_record
                    arm_result.update(
                        status=status.get("status"),
                        worker_status=status,
                    )
                    failure_path = process / "owned-worker-failure.json"
                    if failure_path.is_file():
                        arm_result["owned_worker_failure"] = json.loads(
                            failure_path.read_bytes()
                        )
                    active_arm_stage = "postchecks"
                    postcheck_errors, postcheck_primary = after_checks(
                        arm, wire_dir, envelope_sha, wire_binding, "after-" + arm
                    )
                    if postcheck_errors:
                        arm_result["postcheck_errors"] = postcheck_errors
                        raise postcheck_primary
                    if (
                        code != 0
                        or status.get("status") != "ARM_RETURNED_UNVALIDATED"
                        or status.get("scientific_validation_passed") is not False
                    ):
                        active_arm_stage = "provisional-boundary"
                        primary = status.get("primary_error")
                        detail = (
                            json.dumps(primary, sort_keys=True, separators=(",", ":"))
                            if primary is not None
                            else status.get("status")
                        )
                        raise TripletFailure(
                            arm + " arm failed provisional boundary: " + str(detail)
                        )
                    active_arm_record = None
                    active_arm_stage = None
                except BaseException:
                    active_arm_record["files"] = _attempt_file_observations(
                        worker, process, arms_root / arm
                    )
                    if active_arm_stage not in ("postchecks", "provisional-boundary"):
                        secondary, _ = after_checks(
                            arm,
                            wire_dir,
                            envelope_sha,
                            wire_binding,
                            "after-failed-" + arm,
                        )
                        if secondary:
                            active_arm_record["postcheck_errors"] = secondary
                    raise
            for wire_path, wire_sha, wire_binding in retained_wires:
                worker._read_wire_retained(wire_path, wire_sha, wire_binding)
            for arm in ARMS:
                _arm_prefix_check(worker, prefixes[arm], prefix_identities[arm])
            if not test_only:
                _parent_prefix_check(parent_prefix, parent_prefix_identity)
            guard.check("triplet-final")
            _raise_for_signal(signals, "triplet-final")
            result["status"] = "PROVISIONAL_TRIPLET_RETURNED_UNVALIDATED"
    except BaseException as exception:
        error = exception
        if active_arm_record is not None and "primary_error" not in active_arm_record:
            active_arm_record["status"] = "FAILED"
            active_arm_record["primary_error"] = dict(
                stage=active_arm_stage or "arm",
                type=type(exception).__name__,
                message=str(exception),
            )
        result["error"] = dict(type=type(exception).__name__, message=str(exception))
    finally:
        if snapshots is not None:
            final_errors = []
            final_checks = [
                (
                    "current",
                    lambda: (
                        _test_dependencies["current_check"]
                        if test_only
                        else lambda *_: _recheck_real(snapshots, project)
                    )(snapshots),
                ),
                (
                    "source",
                    lambda: (
                        _test_dependencies["source_recheck"]
                        if test_only
                        else worker.recheck_project_sources
                    )(project),
                ),
            ]
            final_checks.extend(
                (
                    "wire:" + path.parent.parent.name,
                    lambda path=path, sha=sha, binding=binding: worker._read_wire_retained(
                        path, sha, binding
                    ),
                )
                for path, sha, binding in retained_wires
            )
            final_checks.extend(
                (
                    "prefix:" + arm,
                    lambda arm=arm: _arm_prefix_check(
                        worker, prefixes[arm], prefix_identities[arm]
                    ),
                )
                for arm in ARMS
            )
            if not test_only:
                final_checks.append(
                    (
                        "parent-prefix",
                        lambda: _parent_prefix_check(
                            parent_prefix, parent_prefix_identity
                        ),
                    )
                )
            for name, check in final_checks:
                try:
                    check()
                except BaseException as final_error:
                    final_errors.append(
                        dict(
                            check=name,
                            type=type(final_error).__name__,
                            message=str(final_error),
                        )
                    )
                    if error is None:
                        error = final_error
            if final_errors:
                result["final_recheck_error"] = final_errors[0]
                result["final_recheck_errors"] = final_errors
                result["status"] = "FAILED"
        try:
            signal_number = _signal_number(signals)
        except BaseException as signal_error:
            signal_row = dict(
                check="signal-state",
                type=type(signal_error).__name__,
                message=str(signal_error),
            )
            result.setdefault("final_recheck_errors", []).append(signal_row)
            result.setdefault("final_recheck_error", signal_row)
            if error is None:
                error = signal_error
                result["error"] = dict(
                    type=type(signal_error).__name__, message=str(signal_error)
                )
        else:
            if signal_number is not None:
                result["signal"] = signal_number
                signal_error = TripletFailure(
                    f"signal {signal_number} observed during finalization"
                )
                signal_row = dict(
                    check="signal-state",
                    type=type(signal_error).__name__,
                    message=str(signal_error),
                )
                result.setdefault("final_recheck_errors", []).append(signal_row)
                result.setdefault("final_recheck_error", signal_row)
                if error is None:
                    error = signal_error
                    result["error"] = dict(
                        type=type(signal_error).__name__, message=str(signal_error)
                    )
        if error is not None:
            result["status"] = "FAILED"
        result["signal_acceptance_cutoff"] = (
            "after-initial-triplet-status-fsync-before-final-return"
        )
        status_path = out / "triplet-status.json"
        try:
            written_raw = _canonical(result) + b"\n"
            status_identity = _write_exclusive(status_path, result)
            _read_owned_status_binding(
                status_path, out, attempt_identity, written_raw, status_identity
            )
            postwrite_signal_error = None
            try:
                postwrite_signal = _signal_number(signals)
            except BaseException as signal_error:
                postwrite_signal = None
                postwrite_signal_error = signal_error
            if postwrite_signal_error is not None:
                signal_row = dict(
                    check="signal-state-after-status-fsync",
                    type=type(postwrite_signal_error).__name__,
                    message=str(postwrite_signal_error),
                )
                result.setdefault("final_recheck_errors", []).append(signal_row)
                result.setdefault("final_recheck_error", signal_row)
                if error is None:
                    error = postwrite_signal_error
                    result["error"] = dict(
                        type=type(postwrite_signal_error).__name__,
                        message=str(postwrite_signal_error),
                    )
                result["status"] = "FAILED"
            elif (
                postwrite_signal is not None
                and result.get("signal") != postwrite_signal
            ):
                result["signal"] = postwrite_signal
                signal_error = TripletFailure(
                    f"signal {postwrite_signal} observed during triplet-status fsync"
                )
                signal_row = dict(
                    check="signal-state-after-status-fsync",
                    type=type(signal_error).__name__,
                    message=str(signal_error),
                )
                result.setdefault("final_recheck_errors", []).append(signal_row)
                result.setdefault("final_recheck_error", signal_row)
                if error is None:
                    error = signal_error
                    result["error"] = dict(
                        type=type(signal_error).__name__, message=str(signal_error)
                    )
                result["status"] = "FAILED"
            if postwrite_signal_error is not None or (
                postwrite_signal is not None
                and _canonical(result) + b"\n" != written_raw
            ):
                _read_owned_status_binding(
                    status_path, out, attempt_identity, written_raw, status_identity
                )
                _replace_json(status_path, result)
                _read_owned_status_binding(
                    status_path, out, attempt_identity, _canonical(result) + b"\n"
                )
        finally:
            signal_stack.close()
    if error is not None:
        if isinstance(error, (ValueError, TripletFailure)):
            raise error
        raise TripletFailure(str(error)) from error
    return result


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        run_triplet(args.metadata_bundle, args.fresh_out, args.selected_uuid)
    except BaseException as error:
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

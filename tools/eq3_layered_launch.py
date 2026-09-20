#!/usr/bin/env python3
"""Fail-closed launcher for an approved, bounded EQ3 thermal-only run.

The launch document intentionally does not contain the scientific manifest
hash.  Instead, the scientific manifest binds the exact launch-document bytes
as ``layered-launch-manifest`` and the independent user confirmation binds the
scientific manifest hash.  This keeps the binding acyclic.
"""

import argparse
import hashlib
import json
import math
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from eq3_experiment_gate import GateError, load_json, observed_git_state, validate_gate


SCHEMA = "eq3-layered-launch-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_THREADS = 1
MAX_RAM_GIB = 12
MAX_WATCHDOG_SECONDS = 600
MAX_OUTPUT_GIB = 4
RECEIPT_NAMES = {"DONE.json", "FAILED.json"}


class LaunchError(ValueError):
    """Stable refusal/failure carrying whether a child process was started."""

    def __init__(self, code, message, launch_performed=False):
        super().__init__(message)
        self.code = code
        self.launch_performed = launch_performed


def _fail(code, message, launch_performed=False):
    raise LaunchError(code, message, launch_performed)


def _exact_keys(value, required, label):
    if not isinstance(value, dict):
        _fail("LAUNCH_SCHEMA_INVALID", f"{label} must be an object")
    missing = set(required) - set(value)
    extra = set(value) - set(required)
    if missing or extra:
        _fail("LAUNCH_SCHEMA_INVALID", f"{label} missing={sorted(missing)} extra={sorted(extra)}")


def _string(value, label):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        _fail("LAUNCH_SCHEMA_INVALID", f"{label} must be a non-empty string without NUL")
    return value


def _number(value, label, *, integer=False):
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected) or not math.isfinite(value):
        _fail("LAUNCH_SCHEMA_INVALID", f"{label} has an invalid numeric value")
    return value


def _relative(root, value, label, *, must_be_file=False, must_be_dir=False):
    text = _string(value, label)
    candidate = Path(text)
    if candidate.is_absolute():
        _fail("LAUNCH_PATH_INVALID", f"{label} must be relative to --root")
    resolved_root = Path(root).resolve()
    resolved = (resolved_root / candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        _fail("LAUNCH_PATH_INVALID", f"{label} escapes --root")
    if must_be_file and not resolved.is_file():
        _fail("LAUNCH_PATH_INVALID", f"{label} is not a file: {text}")
    if must_be_dir and not resolved.is_dir():
        _fail("LAUNCH_PATH_INVALID", f"{label} is not a directory: {text}")
    return resolved


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact(value, root, label):
    _exact_keys(value, {"logical_id", "semantic_role", "sha256", "path"}, label)
    logical_id = _string(value["logical_id"], f"{label}.logical_id")
    _string(value["semantic_role"], f"{label}.semantic_role")
    declared = value["sha256"]
    if not isinstance(declared, str) or not SHA256_RE.fullmatch(declared):
        _fail("LAUNCH_SCHEMA_INVALID", f"{label}.sha256 must be lowercase SHA-256")
    path = _relative(root, value["path"], f"{label}.path", must_be_file=True)
    return logical_id, declared, path


def _scientific_artifacts(manifest):
    values = []
    values.extend(manifest["code"]["artifacts"])
    values.extend(manifest["dependencies"])
    values.extend(manifest["inputs"])
    values.extend(item["evidence"] for item in manifest["prerequisites"])
    return values


def _bound_artifact(manifest, logical_id):
    matches = [item for item in _scientific_artifacts(manifest)
               if item["logical_id"] == logical_id]
    if len(matches) != 1:
        _fail("LAUNCH_ARTIFACT_BINDING_MISMATCH",
              f"{logical_id!r} must have exactly one scientific-manifest binding")
    return matches[0]


def _validate_command_paths(argv):
    for index, token in enumerate(argv):
        _string(token, f"command.argv[{index}]")
        path_like = token.split("=", 1)[1] if token.startswith("--") and "=" in token else token
        if Path(path_like).is_absolute():
            _fail("LAUNCH_PATH_INVALID", f"command.argv[{index}] embeds an absolute path")


def _sealed_input_files(directory):
    """Return regular input files, refusing links that could escape the seal."""
    files = set()
    for current, directories, names in os.walk(directory, followlinks=False):
        current_path = Path(current)
        for name in directories:
            if (current_path / name).is_symlink():
                _fail("UNBOUND_INPUT_PRESENT", "input directory must not contain symlinked directories")
        for name in names:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                _fail("UNBOUND_INPUT_PRESENT", "input directory must contain only regular files")
            files.add(path.resolve())
    return files


def validate_launch(manifest, approval, launch, launch_path, root, approval_path=None,
                    observed_code_revision=None, observed_dirty_diff_sha256=None):
    """Validate all approval, byte binding, stage, artifact and safety gates."""
    try:
        gate = validate_gate(
            manifest, approval, root, approval_path=approval_path,
            observed_code_revision=observed_code_revision,
            observed_dirty_diff_sha256=observed_dirty_diff_sha256,
        )
    except GateError as exc:
        raise LaunchError(exc.code, str(exc), False) from exc

    required = {"schema_version", "experiment_id", "version", "run_id", "stage",
                "readiness", "backend", "artifacts", "command", "output", "limits"}
    _exact_keys(launch, required, "launch")
    if launch["schema_version"] != SCHEMA:
        _fail("LAUNCH_SCHEMA_INVALID", f"unsupported launch schema: {launch['schema_version']!r}")
    if launch["experiment_id"] != gate["experiment_id"] or launch["version"] != gate["version"]:
        _fail("RUN_BINDING_MISMATCH", "launch experiment_id/version differs from scientific manifest")
    run_id = _string(launch["run_id"], "run_id")
    if not SAFE_ID_RE.fullmatch(run_id):
        _fail("RUN_BINDING_MISMATCH", "run_id is not a portable path component")
    if launch["stage"] != "thermal_only":
        _fail("STAGE_NOT_ALLOWED", "only the approved thermal_only stage may execute")
    if launch["readiness"] != "READY":
        _fail("READINESS_BLOCKED", f"launch readiness is {launch['readiness']!r}, not READY")

    launch_file = Path(launch_path).resolve()
    if not launch_file.is_file():
        _fail("LAUNCH_PATH_INVALID", "launch document is not a file")
    bound_launch = _bound_artifact(manifest, "layered-launch-manifest")
    if _hash(launch_file) != bound_launch["sha256"]:
        _fail("ARTIFACT_HASH_MISMATCH", "launch document bytes differ from scientific binding")

    backend_id, backend_sha, backend_path = _artifact(launch["backend"], root, "backend")
    backend_binding = _bound_artifact(manifest, backend_id)
    if backend_sha != backend_binding["sha256"] or launch["backend"]["path"] != backend_binding["path"]:
        _fail("BACKEND_HASH_MISMATCH", "backend does not match its scientific-manifest binding")
    if _hash(backend_path) != backend_sha:
        _fail("BACKEND_HASH_MISMATCH", "backend bytes do not match the declared SHA-256")
    if not os.access(backend_path, os.X_OK):
        _fail("BACKEND_NOT_EXECUTABLE", "bound backend is not executable")

    if not isinstance(launch["artifacts"], list) or not launch["artifacts"]:
        _fail("LAUNCH_SCHEMA_INVALID", "artifacts must be a non-empty array")
    seen = set()
    for index, item in enumerate(launch["artifacts"]):
        logical_id, declared, path = _artifact(item, root, f"artifacts[{index}]")
        if logical_id in seen:
            _fail("LAUNCH_SCHEMA_INVALID", "artifact logical_id values must be unique")
        seen.add(logical_id)
        binding = _bound_artifact(manifest, logical_id)
        if declared != binding["sha256"] or item["path"] != binding["path"]:
            _fail("LAUNCH_ARTIFACT_BINDING_MISMATCH", f"{logical_id!r} differs from its binding")
        if _hash(path) != declared:
            _fail("ARTIFACT_HASH_MISMATCH", f"{logical_id!r} bytes differ from declared SHA-256")

    _exact_keys(launch["command"], {"argv", "cwd"}, "command")
    argv = launch["command"]["argv"]
    if not isinstance(argv, list) or not argv:
        _fail("LAUNCH_SCHEMA_INVALID", "command.argv must be a non-empty array")
    _validate_command_paths(argv)
    if argv[0] != launch["backend"]["path"]:
        _fail("BACKEND_BINDING_MISMATCH", "command.argv[0] is not the bound backend path")
    cwd = _relative(root, launch["command"]["cwd"], "command.cwd", must_be_dir=True)

    declared_inputs = set()
    for item in launch["artifacts"]:
        path = _relative(root, item["path"], "artifact.path", must_be_file=True)
        if cwd not in path.parents:
            _fail("LAUNCH_PATH_INVALID", "every launch artifact must be inside command.cwd")
        if (path.name in RECEIPT_NAMES or path.name in
                {'stdout.log', 'stderr.log', 'rc_energy_receipt.json', 'rc_energy_receipt.json.tmp',
                 'xaxis.txt', 'yaxis.txt'} or re.fullmatch(r'field_\d+\.txt', path.name)):
            _fail("RESERVED_INPUT_NAME", f"input artifact uses reserved name {path.name}")
        declared_inputs.add(path)
    actual_inputs = _sealed_input_files(cwd)
    if actual_inputs != declared_inputs:
        unknown = sorted(str(path.relative_to(cwd)) for path in actual_inputs - declared_inputs)
        missing = sorted(str(path.relative_to(cwd)) for path in declared_inputs - actual_inputs)
        _fail("UNBOUND_INPUT_PRESENT",
              f"command.cwd is not sealed to declared artifacts; unknown={unknown} missing={missing}")

    _exact_keys(launch["output"], {"path", "max_new_gib"}, "output")
    output_path = _relative(root, launch["output"]["path"], "output.path")
    if run_id not in Path(launch["output"]["path"]).parts:
        _fail("RUN_BINDING_MISMATCH", "output.path must contain run_id as an exact component")
    if output_path.exists():
        _fail("OUTPUT_ALREADY_EXISTS", "output.path must not already exist")
    if not output_path.parent.is_dir():
        _fail("LAUNCH_PATH_INVALID", "output.path parent directory must already exist")
    if cwd == output_path or cwd in output_path.parents or output_path in cwd.parents:
        _fail("LAUNCH_PATH_INVALID", "input and output directories must not overlap")
    output_gib = _number(launch["output"]["max_new_gib"], "output.max_new_gib")

    _exact_keys(launch["limits"],
                {"threads", "ram_gib", "watchdog_seconds", "gpu_compute_minutes"}, "limits")
    threads = _number(launch["limits"]["threads"], "limits.threads", integer=True)
    ram_gib = _number(launch["limits"]["ram_gib"], "limits.ram_gib")
    watchdog = _number(launch["limits"]["watchdog_seconds"], "limits.watchdog_seconds")
    gpu_minutes = _number(launch["limits"]["gpu_compute_minutes"], "limits.gpu_compute_minutes")
    if gate.get("resource_policy") == "PER_EXPERIMENT_USER_CONFIRMED":
        limits=gate["resource_limits"]
        if (threads != limits["threads"] or ram_gib != limits["process_ram_gib"] or
                watchdog != limits["watchdog_s"] or output_gib != limits["point_disk_gib"] or
                gpu_minutes != 0):
            _fail("LAUNCH_LIMIT_EXCEEDED", "launch limits differ from the bound per-experiment stage scope")
    elif (threads != MAX_THREADS or not 0 < ram_gib <= MAX_RAM_GIB or
            not 0 < watchdog <= MAX_WATCHDOG_SECONDS or gpu_minutes != 0 or
            not 0 < output_gib <= MAX_OUTPUT_GIB):
        _fail("LAUNCH_LIMIT_EXCEEDED", "launch exceeds 1 thread, 12 GiB RAM, 600 s, 4 GiB, or zero-GPU limits")
    requested = manifest["resource_budget"]["requested"]
    if (requested["build_threads"] > threads or requested["ram_gib"] > ram_gib or
            requested["disk_gib"] > output_gib or requested["gpu_compute_minutes"] != 0 or
            requested["cpu_configurations"] != 1 or requested["executions_per_configuration"] != 1):
        _fail("LAUNCH_LIMIT_EXCEEDED", "scientific resource request is not covered by this single-run envelope")

    result={"status": "READY_TO_LAUNCH", "experiment_id": gate["experiment_id"],
            "version": gate["version"], "run_id": run_id, "launch_performed": False}
    if gate.get("resource_policy") == "PER_EXPERIMENT_USER_CONFIRMED":
        result.update(resource_policy=gate["resource_policy"],resource_limits=gate["resource_limits"])
    return result


def _output_bytes(path):
    return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())


def _terminate(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        pass


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sample_linux_rss(pid):
    """Read a point sample for one Linux process; return None when unavailable."""
    try:
        text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    values = {}
    for line in text.splitlines():
        name, separator, remainder = line.partition(":")
        if separator and name in {"VmRSS", "VmHWM"}:
            fields = remainder.split()
            if len(fields) != 2 or fields[1] != "kB" or not fields[0].isdigit():
                return None
            values[name] = int(fields[0])
    if set(values) != {"VmRSS", "VmHWM"}:
        return None
    return values["VmRSS"], values["VmHWM"]


def _record_memory_sample(pid, samples):
    observed = _sample_linux_rss(pid)
    if observed is None:
        return
    rss_kib, hwm_kib = observed
    samples["sample_count"] += 1
    samples["sampled_peak_rss_kib"] = max(
        rss_kib, samples["sampled_peak_rss_kib"] or 0,
    )
    samples["sampled_hwm_kib"] = max(
        hwm_kib, samples["sampled_hwm_kib"] or 0,
    )


def _output_hashes(output):
    records = []
    for path in sorted(Path(output).rglob("*")):
        relative = path.relative_to(output)
        if relative.name in RECEIPT_NAMES or relative.name in {".DONE.json.tmp", ".FAILED.json.tmp"}:
            continue
        if path.is_symlink():
            records.append({"path": relative.as_posix(), "sha256": "UNKNOWN",
                            "size_bytes": "UNKNOWN", "type": "SYMLINK"})
            continue
        if not path.is_file():
            continue
        try:
            records.append({"path": relative.as_posix(), "sha256": _hash(path),
                            "size_bytes": path.stat().st_size, "type": "REGULAR_FILE"})
        except OSError:
            records.append({"path": relative.as_posix(), "sha256": "UNKNOWN",
                            "size_bytes": "UNKNOWN", "type": "REGULAR_FILE"})
    return records


def _write_receipt(output, name, receipt):
    """Publish a receipt without ever replacing a pre-existing path."""
    target = Path(output) / name
    temporary = Path(output) / f".{name}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _receipt(launch, manifest, argv, output, start_utc, started_monotonic,
             status, exit_code, launch_performed, reason, memory_samples):
    return {
        "schema_version": "eq3-layered-run-receipt-v1",
        "status": status,
        "experiment_id": launch["experiment_id"],
        "version": launch["version"],
        "run_id": launch["run_id"],
        "stage": launch["stage"],
        "start_utc": start_utc,
        "end_utc": _utc_now(),
        "wall_time_seconds": round(time.monotonic() - started_monotonic, 6),
        "argv": argv,
        "exit_code": exit_code if exit_code is not None else "UNKNOWN",
        "launch_performed": launch_performed,
        "backend_sha256": launch["backend"]["sha256"],
        "scientific_manifest_hash": manifest["canonical_manifest_hash"],
        "limits": launch["limits"] | {"max_new_gib": launch["output"]["max_new_gib"]},
        "bound_inputs": [
            {"logical_id": item["logical_id"], "path": item["path"],
             "sha256": item["sha256"]}
            for item in launch["artifacts"]
        ],
        "output_file_sha256": _output_hashes(output),
        "max_rss": "UNKNOWN",
        "sampled_peak_rss_kib": (
            memory_samples["sampled_peak_rss_kib"]
            if memory_samples["sampled_peak_rss_kib"] is not None else "UNKNOWN"
        ),
        "sampled_hwm_kib": (
            memory_samples["sampled_hwm_kib"]
            if memory_samples["sampled_hwm_kib"] is not None else "UNKNOWN"
        ),
        "sample_count": memory_samples["sample_count"],
        "rss_sampling_scope": "solver_process_pid_only_not_process_tree",
        "rss_sampling_interpretation": "lower_bound_not_exact_final_peak",
        "reason": reason,
    }


def _best_effort_failed_receipt(output, launch, manifest, argv, start_utc,
                                started_monotonic, error, exit_code, memory_samples):
    receipt = _receipt(
        launch, manifest, argv, output, start_utc, started_monotonic,
        "FAILED", exit_code, error.launch_performed,
        {"code": error.code, "message": str(error)}, memory_samples,
    )
    try:
        _write_receipt(output, "FAILED.json", receipt)
    except OSError:
        pass


def execute_launch(manifest, approval, launch, launch_path, root, approval_path=None,
                   observed_code_revision=None, observed_dirty_diff_sha256=None):
    """Execute exactly one approved process after complete fail-closed validation."""
    result = validate_launch(
        manifest, approval, launch, launch_path, root, approval_path,
        observed_code_revision, observed_dirty_diff_sha256,
    )
    root = Path(root).resolve()
    backend = _relative(root, launch["backend"]["path"], "backend.path", must_be_file=True)
    input_directory = _relative(root, launch["command"]["cwd"], "command.cwd", must_be_dir=True)
    output = _relative(root, launch["output"]["path"], "output.path")
    if result.get("resource_policy") == "PER_EXPERIMENT_USER_CONFIRMED":
        limits=result["resource_limits"];task_root=root/"eq3_thermal" if (root/"eq3_thermal").is_dir() else root
        point_bytes=int(limits["point_disk_gib"]*1024**3)
        if (_output_bytes(task_root)+point_bytes>limits["task_disk_gib"]*1024**3 or
                shutil.disk_usage(root).free<point_bytes+limits["min_free_disk_gib"]*1024**3):
            _fail("LAUNCH_LIMIT_EXCEEDED", "bound task disk or free-space reserve cannot cover this point")
    if _hash(backend) != launch["backend"]["sha256"]:
        _fail("BACKEND_HASH_MISMATCH", "backend changed between validation and launch")
    output.mkdir(mode=0o750)
    argv = [str(backend), *launch["command"]["argv"][1:]]
    start_utc = _utc_now()
    started = time.monotonic()
    process = None
    memory_samples = {
        "sampled_peak_rss_kib": None,
        "sampled_hwm_kib": None,
        "sample_count": 0,
    }
    try:
        for item in launch["artifacts"]:
            source = _relative(root, item["path"], "artifact.path", must_be_file=True)
            relative = source.relative_to(input_directory)
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
            if _hash(destination) != item["sha256"]:
                _fail("ARTIFACT_HASH_MISMATCH", f"staged input changed: {item['logical_id']}")
        output_limit_bytes = int(launch["output"]["max_new_gib"] * 1024 ** 3)
        if _output_bytes(output) > output_limit_bytes:
            _fail("OUTPUT_LIMIT_EXCEEDED", "staged inputs already exceed the output byte budget", False)
        env = os.environ.copy()
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                     "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
            env[name] = "1"
        env["CUDA_VISIBLE_DEVICES"] = ""
        env["EQ3_OUTPUT_DIR"] = str(output)
        env["EQ3_ARTIFACT_ROOT"] = str(root)
        ram_bytes = int(launch["limits"]["ram_gib"] * 1024 ** 3)

        def restrict_process():
            resource.setrlimit(resource.RLIMIT_AS, (ram_bytes, ram_bytes))

        process_started = time.monotonic()
        with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
            try:
                process = subprocess.Popen(
                    argv, cwd=output, env=env, stdout=stdout, stderr=stderr,
                    start_new_session=True, preexec_fn=restrict_process,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                _fail("BACKEND_START_FAILED", str(exc), False)
            while True:
                _record_memory_sample(process.pid, memory_samples)
                if process.poll() is not None:
                    break
                if time.monotonic() - process_started > launch["limits"]["watchdog_seconds"]:
                    _terminate(process)
                    _fail("WATCHDOG_EXPIRED", "backend exceeded its watchdog", True)
                if _output_bytes(output) > output_limit_bytes:
                    _terminate(process)
                    _fail("OUTPUT_LIMIT_EXCEEDED", "new output exceeded its byte budget", True)
                time.sleep(0.5)
        total = _output_bytes(output)
        if total > output_limit_bytes:
            _fail("OUTPUT_LIMIT_EXCEEDED", "new output exceeded its byte budget", True)
        if process.returncode != 0:
            _fail("BACKEND_FAILED", f"backend exited with status {process.returncode}", True)
    except LaunchError as exc:
        _best_effort_failed_receipt(
            output, launch, manifest, argv, start_utc, started, exc,
            process.returncode if process is not None else None,
            memory_samples,
        )
        raise
    except OSError as exc:
        error = LaunchError("INPUT_STAGING_FAILED", str(exc), process is not None)
        _best_effort_failed_receipt(
            output, launch, manifest, argv, start_utc, started, error,
            process.returncode if process is not None else None,
            memory_samples,
        )
        raise error from exc

    done = _receipt(
        launch, manifest, argv, output, start_utc, started, "DONE",
        process.returncode, True, None, memory_samples,
    )
    try:
        _write_receipt(output, "DONE.json", done)
    except OSError as exc:
        error = LaunchError("RECEIPT_WRITE_FAILED", str(exc), True)
        _best_effort_failed_receipt(
            output, launch, manifest, argv, start_utc, started, error, process.returncode,
            memory_samples,
        )
        raise error from exc
    result.update({"status": "COMPLETED", "launch_performed": True,
                   "output_path": launch["output"]["path"], "output_bytes": _output_bytes(output),
                   "returncode": process.returncode})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("check", "validate without executing"),
                            ("run", "execute one approved thermal-only run")):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--approval", type=Path, required=True)
        command.add_argument("--launch", type=Path, required=True)
        command.add_argument("--root", type=Path, default=Path.cwd())
        command.add_argument(
            "--code-root", type=Path,
            help="Git checkout to observe for revision/diff (defaults to --root)",
        )
    args = parser.parse_args(argv)
    try:
        manifest = load_json(args.manifest)
        approval = load_json(args.approval)
        launch = load_json(args.launch)
        code_root = args.code_root if args.code_root is not None else args.root
        observed_revision, observed_diff = observed_git_state(code_root)
        function = validate_launch if args.command == "check" else execute_launch
        result = function(manifest, approval, launch, args.launch, args.root,
                          approval_path=args.approval,
                          observed_code_revision=observed_revision,
                          observed_dirty_diff_sha256=observed_diff)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (GateError, LaunchError) as exc:
        print(json.dumps({"status": "REFUSED", "code": exc.code, "message": str(exc),
                          "launch_performed": getattr(exc, "launch_performed", False)},
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
